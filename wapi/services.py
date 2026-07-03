"""Regras de negocio das Conversas reais alimentadas pela W-API.

Concentra a criacao de contato/conversa/mensagem a partir de mensagens
recebidas pelo webhook e de mensagens enviadas pelo sistema, incluindo midia
(imagem/audio/video/documento/sticker/gif) com download seguro.
"""
import logging
from urllib import error, request

from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.models import Contact, Conversation, Message
from wapi.parser import normalize_phone

media_logger = logging.getLogger('beezap.wapi.media')

MEDIA_TYPES = ('image', 'audio', 'video', 'document', 'sticker', 'gif')

# Rotulo amigavel para a "ultima mensagem" da conversa por tipo.
TYPE_PREVIEW = {
    'image': '\U0001F4F7 Imagem',
    'audio': '\U0001F3A7 Audio',
    'video': '\U0001F3A5 Video',
    'gif': '\U0001F39E️ GIF',
    'sticker': '\U0001F49F Figurinha',
    'reaction': '\U0001F44D Reacao',
    'document': '\U0001F4C4 Documento',
    'location': '\U0001F4CD Localizacao',
    'contact': '\U0001F464 Contato',
    'unknown': 'Mensagem',
}

# Tipo do BEEZAP -> "type" esperado pelo endpoint download-media da W-API.
_DOWNLOAD_TYPE = {
    'image': 'image', 'sticker': 'image', 'gif': 'video',
    'audio': 'audio', 'video': 'video', 'document': 'document',
}

_MIME_EXT = {
    'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
    'audio/mpeg': 'mp3', 'audio/mp3': 'mp3', 'audio/ogg': 'ogg', 'audio/opus': 'ogg',
    'video/mp4': 'mp4', 'application/pdf': 'pdf',
}


def _summary_text(message_type, text):
    if message_type == 'text':
        return text or ''
    label = TYPE_PREVIEW.get(message_type, 'Mensagem')
    if message_type in ('image', 'video', 'document') and (text or '').strip():
        return f'{label} {text.strip()}'
    return label


def get_or_create_contact(phone, name=''):
    phone = normalize_phone(phone)
    if not phone:
        return None
    name = (name or '').strip()
    contact, _created = Contact.objects.get_or_create(phone=phone, defaults={'name': name})
    if name and not contact.name:
        contact.name = name
        contact.save(update_fields=['name', 'updated_at'])
    return contact


def get_or_create_open_conversation(contact):
    conversation = (
        contact.conversations
        .exclude(status='closed')
        .order_by('-last_message_at', '-created_at')
        .first()
    )
    if conversation:
        return conversation
    return Conversation.objects.create(contact=contact)


def update_conversation_summary(conversation, text, direction):
    conversation.last_message_text = text or ''
    conversation.last_message_at = timezone.now()
    update_fields = ['last_message_text', 'last_message_at', 'updated_at']
    if direction == 'in':
        conversation.unread_count = (conversation.unread_count or 0) + 1
        update_fields.append('unread_count')
    conversation.save(update_fields=update_fields)


def _ext_for_mime(mimetype):
    return _MIME_EXT.get((mimetype or '').split(';')[0].strip().lower(), 'bin')


def _download_to_media_file(message, file_link, mimetype):
    """Baixa o arquivo do fileLink e salva localmente em MEDIA (nao expira)."""
    try:
        http_request = request.Request(file_link, headers={'User-Agent': 'BEEZAP'})
        with request.urlopen(http_request, timeout=30) as response:
            data = response.read()
    except (error.URLError, error.HTTPError, ValueError):
        return False
    try:
        message.media_file.save(f'wapi_{message.id}.{_ext_for_mime(mimetype)}', ContentFile(data), save=False)
        return True
    except Exception:
        media_logger.exception('Falha ao salvar arquivo de midia local.')
        return False


def _try_download_media(message, media):
    """Baixa a midia da mensagem recebida usando o endpoint download-media.

    Preferencia por salvar o arquivo localmente (fileLink e temporario). Nunca
    quebra o recebimento: em falha, marca media_status='unavailable'.
    """
    from wapi.client import download_media  # import tardio evita qualquer ciclo

    try:
        result = download_media(
            media.get('media_key'),
            media.get('direct_path'),
            _DOWNLOAD_TYPE.get(message.message_type, 'image'),
            media.get('media_mimetype') or message.media_mimetype,
        )
    except Exception:
        media_logger.exception('Erro ao chamar download-media da W-API.')
        result = None

    if not result:
        message.media_status = 'unavailable'
        message.save(update_fields=['media_status'])
        return

    file_link = result.get('fileLink') or result.get('fileURL') or result.get('url') or ''
    mimetype = result.get('mimetype') or media.get('media_mimetype') or message.media_mimetype
    if mimetype:
        message.media_mimetype = mimetype

    if file_link and _download_to_media_file(message, file_link, mimetype):
        message.media_status = 'ok'
    elif file_link:
        # Nao conseguiu salvar local; guarda o link (pode expirar).
        message.media_url = file_link
        message.media_status = 'ok'
    else:
        message.media_status = 'unavailable'
    message.save(update_fields=['media_file', 'media_url', 'media_mimetype', 'media_status'])


def save_incoming_message(phone, message_type='text', text='', sender_name='',
                          external_message_id='', payload=None, media=None):
    """Cria contato/conversa (se necessario) e registra a mensagem recebida,
    de qualquer tipo. Para midia, tenta baixar o arquivo."""
    contact = get_or_create_contact(phone, sender_name)
    if contact is None:
        return None
    conversation = get_or_create_open_conversation(contact)
    media = media or {}
    is_media = message_type in MEDIA_TYPES

    message = Message.objects.create(
        conversation=conversation,
        direction='in',
        message_type=message_type,
        text=text or '',
        phone=contact.phone,
        sender_name=(sender_name or '').strip(),
        external_message_id=external_message_id or '',
        status='received',
        media_url=(media.get('media_url') or '') if is_media else '',
        media_mimetype=(media.get('media_mimetype') or '') if is_media else '',
        media_status='pending' if is_media else 'none',
        raw_payload=payload if isinstance(payload, dict) else None,
    )
    if is_media:
        _try_download_media(message, media)
    update_conversation_summary(conversation, _summary_text(message_type, text), 'in')
    return message


def save_incoming_text_message(phone, text, sender_name='', external_message_id='', payload=None):
    """Compatibilidade: mensagem de texto recebida."""
    return save_incoming_message(
        phone=phone, message_type='text', text=text, sender_name=sender_name,
        external_message_id=external_message_id, payload=payload,
    )


def save_outgoing_text_message(conversation, text, external_message_id='', status='sent'):
    return save_outgoing_message(conversation, 'text', text=text, external_message_id=external_message_id, status=status)


def save_outgoing_message(conversation, message_type='text', text='', external_message_id='',
                          status='sent', media_url='', media_mimetype=''):
    """Registra a mensagem enviada pelo atendente na conversa."""
    message = Message.objects.create(
        conversation=conversation,
        direction='out',
        message_type=message_type,
        text=text or '',
        phone=conversation.contact.phone,
        external_message_id=external_message_id or '',
        status=status,
        media_url=media_url or '',
        media_mimetype=media_mimetype or '',
        media_status='ok' if message_type in MEDIA_TYPES else 'none',
    )
    update_conversation_summary(conversation, _summary_text(message_type, text), 'out')
    return message
