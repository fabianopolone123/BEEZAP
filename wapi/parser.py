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
    return str(value).strip()


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes', 'sim')
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _normalize_phone(value):
    """Remove sufixos de JID do WhatsApp para manter apenas o numero."""
    text = _as_text(value)
    if not text:
        return ''
    # Ex.: 5511999999999@s.whatsapp.net / @c.us / @g.us
    text = text.split('@', 1)[0]
    # Ex.: 5511999999999:12 (identificador de dispositivo)
    text = text.split(':', 1)[0]
    return text.strip()


def parse_wapi_webhook_payload(payload):
    if not isinstance(payload, dict):
        payload = {}

    event_type = _safe_get(
        payload,
        ('event',),
        ('eventType',),
        ('type',),
        ('data', 'event'),
        ('data', 'eventType'),
        ('data', 'type'),
    )
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
        ('contact', 'phone'),
        ('contact', 'number'),
        ('data', 'phone'),
        ('data', 'from'),
        ('data', 'sender'),
        ('data', 'remoteJid'),
        ('data', 'contact', 'phone'),
        ('key', 'remoteJid'),
        ('data', 'key', 'remoteJid'),
        ('message', 'phone'),
        ('message', 'from'),
        ('message', 'remoteJid'),
        ('data', 'message', 'phone'),
        ('data', 'message', 'from'),
        ('data', 'message', 'remoteJid'),
        ('messages', 0, 'key', 'remoteJid'),
        ('messages', 0, 'from'),
        ('data', 'messages', 0, 'key', 'remoteJid'),
        ('data', 'messages', 0, 'from'),
    )
    contact_name = _safe_get(
        payload,
        ('contactName',),
        ('senderName',),
        ('pushName',),
        ('name',),
        ('data', 'contactName'),
        ('data', 'senderName'),
        ('data', 'pushName'),
        ('data', 'name'),
        ('contact', 'name'),
        ('contact', 'pushName'),
        ('sender', 'name'),
        ('data', 'contact', 'name'),
        ('data', 'contact', 'pushName'),
        ('data', 'sender', 'name'),
        ('messages', 0, 'pushName'),
        ('data', 'messages', 0, 'pushName'),
    )
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
        ('data', 'message', 'text'),
        ('data', 'message', 'body'),
        ('data', 'message', 'conversation'),
        ('data', 'message', 'extendedTextMessage', 'text'),
        ('message', 'text'),
        ('message', 'body'),
        ('message', 'conversation'),
        ('message', 'extendedTextMessage', 'text'),
        ('textMessage', 'text'),
        ('data', 'textMessage', 'text'),
        ('messages', 0, 'message', 'conversation'),
        ('messages', 0, 'message', 'extendedTextMessage', 'text'),
        ('data', 'messages', 0, 'message', 'conversation'),
        ('data', 'messages', 0, 'message', 'extendedTextMessage', 'text'),
    )
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
        'phone': _normalize_phone(phone),
        'contact_name': _as_text(contact_name),
        'message_id': _as_text(message_id),
        'message_type': _as_text(message_type, 'unknown') or 'unknown',
        'message_text': _as_text(message_text),
        'from_me': _as_bool(from_me),
    }
