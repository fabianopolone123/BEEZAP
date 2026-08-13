import json
import logging
from dataclasses import dataclass
from urllib import error, parse, request

from django.conf import settings

from accounts.models import WapiConfiguration
from wapi.parser import normalize_recipient


WAPI_MESSAGE_PREFIX = '/v1/message/'
WAPI_GROUP_PREFIX = '/v1/group/'
WAPI_INSTANCE_PREFIX = '/v1/instance/'

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


def _company_config(company):
    """Credenciais da W-API DA EMPRESA informada (multiempresa).

    Cada empresa cliente tem a sua propria instancia/token da W-API, entao toda
    chamada precisa dizer de qual empresa se trata. Passar `company=None` e
    considerado erro de programacao: seria enviar mensagem pela instancia errada.
    """
    if company is None:
        raise ValueError('Informe a empresa (company) para usar a W-API.')
    return WapiConfiguration.for_company(company)


def _wapi_get(action, company, prefix=WAPI_MESSAGE_PREFIX, timeout=30):
    """GET em https://api.w-api.app<prefix><action>?instanceId=...

    Retorna (ok, status_code, body, friendly_error). O body pode ser lista OU
    dict (ex.: get-all-groups devolve uma lista). Nunca expoe token nem traceback.
    """
    config = _company_config(company)
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


def _wapi_post(action, payload, company, timeout=30):
    """POST em https://api.w-api.app/v1/message/<action>?instanceId=...

    Centraliza credenciais, headers e tratamento de erro. Retorna uma tupla
    (ok, status_code, body_dict, friendly_error). Nunca expoe token nem traceback.
    As credenciais sao as DA EMPRESA informada (ver `_company_config`).
    """
    config = _company_config(company)
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


def _send(action, phone, extra, company):
    """Monta o body {phone, ...} e devolve WapiSendResult padronizado.

    O campo `phone` aceita telefone (so digitos) OU o JID de grupo (@g.us) / LID
    (@lid) para responder no lugar certo — nunca o participante individual."""
    normalized_phone = normalize_recipient(phone)
    if not normalized_phone:
        return WapiSendResult(success=False, error='Telefone invalido para envio.')
    payload = {'phone': normalized_phone}
    payload.update({k: v for k, v in extra.items() if v not in (None, '')})
    ok, status, body, err = _wapi_post(action, payload, company)
    if not ok:
        return WapiSendResult(success=False, status_code=status, error=err)
    return WapiSendResult(
        success=True,
        message_id=_extract_message_id(body),
        inserted_id=_extract_inserted_id(body),
        status_code=status,
    )


# --- Envio LITE (confirmado na documentacao/Postman da W-API) ---
#
# MULTIEMPRESA: `company` e OBRIGATORIO e somente-nomeado em todas as funcoes
# publicas. Cada empresa cliente tem a sua instancia da W-API; deixar a empresa
# implicita mandaria a mensagem pelo WhatsApp de outro cliente. Ser somente-nomeado
# garante que nenhuma chamada antiga passe pela posicao errada em silencio.

def send_text_message(phone, message, *, company):
    return _send('send-text', phone, {'message': message}, company)


def send_image_message(phone, image, caption=None, *, company):
    return _send('send-image', phone, {'image': image, 'caption': caption}, company)


def send_audio_message(phone, audio, *, company):
    return _send('send-audio', phone, {'audio': audio}, company)


def send_video_message(phone, video, caption=None, *, company):
    return _send('send-video', phone, {'video': video, 'caption': caption}, company)


def send_document_message(phone, document, file_name=None, caption=None, extension=None,
                          *, company):
    # A W-API exige `extension` (ex.: "pdf"); sem ela responde HTTP 500
    # "A extensao do arquivo e obrigatoria.".
    return _send('send-document', phone, {
        'document': document, 'fileName': file_name,
        'extension': extension, 'caption': caption,
    }, company)


