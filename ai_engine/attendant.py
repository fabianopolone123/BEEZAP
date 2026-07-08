"""Atendente virtual (IA) de recepcao.

Orquestra a conversa com o cliente numa conversa DIRETA: da boas-vindas,
entende a intencao e transfere para o setor certo, deixando a conversa
'Aguardando' (pending) um atendente humano. Para de agir assim que transfere
ou assim que um humano assume a conversa.

As FALAS do bot sao modelos de texto prontos (nao geradas pela IA). A IA local
so e usada para CLASSIFICAR a intencao em um setor (ver `classify_intent`), o
que mantem a tarefa dentro da capacidade de um modelo pequeno.
"""
import logging
import threading

from .services import classify_intent, generate_reply_and_route

ai_logger = logging.getLogger('beezap.ai')

# Fala minima usada SO quando a IA generativa nao respondeu (modelo local fora do
# ar), para o cliente nao ficar sem resposta. Nao e uma "mensagem padrao" de fluxo.
GENERATIVE_SAFETY_REPLY = 'So um momento, ja vou te ajudar.'

# Usada quando o modelo respondeu mas nao escreveu texto ao cliente (ex.: mandou
# so o marcador [SETOR: ...]). Evita o bot ficar mudo no modo generativo.
GENERATIVE_EMPTY_FALLBACK = 'Pode me contar, em poucas palavras, o que voce precisa?'


# --- Falas do bot (modelos de texto fixos) --------------------------------

CLARIFY_TEMPLATE = (
    'Desculpe, ainda nao entendi bem o que voce precisa. '
    'Pode me contar com um pouco mais de detalhe como posso ajudar?'
)

CLARIFY_AFTER_GREETING_TEMPLATE = (
    'Estou por aqui. Para te direcionar ao setor certo, me diga em poucas palavras '
    'o assunto do atendimento: financeiro, compras, suporte ou outro.'
)

CLARIFY_REPEAT_TEMPLATE = (
    'Ainda preciso entender o assunto para chamar o setor correto. '
    'Por exemplo: boleto, pagamento, compra, cotacao, produto ou suporte.'
)

NON_TEXT_TEMPLATE = (
    'Recebi seu envio! Para eu te direcionar certinho, '
    'pode me dizer em poucas palavras o que voce precisa?'
)

TRANSFER_TO_SECTOR_TEMPLATE = (
    'Perfeito! Vou te transferir para o setor de {setor}. '
    'Em instantes um de nossos atendentes continua com voce. \U0001F60A'
)

TRANSFER_GENERIC_TEMPLATE = (
    'Obrigado! Ja estou te encaminhando para um de nossos atendentes. '
    'Em instantes alguem continua o seu atendimento. \U0001F60A'
)

GREETING_TERMS = {
    'oi', 'ola', 'olá', 'bom dia', 'boa tarde', 'boa noite', 'e ai', 'e aí',
}


# --- Envio das falas -------------------------------------------------------

def _send_bot_message(conversation, text):
    """Envia uma fala do bot pelo W-API e salva como mensagem da IA (is_ai=True).

    Reusa o mesmo servico de envio/persistencia das mensagens do atendente.
    Nunca levanta excecao: em falha, apenas loga (sem expor token)."""
    from wapi.client import send_text_message
    from wapi.services import save_outgoing_text_message

    text = (text or '').strip()
    recipient = (conversation.recipient or '').strip()
    if not text or not recipient:
        return None

    try:
        result = send_text_message(phone=recipient, message=text)
    except Exception:
        ai_logger.exception('atendente IA: erro ao enviar mensagem (conv=%s).', conversation.id)
        return None

    if not result.success:
        ai_logger.warning('atendente IA: W-API recusou o envio (conv=%s).', conversation.id)
        return None

    return save_outgoing_text_message(
        conversation, text, external_message_id=result.message_id or '', status='sent', is_ai=True,
    )


# --- Maquina de estados ----------------------------------------------------

