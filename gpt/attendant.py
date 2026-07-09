"""Atendente virtual (IA / GPT) — recepcao/triagem do primeiro atendimento.

A IA faz o PRIMEIRO atendimento de conversas DIRETAS que ainda nao tem setor
nem atendente: cumprimenta conforme o horario, entende o que o cliente precisa e
encaminha para o setor certo (ou para o atendente citado). Ao encaminhar, ela sai
de cena e a conversa fica em aberto para o setor pegar.

Contexto enviado ao GPT (montado automaticamente):
  - o prompt/persona (OpenAiConfiguration.instructions);
  - a data/hora atual (para a saudacao certa);
  - os setores disponiveis (nome + descricao);
  - os atendentes cadastrados (nome + setor);
  - as ultimas ~5 trocas (ate CONTEXT_MESSAGES mensagens) cliente<->IA;
  - a mensagem atual do cliente (ultima do historico).

Roda SEMPRE em background (thread) para nunca travar o recebimento do webhook.
Nunca levanta excecao para fora. A IA so atua com o interruptor `enabled` ligado.
"""

import json
import logging
import threading

from django.db.models import F
from django.utils import timezone

from accounts.models import (
    Attendant,
    Conversation,
    OpenAiConfiguration,
    Sector,
)

ai_logger = logging.getLogger('beezap.gpt')

# Ate ~5 trocas cliente<->IA (10 mensagens) de contexto.
CONTEXT_MESSAGES = 10

# Persona padrao (editavel na tela). Os setores/atendentes/mensagens sao anexados
# automaticamente pelo codigo — nao precisam estar aqui.
DEFAULT_INSTRUCTIONS = (
    'Voce e um atendente virtual da BEEZAP, simpatico, educado e objetivo. '
    'Seu papel e dar o primeiro atendimento: cumprimente o cliente conforme o '
    'horario, pergunte como pode ajudar e entenda qual e a necessidade dele. '
    'Quando entender o que o cliente precisa, encaminhe para o setor certo (ou '
    'para o atendente que o cliente citar, se ele pedir alguem especifico) e avise '
    'o cliente de forma breve. Se o pedido estiver vago, faca perguntas curtas para '
    'entender melhor. Nunca invente informacoes, nao prometa prazos e nao peca dados '
    'sensiveis. Responda sempre em portugues, com mensagens curtas e claras.'
)

# Regra de formato SEMPRE anexada (garante saida parseavel, mesmo com prompt livre).
OUTPUT_RULE = (
    'Responda SEMPRE em JSON valido, sem nenhum texto fora do JSON, exatamente '
    'neste formato: {"mensagem": "<texto para enviar ao cliente>", '
    '"setor": "<nome exato de um setor da lista, ou vazio>", '
    '"atendente": "<nome exato de um atendente da lista, ou vazio>"}. '
    'Preencha "setor" OU "atendente" somente quando tiver certeza de para onde '
    'encaminhar; caso contrario, deixe os dois vazios e use "mensagem" para '
    'continuar o atendimento. Nao preencha os dois ao mesmo tempo.'
)

# Fala fixa de encaminhamento (usada no fallback quando nao ha resposta da IA).
HANDOFF_NOTICE = 'Vou te encaminhar para o nosso atendimento. So um momento, por favor.'


def _greeting_for(now):
    hour = now.hour
    if 5 <= hour < 12:
        return 'Bom dia'
    if 12 <= hour < 18:
        return 'Boa tarde'
    return 'Boa noite'


def available_sectors():
    return list(Sector.objects.all().order_by('name'))


def available_attendants():
    return list(
        Attendant.objects.filter(user__is_active=True)
        .prefetch_related('sectors')
        .order_by('name')
    )


def sectors_context_text(sectors=None):
    sectors = sectors if sectors is not None else available_sectors()
    if not sectors:
        return '(nenhum setor cadastrado)'
    lines = []
    for sector in sectors:
        desc = (sector.description or '').strip()
        lines.append(f'- {sector.name}: {desc}' if desc else f'- {sector.name}')
    return '\n'.join(lines)


def attendants_context_text(attendants=None):
    attendants = attendants if attendants is not None else available_attendants()
    if not attendants:
        return '(nenhum atendente cadastrado)'
    lines = []
    for attendant in attendants:
        secs = ', '.join(sec.name for sec in attendant.sectors.all())
        lines.append(f'- {attendant.name} (setor: {secs})' if secs else f'- {attendant.name}')
    return '\n'.join(lines)


