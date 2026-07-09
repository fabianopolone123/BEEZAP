"""Cliente da API do OpenAI (GPT).

Segue o mesmo estilo de `wapi/client.py`: usa apenas a biblioteca padrao
(`urllib`, sem dependencia pip nova), centraliza credenciais/headers/erros e
NUNCA expoe a API Key, o corpo bruto da resposta nem traceback ao usuario final.

A API Key e o modelo vem de `OpenAiConfiguration` (banco), com fallback opcional
para as variaveis de ambiente `OPENAI_API_KEY`/`OPENAI_MODEL`.
"""

import json
import logging
from dataclasses import dataclass
from urllib import error, request

from django.conf import settings

from accounts.models import OpenAiConfiguration


OPENAI_CHAT_PATH = '/v1/chat/completions'

# Mensagens amigaveis (nunca expor API Key, corpo bruto ou traceback).
GPT_GENERIC_ERROR = 'Nao foi possivel falar com o GPT agora. Tente novamente em instantes.'
GPT_CONFIG_ERROR = 'Cadastre a API Key do GPT antes de usar a inteligencia.'
GPT_AUTH_ERROR = 'A API Key do GPT foi recusada. Confira a chave cadastrada.'
GPT_QUOTA_ERROR = 'Sem creditos disponiveis na conta do OpenAI. Verifique o saldo/cobranca.'
GPT_RATE_ERROR = 'Muitas solicitacoes ao GPT em pouco tempo. Aguarde alguns segundos e tente de novo.'
GPT_MODEL_ERROR = 'O modelo de GPT configurado nao esta disponivel para esta chave.'

gpt_logger = logging.getLogger('beezap.gpt')


@dataclass
class GptResult:
    success: bool
    text: str | None = None
    model: str | None = None
    status_code: int | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _friendly_http_error(status, body_text):
    """Traduz o erro HTTP do OpenAI para uma mensagem simples ao usuario."""
    low = (body_text or '').lower()
    if status == 401:
        return GPT_AUTH_ERROR
    if status == 403:
        return GPT_AUTH_ERROR
    if status == 404 and 'model' in low:
        return GPT_MODEL_ERROR
    if status == 429:
        if 'insufficient_quota' in low or 'quota' in low or 'billing' in low:
            return GPT_QUOTA_ERROR
        return GPT_RATE_ERROR
    if status == 400 and 'model' in low:
        return GPT_MODEL_ERROR
    return GPT_GENERIC_ERROR


def _extract_reply_text(body):
    """Le o texto da resposta em choices[0].message.content."""
    if not isinstance(body, dict):
        return ''
    choices = body.get('choices')
    if isinstance(choices, list) and choices:
        message = choices[0].get('message') if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get('content')
            if isinstance(content, str):
                return content.strip()
    return ''


def _extract_usage(body):
    """Le usage.{prompt_tokens, completion_tokens, total_tokens} da resposta."""
    if not isinstance(body, dict):
        return (0, 0, 0)
    usage = body.get('usage')
    if not isinstance(usage, dict):
        return (0, 0, 0)

    def _as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    prompt = _as_int(usage.get('prompt_tokens'))
    completion = _as_int(usage.get('completion_tokens'))
    total = _as_int(usage.get('total_tokens')) or (prompt + completion)
    return (prompt, completion, total)


def chat_completion(messages, *, model=None, temperature=0.3, max_tokens=None,
                    response_format=None, timeout=None):
    """Envia uma conversa (lista de {role, content}) ao GPT e devolve GptResult.

    `response_format` (ex.: {'type': 'json_object'}) forca a saida em JSON valido.
    Nunca levanta excecao: sempre retorna GptResult(success=...). O texto do erro
    ja e amigavel (sem API Key, corpo bruto ou traceback).
    """
    config = OpenAiConfiguration.get_solo()
    api_key = config.resolved_api_key()
    if not api_key:
        gpt_logger.warning('GPT abortado: API Key ausente.')
        return GptResult(success=False, error=GPT_CONFIG_ERROR)

    used_model = (model or config.resolved_model()).strip()
    payload = {'model': used_model, 'messages': messages}
    if temperature is not None:
        payload['temperature'] = temperature
    if max_tokens:
        payload['max_tokens'] = max_tokens
    if response_format:
        payload['response_format'] = response_format

    final_url = settings.OPENAI_BASE_URL.rstrip('/') + OPENAI_CHAT_PATH
    body = json.dumps(payload).encode('utf-8')
    http_request = request.Request(
        final_url, data=body, method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
    )

    # Diagnostico: guarda o conteudo COMPLETO enviado (so as mensagens, sem a API
    # Key) e o que voltou, para o ADM inspecionar na tela. Nunca derruba a resposta.
    request_text = json.dumps(payload, ensure_ascii=False, indent=2)
    response_text = ''
    result = None

    timeout = timeout or settings.OPENAI_TIMEOUT
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            response_body = response.read().decode('utf-8', 'ignore')
            response_text = response_body
            parsed_body = json.loads(response_body) if response_body else {}
            if 200 <= response.status < 300:
                text = _extract_reply_text(parsed_body)
                prompt_tokens, completion_tokens, total_tokens = _extract_usage(parsed_body)
                if total_tokens:
                    try:
                        OpenAiConfiguration.record_usage(prompt_tokens, completion_tokens, total_tokens)
                    except Exception:
                        # O contador nunca pode derrubar a resposta do GPT.
                        gpt_logger.warning('Falha ao registrar consumo de tokens.', exc_info=False)
                result = GptResult(
                    success=True, text=text, model=used_model, status_code=response.status,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            else:
                gpt_logger.warning('GPT falhou: status=%s modelo=%s corpo=%s', response.status, used_model, response_body[:400])
                result = GptResult(
                    success=False, model=used_model, status_code=response.status,
                    error=_friendly_http_error(response.status, response_body),
                )
    except error.HTTPError as exc:
        try:
            error_body = exc.read().decode('utf-8', 'ignore')
        except Exception:
            error_body = ''
        response_text = error_body
        gpt_logger.warning('GPT falhou: HTTP %s modelo=%s corpo=%s', exc.code, used_model, error_body[:400])
        result = GptResult(
            success=False, model=used_model, status_code=exc.code,
            error=_friendly_http_error(exc.code, error_body),
        )
    except error.URLError as exc:
        response_text = f'(sem conexao) {getattr(exc, "reason", exc)}'
        gpt_logger.warning('GPT sem conexao: %s', getattr(exc, 'reason', exc))
        result = GptResult(success=False, model=used_model, error=GPT_GENERIC_ERROR)
    except json.JSONDecodeError:
        response_text = response_text or '(resposta nao-JSON)'
        gpt_logger.warning('GPT retornou resposta nao-JSON.')
        result = GptResult(success=False, model=used_model, error=GPT_GENERIC_ERROR)
    finally:
        try:
            OpenAiConfiguration.record_last_exchange(request_text, response_text)
        except Exception:
            gpt_logger.warning('Falha ao registrar ultima chamada da IA (diagnostico).')

    return result


def test_connection():
    """Chamada minima para validar a API Key/modelo/creditos (gasta pouquissimo).

    Pede so a palavra 'ok' com poucos tokens. Retorna GptResult.
    """
    return chat_completion(
        [
            {'role': 'system', 'content': 'Responda apenas com a palavra: ok'},
            {'role': 'user', 'content': 'ok'},
        ],
        temperature=0,
        max_tokens=5,
        timeout=30,
    )
