import json
import logging
from dataclasses import dataclass
from urllib import error, parse, request

from django.conf import settings

from accounts.models import WapiConfiguration
from wapi.parser import normalize_recipient


WAPI_MESSAGE_PREFIX = '/v1/message/'
WAPI_GROUP_PREFIX = '/v1/group/'

# Mensagens amigaveis (nunca expor token, payload bruto ou traceback ao usuario).
SEND_GENERIC_ERROR = (
    'Nao foi possivel enviar a mensagem. Verifique a conexao do WhatsApp e tente novamente.'
)
SEND_CONFIG_ERROR = 'Configure a W-API antes de enviar mensagens.'

send_logger = logging.getLogger('beezap.wapi.send')


@dataclass
class WapiSendResult:
    success: bool
    message_id: str | None = None
    inserted_id: str | None = None
    status_code: int | None = None
    error: str | None = None


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


def _build_wapi_url(prefix, action, instance_id):
    """Monta a URL final com instanceId em query string, preservando o prefixo."""
    url = settings.WAPI_BASE_URL.rstrip('/') + prefix + action
    url_parts = parse.urlsplit(url)
    query = parse.parse_qs(url_parts.query, keep_blank_values=True)
    query['instanceId'] = [instance_id]
    return parse.urlunsplit((
        url_parts.scheme, url_parts.netloc, url_parts.path,
        parse.urlencode(query, doseq=True), url_parts.fragment,
    ))