def download_media(media_key, direct_path, media_type, mimetype, *, company):
    """Baixa a midia de uma mensagem recebida. Retorna o corpo (com fileLink,
    expires, mimetype, type) em caso de sucesso, ou None em caso de falha."""
    payload = {
        'mediaKey': media_key or '',
        'directPath': direct_path or '',
        'type': media_type or '',
        'mimetype': mimetype or '',
    }
    ok, _status, body, _err = _wapi_post('download-media', payload, company)
    return body if ok else None


@dataclass
class WapiHealth:
    """Saude da conexao do WhatsApp de UMA empresa (usado no painel do master).

    E so o estado do canal — nunca conteudo de conversa.
    """
    configured: bool          # tem instancia + token cadastrados?
    connected: bool | None    # True/False; None = nao deu para verificar agora
    label: str                # texto curto para a tela
    detail: str = ''          # explicacao amigavel (sem token, sem traceback)


def check_connection(*, company):
    """Consulta a W-API se a instancia DA EMPRESA esta conectada ao WhatsApp.

    Primeiro tenta o endpoint de status da instancia; se essa rota nao existir no
    plano/versao da conta (404), cai numa chamada conhecida (`get-all-groups`) so
    para saber se a credencial responde. Nunca levanta excecao e nunca expoe token.
    """
    config = _company_config(company)
    if not config.resolved_instance_id().strip() or not config.resolved_token().strip():
        return WapiHealth(
            configured=False, connected=False, label='Nao configurado',
            detail='Cadastre o Instance ID e o Token na aba WhatsApp deste cliente.',
        )

    ok, status, body, _err = _wapi_get('status-instance', company, prefix=WAPI_INSTANCE_PREFIX)
    if ok:
        connected = _health_from_status_body(body)
        if connected is None:
            return WapiHealth(
                configured=True, connected=None, label='Nao foi possivel verificar',
                detail='A W-API respondeu, mas nao informou o estado da conexao.',
            )
        return WapiHealth(
            configured=True, connected=connected,
            label='Conectado' if connected else 'Desconectado',
            detail='' if connected else 'Reconecte o WhatsApp no painel da W-API (ler o QR Code).',
        )

    # Rota de status indisponivel: usa uma chamada que sabemos existir como sonda.
    if status in (404, 405):
        probe_ok, _s, _b, _e = _wapi_get('get-all-groups', company, prefix=WAPI_GROUP_PREFIX)
        if probe_ok:
            return WapiHealth(
                configured=True, connected=True, label='Conectado',
                detail='Verificado pela consulta de grupos (a conta respondeu).',
            )

    if status in (401, 403):
        return WapiHealth(
            configured=True, connected=False, label='Credencial recusada',
            detail='A W-API recusou o Instance ID/Token deste cliente.',
        )
    return WapiHealth(
        configured=True, connected=None, label='Nao foi possivel verificar',
        detail='A W-API nao respondeu agora. Tente de novo em alguns instantes.',
    )


def _health_from_status_body(body):
    """Le o corpo do status-instance e diz se esta conectado (None = nao deu p/ saber).

    A W-API ja devolveu esse estado com nomes diferentes conforme a versao, entao
    aceitamos as formas conhecidas em vez de fixar uma so.
    """
    if not isinstance(body, dict):
        return None
    for key in ('connected', 'isConnected', 'loggedIn'):
        value = body.get(key)
        if isinstance(value, bool):
            return value
    for key in ('status', 'state', 'connectionStatus', 'instanceStatus'):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip().lower()
            if text in ('connected', 'open', 'online', 'authenticated', 'inchat', 'success'):
                return True
            if text in ('disconnected', 'close', 'closed', 'offline', 'unpaired', 'error', 'failed'):
                return False
    return None


def get_all_groups(*, company):
    """Lista os grupos/comunidades da conta conectada DA EMPRESA (LITE/PRO).

    GET /v1/group/get-all-groups?instanceId=... — usado para descobrir o nome
    real dos grupos (o webhook geralmente traz so o JID). Retorna o corpo (lista
    ou dict, conforme a W-API) em caso de sucesso, ou None em caso de falha."""
    ok, _status, body, _err = _wapi_get('get-all-groups', company, prefix=WAPI_GROUP_PREFIX)
    return body if ok else None
