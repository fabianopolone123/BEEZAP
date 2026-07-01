def _safe_get(payload, *paths):
    for path in paths:
        current = payload
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = None
                break
        if current not in (None, ''):
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


def parse_wapi_webhook_payload(payload):
    if not isinstance(payload, dict):
        payload = {}

    event_type = _safe_get(
        payload,
        ('event',),
        ('eventType',),
        ('type',),
        ('data', 'event'),
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
        ('remoteJid',),
        ('data', 'phone'),
        ('data', 'from'),
        ('data', 'remoteJid'),
        ('message', 'phone'),
        ('message', 'from'),
        ('message', 'remoteJid'),
        ('data', 'message', 'phone'),
        ('data', 'message', 'from'),
        ('data', 'message', 'remoteJid'),
    )
    contact_name = _safe_get(
        payload,
        ('contactName',),
        ('senderName',),
        ('pushName',),
        ('data', 'contactName'),
        ('data', 'senderName'),
        ('data', 'pushName'),
        ('contact', 'name'),
        ('data', 'contact', 'name'),
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
        ('message',),
        ('data', 'text'),
        ('data', 'body'),
        ('data', 'message', 'text'),
        ('data', 'message', 'body'),
        ('message', 'text'),
        ('message', 'body'),
        ('textMessage', 'text'),
        ('data', 'textMessage', 'text'),
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
    )

    return {
        'event_type': _as_text(event_type, 'unknown') or 'unknown',
        'instance_id': _as_text(instance_id),
        'phone': _as_text(phone),
        'contact_name': _as_text(contact_name),
        'message_id': _as_text(message_id),
        'message_type': _as_text(message_type, 'unknown') or 'unknown',
        'message_text': _as_text(message_text),
        'from_me': _as_bool(from_me),
    }
