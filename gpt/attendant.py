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
Nunca levanta excecao para fora. A IA so atua quando o MODO mestre da empresa e `ai`
(`MenuBotConfiguration.mode`) — ver `_should_handle`; o antigo interruptor
`OpenAiConfiguration.enabled` nao existe mais.

LIMITE DE TENTATIVAS (`max_turns`) — conta TRIAGEM, nao resposta:
  - `Conversation.ai_turns` so anda quando a mensagem do cliente traz algo para
    triar (`_message_has_intent`): saudacao/ping ("oi", "teste") nao gastam tentativa;
  - o modelo e AVISADO quando esta na ultima tentativa (`FINAL_TURN_RULE`), para
    decidir o setor em vez de fazer mais uma pergunta que seria descartada;
  - ao desistir, o aviso NOMEIA o setor de destino (`HANDOFF_NOTICE_TEMPLATE`);
  - `MAX_REPLIES_PER_SEGMENT` e o teto absoluto de falas, para saudacao repetida
    nao virar conversa infinita.
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

# Prompt padrao (persona + REGRAS DE COMPORTAMENTO) — totalmente editavel na tela.
# O codigo ainda anexa automaticamente APENAS os dados dinamicos (data/hora, tempo
# desde a ultima mensagem, lista de setores/atendentes, qual e o setor geral) e a
# regra de formato JSON (necessaria para o sistema ler a resposta).
DEFAULT_INSTRUCTIONS = (
    'Voce e o atendente virtual do WhatsApp da empresa indicada no contexto. '
    'Sua funcao e fazer apenas o PRIMEIRO atendimento: acolher o cliente, entender '
    'a necessidade e encaminhar para o lugar certo. Voce NAO resolve o assunto em si.\n\n'
    'Como se comportar:\n'
    '- Seja simpatico, educado, paciente e objetivo. Nunca responda de forma seca ou rispida.\n'
    '- Seja BREVE: no maximo 1 ou 2 frases curtas por mensagem, em tom de conversa de '
    'WhatsApp. Nao escreva textos longos. So liste opcoes quando o cliente disser que '
    'nao sabe do que precisa — nesse caso, listar e o que resolve.\n'
    '- Comece a PRIMEIRA mensagem do atendimento com a saudacao do horario (bom dia, boa '
    'tarde ou boa noite, conforme indicado no contexto). Nao use apenas "Ola" e nao repita '
    'a saudacao nas mensagens seguintes.\n'
    '- Apresente-se brevemente como atendente virtual da empresa (use o nome dela, que '
    'esta no contexto) no inicio da conversa, ou novamente se ja fizer bastante tempo '
    'desde a ultima mensagem. Nunca cite o nome do sistema que voce usa.\n'
    '- Pergunte de forma clara como pode ajudar. Se o pedido estiver vago, faca UMA pergunta '
    'curta por vez para entender melhor — sempre sobre o ASSUNTO, nunca sobre para qual '
    'setor encaminhar.\n'
    '- Se voce nao tiver certeza do que o cliente escreveu (erro de digitacao, mensagem '
    'truncada), NAO adivinhe nem afirme que entendeu: peca para ele repetir com outras '
    'palavras, em uma frase curta.\n'
    '- Quando entender a necessidade, encaminhe para o setor mais adequado da lista de setores '
    'disponiveis e avise o cliente com uma frase curta e educada.\n'
    '- Se o cliente pedir uma pessoa/atendente especifico que esteja na lista de atendentes, '
    'encaminhe para essa pessoa, confirmando de forma breve.\n'
    '- Se o cliente citar uma pessoa que NAO esta na lista de atendentes, NAO diga que vai '
    'verificar a disponibilidade dela nem prometa retorno; pergunte, em uma frase, qual e o '
    'assunto, para encaminhar ao setor certo (ou ao setor geral).\n'
    '- Se a necessidade NAO se encaixar em nenhum setor especifico (por exemplo: vagas de '
    'emprego, parcerias ou assuntos gerais), encaminhe para o setor geral, em vez de tentar '
    'responder o conteudo.\n'
    '- Nunca invente informacoes, precos, prazos, links ou procedimentos. Nao peca dados '
    'sensiveis (senha, cartao, documentos). Se o cliente fugir do assunto, traga a conversa '
    'de volta ao objetivo com educacao.\n\n'
    'Seu objetivo e acolher o cliente, entender a necessidade e direciona-lo corretamente.'
)

