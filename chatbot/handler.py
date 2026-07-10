"""Chatbot de menu — atendimento automatico SEM IA (sem custo).

Faz o PRIMEIRO atendimento de conversas DIRETAS que ainda nao tem setor nem
atendente, por um menu numerado fixo (nada de IA):

  1. No primeiro contato do atendimento, envia a saudacao + o menu.
  2. A cada mensagem seguinte, interpreta o texto como a escolha de uma opcao:
     - opcao valida -> avisa o cliente (mensagem de confirmacao) e encaminha para
       o setor da opcao (conversa fica "pending" para o setor pegar);
     - opcao invalida -> reexibe o menu, contando a tentativa;
     - apos `max_attempts` tentativas invalidas -> avisa que nao entendeu e
       encaminha para um atendente humano (setor de fallback, ou deixa aguardando).

Espelha a estrutura do atendente virtual (`gpt/attendant.py`): roda SEMPRE em
background (thread), nunca levanta excecao para fora e so atua quando o MODO mestre
(`MenuBotConfiguration.mode`) esta em `menu`. Reaproveita as divisorias de
atendimento e o contador `Conversation.ai_turns` (aqui = tentativas invalidas).
"""

import logging
import threading

from accounts.models import Conversation, MenuBotConfiguration

bot_logger = logging.getLogger('beezap.chatbot')

# Textos padrao do menu (editaveis na tela Atendimento). {saudacao} vira a saudacao
# do horario; {setor}, na confirmacao, vira o nome do setor escolhido.
DEFAULT_GREETING = 'Ola, {saudacao}! Seja bem-vindo(a) a BEEZAP. 😊'
DEFAULT_MENU_INTRO = (
    'Para agilizar o seu atendimento, digite o numero da opcao desejada:'
)
DEFAULT_INVALID_MESSAGE = (
    'Nao entendi a sua resposta. Por favor, digite apenas o numero de uma das '
    'opcoes do menu.'
)
DEFAULT_CONFIRMATION_MESSAGE = (
    'Certo! Vou te encaminhar para o setor {setor}. Em instantes um atendente '
    'continua o seu atendimento por aqui. 🙂'
)
DEFAULT_HANDOFF_MESSAGE = (
    'Desculpe, nao consegui entender a sua escolha. Vou pedir para um de nossos '
    'atendentes falar com voce. So um momento, por favor.'
)


def _greeting_for(now):
    hour = now.hour
    if 5 <= hour < 12:
        return 'Bom dia'
    if 12 <= hour < 18:
        return 'Boa tarde'
    return 'Boa noite'


def resolved_greeting(config):
    return (config.greeting or '').strip() or DEFAULT_GREETING


def resolved_menu_intro(config):
    return (config.menu_intro or '').strip() or DEFAULT_MENU_INTRO


def resolved_invalid_message(config):
    return (config.invalid_message or '').strip() or DEFAULT_INVALID_MESSAGE


def resolved_confirmation_message(config):
    return (config.confirmation_message or '').strip() or DEFAULT_CONFIRMATION_MESSAGE


def resolved_handoff_message(config):
    return (config.handoff_message or '').strip() or DEFAULT_HANDOFF_MESSAGE


def _options_text(config):
    """Linhas numeradas do menu: '1 - Financeiro'. Ignora opcoes sem setor."""
    lines = []
    for option in config.ordered_options():
        label = (option.label or '').strip()
        if not label:
            continue
        lines.append(f'{option.order} - {label}')
    return '\n'.join(lines)


def build_menu_text(config, now=None, include_greeting=True):
    """Monta o texto enviado ao cliente: saudacao (opcional) + intro + opcoes."""
    from django.utils import timezone

    now = now or timezone.localtime()
    parts = []
    if include_greeting:
        greeting = resolved_greeting(config).replace('{saudacao}', _greeting_for(now))
        parts.append(greeting)
    intro = resolved_menu_intro(config)
    options = _options_text(config)
    parts.append(f'{intro}\n\n{options}' if options else intro)
    return '\n\n'.join(p for p in parts if p.strip())


def render_confirmation(config, sector):
    text = resolved_confirmation_message(config)
    return text.replace('{setor}', sector.name if sector else '')


def _match_option(config, text):
    """Acha a opcao escolhida pelo texto do cliente. Aceita o numero da opcao
    (ex.: '1', ' 2 ', '3.') ou o nome exato do setor/rotulo (sem diferenciar
    maiusculas). Retorna a MenuOption ou None."""
    raw = (text or '').strip()
    if not raw:
        return None
    options = config.ordered_options()
    # Por numero digitado (pega o primeiro grupo de digitos, ex.: '1.' -> '1').
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if digits:
        for option in options:
            if str(option.order) == digits:
                return option
    # Por nome (rotulo ou setor), tolerante.
    low = raw.lower()
    for option in options:
        if (option.label or '').strip().lower() == low:
            return option
        if option.sector and option.sector.name.strip().lower() == low:
            return option
    return None


def _last_divider_time(conversation):
    divider = (
        conversation.messages.filter(message_type='system')
        .order_by('-created_at').first()
    )
    return divider.created_at if divider else None


def _menu_already_presented(conversation):
    """O menu ja foi enviado neste atendimento? (mensagem automatica `out` apos a
    ultima divisoria). Se sim, a proxima mensagem do cliente e uma ESCOLHA."""
    qs = (
        conversation.messages
        .filter(direction='out', is_ai=True)
        .exclude(message_type='system')
    )
    since = _last_divider_time(conversation)
    if since:
        qs = qs.filter(created_at__gt=since)
    return qs.exists()


