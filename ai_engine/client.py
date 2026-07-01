import json
import socket
from dataclasses import dataclass
from urllib import error, request


CHAT_PATH = '/api/chat'


@dataclass
class OllamaResult:
    success: bool
    content: str = ''
    status_code: int | None = None
    error: str = ''
    unavailable: bool = False


def _build_url(base_url):
    return base_url.rstrip('/') + CHAT_PATH


def chat_with_ollama(base_url, model, messages, timeout, temperature=0.2, num_predict=180, num_gpu=0):
    payload = json.dumps({
        'model': model,
        'messages': messages,
        'stream': False,
        'options': {
            'temperature': temperature,
            'num_predict': num_predict,
            'num_gpu': num_gpu,
        },
    }).encode('utf-8')

    http_request = request.Request(
        _build_url(base_url),
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )

    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            response_body = response.read().decode('utf-8', 'ignore')
            parsed_body = json.loads(response_body) if response_body else {}
            message = parsed_body.get('message', {})
            content = message.get('content', '') if isinstance(message, dict) else ''
            return OllamaResult(
                success=200 <= response.status < 300 and bool(content.strip()),
                content=content.strip(),
                status_code=response.status,
                error='' if content.strip() else 'Resposta vazia da IA local.',
            )
    except error.HTTPError as exc:
        return OllamaResult(
            success=False,
            status_code=exc.code,
            error='A IA local retornou erro.',
            unavailable=500 <= exc.code,
        )
    except error.URLError:
        return OllamaResult(
            success=False,
            error='Nao foi possivel conectar com a IA local.',
            unavailable=True,
        )
    except (TimeoutError, socket.timeout):
        return OllamaResult(
            success=False,
            error='A IA local demorou para responder.',
            unavailable=True,
        )
    except json.JSONDecodeError:
        return OllamaResult(
            success=False,
            error='A IA local retornou uma resposta invalida.',
        )