def _human_took_over(conversation):
    """True se um humano respondeu depois que a IA iniciou este atendimento.

    Mensagens antigas enviadas antes da IA existir/atuar nao podem bloquear uma
    nova recepcao. So consideramos "humano assumiu" quando ha fala humana
    posterior a uma fala da propria IA.
    """
    latest_ai = (
        conversation.messages
        .filter(direction='out', is_ai=True)
        .order_by('-created_at')
        .first()
    )
    if latest_ai is None:
        return False
    return conversation.messages.filter(
        direction='out', is_ai=False,
    ).filter(
        created_at__gt=latest_ai.created_at,
    ).exists() or conversation.messages.filter(
        direction='out', is_ai=False,
        created_at=latest_ai.created_at,
        id__gt=latest_ai.id,
    ).exists()


def _route_to_sector(conversation, sector, announce=True):
    """Transfere a conversa: define setor (pode ser None), marca Aguardando e
    encerra a atuacao da IA. Com `announce=True`, envia a fala de transferencia
    pronta; no modo generativo (`announce=False`) a propria IA ja avisou o cliente."""
    conversation.sector = sector
    conversation.status = 'pending'
    conversation.ai_state = 'handed_off'
    conversation.save(update_fields=['sector', 'status', 'ai_state', 'updated_at'])
    if announce:
        if sector is not None:
            _send_bot_message(conversation, TRANSFER_TO_SECTOR_TEMPLATE.format(setor=sector.name))
        else:
            _send_bot_message(conversation, TRANSFER_GENERIC_TEMPLATE)
    ai_logger.info(
        'atendente IA: conversa %s transferida (setor=%s).',
        conversation.id, sector.name if sector else '-',
    )


def _is_greeting(text):
    normalized = ' '.join((text or '').lower().split())
    return normalized in GREETING_TERMS


def _recent_customer_context(conversation, current_message, limit=5):
    """Monta uma janela curta do que o cliente disse durante a recepcao atual.

    A classificacao usa esse bloco para nao decidir olhando so a ultima mensagem
    solta. Mantemos apenas mensagens recebidas de texto e ignoramos falas da IA.
    """
    messages = list(
        conversation.messages
        .filter(direction='in', message_type='text')
        .order_by('-created_at')[:limit]
    )
    messages.reverse()
    parts = []
    seen_ids = set()
    for item in messages:
        seen_ids.add(item.id)
        text = (item.text or '').strip()
        if text:
            parts.append(text)
    if current_message.id not in seen_ids:
        text = (current_message.text or '').strip()
        if text:
            parts.append(text)
    return '\n'.join(parts[-limit:])


def _sibling_conversation_ids(conversation, limit=10):
    """Conversas ANTERIORES do mesmo contato (por contato ou, sem contato, pelo
    external_id da direta), da mais recente para a mais antiga."""
    from accounts.models import Conversation

    qs = Conversation.objects.exclude(id=conversation.id)
    if conversation.contact_id:
        qs = qs.filter(contact_id=conversation.contact_id)
    elif conversation.external_id:
        qs = qs.filter(external_id=conversation.external_id, chat_type='private')
    else:
        return []
    return list(
        qs.order_by('-last_message_at', '-created_at').values_list('id', flat=True)[:limit]
    )


# Quantas mensagens de conversas ANTERIORES do contato entram no contexto da IA.
# ~10 trocas (cliente + atendimento) = ~20 mensagens.
CONTACT_HISTORY_MAX_MESSAGES = 20


def _contact_history_context(conversation, limit_msgs=CONTACT_HISTORY_MAX_MESSAGES):
    """Resumo das ultimas mensagens de texto em conversas ANTERIORES com o mesmo
    contato (~10 trocas), para a IA se inteirar do historico. Vazio se nao houver."""
    from accounts.models import Message

    conv_ids = _sibling_conversation_ids(conversation)
    if not conv_ids:
        return ''
    messages = list(
        Message.objects
        .filter(conversation_id__in=conv_ids, message_type='text')
        .exclude(text='')
        .order_by('-created_at')[:limit_msgs]
    )
    messages.reverse()
    lines = []
    for item in messages:
        text = (item.text or '').strip()
        if not text:
            continue
        who = 'Cliente' if item.direction == 'in' else 'Atendimento'
        lines.append(f'{who}: {text[:200]}')
    return '\n'.join(lines)