# Regra de formato SEMPRE anexada (garante saida parseavel, mesmo com prompt livre).
OUTPUT_RULE = (
    'Responda SEMPRE em JSON valido, sem nenhum texto fora do JSON, exatamente '
    'neste formato: {"mensagem": "<texto para enviar ao cliente>", '
    '"setor": "<nome exato de um setor da lista, ou vazio>", '
    '"atendente": "<nome exato de um atendente da lista, ou vazio>"}. '
    'Preencha "setor" OU "atendente" somente quando tiver certeza de para onde '
    'encaminhar; caso contrario, deixe os dois vazios e use "mensagem" para '
    'continuar o atendimento. Nao preencha os dois ao mesmo tempo. '
    'O campo "mensagem" NUNCA pode ficar vazio: mesmo ao encaminhar, escreva a frase '
    'curta que avisa o cliente para onde ele esta sendo levado. '
    'Se a sua "mensagem" contiver uma PERGUNTA, deixe "setor" e "atendente" VAZIOS: '
    'perguntar e encaminhar na MESMA resposta faz o cliente responder para uma fila, '
    'porque voce sai da conversa no mesmo instante em que pergunta. Pergunte agora e '
    'encaminhe na resposta SEGUINTE, depois de ler o que ele responder.'
)

# Fala de encaminhamento ao desistir: a IA SEMPRE avisa o cliente (nunca transfere
# em silencio) antes de mandar para o setor de fallback / fila humana.
#
# O aviso NOMEIA O SETOR de destino. O texto antigo dizia "nao consegui entender bem
# a sua solicitacao" em TODO handoff — inclusive quando o cliente tinha acabado de ser
# claro ("queria ver algo relacionado a contas e pagamentos, com quem eu falo?"). Era
# uma desculpa falsa, e ainda deixava sem resposta justamente a pergunta que o cliente
# fez: para quem ele estava sendo mandado. `HANDOFF_NOTICE` (generico) sobrevive so
# para o caso de nao existir setor nenhum para nomear.
HANDOFF_NOTICE_TEMPLATE = (
    'Vou te encaminhar para o setor {setor}, que vai poder te ajudar melhor. '
    'So um momento, por favor.'
)
HANDOFF_NOTICE = (
    'Desculpe, nao consegui entender bem a sua solicitacao. Vou pedir para um de '
    'nossos atendentes falar com voce. So um momento, por favor.'
)

# REGRA DE TRIAGEM — SEMPRE anexada, como a `OUTPUT_RULE`, e NAO editavel de proposito.
#
# Nao esta em `DEFAULT_INSTRUCTIONS` porque aquele texto e so o PADRAO: quem ja salvou
# um prompt proprio na tela guardou uma copia do padrao antigo, e mudanca la nao chega
# nele. Isto aqui nao e persona nem texto de negocio — e como a triagem funciona, entao
# vale para toda empresa, com prompt customizado ou nao.
#
# Caso real (26/08/2026): o cliente escreveu "ao tenho certre\zA" (erro de digitacao de
# "nao tenho certeza") e a IA respondeu "Entendi, voce precisa de uma certidao. Pode me
# informar qual setor posso encaminhar sua solicitacao?". Dois erros numa frase so:
# afirmou ter entendido o que nao entendeu, e devolveu ao CLIENTE a decisao que e da
# IA — o cliente final nao conhece (nem tem por que conhecer) os setores da empresa.
# Quando ele repetiu que nao tinha certeza, a conversa estourou o limite e caiu no Geral.
TRIAGE_RULE = (
    'REGRA DE TRIAGEM (vale sempre, acima de qualquer outra instrucao): '
    'NUNCA pergunte ao cliente para qual SETOR ele quer ser encaminhado, nem cite a '
    'estrutura interna da empresa. Ele nao conhece os setores, e escolher o destino e '
    'SEU trabalho, nao dele — pergunte sempre sobre o ASSUNTO. '
    'Se o cliente disser que nao sabe, nao tem certeza ou nao souber explicar, NAO '
    'repita a mesma pergunta e NAO encaminhe ainda: ofereca numa UNICA mensagem curta '
    'as opcoes de assunto correspondentes aos setores disponiveis, para ele so '
    'escolher — e nessa resposta, como ela e uma pergunta, "setor" e "atendente" ficam '
    'VAZIOS. Encaminhar para o setor geral/curinga e o ULTIMO recurso e acontece numa '
    'resposta SEPARADA, sem pergunta nenhuma, depois que voce ja ofereceu as opcoes e '
    'leu que o cliente ainda assim nao escolheu.'
)

