# Chaves provaveis para busca recursiva (fallback), em ordem de preferencia.
EVENT_KEYS = ('event', 'type', 'eventtype', 'event_type', 'webhooktype')
PHONE_KEYS = (
    'participant', 'remotejid', 'senderphone', 'sendernumber',
    'phone', 'from', 'sender', 'chatid', 'jid', 'number', 'id',
)
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


# Sufixos de JID que NAO representam telefone de uma pessoa: grupo (@g.us),
# identificador interno (@lid), canal/newsletter (@newsletter) e transmissao
# (@broadcast, inclusive "status@broadcast").
_NON_PERSONAL_JID_SUFFIXES = ('@g.us', '@lid', '@newsletter', '@broadcast')

# Telefone real (E.164) tem no maximo 15 digitos. IDs internos do WhatsApp de
# grupo/canal ("120363...") sao mais longos e NAO sao telefone.
_MAX_PHONE_DIGITS = 15


def normalize_phone(value):
    """Extrai apenas os digitos do telefone (DDI + DDD + numero).

    "5516999999999@s.whatsapp.net" -> "5516999999999"
    "+55 (16) 99999-9999"          -> "5516999999999"
    Retorna vazio se nao houver numero valido (grupo/canal/transmissao/LID, ou
    id interno longo demais para ser telefone, como "120363...").
    """
    text = _as_text(value)
    if not text:
        return ''
    # Grupo/canal/transmissao/LID nao sao telefone de pessoa.
    low = text.lower()
    if any(suffix in low for suffix in _NON_PERSONAL_JID_SUFFIXES):
        return ''
    # Remove sufixos de JID do WhatsApp e identificador de dispositivo.
    text = text.split('@', 1)[0].split(':', 1)[0]
    digits = _only_digits(text)
    # Telefone real tem DDI+DDD+numero e no maximo 15 digitos; fora disso e ruido
    # ou id interno do WhatsApp (ex.: JID numerico de grupo/canal).
    if len(digits) < 8 or len(digits) > _MAX_PHONE_DIGITS:
        return ''
    return digits


def is_group_jid(value):
    """True para IDs que representam conversa coletiva/nao-pessoal: grupo (@g.us),
    canal (@newsletter) ou transmissao (@broadcast), e tambem o id numerico
    interno "pelado" do WhatsApp (ex.: "120363...") que as vezes chega sem sufixo.

    JIDs de pessoa (@s.whatsapp.net, @c.us, @lid) sao considerados DIRETOS.
    """
    text = _as_text(value).lower()
    if not text:
        return False
    if text.endswith('@g.us') or text.endswith('@newsletter') or text.endswith('@broadcast'):
        return True
    if '@' in text:
        return False  # outro JID com sufixo (ex.: @lid, @s.whatsapp.net) = direto
    # Numero "pelado" longo demais para telefone => JID interno (grupo/canal).
    return len(_only_digits(text)) > _MAX_PHONE_DIGITS


