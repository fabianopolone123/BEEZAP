import json
import logging
from dataclasses import dataclass
from urllib import error, parse, request

from django.conf import settings

from accounts.models import WapiConfiguration
from wapi.parser import normalize_phone


SEND_TEXT_PATH = '/v1/message/send-text'

# Mensagens amigaveis (nunca expor token, payload bruto ou traceback ao usuario).
SEND_GENERIC_ERROR = (
    'Nao foi possivel enviar a mensagem. Verifique a conexao do WhatsApp e tente novamente.'
)
SEND_CONFIG_ERROR = 'Configure a W-API antes de enviar mensagens.'

send_logger = logging.getLogger('beezap.wapi.send')


def _response_indicates_error(body):
    """Detecta erro logico mesmo quando a W-API responde HTTP 2xx."""
    if not isinstance(body, dict):
        return False
    err = body.get('error')
    if isinstance(err, bool):
        return err
    if isinstance(err, str) and err.strip():
        return True
    status = body.get('status')
    if isinstance(status, str) and status.strip().lower() in ('error', 'failed', 'disconnected'):
        return True
    return False


@dataclass
class WapiSendResult:
    success: bool
    message_id: str | None = None
    inserted_id: str | None = None
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


def _extract_inserted_id(payload):
    if not isinstance(payload, dict):
        return None
    for key in ('insertedId', 'inserted_id'):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for nested_key in ('message', 'data', 'result'):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            nested_id = _extract_inserted_id(nested)
            if nested_id:
                return nested_id
    return None


def send_text_message(phone, message):
    config = WapiConfiguration.get_solo()
    instance_id = config.resolved_instance_id().strip()
    token = config.resolved_token().strip()

    if not instance_id or not token:
        send_logger.warning('Envio W-API abortado: configuracao ausente (instance/token).')
        return WapiSendResult(success=False, error=SEND_CONFIG_ERROR)

    # Normaliza o telefone (apenas digitos) usando a mesma regra do recebimento.
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return WapiSendResult(success=False, error='Telefone invalido para envio.')

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
        'phone': normalized_phone,
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
            http_ok = 200 <= response.status < 300
            logical_error = _response_indicates_error(parsed_body)
            if http_ok and not logical_error:
                return WapiSendResult(
                    success=True,
                    message_id=_extract_message_id(parsed_body),
                    inserted_id=_extract_inserted_id(parsed_body),
                    status_code=response.status,
                )
            # HTTP 2xx mas a W-API sinalizou erro (ex.: instancia desconectada).
            send_logger.warning(
                'Envio W-API falhou: status=%s corpo=%s',
                response.status,
                response_body[:500],
            )
            return WapiSendResult(success=False, status_code=response.status, error=SEND_GENERIC_ERROR)
    except error.HTTPError as exc:
        # Loga o motivo real (sem token; o corpo de resposta nao contem o token).
        try:
            error_body = exc.read().decode('utf-8', 'ignore')[:500]
        except Exception:
            error_body = ''
        send_logger.warning('Envio W-API falhou: HTTP %s corpo=%s', exc.code, error_body)
        return WapiSendResult(success=False, status_code=exc.code, error=SEND_GENERIC_ERROR)
    except error.URLError as exc:
        send_logger.warning('Envio W-API sem conexao: %s', getattr(exc, 'reason', exc))
        return WapiSendResult(success=False, error=SEND_GENERIC_ERROR)
    except json.JSONDecodeError:
        send_logger.warning('Envio W-API retornou resposta nao-JSON.')
        return WapiSendResult(success=False, error=SEND_GENERIC_ERROR)