def _conversation_transcript(conversation, current_message, limit=8):
    """Transcricao curta da conversa ATUAL (Cliente/Voce) para o modo generativo,
    garantindo que a mensagem atual do cliente esteja no final."""
    messages = list(
        conversation.messages
        .filter(message_type='text')
        .order_by('-created_at')[:limit]
    )
    messages.reverse()
    seen = set()
    lines = []
    for item in messages:
        seen.add(item.id)
        text = (item.text or '').strip()
        if not text:
            continue
        who = 'Voce' if item.direction == 'out' else 'Cliente'
        lines.append(f'{who}: {text}')
    if current_message.id not in seen:
        text = (current_message.text or '').strip()
        if text:
            lines.append(f'Cliente: {text}')
    return '\n'.join(lines[-limit:])


def _handle_generative_turn(conversation, config, message, sectors):
    """Modo generativo: a IA escreve a resposta ao cliente e decide o roteamento.

    Nao usa as falas prontas (boas-vindas/esclarecer/transferir); a propria IA
    conduz a conversa. `_route_to_sector(..., announce=False)` so muda o estado,
    pois a fala de transferencia ja foi enviada pela IA."""
    result = generate_reply_and_route(
        config.render_instructions(),
        sectors,
        _conversation_transcript(conversation, message),
        history=_contact_history_context(conversation),
        fallback_sector=config.fallback_sector,
    )

    # Log de diagnostico: mostra o que o modelo respondeu e a decisao tomada.
    ai_logger.info(
        'atendente IA (generativo) conv=%s: action=%s setor=%s available=%s reply=%r raw=%r',
        conversation.id, result.action,
        result.sector.name if result.sector else '-', result.available,
        (result.reply or '')[:120], (result.raw or '')[:200],
    )

    # A IA decidiu o setor: fala (se escreveu algo) e transfere sem template extra.
    # Se nao escreveu nada, transfere AVISANDO com a fala pronta (nunca mudo).
    if result.action == 'route':
        if result.reply:
            _send_bot_message(conversation, result.reply)
            _route_to_sector(conversation, result.sector, announce=False)
        else:
            _route_to_sector(conversation, result.sector, announce=True)
        return

    # 'continue': ainda conversando. Conta o turno.
    conversation.ai_turns = conversation.ai_turns + 1
    conversation.save(update_fields=['ai_turns', 'updated_at'])

    # Atingiu o limite de tentativas: encaminha AVISANDO o cliente (nunca fica
    # mudo), em vez de largar a conversa numa fila silenciosa.
    if conversation.ai_turns >= config.max_turns:
        _route_to_sector(conversation, config.fallback_sector, announce=True)
        return

    # Ainda dentro do limite: envia a fala da IA. Se o modelo nao escreveu texto
    # (so o marcador) usa o fallback de esclarecimento; se estava fora do ar, a
    # fala de seguranca. NUNCA fica mudo.
    reply = result.reply
    if not reply:
        reply = GENERATIVE_SAFETY_REPLY if not result.available else GENERATIVE_EMPTY_FALLBACK
    _send_bot_message(conversation, reply)


def _clarify_text_for_turn(conversation, message):
    if _is_greeting(message.text):
        return CLARIFY_AFTER_GREETING_TEMPLATE
    if conversation.ai_turns > 1:
        return CLARIFY_REPEAT_TEMPLATE
    return CLARIFY_TEMPLATE


def _handle_undefined_turn(conversation, config, clarify_text):
    """Intencao ainda indefinida: pede para esclarecer ate `max_turns`; ao atingir
    o limite, transfere assim mesmo para o setor de fallback (ou sem setor)."""
    if conversation.ai_turns < config.max_turns:
        _send_bot_message(conversation, clarify_text)
        conversation.ai_turns = conversation.ai_turns + 1
        conversation.save(update_fields=['ai_turns', 'updated_at'])
        return
    _route_to_sector(conversation, config.fallback_sector)