def _human_replied_in_segment(conversation):
    """Um humano ja respondeu neste atendimento? (o bot nao fala por cima).

    Mensagens enviadas (out), NAO-automaticas (is_ai=False) e nao-sistema, depois
    da ultima divisoria."""
    qs = (
        conversation.messages
        .filter(direction='out', is_ai=False)
        .exclude(message_type='system')
    )
    since = _last_divider_time(conversation)
    if since:
        qs = qs.filter(created_at__gt=since)
    return qs.exists()


def _current_incoming(conversation):
    """Ultima mensagem RECEBIDA (nao-sistema) do atendimento atual — a escolha do
    cliente a interpretar."""
    qs = conversation.messages.filter(direction='in').exclude(message_type='system')
    since = _last_divider_time(conversation)
    if since:
        qs = qs.filter(created_at__gt=since)
    return qs.order_by('-created_at').first()


def _send_reply(conversation, text):
    """Envia uma fala do bot ao cliente pela W-API e salva como mensagem automatica."""
    text = (text or '').strip()
    if not text:
        return False
    from wapi.client import send_text_message
    from wapi.services import save_outgoing_text_message
    result = send_text_message(conversation.recipient, text)
    if result.success:
        save_outgoing_text_message(
            conversation, text, external_message_id=result.message_id or '', is_ai=True
        )
        return True
    bot_logger.warning('Chatbot nao conseguiu enviar resposta (conv=%s): %s',
                       conversation.id, result.error)
    return False


def _route_to_sector(conversation, sector, confirmation=''):
    """Avisa o cliente (se houver confirmacao) e encaminha para o setor: fica
    AGUARDANDO na fila do setor (pendente, sem atribuir a ninguem). NAO insere
    divisoria — o atendimento e o mesmo, entao quem assumir ve todo o historico
    (inclusive a conversa do menu com o cliente)."""
    if confirmation:
        _send_reply(conversation, confirmation)
    Conversation.objects.filter(pk=conversation.id).update(
        sector=sector, assigned_attendant=None, status='pending', ai_turns=0,
    )
    bot_logger.info('Chatbot encaminhou conv=%s para setor=%s (aguardando)', conversation.id, sector.name)


def _handoff(conversation, config):
    """Desiste do menu AVISANDO o cliente e encaminha para o fallback (ou deixa
    aguardando um atendente, sem setor). Sem divisoria (ver _route_to_sector)."""
    _send_reply(conversation, resolved_handoff_message(config))
    fallback = config.fallback_sector
    if fallback:
        Conversation.objects.filter(pk=conversation.id).update(
            sector=fallback, assigned_attendant=None, status='pending', ai_turns=0,
        )
        bot_logger.info('Chatbot handoff conv=%s -> setor=%s', conversation.id, fallback.name)
    else:
        Conversation.objects.filter(pk=conversation.id).update(
            status='pending', ai_turns=config.max_attempts,
        )
        bot_logger.info('Chatbot handoff conv=%s sem setor (aguardando humano)', conversation.id)


def _should_handle(conversation):
    """Retorna a config se o chatbot de menu deve atuar nesta conversa, senao None."""
    config = MenuBotConfiguration.get_solo()
    if config.mode != MenuBotConfiguration.MODE_MENU:
        return None
    if conversation.chat_type != 'private':
        return None
    if conversation.status == 'closed':
        return None
    if conversation.assigned_attendant_id or conversation.sector_id:
        return None
    return config


def handle_incoming_for_menu(conversation_id):
    """Processa (sincrono) uma mensagem recebida com o chatbot de menu.

    Chamado em background por handle_incoming_for_menu_async."""
    conversation = (
        Conversation.objects
        .select_related('contact', 'assigned_attendant', 'sector')
        .filter(pk=conversation_id)
        .first()
    )
    if conversation is None:
        return
    config = _should_handle(conversation)
    if config is None:
        return
    if _human_replied_in_segment(conversation):
        bot_logger.info('Chatbot nao atua (humano ja respondeu) conv=%s', conversation_id)
        return

    # Primeiro contato deste atendimento: manda a saudacao + o menu e aguarda.
    if not _menu_already_presented(conversation):
        _send_reply(conversation, build_menu_text(config))
        return

    incoming = _current_incoming(conversation)
    if incoming is None:
        return

    option = _match_option(config, incoming.text)
    if option and option.sector_id:
        _route_to_sector(conversation, option.sector, render_confirmation(config, option.sector))
        return

    # Escolha invalida: conta a tentativa. Se estourou o limite, desiste avisando
    # e encaminha ao fallback; senao reexibe o menu.
    new_attempts = conversation.ai_turns + 1
    if new_attempts >= config.max_attempts:
        _handoff(conversation, config)
    else:
        _send_reply(
            conversation,
            resolved_invalid_message(config) + '\n\n' + build_menu_text(config, include_greeting=False),
        )
        Conversation.objects.filter(pk=conversation.id).update(ai_turns=new_attempts)


# Evita processar a mesma conversa em paralelo (mensagens em rajada).
_menu_lock = threading.Lock()
_menu_active = set()


def handle_incoming_for_menu_async(conversation_id):
    """Dispara o chatbot de menu em background (thread daemon).

    Nunca bloqueia o recebimento do webhook. Evita rodar em paralelo para a mesma
    conversa e fecha a conexao de banco ao fim (padrao de handle_incoming_for_ai_async)."""
    with _menu_lock:
        if conversation_id in _menu_active:
            return False
        _menu_active.add(conversation_id)

    def _worker():
        from django.db import connection
        try:
            handle_incoming_for_menu(conversation_id)
        except Exception:
            bot_logger.exception('Falha no chatbot de menu (conv=%s).', conversation_id)
        finally:
            connection.close()
            with _menu_lock:
                _menu_active.discard(conversation_id)

    threading.Thread(target=_worker, name=f'menu-{conversation_id}', daemon=True).start()
    return True
