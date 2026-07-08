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
    """True se existe qualquer mensagem enviada que NAO foi do bot (humano assumiu,
    inclusive respondendo pelo proprio celular -> chega como from_me/out)."""
    return conversation.messages.filter(direction='out', is_ai=False).exists()


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
    if conversation.ai_state != 'active':
        return
    if message.direction != 'in':
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
    intent = classify_intent(message.text, sectors)
    if intent.decided:
        _route_to_sector(conversation, intent.sector)
        return
    _handle_undefined_turn(conversation, config, CLARIFY_TEMPLATE)


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