def handle_incoming_for_ai(conversation, message):
    """Processa uma mensagem recebida com o atendente virtual (sincrono).

    Aplica as guardas, executa a maquina de estados e envia as falas. Nao roda em
    grupo, em conversa fechada/atribuida, com a IA desligada, nem quando um humano
    ja assumiu. Seguro para chamar em background."""
    from accounts.models import AiAttendantConfig, Sector

    config = AiAttendantConfig.get_solo()
    if not config.enabled:
        return
    if conversation.chat_type != 'private':
        return
    if conversation.status == 'closed':
        return
    if conversation.assigned_attendant_id is not None:
        ai_logger.info('atendente IA: conv=%s ignorada (ja tem atendente).', conversation.id)
        return
    if conversation.sector_id is not None:
        ai_logger.info('atendente IA: conv=%s ignorada (ja roteada para um setor).', conversation.id)
        return
    if message.direction != 'in':
        return
    if conversation.ai_state == 'off' and not _human_took_over(conversation):
        conversation.ai_state = 'active'
        conversation.save(update_fields=['ai_state', 'updated_at'])
    if conversation.ai_state != 'active':
        ai_logger.info('atendente IA: conv=%s ignorada (ai_state=%s).', conversation.id, conversation.ai_state)
        return

    if _human_took_over(conversation):
        conversation.ai_state = 'off'
        conversation.save(update_fields=['ai_state', 'updated_at'])
        return

    # Modo generativo: a IA escreve as respostas (sem mensagens prontas).
    if config.generative_replies:
        _handle_generative_turn(conversation, config, message, list(Sector.objects.all()))
        return

    # 1a interacao: boas-vindas + pergunta (nao classifica ainda).
    if conversation.ai_turns == 0:
        _send_bot_message(conversation, config.render_welcome())
        conversation.ai_turns = 1
        conversation.save(update_fields=['ai_turns', 'updated_at'])
        return

    # Mensagem sem texto util (foto/audio/etc.): pede para descrever em palavras.
    if message.message_type != 'text' or not (message.text or '').strip():
        _handle_undefined_turn(conversation, config, NON_TEXT_TEMPLATE)
        return

    sectors = list(Sector.objects.all())
    intent = classify_intent(
        _recent_customer_context(conversation, message),
        sectors,
        llm_only=config.llm_only,
        history=_contact_history_context(conversation),
        instructions=config.render_instructions(),
    )
    if intent.decided:
        _route_to_sector(conversation, intent.sector)
        return
    _handle_undefined_turn(conversation, config, _clarify_text_for_turn(conversation, message))


# --- Disparo em background -------------------------------------------------
# Evita rodar dois processamentos em paralelo para a mesma conversa (serializa
# por conversa) e nunca bloqueia o webhook.
_ai_lock = threading.Lock()
_ai_active = set()


def handle_incoming_for_ai_async(conversation, message):
    """Dispara `handle_incoming_for_ai` em uma thread daemon.

    Recarrega os objetos por id dentro da thread (conexao de banco propria) e
    fecha a conexao ao fim, no mesmo padrao de `retry_conversation_media_async`.
    """
    from accounts.models import AiAttendantConfig

    # Guardas baratas antes de gastar uma thread: so conversa direta recebida e
    # apenas quando o atendente virtual esta ligado.
    if conversation.chat_type != 'private' or message.direction != 'in':
        return False
    if not AiAttendantConfig.get_solo().enabled:
        return False

    conversation_id = conversation.id
    message_id = message.id

    with _ai_lock:
        if conversation_id in _ai_active:
            return False
        _ai_active.add(conversation_id)

    def _worker():
        from django.db import connection
        from accounts.models import Conversation, Message
        try:
            conv = Conversation.objects.filter(pk=conversation_id).first()
            msg = Message.objects.filter(pk=message_id).first()
            if conv is not None and msg is not None:
                handle_incoming_for_ai(conv, msg)
        except Exception:
            ai_logger.exception('atendente IA: erro ao processar (conv=%s).', conversation_id)
        finally:
            connection.close()
            with _ai_lock:
                _ai_active.discard(conversation_id)

    threading.Thread(target=_worker, name='ai-attendant-%s' % conversation_id, daemon=True).start()
    return True