def resolved_instructions(config):
    return (config.instructions or '').strip() or DEFAULT_INSTRUCTIONS


def build_system_prompt(config, now=None):
    """Monta o prompt de sistema: persona + horario + setores + atendentes + formato."""
    now = now or timezone.localtime()
    greeting = _greeting_for(now)
    return '\n\n'.join([
        resolved_instructions(config),
        f'Data e hora atual: {now.strftime("%d/%m/%Y %H:%M")}. '
        f'Saudacao adequada para agora: "{greeting}".',
        'Setores disponiveis para transferencia:\n' + sectors_context_text(),
        'Atendentes cadastrados:\n' + attendants_context_text(),
        OUTPUT_RULE,
    ])


def _message_role_text(message):
    """Converte uma Message para (role, texto) do formato do GPT."""
    role = 'assistant' if message.direction == 'out' else 'user'
    text = (message.text or '').strip()
    if not text and message.message_type != 'text':
        label = message.get_message_type_display()
        text = (f'[cliente enviou: {label}]' if message.direction == 'in'
                else f'[enviado: {label}]')
    return role, text


def build_history(conversation):
    """Ultimas CONTEXT_MESSAGES mensagens reais (sem divisorias) em ordem cronologica."""
    messages = list(
        conversation.messages
        .exclude(message_type='system')
        .order_by('-created_at')[:CONTEXT_MESSAGES]
    )
    messages.reverse()
    history = []
    for message in messages:
        role, text = _message_role_text(message)
        if text:
            history.append({'role': role, 'content': text})
    return history


