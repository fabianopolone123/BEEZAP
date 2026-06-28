import json
from dataclasses import dataclass
from urllib import error, parse, request

from django.conf import settings

from accounts.models import WapiConfiguration


SEND_TEXT_PATH = '/send-text'


@dataclass
class WapiSendResult:
    success: bool
    message_id: str | None = None
    status_code: int | None = None
    error: str | None = None


def _extract_message_id(payload):
    if not isinstance(payload, dict):
        return None
    for key in ('messageId', 'id', 'message_id'):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for nested_key in ('message', 'data', 'result'):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            nested_id = _extract_message_id(nested)
            if nested_id:
                return nested_id
    return None


def send_text_message(phone, message):
    config = WapiConfiguration.get_solo()
    instance_id = config.resolved_instance_id().strip()
    token = config.resolved_token().strip()

    if not instance_id:
        return WapiSendResult(success=False, error='Instance ID da W-API nao configurado.')
    if not token:
        return WapiSendResult(success=False, error='Token da W-API nao configurado.')

    url = settings.WAPI_BASE_URL.rstrip('/') + SEND_TEXT_PATH
    url_parts = parse.urlsplit(url)
    query = parse.parse_qs(url_parts.query, keep_blank_values=True)
    query['instanceId'] = [instance_id]
    final_url = parse.urlunsplit((
        url_parts.scheme,
        url_parts.netloc,
        url_parts.path,
        parse.urlencode(query, doseq=True),
        url_parts.fragment,
    ))

    payload = json.dumps({
        'phone': phone,
        'message': message,
    }).encode('utf-8')

    http_request = request.Request(
        final_url,
        data=payload,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )

    try:
        with request.urlopen(http_request, timeout=15) as response:
            response_body = response.read().decode('utf-8', 'ignore')
            parsed_body = json.loads(response_body) if response_body else {}
            return WapiSendResult(
                success=200 <= response.status < 300,
                message_id=_extract_message_id(parsed_body),
                status_code=response.status,
                error=None if 200 <= response.status < 300 else 'Nao foi possivel enviar a mensagem. Verifique a instancia, o token e tente novamente.',
            )
    except error.HTTPError as exc:
        return WapiSendResult(
            success=False,
            status_code=exc.code,
            error='Nao foi possivel enviar a mensagem. Verifique a instancia, o token e tente novamente.',
        )
    except error.URLError:
        return WapiSendResult(
            success=False,
            error='Nao foi possivel conectar com a W-API.',
        )
    except json.JSONDecodeError:
        return WapiSendResult(
            success=False,
            error='A W-API retornou uma resposta invalida.',
        )
