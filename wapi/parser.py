# Chaves provaveis para busca recursiva (fallback), em ordem de preferencia.
EVENT_KEYS = ('event', 'type', 'eventtype', 'event_type', 'webhooktype')
PHONE_KEYS = ('phone', 'from', 'sender', 'remotejid', 'jid', 'number')
NAME_KEYS = ('sendername', 'pushname', 'contactname', 'notifyname', 'name')
TEXT_KEYS = ('conversation', 'text', 'body', 'caption', 'content', 'message')


def _safe_get(payload, *paths):
    for path in paths:
        current = payload
        found = True
        for key in path:
            if isinstance(key, int):
                if isinstance(current, (list, tuple)) and -len(current) <= key < len(current):
                    current = current[key]
                else:
                    found = False
                    break
            elif isinstance(current, dict):
                current = current.get(key)
            else:
                found = False
                break
        # So aceitamos valores escalares como resultado final; se o caminho parar
        # em um dict/lista (ex.: "message" quando o texto esta em "message.body"),
        # continuamos tentando os proximos caminhos.
        if found and current not in (None, '') and not isinstance(current, (dict, list, tuple)):
            return current
    return None


def _as_text(value, default=''):
    if value in (None, ''):
        return default
    if isinstance(value, (dict, list, tuple)):
        return default
    return str(value).strip()


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes', 'sim')
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _only_digits(value):
    return ''.join(ch for ch in str(value) if ch.isdigit())


def normalize_phone(value):
    """Extrai apenas os digitos do telefone (DDI + DDD + numero).

    "5516999999999@s.whatsapp.net" -> "5516999999999"
    "+55 (16) 99999-9999"          -> "5516999999999"
    Retorna vazio se nao houver numero valido.
    """
    text = _as_text(value)
    if not text:
        return ''
    # Remove sufixos de JID do WhatsApp e identificador de dispositivo.
    text = text.split('@', 1)[0].split(':', 1)[0]
    digits = _only_digits(text)
    # Um telefone real tem pelo menos DDI+DDD+numero; evita capturar ruido.
    return digits if len(digits) >= 8 else ''


def _deep_find(node, target_keys, validate):
    """Procura recursivamente, em qualquer profundidade, o primeiro valor escalar
    cuja chave (em minusculas) esteja em target_keys e passe no validador."""
    for target in target_keys:
        found = _deep_find_key(node, target, validate)
        if found not in (None, ''):
            return found
    return None


def _deep_find_key(node, target_key, validate):
    if isinstance(node, dict):
        # 1) match direto de chave neste nivel
        for key, value in node.items():
            if (
                isinstance(key, str)
                and key.lower() == target_key
                and not isinstance(value, (dict, list, tuple))
                and value not in (None, '')
                and validate(value)
            ):
                return value
        # 2) desce para os filhos
        for value in node.values():
            found = _deep_find_key(value, target_key, validate)
            if found not in (None, ''):
                return found
    elif isinstance(node, (list, tuple)):
        for item in node:
            found = _deep_find_key(item, target_key, validate)
            if found not in (None, ''):
                return found
    return None


def _valid_any(value):
    return value not in (None, '')


def _valid_phone(value):
    return len(normalize_phone(value)) >= 8


def _valid_text(value):
    return isinstance(value, str) and bool(value.strip())


def _valid_name(value):
    # Nome nao pode ser um numero de telefone nem valor booleano.
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    digits = _only_digits(text)
    return len(digits) < 8