def _parse_decision(raw):
    """Le {mensagem, setor, atendente} da saida JSON do GPT (tolerante)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        return {
            'mensagem': str(data.get('mensagem') or data.get('message') or '').strip(),
            'setor': str(data.get('setor') or data.get('sector') or '').strip(),
            'atendente': str(data.get('atendente') or data.get('attendant') or '').strip(),
        }
    # Se nao veio JSON, trata o texto cru como mensagem ao cliente.
    return {'mensagem': (raw or '').strip(), 'setor': '', 'atendente': ''}


def _match_sector(name):
    name = (name or '').strip()
    if not name:
        return None
    return Sector.objects.filter(name__iexact=name).first()


def _match_attendant(name):
    name = (name or '').strip()
    if not name:
        return None
    return Attendant.objects.filter(name__iexact=name, user__is_active=True).first()


def _human_replied_in_segment(conversation):
    """Um humano ja respondeu neste atendimento? (a IA nao deve falar por cima).

    Considera mensagens enviadas (out), NAO-IA e nao-sistema, depois da ultima
    divisoria de atendimento."""
    last_divider = (
        conversation.messages.filter(message_type='system')
        .order_by('-created_at').first()
    )
    qs = (
        conversation.messages
        .filter(direction='out', is_ai=False)
        .exclude(message_type='system')
    )
    if last_divider:
        qs = qs.filter(created_at__gt=last_divider.created_at)
    return qs.exists()


def _send_ai_reply(conversation, text):
    """Envia a fala da IA ao cliente pela W-API e salva como mensagem da IA."""
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
    ai_logger.warning('IA nao conseguiu enviar resposta (conv=%s): %s', conversation.id, result.error)
    return False


def _route_to_sector(conversation, sector):
    """Encaminha para um setor (fila): setor + status pendente, sem atendente."""
    from wapi.services import save_system_message
    Conversation.objects.filter(pk=conversation.id).update(
        sector=sector, assigned_attendant=None, status='pending', ai_turns=0,
    )
    save_system_message(conversation, f'Encaminhado para o setor {sector.name} pela IA')
    ai_logger.info('IA encaminhou conv=%s para setor=%s', conversation.id, sector.name)


def _route_to_attendant(conversation, attendant):
    """Encaminha para um atendente especifico (assume o setor dele, se houver)."""
    from wapi.services import save_system_message
    sector = attendant.sectors.first()
    Conversation.objects.filter(pk=conversation.id).update(
        assigned_attendant=attendant, sector=sector, status='open', ai_turns=0,
    )
    save_system_message(conversation, f'Encaminhado para {attendant.name} pela IA')
    ai_logger.info('IA encaminhou conv=%s para atendente=%s', conversation.id, attendant.name)


def _resolve_fallback_sector(config):
    if config.fallback_sector_id:
        return config.fallback_sector
    # Sem fallback configurado: tenta um setor chamado "Geral".
    return Sector.objects.filter(name__iexact='Geral').first()


def _handoff_to_fallback(conversation, config, reply=''):
    """Encaminha para o setor de fallback (ou deixa em aberto se nao houver)."""
    _send_ai_reply(conversation, reply or HANDOFF_NOTICE)
    fallback = _resolve_fallback_sector(config)
    if fallback:
        _route_to_sector(conversation, fallback)
    else:
        # Sem fallback: deixa aguardando, sem setor, para qualquer atendente pegar.
        Conversation.objects.filter(pk=conversation.id).update(status='pending', ai_turns=0)
        ai_logger.info('IA sem fallback: conv=%s deixada aguardando sem setor', conversation.id)


def _should_handle(conversation):
    """Retorna a config se a IA deve atuar nesta conversa, senao None."""
    config = OpenAiConfiguration.get_solo()
    if not config.enabled or not config.has_api_key:
        return None
    if conversation.chat_type != 'private':
        return None
    if conversation.status == 'closed':
        return None
    if conversation.assigned_attendant_id or conversation.sector_id:
        return None
    return config


def handle_incoming_for_ai(conversation_id):
    """Processa (sincrono) uma mensagem recebida com a IA de recepcao.

    Chamado em background por handle_incoming_for_ai_async. Nunca lanca excecao
    para fora do worker."""
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
        ai_logger.info('IA nao atua (humano ja respondeu) conv=%s', conversation_id)
        return

    # Limite de seguranca: se ja atingiu max_turns sem decidir, encaminha ao fallback.
    if conversation.ai_turns >= config.max_turns:
        _handoff_to_fallback(conversation, config)
        return

    history = build_history(conversation)
    if not history:
        return

    messages = [{'role': 'system', 'content': build_system_prompt(config)}] + history

    from gpt.client import chat_completion
    result = chat_completion(
        messages, temperature=0.3, max_tokens=400,
        response_format={'type': 'json_object'},
    )
    if not result.success:
        ai_logger.warning('IA/GPT falhou (conv=%s modelo=%s): %s',
                          conversation_id, result.model, result.error)
        return

    decision = _parse_decision(result.text)
    reply = decision['mensagem']
    attendant = _match_attendant(decision['atendente'])
    sector = _match_sector(decision['setor'])

    if attendant:
        _send_ai_reply(conversation, reply)
        _route_to_attendant(conversation, attendant)
        return
    if sector:
        _send_ai_reply(conversation, reply)
        _route_to_sector(conversation, sector)
        return

    # Nao decidiu o destino: conta o turno. Se atingir o limite agora, encaminha
    # ao fallback (avisando); senao, envia a fala de esclarecimento e segue.
    new_turns = conversation.ai_turns + 1
    if new_turns >= config.max_turns:
        _handoff_to_fallback(conversation, config, reply=reply)
    else:
        _send_ai_reply(conversation, reply)
        Conversation.objects.filter(pk=conversation.id).update(ai_turns=new_turns)


# Evita processar a mesma conversa em paralelo (mensagens em rajada).
_ai_lock = threading.Lock()
_ai_active = set()


def handle_incoming_for_ai_async(conversation_id):
    """Dispara o atendente virtual em background (thread daemon).

    Nunca bloqueia o recebimento do webhook. Evita rodar em paralelo para a mesma
    conversa e fecha a conexao de banco ao fim (padrao de retry_conversation_media_async)."""
    with _ai_lock:
        if conversation_id in _ai_active:
            return False
        _ai_active.add(conversation_id)

    def _worker():
        from django.db import connection
        try:
            handle_incoming_for_ai(conversation_id)
        except Exception:
            ai_logger.exception('Falha no atendimento IA (conv=%s).', conversation_id)
        finally:
            connection.close()
            with _ai_lock:
                _ai_active.discard(conversation_id)

    threading.Thread(target=_worker, name=f'ai-{conversation_id}', daemon=True).start()
    return True