# ULTIMO TURNO: linha anexada ao prompt quando esta e a ultima resposta que a IA pode
# dar antes do teto (`max_turns`). Sem ela o modelo nao sabia que ia ser cortado, entao
# seguia fazendo pergunta de esclarecimento e o sistema descartava a resposta dele para
# transferir em cima. Avisado, ele decide o setor no lugar de perguntar.
FINAL_TURN_RULE = (
    'ATENCAO — ULTIMO TURNO: esta e a sua ULTIMA resposta neste atendimento. NAO faca '
    'mais perguntas de esclarecimento. Escolha AGORA o setor mais adequado da lista e '
    'preencha o campo "setor"; se nada se encaixar, use o setor geral/curinga. Na '
    '"mensagem", avise o cliente para qual setor ele esta sendo encaminhado.'
)


def _greeting_for(now):
    hour = now.hour
    if 5 <= hour < 12:
        return 'Bom dia'
    if 12 <= hour < 18:
        return 'Boa tarde'
    return 'Boa noite'


def available_sectors(company):
    """Setores DA EMPRESA. O escopo por empresa é obrigatório: sem ele a IA
    ofereceria ao cliente os setores de outra empresa."""
    return list(Sector.objects.filter(company=company).order_by('name'))


def available_attendants(company):
    """Atendentes ativos DA EMPRESA (mesmo motivo de `available_sectors`)."""
    return list(
        Attendant.objects.filter(company=company, user__is_active=True)
        .prefetch_related('sectors')
        .order_by('name')
    )


def sectors_context_text(sectors):
    if not sectors:
        return '(nenhum setor cadastrado)'
    lines = []
    for sector in sectors:
        desc = (sector.description or '').strip()
        lines.append(f'- {sector.name}: {desc}' if desc else f'- {sector.name}')
    return '\n'.join(lines)


def attendants_context_text(attendants):
    if not attendants:
        return '(nenhum atendente cadastrado)'
    lines = []
    for attendant in attendants:
        secs = ', '.join(sec.name for sec in attendant.sectors.all())
        lines.append(f'- {attendant.name} (setor: {secs})' if secs else f'- {attendant.name}')
    return '\n'.join(lines)


def resolved_instructions(config):
    return (config.instructions or '').strip() or DEFAULT_INSTRUCTIONS


# Mensagens que NAO sao um pedido: ping, saudacao e educacao solta. Elas nao dao a
# IA nada para triar, entao nao gastam tentativa (ver `_message_has_intent`). Texto
# ja normalizado (minusculo, sem acento e sem pontuacao) — comparacao exata, nunca
# "contem": "bom dia, preciso da segunda via" e um pedido de verdade.
NO_INTENT_MESSAGES = frozenset({
    'oi', 'ola', 'ole', 'opa', 'eae', 'e ai', 'eai', 'salve', 'alo', 'oi oi',
    'bom dia', 'boa tarde', 'boa noite', 'boa', 'bom',
    'teste', 'test', 'testando', 'testes',
    'tudo bem', 'tudo bom', 'td bem', 'blz', 'beleza', 'ok', 'okay', 'okey',
    'sim', 'nao', 'certo', 'obrigado', 'obrigada', 'valeu', 'vlw', 'de nada',
    'oi bom dia', 'oi boa tarde', 'oi boa noite',
    'ola bom dia', 'ola boa tarde', 'ola boa noite',
})

# Minimo de caracteres alfanumericos para uma mensagem valer como pedido. Abaixo
# disso ("?", "...", "kk") nao ha o que triar.
MIN_INTENT_CHARS = 3

# Teto ABSOLUTO de falas da IA num mesmo atendimento. Existe por causa do
# `_message_has_intent`: como saudacao nao gasta tentativa, um cliente mandando "oi"
# em sequencia manteria a IA respondendo (e consumindo token) sem fim. Este teto conta
# as falas de verdade, entao a conversa sempre termina caindo numa fila humana.
MAX_REPLIES_PER_SEGMENT = 8


def _strip_accents(text):
    import unicodedata
    normalized = unicodedata.normalize('NFD', text)
    return ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')


