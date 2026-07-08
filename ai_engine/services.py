import re
from dataclasses import dataclass, field

from django.conf import settings

from .client import chat_with_ollama
from .fallbacks import (
    AI_EMPTY_MESSAGE_ERROR,
    AI_GENERAL_ERROR,
    AI_UNAVAILABLE_ERROR,
    AI_UNAVAILABLE_REPLY,
)
from .prompts import (
    build_generative_reply_messages,
    build_intent_classification_messages,
    build_messages,
    build_messages_with_rules,
)


MAX_MESSAGE_LENGTH = 1200
MAX_CONTEXT_LENGTH = 1600
MAX_RELEVANT_RULES = 5
RULES_HANDOFF_REPLY = 'Vou encaminhar sua solicitacao para um atendente.'
RULES_UNAVAILABLE_REPLY = 'No momento nao consegui responder automaticamente. Vou encaminhar para um atendente.'
AMBIGUOUS_TERMS = (
    'ajuda', 'ajudar', 'duvida', 'dúvida', 'problema', 'erro', 'errado',
    'tudo errado', 'nao sei', 'não sei', 'nao tenho certeza', 'não tenho certeza',
    'sem certeza', 'confuso', 'complicado', 'ruim',
)


@dataclass
class AiReplyResult:
    success: bool
    reply: str = ''
    error: str = ''
    unavailable: bool = False
    rules: list = field(default_factory=list)
    no_rules_found: bool = False


def _clean_text(value, max_length):
    cleaned = ' '.join((value or '').split())
    return cleaned[:max_length]


def _clean_reply(value):
    lines = [line.strip() for line in (value or '').splitlines() if line.strip()]
    text = ' '.join(lines)
    sentences = [part.strip() for part in text.split('.') if part.strip()]
    if len(sentences) > 3:
        text = '. '.join(sentences[:3]) + '.'
    return text[:600].strip()


def _normalize_search_text(value):
    return ' '.join((value or '').lower().split())


def get_relevant_rules(message, sector=None, limit=MAX_RELEVANT_RULES):
    from accounts.models import AutomationRule

    normalized_message = _normalize_search_text(message)
    if not normalized_message:
        return []

    sector_id = getattr(sector, 'id', sector)
    rules = AutomationRule.objects.filter(is_active=True).select_related('sector')
    if sector_id:
        rules = rules.filter(sector__isnull=True) | AutomationRule.objects.filter(
            is_active=True,
            sector_id=sector_id,
        ).select_related('sector')
    else:
        rules = rules.filter(sector__isnull=True)

    matches = []
    for rule in rules:
        score = 0
        for keyword in rule.keyword_list:
            if keyword and keyword in normalized_message:
                score += 2
        if rule.customer_example and _normalize_search_text(rule.customer_example) in normalized_message:
            score += 1
        if score:
            if sector_id and rule.sector_id == sector_id:
                score += 1
            matches.append((score, rule))

    matches.sort(key=lambda item: (-item[0], item[1].sector_id is None, item[1].title))
    return [rule for _, rule in matches[:limit]]


@dataclass
class IntentResult:
    """Resultado da classificacao de intencao do cliente.

    `sector` e o Sector escolhido (ou None se indefinido). `source` diz como foi
    decidido ('keyword', 'llm' ou 'undefined') — util para log/diagnostico.
    """
    sector: object = None
    source: str = 'undefined'
    raw: str = ''

    @property
    def decided(self):
        return self.sector is not None


def _sectors_block(sectors):
    """Texto com os setores disponiveis (nome + descricao) para o prompt."""
    blocks = []
    for sector in sectors:
        line = f'- {sector.name}'
        description = (getattr(sector, 'description', '') or '').strip()
        if description:
            line += f': {description}'
        blocks.append(line)
    return '\n'.join(blocks)


def _match_sector_by_name(sectors, name):
    """Casa o nome retornado pelo modelo com um Sector real (defensivo)."""
    target = _normalize_search_text(name)
    if not target or target == 'indefinido':
        return None
    # 1) match exato pelo nome normalizado.
    for sector in sectors:
        if _normalize_search_text(sector.name) == target:
            return sector
    # 2) o modelo pode devolver o nome dentro de uma frase curta; aceita conter.
    for sector in sectors:
        sector_name = _normalize_search_text(sector.name)
        if sector_name and (sector_name in target or target in sector_name):
            return sector
    return None


