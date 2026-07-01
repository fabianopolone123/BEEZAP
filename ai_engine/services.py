from dataclasses import dataclass, field

from django.conf import settings

from .client import chat_with_ollama
from .fallbacks import (
    AI_EMPTY_MESSAGE_ERROR,
    AI_GENERAL_ERROR,
    AI_UNAVAILABLE_ERROR,
    AI_UNAVAILABLE_REPLY,
)
from .prompts import build_messages, build_messages_with_rules


MAX_MESSAGE_LENGTH = 1200
MAX_CONTEXT_LENGTH = 1600
MAX_RELEVANT_RULES = 5
RULES_HANDOFF_REPLY = 'Vou encaminhar sua solicitacao para um atendente.'
RULES_UNAVAILABLE_REPLY = 'No momento nao consegui responder automaticamente. Vou encaminhar para um atendente.'


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