def is_ignorable_jid(value):
    """True para conversas que NAO sao atendimento e devem ser ignoradas:
    canal/newsletter (@newsletter) e transmissao/status (@broadcast).

    Grupos (@g.us) e diretas seguem normais; so os sufixos de broadcast/canal
    (mensagens de mao unica, sem atendimento) sao descartados.
    """
    text = _as_text(value).lower()
    return text.endswith('@newsletter') or text.endswith('@broadcast')


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

    # Prioridade: remetente real em estruturas aninhadas da W-API; depois campos
    # de nivel raiz; por fim, busca recursiva. A cada passo validamos/normalizamos
    # o telefone para nao aceitar grupo (@g.us) nem valores invalidos.
    phone = ''
    for phone_path in (
        # Remetente real (objeto "sender" da W-API Lite): sender.id e o telefone.
        ('sender', 'id'),
        ('sender', 'phone'),
        ('sender', 'number'),
        ('data', 'sender', 'id'),
        ('data', 'sender', 'phone'),
        ('data', 'sender', 'number'),
        # Estruturas aninhadas comuns (Baileys/grupos): participant e o autor real.
        ('data', 'key', 'participant'),
        ('data', 'key', 'remoteJid'),
        ('data', 'participant'),
        ('data', 'remoteJid'),
        ('data', 'from'),
        ('data', 'sender'),
        ('data', 'senderPhone'),
        ('data', 'senderNumber'),
        ('data', 'chatId'),
        ('data', 'phone'),
        ('data', 'number'),
        ('data', 'jid'),
        ('data', 'contact', 'phone'),
        ('data', 'contact', 'number'),
        ('data', 'contact', 'id'),
        ('data', 'message', 'from'),
        ('data', 'message', 'sender'),
        ('data', 'message', 'remoteJid'),
        ('key', 'participant'),
        ('key', 'remoteJid'),
        ('phone',),
        ('from',),
        ('sender',),
        ('senderPhone',),
        ('senderNumber',),
        ('remoteJid',),
        ('participant',),
        ('jid',),
        ('number',),
        ('chatId',),
        ('chat', 'id'),
        ('data', 'chat', 'id'),
        ('contact', 'phone'),
        ('contact', 'number'),
        ('contact', 'id'),
        ('messages', 0, 'key', 'participant'),
        ('messages', 0, 'key', 'remoteJid'),
        ('data', 'messages', 0, 'key', 'participant'),
        ('data', 'messages', 0, 'key', 'remoteJid'),
    ):
        candidate = normalize_phone(_safe_get(payload, phone_path))
        if candidate:
            phone = candidate
            break
    if not phone:
        phone = normalize_phone(_deep_find(payload, PHONE_KEYS, _valid_phone))

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
        'phone': phone,
        'contact_name': _as_text(contact_name),
        'message_id': _as_text(message_id),
        'message_type': _as_text(message_type, 'unknown') or 'unknown',
        'message_text': _as_text(message_text),
        'from_me': _as_bool(from_me),
    }


def _message_content(payload):
    """Retorna o dict de conteudo da mensagem (msgContent/message), onde ficam as
    chaves de tipo (imageMessage, audioMessage, conversation, etc.)."""
    for path in (
        ('msgContent',),
        ('message',),
        ('data', 'message'),
        ('data', 'msgContent'),
        ('messages', 0, 'message'),
        ('data', 'messages', 0, 'message'),
    ):
        node = payload
        ok = True
        for key in path:
            if isinstance(key, int):
                if isinstance(node, (list, tuple)) and -len(node) <= key < len(node):
                    node = node[key]
                else:
                    ok = False
                    break
            elif isinstance(node, dict):
                node = node.get(key)
            else:
                ok = False
                break
        if ok and isinstance(node, dict):
            return node
    return {}


# Chave de conteudo -> tipo normalizado do BEEZAP.
_MEDIA_CONTENT_TYPES = (
    ('imageMessage', 'image'),
    ('audioMessage', 'audio'),
    ('videoMessage', 'video'),
    ('stickerMessage', 'sticker'),
    ('documentMessage', 'document'),
)


def parse_wapi_media(payload):
    """Detecta o tipo normalizado da mensagem e os metadados de midia.

    Retorna: message_type, caption, media_mimetype, media_key, direct_path,
    media_url, reaction. Defensivo: nunca quebra e cai em 'text'/'unknown'.
    """
    if not isinstance(payload, dict):
        payload = {}

    result = {
        'message_type': 'text',
        'caption': '',
        'media_mimetype': '',
        'media_key': '',
        'direct_path': '',
        'media_url': '',
        'reaction': '',
    }

    content = _message_content(payload)

    # Texto (conversation / extendedTextMessage) ou payload simples sem conteudo.
    if not content or 'conversation' in content or 'extendedTextMessage' in content:
        result['message_type'] = 'text'
        return result

    # Reacao.
    if isinstance(content.get('reactionMessage'), dict):
        result['message_type'] = 'reaction'
        result['reaction'] = _as_text(content['reactionMessage'].get('text'))
        return result

    # Midia (imagem/audio/video/sticker/documento; video com gifPlayback -> gif).
    for key, mtype in _MEDIA_CONTENT_TYPES:
        node = content.get(key)
        if isinstance(node, dict):
            if mtype == 'video' and _as_bool(node.get('gifPlayback')):
                mtype = 'gif'
            result['message_type'] = mtype
            result['media_mimetype'] = _as_text(node.get('mimetype'))
            result['media_key'] = _as_text(node.get('mediaKey'))
            result['direct_path'] = _as_text(node.get('directPath'))
            result['media_url'] = _as_text(node.get('url'))
            result['caption'] = _as_text(node.get('caption') or node.get('fileName'))
            return result

    if 'locationMessage' in content:
        result['message_type'] = 'location'
        return result
    if 'contactMessage' in content or 'contactsArrayMessage' in content:
        result['message_type'] = 'contact'
        return result

    # Fallback por tipo textual explicito, se a W-API mandar.
    explicit = _safe_get(
        payload,
        ('type',), ('messageType',), ('typeMessage',),
        ('data', 'type'), ('data', 'messageType'), ('data', 'typeMessage'),
    )
    explicit = explicit.lower() if isinstance(explicit, str) else ''
    for keyword in ('image', 'audio', 'video', 'sticker', 'document', 'gif', 'reaction'):
        if keyword in explicit:
            result['message_type'] = keyword
            return result

    result['message_type'] = 'unknown'
    return result