def _normalize_for_intent(text):
    """Minusculo, sem acento, sem pontuacao/emoji e com espacos colapsados."""
    import re
    text = _strip_accents((text or '').lower())
    text = re.sub(r'[^a-z0-9\s]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _message_has_intent(text):
    """A mensagem do cliente traz algo para TRIAR?

    O contador de tentativas (`Conversation.ai_turns`, limitado por `max_turns`) so
    pode andar quando a IA esta de fato tentando achar um setor. Antes ele contava
    TODA resposta: um "teste" queimava uma das tres tentativas, e o cliente que
    explicava o que queria na terceira mensagem era transferido no escuro, com a
    resposta do GPT descartada. Saudacao e ping nao sao pedido — nao gastam tentativa.
    """
    normalized = _normalize_for_intent(text)
    if not normalized:
        return False
    if normalized in NO_INTENT_MESSAGES:
        return False
    return len(normalized.replace(' ', '')) >= MIN_INTENT_CHARS


def _last_incoming_text(conversation):
    """Texto da ultima mensagem RECEBIDA do atendimento atual (a que disparou a IA)."""
    message = (
        conversation.messages.filter(direction='in')
        .exclude(message_type='system')
        .order_by('-created_at').first()
    )
    return (message.text or '') if message else ''


def _ai_replies_in_segment(conversation):
    """Quantas vezes a IA ja falou neste atendimento (apos a ultima divisoria)."""
    last_divider = (
        conversation.messages.filter(message_type='system')
        .order_by('-created_at').first()
    )
    qs = conversation.messages.filter(direction='out', is_ai=True)
    if last_divider:
        qs = qs.filter(created_at__gt=last_divider.created_at)
    return qs.count()


def _time_since_previous_text(conversation):
    """Tempo desde a mensagem ANTERIOR (a penultima), para a IA decidir se deve
    reapresentar apos um tempo. A ultima mensagem e a atual do cliente."""
    recent = list(
        conversation.messages.exclude(message_type='system')
        .order_by('-created_at')[:2]
    )
    if len(recent) < 2:
        return 'Esta e a primeira mensagem desta conversa (apresente-se).'
    delta = recent[0].created_at - recent[1].created_at
    secs = max(0, int(delta.total_seconds()))
    if secs < 3600:
        return 'A mensagem anterior foi ha poucos minutos (mesma conversa em andamento).'
    if secs < 86400:
        return f'A mensagem anterior foi ha cerca de {secs // 3600} hora(s).'
    return (f'A mensagem anterior foi ha cerca de {secs // 86400} dia(s) — '
            'provavelmente uma nova conversa, vale se reapresentar.')


def build_system_prompt(config, company, now=None, context_note='', final_turn=False):
    """Monta o prompt de sistema enviado ao GPT (setores/atendentes DA EMPRESA).

    = prompt editavel do usuario (persona + regras de comportamento)
      + DADOS DINAMICOS anexados automaticamente (data/hora + saudacao, tempo desde
        a ultima msg, setores, atendentes, qual e o setor geral)
      + a REGRA DE TRIAGEM (`TRIAGE_RULE`, nao editavel — nunca perguntar ao cliente
        para qual setor encaminhar)
      + a regra de formato JSON (obrigatoria para o sistema ler a resposta).

    `final_turn` anexa o aviso de ULTIMO TURNO (`FINAL_TURN_RULE`). O modelo nao tinha
    como saber que a proxima resposta dele seria descartada pelo teto `max_turns`, e
    por isso continuava perguntando quando ja devia decidir.
    """
    now = now or timezone.localtime()
    greeting = _greeting_for(now)
    time_line = (
        f'Data e hora atual: {now.strftime("%d/%m/%Y %H:%M")}. '
        f'Saudacao adequada para agora: "{greeting}".'
    )
    if context_note:
        time_line += ' ' + context_note

    # MULTIEMPRESA: a IA atende EM NOME DA EMPRESA CLIENTE, nao da plataforma. O
    # prompt editavel e UM SO para todas as empresas (a config do GPT e da
    # plataforma), entao o nome de quem esta atendendo tem que entrar como dado
    # DINAMICO, junto dos setores e atendentes. Sem esta linha, o atendente virtual
    # de todos os clientes se apresentava com o mesmo nome — e o padrao antigo trazia
    # o nome do proprio sistema fixo no texto.
    company_line = (
        f'Voce esta atendendo em nome da empresa "{company.display_name}". '
        'Use esse nome ao se apresentar; nunca mencione o nome do sistema.'
        if company is not None else ''
    )
    parts = [
        resolved_instructions(config),
        company_line,
        time_line,
        'Setores disponiveis para transferencia:\n' + sectors_context_text(available_sectors(company)),
        'Atendentes cadastrados:\n' + attendants_context_text(available_attendants(company)),
    ]
    general = _resolve_fallback_sector(company)
    if general:
        parts.append(
            f'Setor geral/curinga (use quando o pedido nao se encaixar em nenhum '
            f'setor especifico): "{general.name}".'
        )
    parts.append(TRIAGE_RULE)
    parts.append(OUTPUT_RULE)
    if final_turn:
        parts.append(FINAL_TURN_RULE)
    return '\n\n'.join(p for p in parts if p)


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
    """Mensagens do ATENDIMENTO ATUAL (apos a ultima divisoria), sem as divisorias,
    em ordem cronologica, limitado a CONTEXT_MESSAGES.

    Escopo por segmento: ao Encerrar/reabrir, a IA comeca com contexto limpo, sem
    arrastar mensagens de atendimentos anteriores."""
    last_divider = (
        conversation.messages.filter(message_type='system')
        .order_by('-created_at').first()
    )
    qs = conversation.messages.exclude(message_type='system')
    if last_divider:
        qs = qs.filter(created_at__gt=last_divider.created_at)
    messages = list(qs.order_by('-created_at')[:CONTEXT_MESSAGES])
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


def _match_sector(name, company):
    """Casa o setor que a IA escolheu, DENTRO da empresa da conversa."""
    name = (name or '').strip()
    if not name:
        return None
    return Sector.objects.filter(company=company, name__iexact=name).first()


def _match_attendant(name, company):
    """Casa o atendente que a IA citou, DENTRO da empresa da conversa."""
    name = (name or '').strip()
    if not name:
        return None
    return Attendant.objects.filter(
        company=company, name__iexact=name, user__is_active=True
    ).first()


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
    # Envio pela instancia da W-API DA EMPRESA da conversa (multiempresa).
    result = send_text_message(
        conversation.recipient, text, company=conversation.company
    )
    if result.success:
        save_outgoing_text_message(
            conversation, text, external_message_id=result.message_id or '', is_ai=True
        )
        return True
    ai_logger.warning('IA nao conseguiu enviar resposta (conv=%s): %s', conversation.id, result.error)
    return False


def _route_to_sector(conversation, sector):
    """Encaminha para um setor: fica AGUARDANDO na fila do setor (status pendente,
    SEM atribuir a ninguem) — o time do setor e notificado e alguem clica em Assumir.

    NAO insere divisoria: o encaminhamento e parte do MESMO atendimento, entao o
    atendente que assumir ve todo o historico (inclusive a conversa com a IA)."""
    Conversation.objects.filter(pk=conversation.id).update(
        sector=sector, assigned_attendant=None, status='pending', ai_turns=0,
    )
    ai_logger.info('IA encaminhou conv=%s para setor=%s (aguardando)', conversation.id, sector.name)


def _sector_for_attendant(attendant):
    """Setor de destino de um atendente citado pelo cliente.

    Prefere um setor ESPECIFICO (todos os atendentes estao no 'Geral' padrao, entao o
    Geral so e usado se a pessoa nao tiver nenhum outro)."""
    sectors = list(attendant.sectors.all())
    sector = next((s for s in sectors if not s.is_general), None)
    if sector is None:
        sector = sectors[0] if sectors else None
    return sector


def _route_to_attendant(conversation, attendant):
    """Cliente citou um atendente: encaminha para o SETOR dele, deixando AGUARDANDO
    (fila do setor, sem atribuir a pessoa). A atribuicao acontece quando alguem
    assume. Sem divisoria (mesmo atendimento; ver _route_to_sector)."""
    sector = _sector_for_attendant(attendant)
    Conversation.objects.filter(pk=conversation.id).update(
        sector=sector, assigned_attendant=None, status='pending', ai_turns=0,
    )
    ai_logger.info('IA encaminhou conv=%s para setor=%s (atendente citado: %s)',
                   conversation.id, sector.name if sector else '-', attendant.name)


def _is_question(text):
    """A fala do modelo termina perguntando alguma coisa?

    Serve de TRAVA para o defeito visto em producao (26/08/2026, conv=29): o modelo
    respondeu `{"mensagem": "Claro, posso ajudar com duvidas sobre servicos, pagamentos
    ou informacoes gerais. Qual dessas opcoes voce gostaria de explorar?",
    "setor": "Geral"}` — perguntou E encaminhou na MESMA resposta. O sistema tratou o
    `setor` preenchido como decisao tomada, encaminhou no mesmo segundo, e quando o
    cliente respondeu 13 segundos depois a conversa ja tinha saido da IA: ninguem
    respondeu mais nada. Da tela, parecia "transferiu para o Geral sem falar nada".

    A `OUTPUT_RULE` ja proibe isso, mas regra de prompt nao e garantia — o modelo erra.
    Perguntar vence o encaminhar: adiar a transferencia por um turno nao custa nada
    (o limite `max_turns` e o handoff continuam de pe), enquanto encaminhar em cima de
    uma pergunta deixa o cliente falando sozinho para uma fila.
    """
    return (text or '').strip().endswith('?')


def _announce_transfer(conversation, sector, reply=''):
    """Avisa o cliente ANTES de encaminhar — e NUNCA deixa passar em silencio.

    `_send_ai_reply` devolve False em dois casos que ate aqui eram ignorados: a fala
    veio VAZIA (o modelo respondeu `{"mensagem": "", "setor": "..."}`, coisa que ele
    faz justamente quando ja decidiu o destino) e o envio pela W-API FALHOU. Nos dois,
    o encaminhamento acontecia mesmo assim e o cliente ficava sem nenhuma resposta,
    olhando para a conversa parada — foi o "mandou para o Geral sem falar nada"
    relatado em produção. Aqui, se a fala do modelo nao chegou, entra o aviso padrao
    que nomeia o setor. Se nem esse for, o motivo fica no log (`beezap.gpt`).
    """
    if _send_ai_reply(conversation, reply):
        return True
    if sector is not None:
        return _send_ai_reply(
            conversation, HANDOFF_NOTICE_TEMPLATE.format(setor=sector.name)
        )
    return _send_ai_reply(conversation, HANDOFF_NOTICE)


def _resolve_fallback_sector(company):
    """Para onde encaminhar quando a IA nao entende o pedido.

    A configuracao do GPT e da PLATAFORMA e por isso nao pode apontar para um setor
    (setor pertence a uma empresa). O destino usado e o MESMO da empresa que o
    chatbot de menu usa — `MenuBotConfiguration.fallback_sector` — e, na falta dele,
    um setor chamado "Geral" daquela empresa.
    """
    from accounts.models import MenuBotConfiguration
    menu_config = MenuBotConfiguration.for_company(company)
    if menu_config.fallback_sector_id:
        return menu_config.fallback_sector
    return Sector.objects.filter(company=company, name__iexact='Geral').first()


def _handoff_to_fallback(conversation, config):
    """Desiste da triagem AVISANDO o cliente e SEMPRE encaminha para um setor humano.

    Sempre envia a mensagem de handoff antes (nunca transfere em silencio) e sempre
    encaminha para um SETOR: o fallback configurado, um setor 'Geral' existente ou —
    em ultimo caso — um 'Geral' criado na hora. Antes, sem fallback, a conversa ficava
    `pending` SEM setor: nao entrava em nenhuma fila e so o admin a via (parecia que
    'nao transferiu para ninguem'). Agora sempre cai numa fila real.

    O SETOR E RESOLVIDO ANTES DA FALA, para o aviso poder NOMEA-LO. O texto antigo era
    sempre 'nao consegui entender bem a sua solicitacao', mesmo quando o cliente tinha
    sido claro e tinha perguntado exatamente "com quem eu falo?" — dizia uma desculpa
    falsa e nao respondia a pergunta. Ver HANDOFF_NOTICE_TEMPLATE.
    """
    fallback = (_resolve_fallback_sector(conversation.company)
                or Sector.ensure_general(conversation.company))
    if fallback is not None:
        aviso = HANDOFF_NOTICE_TEMPLATE.format(setor=fallback.name)
    else:
        aviso = HANDOFF_NOTICE
    _send_ai_reply(conversation, aviso)
    _route_to_sector(conversation, fallback)


def _should_handle(conversation):
    """Retorna a config se a IA deve atuar nesta conversa, senao None.

    A ativacao vem do MODO mestre (`MenuBotConfiguration.mode == 'ai'`), fonte unica
    da verdade de qual atendimento automatico roda — nao mais do antigo
    `OpenAiConfiguration.enabled`."""
    from accounts.models import MenuBotConfiguration
    # A API Key/modelo do GPT sao da PLATAFORMA (uma so); o MODO de primeiro
    # atendimento (usar IA, chatbot ou nada) e da EMPRESA.
    config = OpenAiConfiguration.get_solo()
    if MenuBotConfiguration.for_company(conversation.company).mode != MenuBotConfiguration.MODE_AI:
        return None
    if not config.has_api_key:
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
        .select_related('company', 'contact', 'assigned_attendant', 'sector')
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

    # Limite de seguranca: se ja atingiu max_turns sem decidir, avisa e encaminha
    # (o handoff sempre acha/cria um setor, entao a conversa nao fica orfa).
    if conversation.ai_turns >= config.max_turns:
        _handoff_to_fallback(conversation, config)
        return

    # Teto ABSOLUTO de falas no mesmo atendimento. Como saudacao/ping nao gastam
    # tentativa (ver `_message_has_intent`), sem este teto um cliente mandando "oi"
    # sem parar manteria a IA respondendo e consumindo token para sempre.
    if _ai_replies_in_segment(conversation) >= MAX_REPLIES_PER_SEGMENT:
        ai_logger.info('IA atingiu o teto de falas do atendimento conv=%s', conversation_id)
        _handoff_to_fallback(conversation, config)
        return

    history = build_history(conversation)
    if not history:
        return

    # A TENTATIVA so conta quando ha o que triar. "teste"/"oi" nao e pedido: antes
    # queimava uma das tentativas e o cliente que so explicava o assunto na terceira
    # mensagem era cortado justamente ali. Ver `_message_has_intent`.
    conta_tentativa = _message_has_intent(_last_incoming_text(conversation))
    # ULTIMO TURNO: se esta tentativa fecha o teto, o modelo precisa SABER disso para
    # decidir o setor agora em vez de fazer mais uma pergunta (que seria descartada).
    final_turn = conta_tentativa and (conversation.ai_turns + 1 >= config.max_turns)

    # Setores/atendentes do prompt e a API Key sao SEMPRE da empresa da conversa.
    company = conversation.company
    system_prompt = build_system_prompt(
        config, company, context_note=_time_since_previous_text(conversation),
        final_turn=final_turn,
    )
    messages = [{'role': 'system', 'content': system_prompt}] + history

    from gpt.client import chat_completion
    result = chat_completion(
        messages, company=company, temperature=0.3, max_tokens=400,
        response_format={'type': 'json_object'},
    )
    if not result.success:
        ai_logger.warning('IA/GPT falhou (conv=%s modelo=%s): %s',
                          conversation_id, result.model, result.error)
        return

    decision = _parse_decision(result.text)
    reply = decision['mensagem']
    attendant = _match_attendant(decision['atendente'], company)
    sector = _match_sector(decision['setor'], company)

    # DIAGNOSTICO: o que o modelo pediu e o que o sistema conseguiu casar. Quando a
    # conversa "para sozinha", e aqui que se ve o motivo — um `setor` que nao existe
    # com aquele nome na empresa nao casa e a decisao e simplesmente ignorada, sem
    # nada aparecer em tela. Nunca registra o texto da conversa, so os nomes.
    if decision['setor'] and sector is None:
        ai_logger.warning(
            'IA pediu setor inexistente na empresa (conv=%s pedido=%r) — decisao ignorada.',
            conversation_id, decision['setor'],
        )
    if decision['atendente'] and attendant is None:
        ai_logger.warning(
            'IA citou atendente inexistente na empresa (conv=%s pedido=%r).',
            conversation_id, decision['atendente'],
        )

    # PERGUNTOU? Entao nao encaminha ainda. O modelo as vezes devolve a pergunta e o
    # destino na MESMA resposta; encaminhar ali faz o cliente responder para uma fila
    # (ver `_is_question`). A decisao e descartada SO deste turno — ele decide de novo
    # depois de ler a resposta, e o limite/handoff seguem valendo.
    if (attendant or sector) and _is_question(reply):
        ai_logger.info(
            'IA perguntou e escolheu destino na mesma resposta (conv=%s destino=%r): '
            'encaminhamento adiado para o proximo turno.',
            conversation_id, decision['atendente'] or decision['setor'],
        )
        attendant = None
        sector = None

    # Encaminhar SEMPRE avisa o cliente: `_announce_transfer` cobre a fala vazia e a
    # falha de envio, que antes viravam transferencia muda.
    if attendant:
        _announce_transfer(conversation, _sector_for_attendant(attendant), reply)
        _route_to_attendant(conversation, attendant)
        return
    if sector:
        _announce_transfer(conversation, sector, reply)
        _route_to_sector(conversation, sector)
        return

    # Nao decidiu o destino. A tentativa so e contada quando a mensagem do cliente
    # trazia algo para triar (`conta_tentativa`): saudacao e ping deixam a conversa
    # seguir sem gastar o limite.
    if not conta_tentativa:
        if not _send_ai_reply(conversation, reply):
            # Sem fala e sem destino a conversa fica parada sem nenhum rastro — o
            # cliente ve a IA emudecer. O motivo tem que sair no log.
            ai_logger.warning('IA nao falou nada e nao encaminhou (conv=%s).', conversation_id)
        return

    # Se a tentativa fecha o limite, DESISTE avisando o cliente (o aviso de handoff
    # nomeia o setor de destino, nao a pergunta de esclarecimento que o modelo devolveu
    # — mandar a pergunta E transferir deixaria o cliente respondendo para uma fila).
    new_turns = conversation.ai_turns + 1
    if new_turns >= config.max_turns:
        _handoff_to_fallback(conversation, config)
        return
    if not _send_ai_reply(conversation, reply):
        # Nem falou nem encaminhou: a conversa ficaria muda e fora de qualquer fila.
        # Melhor cair para um humano do que emudecer com o cliente esperando.
        ai_logger.warning('IA sem resposta para enviar (conv=%s): encaminhando.', conversation_id)
        _handoff_to_fallback(conversation, config)
        return
    Conversation.objects.filter(pk=conversation.id).update(ai_turns=new_turns)


def handle_incoming_for_ai_async(conversation_id):
    """Dispara o atendente virtual em background (thread daemon).

    Nunca bloqueia o recebimento do webhook. A trava contra processar a mesma
    conversa duas vezes fica NO BANCO (`wapi/autoreply_lock.py`), nao num `set()` em
    memoria: com `--workers 2` cada worker tinha o seu set, e uma rajada de mensagens
    caindo em processos diferentes fazia o cliente receber DUAS respostas.

    Ao terminar, se chegou mensagem nova durante o processamento, roda de novo — a
    trava antiga descartava essa mensagem e a conversa podia ficar parada.
    """
    from wapi import autoreply_lock

    if not autoreply_lock.acquire(conversation_id):
        ai_logger.info('IA ja esta processando esta conversa (conv=%s).', conversation_id)
        return False

    def _worker():
        from django.db import connection
        try:
            _processar_com_reprocesso(conversation_id)
        except Exception:
            ai_logger.exception('Falha no atendimento IA (conv=%s).', conversation_id)
        finally:
            try:
                autoreply_lock.release(conversation_id)
            finally:
                connection.close()

    threading.Thread(target=_worker, name=f'ai-{conversation_id}', daemon=True).start()
    return True


# Quantas vezes reprocessar quando chega mensagem nova durante o processamento.
# Limite para uma rajada muito longa nao virar loop.
MAX_REPROCESSOS = 3


def _ultima_mensagem_recebida(conversation_id):
    from accounts.models import Message
    return (
        Message.objects
        .filter(conversation_id=conversation_id, direction='in')
        .order_by('-created_at').values_list('pk', flat=True).first()
    )


def _processar_com_reprocesso(conversation_id):
    """Roda a IA e, se o cliente mandou algo novo no meio, roda de novo.

    Sem isto, a mensagem que chegasse durante o processamento seria descartada pela
    trava: o cliente digitava a resposta, ninguem processava, e a conversa ficava
    parada sem cair em fila.
    """
    for _ in range(MAX_REPROCESSOS):
        antes = _ultima_mensagem_recebida(conversation_id)
        handle_incoming_for_ai(conversation_id)
        if _ultima_mensagem_recebida(conversation_id) == antes:
            return
        ai_logger.info('Mensagem nova durante o processamento (conv=%s): refazendo.',
                       conversation_id)
