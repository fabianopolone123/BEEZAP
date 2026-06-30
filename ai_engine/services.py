from dataclasses import dataclass

from django.conf import settings

from .client import chat_with_ollama
from .fallbacks import (
    AI_EMPTY_MESSAGE_ERROR,
    AI_GENERAL_ERROR,
    AI_UNAVAILABLE_ERROR,
    AI_UNAVAILABLE_REPLY,
)
from .prompts import build_messages


MAX_MESSAGE_LENGTH = 1200
MAX_CONTEXT_LENGTH = 1600


@dataclass
class AiReplyResult:
    success: bool
    reply: str = ''
    error: str = ''
    unavailable: bool = False


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
