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

from .services import classify_intent

ai_logger = logging.getLogger('beezap.ai')


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


def _route_to_sector(conversation, sector):
    """Transfere a conversa: define setor (pode ser None), marca Aguardando e
    encerra a atuacao da IA, avisando o cliente com a fala de transferencia."""
    conversation.sector = sector
    conversation.status = 'pending'
    conversation.ai_state = 'handed_off'
    conversation.save(update_fields=['sector', 'status', 'ai_state', 'updated_at'])
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


def _sibling_conversation_ids(conversation, limit=5):
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


def _contact_history_context(conversation, limit_msgs=8):
    """Resumo curto das ultimas mensagens de texto em conversas ANTERIORES com o
    mesmo contato, para a IA se inteirar do historico. Vazio se nao houver."""
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
        return
    if conversation.sector_id is not None:
        return
    if message.direction != 'in':
        return
    if conversation.ai_state == 'off' and not _human_took_over(conversation):
        conversation.ai_state = 'active'
        conversation.save(update_fields=['ai_state', 'updated_at'])
    if conversation.ai_state != 'active':
        return

    if _human_took_over(conversation):
        conversation.ai_state = 'off'
        conversation.save(update_fields=['ai_state', 'updated_at'])
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