# ======================================================================
# Contexto da conversa: GRUPO vs DIRETA/PRIVADA
# ======================================================================

def strip_jid(value):
    """Remove sufixos de JID (@...) e de dispositivo (:...), preservando o id/numero.

    "5511999999999@s.whatsapp.net" -> "5511999999999"
    "183545595199545@lid"          -> "183545595199545"
    """
    text = _as_text(value)
    return text.split('@', 1)[0].split(':', 1)[0] if text else ''


def normalize_recipient(value):
    """Destinatario para ENVIO pela W-API.

    Mantem o JID de grupo (@g.us) ou o LID (@lid) como estao (a W-API precisa do
    JID completo para responder no lugar certo); para telefone comum, retorna
    apenas os digitos.
    """
    text = _as_text(value)
    low = text.lower()
    if low.endswith('@g.us') or low.endswith('@lid'):
        return text
    return normalize_phone(text)


# Caminhos onde a W-API costuma trazer o ID real da conversa (chat/remoteJid).
# O remoteJid aparece primeiro porque e ele que carrega o sufixo "@g.us" de grupo.
_CHAT_ID_PATHS = (
    ('data', 'key', 'remoteJid'),
    ('message', 'key', 'remoteJid'),
    ('data', 'message', 'key', 'remoteJid'),
    ('key', 'remoteJid'),
    ('data', 'remoteJid'),
    ('remoteJid',),
    ('chatId',),
    ('data', 'chatId'),
    ('chat', 'id'),
    ('data', 'chat', 'id'),
    ('groupId',),
    ('data', 'groupId'),
    ('messages', 0, 'key', 'remoteJid'),
    ('data', 'messages', 0, 'key', 'remoteJid'),
    ('phone',),
    ('data', 'phone'),
    ('sender', 'id'),
    ('data', 'sender', 'id'),
)

# Caminhos do remetente/participante individual (quem escreveu dentro do grupo).
_PARTICIPANT_PATHS = (
    ('data', 'key', 'participant'),
    ('message', 'key', 'participant'),
    ('data', 'message', 'key', 'participant'),
    ('key', 'participant'),
    ('data', 'participant'),
    ('participant',),
    ('author',),
    ('data', 'author'),
    ('sender', 'id'),
    ('data', 'sender', 'id'),
    ('from',),
    ('data', 'from'),
    ('messages', 0, 'key', 'participant'),
    ('data', 'messages', 0, 'key', 'participant'),
)

_FROM_ME_PATHS = (
    ('fromMe',),
    ('from_me',),
    ('key', 'fromMe'),
    ('data', 'fromMe'),
    ('data', 'from_me'),
    ('data', 'key', 'fromMe'),
    ('message', 'fromMe'),
    ('message', 'key', 'fromMe'),
    ('data', 'message', 'key', 'fromMe'),
    ('messages', 0, 'key', 'fromMe'),
    ('data', 'messages', 0, 'key', 'fromMe'),
)