def _keyword_sector(message):
    """1a camada (deterministica): casa a mensagem com as palavras-chave das regras
    de atendimento que TEM setor. Retorna o Sector da regra com mais palavras-chave
    presentes na mensagem, ou None. (Nao usa get_relevant_rules porque aquele, sem
    setor informado, so considera regras sem setor.)"""
    from accounts.models import AutomationRule

    normalized = _normalize_search_text(message)
    if not normalized:
        return None
    best_sector = None
    best_score = 0
    rules = AutomationRule.objects.filter(is_active=True, sector__isnull=False).select_related('sector')
    for rule in rules:
        score = sum(1 for keyword in rule.keyword_list if keyword and keyword in normalized)
        if score > best_score:
            best_score = score
            best_sector = rule.sector
    return best_sector


def _looks_ambiguous_without_sector_hint(message):
    """Evita o modelo pequeno chutar setor quando o cliente ainda nao deu assunto.

    Essa funcao roda depois da camada de palavras-chave. Portanto, se a mensagem
    tivesse boleto, compra, cotacao etc., ja teria sido roteada antes. Aqui
    seguramos textos vagos como "ta dando tudo errado" para pedir esclarecimento.
    """
    normalized = _normalize_search_text(message)
    if not normalized:
        return True
    words = [part for part in normalized.split() if len(part) > 2]
    if len(words) <= 2:
        return True
    return any(term in normalized for term in AMBIGUOUS_TERMS)


def classify_intent(message, sectors, model=None, base_url=None, timeout=None,
                    llm_only=False, history='', instructions=''):
    """Decide para qual Setor a mensagem do cliente deve ir.

    Estrategia em camadas (torna o modelo pequeno suficiente):
      1) palavras-chave das regras de atendimento (deterministico, prioridade);
      2) classificacao pela IA local (escolhe 1 setor da lista ou INDEFINIDO);
      3) INDEFINIDO se nada decidir.
    Nunca levanta excecao de rede: em falha da IA, cai em INDEFINIDO.

    `llm_only=True` (modo de teste) pula as camadas deterministicas (1 e a trava
    anti-ambiguidade) e deixa o modelo decidir sozinho. `history` e um resumo
    curto de conversas anteriores com o mesmo contato, passado ao modelo como
    contexto.
    """
    cleaned_message = _clean_text(message, MAX_MESSAGE_LENGTH)
    sectors = list(sectors)
    if not cleaned_message or not sectors:
        return IntentResult(sector=None, source='undefined')

    if not llm_only:
        # 1) Camada deterministica por palavras-chave.
        keyword_sector = _keyword_sector(cleaned_message)
        if keyword_sector is not None:
            return IntentResult(sector=keyword_sector, source='keyword')

        if _looks_ambiguous_without_sector_hint(cleaned_message):
            return IntentResult(sector=None, source='undefined')

    # 2) Camada LLM (classificacao simples: nome do setor ou INDEFINIDO).
    result = chat_with_ollama(
        base_url=base_url or settings.OLLAMA_BASE_URL,
        model=model or settings.OLLAMA_MODEL,
        messages=build_intent_classification_messages(
            cleaned_message, _sectors_block(sectors), history=history, instructions=instructions,
        ),
        timeout=timeout or settings.OLLAMA_TIMEOUT,
        temperature=settings.OLLAMA_TEMPERATURE,
        num_predict=40,
        num_gpu=settings.OLLAMA_NUM_GPU,
        keep_alive=settings.OLLAMA_KEEP_ALIVE,
    )
    if result.success:
        matched = _match_sector_by_name(sectors, result.content)
        if matched is not None:
            return IntentResult(sector=matched, source='llm', raw=result.content)
        return IntentResult(sector=None, source='undefined', raw=result.content)

    # 3) IA indisponivel/erro: indefinido (o orquestrador cuida do fallback).
    return IntentResult(sector=None, source='undefined')


# ======================================================================
# Modo generativo: a IA escreve a resposta ao cliente e decide o roteamento
# ======================================================================