def _wapi_get(action, prefix=WAPI_MESSAGE_PREFIX, timeout=30):
    """GET em https://api.w-api.app<prefix><action>?instanceId=...

    Retorna (ok, status_code, body, friendly_error). O body pode ser lista OU
    dict (ex.: get-all-groups devolve uma lista). Nunca expoe token nem traceback.
    """
    config = WapiConfiguration.get_solo()
    instance_id = config.resolved_instance_id().strip()
    token = config.resolved_token().strip()
    if not instance_id or not token:
        send_logger.warning('W-API GET abortado (%s): configuracao ausente.', action)
        return (False, None, None, SEND_CONFIG_ERROR)

    final_url = _build_wapi_url(prefix, action, instance_id)
    http_request = request.Request(
        final_url, method='GET',
        headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'},
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            response_body = response.read().decode('utf-8', 'ignore')
            parsed_body = json.loads(response_body) if response_body else None
            if 200 <= response.status < 300:
                return (True, response.status, parsed_body, None)
            send_logger.warning('W-API GET %s falhou: status=%s corpo=%s', action, response.status, response_body[:500])
            return (False, response.status, None, SEND_GENERIC_ERROR)
    except error.HTTPError as exc:
        try:
            error_body = exc.read().decode('utf-8', 'ignore')[:500]
        except Exception:
            error_body = ''
        send_logger.warning('W-API GET %s falhou: HTTP %s corpo=%s', action, exc.code, error_body)
        return (False, exc.code, None, SEND_GENERIC_ERROR)
    except error.URLError as exc:
        send_logger.warning('W-API GET %s sem conexao: %s', action, getattr(exc, 'reason', exc))
        return (False, None, None, SEND_GENERIC_ERROR)
    except json.JSONDecodeError:
        send_logger.warning('W-API GET %s retornou resposta nao-JSON.', action)
        return (False, None, None, SEND_GENERIC_ERROR)


def _wapi_post(action, payload, timeout=30):
    """POST em https://api.w-api.app/v1/message/<action>?instanceId=...

    Centraliza credenciais, headers e tratamento de erro. Retorna uma tupla
    (ok, status_code, body_dict, friendly_error). Nunca expoe token nem traceback.
    """
    config = WapiConfiguration.get_solo()
    instance_id = config.resolved_instance_id().strip()
    token = config.resolved_token().strip()
    if not instance_id or not token:
        send_logger.warning('W-API abortado (%s): configuracao ausente.', action)
        return (False, None, {}, SEND_CONFIG_ERROR)

    final_url = _build_wapi_url(WAPI_MESSAGE_PREFIX, action, instance_id)

    body = json.dumps(payload).encode('utf-8')
    http_request = request.Request(
        final_url, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
    )

    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            response_body = response.read().decode('utf-8', 'ignore')
            parsed_body = json.loads(response_body) if response_body else {}
            http_ok = 200 <= response.status < 300
            if http_ok and not _response_indicates_error(parsed_body):
                return (True, response.status, parsed_body if isinstance(parsed_body, dict) else {}, None)
            send_logger.warning('W-API %s falhou: status=%s corpo=%s', action, response.status, response_body[:500])
            return (False, response.status, {}, SEND_GENERIC_ERROR)
    except error.HTTPError as exc:
        try:
            error_body = exc.read().decode('utf-8', 'ignore')[:500]
        except Exception:
            error_body = ''
        send_logger.warning('W-API %s falhou: HTTP %s corpo=%s', action, exc.code, error_body)
        return (False, exc.code, {}, SEND_GENERIC_ERROR)
    except error.URLError as exc:
        send_logger.warning('W-API %s sem conexao: %s', action, getattr(exc, 'reason', exc))
        return (False, None, {}, SEND_GENERIC_ERROR)
    except json.JSONDecodeError:
        send_logger.warning('W-API %s retornou resposta nao-JSON.', action)
        return (False, None, {}, SEND_GENERIC_ERROR)


def _send(action, phone, extra):
    """Monta o body {phone, ...} e devolve WapiSendResult padronizado.

    O campo `phone` aceita telefone (so digitos) OU o JID de grupo (@g.us) / LID
    (@lid) para responder no lugar certo — nunca o participante individual."""
    normalized_phone = normalize_recipient(phone)
    if not normalized_phone:
        return WapiSendResult(success=False, error='Telefone invalido para envio.')
    payload = {'phone': normalized_phone}
    payload.update({k: v for k, v in extra.items() if v not in (None, '')})
    ok, status, body, err = _wapi_post(action, payload)
    if not ok:
        return WapiSendResult(success=False, status_code=status, error=err)
    return WapiSendResult(
        success=True,
        message_id=_extract_message_id(body),
        inserted_id=_extract_inserted_id(body),
        status_code=status,
    )


# --- Envio LITE (confirmado na documentacao/Postman da W-API) ---

def send_text_message(phone, message):
    return _send('send-text', phone, {'message': message})


def send_image_message(phone, image, caption=None):
    return _send('send-image', phone, {'image': image, 'caption': caption})


def send_audio_message(phone, audio):
    return _send('send-audio', phone, {'audio': audio})


def send_video_message(phone, video, caption=None):
    return _send('send-video', phone, {'video': video, 'caption': caption})


def send_document_message(phone, document, file_name=None, caption=None, extension=None):
    # A W-API exige `extension` (ex.: "pdf"); sem ela responde HTTP 500
    # "A extensao do arquivo e obrigatoria.".
    return _send('send-document', phone, {
        'document': document, 'fileName': file_name,
        'extension': extension, 'caption': caption,
    })


def download_media(media_key, direct_path, media_type, mimetype):
    """Baixa a midia de uma mensagem recebida. Retorna o corpo (com fileLink,
    expires, mimetype, type) em caso de sucesso, ou None em caso de falha."""
    payload = {
        'mediaKey': media_key or '',
        'directPath': direct_path or '',
        'type': media_type or '',
        'mimetype': mimetype or '',
    }
    ok, _status, body, _err = _wapi_post('download-media', payload)
    return body if ok else None


def get_all_groups():
    """Lista os grupos/comunidades da conta conectada (LITE/PRO).

    GET /v1/group/get-all-groups?instanceId=... — usado para descobrir o nome
    real dos grupos (o webhook geralmente traz so o JID). Retorna o corpo (lista
    ou dict, conforme a W-API) em caso de sucesso, ou None em caso de falha."""
    ok, _status, body, _err = _wapi_get('get-all-groups', prefix=WAPI_GROUP_PREFIX)
    return body if ok else None