_SENDER_NAME_PATHS = (
    ('pushName',),
    ('senderName',),
    ('contactName',),
    ('participantName',),
    ('notifyName',),
    ('name',),
    ('sender', 'pushName'),
    ('sender', 'name'),
    ('data', 'pushName'),
    ('data', 'senderName'),
    ('data', 'contactName'),
    ('data', 'participantName'),
    ('data', 'notifyName'),
    ('data', 'name'),
    ('contact', 'name'),
    ('contact', 'pushName'),
    ('data', 'contact', 'name'),
    ('data', 'contact', 'pushName'),
    ('messages', 0, 'pushName'),
    ('data', 'messages', 0, 'pushName'),
)

# Nome do grupo, quando a W-API envia (nem sempre vem).
_GROUP_NAME_PATHS = (
    ('groupName',),
    ('chatName',),
    ('subject',),
    ('data', 'groupName'),
    ('data', 'chatName'),
    ('data', 'subject'),
    ('chat', 'name'),
    ('data', 'chat', 'name'),
    ('groupMetadata', 'subject'),
    ('data', 'groupMetadata', 'subject'),
)


def _first_present(payload, paths):
    for path in paths:
        value = _as_text(_safe_get(payload, path))
        if value:
            return value, path
    return '', None


def _path_label(path):
    return '.'.join(str(p) for p in path) if path else ''


def normalize_wapi_message_context(payload):
    """Descobre o contexto real da mensagem recebida: se e de GRUPO ou DIRETA,
    qual e o ID da conversa (chat_id) e quem enviou (participant).

    Regra decisiva: se o ID da conversa termina em "@g.us", a mensagem e de GRUPO
    e o remetente individual (participant) NUNCA vira o chat_id. Caso contrario
    (numero puro, "@s.whatsapp.net" ou "@lid"), a conversa e DIRETA/PRIVADA.

    Retorna: chat_id, chat_type, is_group, sender_id, participant_id, sender_name,
    from_me, display_name e source (campo de onde o chat_id foi extraido, para log).
    """
    if not isinstance(payload, dict):
        payload = {}

    # 1) chat_id real. Um JID coletivo/nao-pessoal (grupo @g.us, canal
    # @newsletter, transmissao @broadcast ou id numerico interno longo) tem
    # prioridade sobre qualquer telefone/remetente, em qualquer posicao.
    chat_id = ''
    source = None
    first_chat_id = ''
    first_source = None
    for path in _CHAT_ID_PATHS:
        value = _as_text(_safe_get(payload, path))
        if not value:
            continue
        if not first_chat_id:
            first_chat_id, first_source = value, path
        if is_group_jid(value):
            chat_id, source = value, path
            break
    if not chat_id:
        chat_id, source = first_chat_id, first_source

    is_group = is_group_jid(chat_id)
    chat_type = 'group' if is_group else 'private'

    # 2) Participante/remetente individual. Em conversa direta o remetente e o
    # proprio chat_id; em grupo e sempre o participant.
    participant_raw, _ = _first_present(payload, _PARTICIPANT_PATHS)
    if not participant_raw and not is_group:
        participant_raw = chat_id
    participant_id = normalize_phone(participant_raw) or _only_digits(strip_jid(participant_raw))

    # 3) from_me (mensagem enviada pela propria conta conectada).
    from_me_value = None
    for path in _FROM_ME_PATHS:
        found = _safe_get(payload, path)
        if found is not None:
            from_me_value = found
            break
    from_me = _as_bool(from_me_value)

    # 4) Nome do remetente e nome do grupo.
    sender_name, _ = _first_present(payload, _SENDER_NAME_PATHS)
    if not _valid_name(sender_name):
        sender_name = ''
    group_name = ''
    if is_group:
        group_name, _ = _first_present(payload, _GROUP_NAME_PATHS)

    return {
        'chat_id': chat_id,
        'chat_type': chat_type,
        'is_group': is_group,
        'sender_id': participant_id,
        'participant_id': participant_id,
        'sender_name': sender_name,
        'from_me': from_me,
        'display_name': group_name if is_group else sender_name,
        'source': _path_label(source),
    }