MAX_GENERATED_REPLY_LENGTH = 600
_MARKER_BRACKET_RE = re.compile(r'\[\s*setor\s*[:=]\s*([^\]\n]+?)\s*\]', re.IGNORECASE)
_MARKER_LINE_RE = re.compile(r'(?im)^\s*setor\s*[:=]\s*(.+?)\s*$')
# Termos do marcador que significam "ainda nao decidir" (continuar perguntando).
_MARKER_CONTINUE = {'continuar', 'continua', 'indefinido', 'nenhum', 'none', '-', ''}
_MARKER_GERAL = {'geral', 'setor geral', 'general'}


@dataclass
class GenerativeResult:
    """Resultado do modo generativo: a fala ao cliente + a decisao de roteamento.

    `action`: 'route' (encaminhar para `sector`, que pode ser None = geral sem setor)
    ou 'continue' (seguir a conversa / pedir mais informacao). `available` indica se
    o modelo respondeu (False quando a IA local esta fora do ar)."""
    reply: str = ''
    sector: object = None
    action: str = 'continue'
    available: bool = True
    raw: str = ''
    error: str = ''   # motivo da falha quando available=False (diagnostico)
    model: str = ''   # modelo local usado (diagnostico)


def _clean_generated_reply(text):
    """Limpa a fala gerada: remove restos de marcador, espacos e limita o tamanho."""
    cleaned = (text or '').strip()
    # Remove qualquer marcador que tenha sobrado no meio/fim.
    cleaned = _MARKER_BRACKET_RE.sub('', cleaned)
    cleaned = _MARKER_LINE_RE.sub('', cleaned)
    cleaned = '\n'.join(line.rstrip() for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()[:MAX_GENERATED_REPLY_LENGTH]


def _extract_marker(text):
    """Separa a fala do marcador de controle. Retorna (reply_sem_marcador, valor).

    Aceita `[SETOR: X]` em qualquer lugar ou uma linha `SETOR: X`. Sem marcador,
    retorna (texto, None)."""
    raw = text or ''
    matches = list(_MARKER_BRACKET_RE.finditer(raw))
    if matches:
        marker = matches[-1].group(1).strip()
        reply = (raw[:matches[-1].start()] + raw[matches[-1].end():])
        return reply, marker
    line = None
    for line in _MARKER_LINE_RE.finditer(raw):
        pass  # pega a ultima ocorrencia
    if line:
        return raw[:line.start()], line.group(1).strip()
    return raw, None


def _resolve_marker(marker, sectors, fallback_sector):
    """Traduz o marcador para (action, sector). Sem certeza, 'continue' (seguro)."""
    if marker is None:
        return 'continue', None
    norm = _normalize_search_text(marker)
    if norm in _MARKER_CONTINUE:
        return 'continue', None
    if norm in _MARKER_GERAL:
        for sector in sectors:
            if _normalize_search_text(sector.name) in _MARKER_GERAL:
                return 'route', sector
        return 'route', fallback_sector  # pode ser None = encaminhamento geral sem setor
    matched = _match_sector_by_name(sectors, marker)
    if matched is not None:
        return 'route', matched
    return 'continue', None


def generate_reply_and_route(instructions, sectors, transcript, history='',
                             fallback_sector=None, model=None, base_url=None, timeout=None):
    """Modo generativo: pede ao modelo local a proxima fala ao cliente + o marcador
    de roteamento, e devolve um GenerativeResult. Nunca levanta excecao: se a IA
    estiver fora do ar, retorna available=False para o orquestrador tratar."""
    sectors = list(sectors)
    selected_model = model or settings.OLLAMA_MODEL
    result = chat_with_ollama(
        base_url=base_url or settings.OLLAMA_BASE_URL,
        model=selected_model,
        messages=build_generative_reply_messages(
            instructions, _sectors_block(sectors), transcript, history=history,
        ),
        # Timeout generoso: gerar texto demora mais e a 1a mensagem pode carregar
        # o modelo na RAM (partida a frio no CPU).
        timeout=timeout or getattr(settings, 'OLLAMA_GENERATIVE_TIMEOUT', 90),
        temperature=settings.OLLAMA_TEMPERATURE,
        # Resposta curta = muito mais rapido no CPU (gargalo e gerar token a token).
        num_predict=getattr(settings, 'OLLAMA_GENERATIVE_NUM_PREDICT', 120),
        num_gpu=settings.OLLAMA_NUM_GPU,
        keep_alive=settings.OLLAMA_KEEP_ALIVE,
    )
    if not result.success:
        return GenerativeResult(
            reply='', action='continue', available=False,
            error=result.error or 'sem detalhe', model=selected_model,
        )

    reply_raw, marker = _extract_marker(result.content)
    action, sector = _resolve_marker(marker, sectors, fallback_sector)
    return GenerativeResult(
        reply=_clean_generated_reply(reply_raw), sector=sector, action=action,
        available=True, raw=result.content, model=selected_model,
    )


def build_rules_context(message, sector=None):
    rules = get_relevant_rules(message, sector=sector)
    return build_rules_context_from_rules(rules)


def build_rules_context_from_rules(rules):
    if not rules:
        return ''

    blocks = []
    for rule in rules:
        parts = [
            f'Regra: {rule.title}',
            f'Setor: {rule.sector_label}',
            f'Palavras-chave: {rule.keywords}',
            f'Resposta orientada: {rule.response_text}',
        ]
        if rule.internal_instruction:
            parts.append(f'Instrucao interna: {rule.internal_instruction}')
        blocks.append('\n'.join(parts))

    return '\n\n'.join(blocks)


def generate_ai_reply(message, context=None, model=None, base_url=None, timeout=None):
    cleaned_message = _clean_text(message, MAX_MESSAGE_LENGTH)
    if not cleaned_message:
        return AiReplyResult(
            success=False,
            reply='',
            error=AI_EMPTY_MESSAGE_ERROR,
        )

    cleaned_context = _clean_text(context, MAX_CONTEXT_LENGTH)
    selected_model = model or settings.OLLAMA_MODEL
    selected_base_url = base_url or settings.OLLAMA_BASE_URL
    selected_timeout = timeout or settings.OLLAMA_TIMEOUT

    result = chat_with_ollama(
        base_url=selected_base_url,
        model=selected_model,
        messages=build_messages(cleaned_message, cleaned_context),
        timeout=selected_timeout,
        temperature=settings.OLLAMA_TEMPERATURE,
        num_predict=settings.OLLAMA_NUM_PREDICT,
        num_gpu=settings.OLLAMA_NUM_GPU,
    )

    if result.success:
        reply = _clean_reply(result.content)
        if reply:
            return AiReplyResult(success=True, reply=reply)

    return AiReplyResult(
        success=False,
        reply=AI_UNAVAILABLE_REPLY,
        error=AI_UNAVAILABLE_ERROR if result.unavailable else AI_GENERAL_ERROR,
        unavailable=result.unavailable,
    )


def generate_ai_reply_with_rules(message, sector=None, model=None, base_url=None, timeout=None):
    cleaned_message = _clean_text(message, MAX_MESSAGE_LENGTH)
    if not cleaned_message:
        return AiReplyResult(
            success=False,
            reply='',
            error=AI_EMPTY_MESSAGE_ERROR,
            no_rules_found=True,
        )

    rules = get_relevant_rules(cleaned_message, sector=sector)
    if not rules:
        return AiReplyResult(
            success=True,
            reply=RULES_HANDOFF_REPLY,
            rules=[],
            no_rules_found=True,
        )

    rules_context = _clean_text(build_rules_context_from_rules(rules), MAX_CONTEXT_LENGTH)
    selected_model = model or settings.OLLAMA_MODEL
    selected_base_url = base_url or settings.OLLAMA_BASE_URL
    selected_timeout = timeout or settings.OLLAMA_TIMEOUT

    result = chat_with_ollama(
        base_url=selected_base_url,
        model=selected_model,
        messages=build_messages_with_rules(cleaned_message, rules_context),
        timeout=selected_timeout,
        temperature=settings.OLLAMA_TEMPERATURE,
        num_predict=settings.OLLAMA_NUM_PREDICT,
        num_gpu=settings.OLLAMA_NUM_GPU,
    )

    if result.success:
        reply = _clean_reply(result.content)
        if reply:
            return AiReplyResult(success=True, reply=reply, rules=rules)

    return AiReplyResult(
        success=False,
        reply=RULES_UNAVAILABLE_REPLY,
        error=AI_UNAVAILABLE_ERROR if result.unavailable else AI_GENERAL_ERROR,
        unavailable=result.unavailable,
        rules=rules,
    )