def parse_wapi_webhook_payload(payload):
    if not isinstance(payload, dict):
        payload = {}

    event_type = _safe_get(
        payload,
        ('event',),
        ('eventType',),
        ('event_type',),
        ('webhookType',),
        ('type',),
        ('data', 'event'),
        ('data', 'eventType'),
        ('data', 'type'),
    ) or _deep_find(payload, EVENT_KEYS, _valid_any)

    instance_id = _safe_get(
        payload,
        ('instanceId',),
        ('instance_id',),
        ('instance', 'id'),
        ('data', 'instanceId'),
        ('data', 'instance', 'id'),
    )

    phone = _safe_get(
        payload,
        ('phone',),
        ('from',),
        ('sender',),
        ('remoteJid',),
        ('jid',),
        ('number',),
        ('contact', 'phone'),
        ('contact', 'number'),
        ('data', 'phone'),
        ('data', 'from'),
        ('data', 'sender'),
        ('data', 'remoteJid'),
        ('data', 'jid'),
        ('data', 'number'),
        ('data', 'contact', 'phone'),
        ('data', 'contact', 'number'),
        ('key', 'remoteJid'),
        ('data', 'key', 'remoteJid'),
        ('message', 'from'),
        ('message', 'phone'),
        ('data', 'message', 'from'),
        ('data', 'message', 'phone'),
        ('messages', 0, 'key', 'remoteJid'),
        ('data', 'messages', 0, 'key', 'remoteJid'),
    )
    if not _valid_phone(phone):
        phone = _deep_find(payload, PHONE_KEYS, _valid_phone) or phone

    contact_name = _safe_get(
        payload,
        ('senderName',),
        ('pushName',),
        ('contactName',),
        ('notifyName',),
        ('name',),
        ('contact', 'name'),
        ('contact', 'pushName'),
        ('data', 'senderName'),
        ('data', 'pushName'),
        ('data', 'contactName'),
        ('data', 'notifyName'),
        ('data', 'name'),
        ('data', 'contact', 'name'),
        ('data', 'contact', 'pushName'),
        ('messages', 0, 'pushName'),
        ('data', 'messages', 0, 'pushName'),
    )
    if not _valid_name(contact_name):
        contact_name = _deep_find(payload, NAME_KEYS, _valid_name) or contact_name

    message_id = _safe_get(
        payload,
        ('messageId',),
        ('message_id',),
        ('id',),
        ('data', 'messageId'),
        ('data', 'message_id'),
        ('data', 'id'),
        ('message', 'id'),
        ('data', 'message', 'id'),
        ('key', 'id'),
        ('data', 'key', 'id'),
        ('messages', 0, 'key', 'id'),
        ('data', 'messages', 0, 'key', 'id'),
    )

    message_type = _safe_get(
        payload,
        ('messageType',),
        ('message_type',),
        ('typeMessage',),
        ('data', 'messageType'),
        ('data', 'message_type'),
        ('data', 'typeMessage'),
        ('message', 'type'),
        ('data', 'message', 'type'),
    )

    message_text = _safe_get(
        payload,
        ('text',),
        ('body',),
        ('content',),
        ('caption',),
        ('message',),
        ('data', 'text'),
        ('data', 'body'),
        ('data', 'content'),
        ('data', 'caption'),
        ('data', 'message', 'text'),
        ('data', 'message', 'body'),
        ('data', 'message', 'conversation'),
        ('data', 'message', 'extendedTextMessage', 'text'),
        ('data', 'message', 'imageMessage', 'caption'),
        ('data', 'message', 'videoMessage', 'caption'),
        ('message', 'text'),
        ('message', 'body'),
        ('message', 'conversation'),
        ('message', 'extendedTextMessage', 'text'),
        ('message', 'imageMessage', 'caption'),
        ('textMessage', 'text'),
        ('data', 'textMessage', 'text'),
        ('messages', 0, 'message', 'conversation'),
        ('messages', 0, 'message', 'extendedTextMessage', 'text'),
        ('data', 'messages', 0, 'message', 'conversation'),
        ('data', 'messages', 0, 'message', 'extendedTextMessage', 'text'),
    )
    if not _valid_text(message_text):
        message_text = _deep_find(payload, TEXT_KEYS, _valid_text) or message_text

    from_me = _safe_get(
        payload,
        ('fromMe',),
        ('from_me',),
        ('data', 'fromMe'),
        ('data', 'from_me'),
        ('key', 'fromMe'),
        ('data', 'key', 'fromMe'),
        ('message', 'fromMe'),
        ('data', 'message', 'fromMe'),
        ('messages', 0, 'key', 'fromMe'),
        ('data', 'messages', 0, 'key', 'fromMe'),
    )

    return {
        'event_type': _as_text(event_type, 'unknown') or 'unknown',
        'instance_id': _as_text(instance_id),
        'phone': normalize_phone(phone),
        'contact_name': _as_text(contact_name),
        'message_id': _as_text(message_id),
        'message_type': _as_text(message_type, 'unknown') or 'unknown',
        'message_text': _as_text(message_text),
        'from_me': _as_bool(from_me),
    }
