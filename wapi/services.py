"""Regras de negocio das Conversas reais alimentadas pela W-API.

Concentra a criacao de contato/conversa/mensagem a partir de mensagens
recebidas pelo webhook e de mensagens enviadas pelo sistema, incluindo midia
(imagem/audio/video/documento/sticker/gif) com download seguro.
"""
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from urllib import error, request

from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.models import Contact, Conversation, Message
from wapi.parser import (
    normalize_phone,
    normalize_wapi_message_context,
    parse_wapi_media,
    parse_wapi_webhook_payload,
)

ingest_logger = logging.getLogger('beezap.wapi.webhook')

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


def resolve_conversation_for_context(ctx):
    """Encontra (ou cria) a conversa certa a partir do contexto normalizado.

    - GRUPO: keyed pelo JID do grupo (@g.us); nunca cria conversa privada para o
      participante que escreveu no grupo.
    - DIRETA com telefone: usa o contato (telefone), preservando o comportamento
      antigo de reaproveitar a conversa aberta.
    - DIRETA sem telefone (ex.: @lid): keyed pelo proprio chat_id, sem contato.
    """
    chat_id = (ctx.get('chat_id') or '').strip()
    if not chat_id:
        return None

    if ctx.get('is_group'):
        conversation = (
            Conversation.objects
            .filter(external_id=chat_id, chat_type='group')
            .exclude(status='closed')
            .order_by('-last_message_at', '-created_at')
            .first()
        )
        if conversation:
            if ctx.get('display_name') and not conversation.name:
                conversation.name = ctx['display_name']
                conversation.save(update_fields=['name', 'updated_at'])
            return conversation
        return Conversation.objects.create(
            external_id=chat_id,
            chat_type='group',
            name=ctx.get('display_name') or '',
            contact=None,
        )

    # Conversa direta com telefone real.
    phone = normalize_phone(chat_id)
    if phone:
        contact = get_or_create_contact(phone, ctx.get('sender_name'))
        if contact is None:
            return None
        conversation = (
            contact.conversations
            .exclude(status='closed')
            .order_by('-last_message_at', '-created_at')
            .first()
        )
        if conversation:
            if not conversation.external_id or conversation.chat_type != 'private':
                conversation.external_id = conversation.external_id or phone
                conversation.chat_type = 'private'
                conversation.save(update_fields=['external_id', 'chat_type', 'updated_at'])
            return conversation
        return Conversation.objects.create(contact=contact, external_id=phone, chat_type='private')

    # Conversa direta sem telefone (ex.: identificador interno @lid).
    conversation = (
        Conversation.objects
        .filter(external_id=chat_id, chat_type='private')
        .exclude(status='closed')
        .order_by('-last_message_at', '-created_at')
        .first()
    )
    if conversation:
        return conversation
    return Conversation.objects.create(
        external_id=chat_id,
        chat_type='private',
        name=ctx.get('sender_name') or '',
        contact=None,
    )


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


def save_incoming_message(conversation, ctx, message_type='text', text='',
                          external_message_id='', payload=None, media=None):
    """Registra a mensagem na conversa ja resolvida, respeitando grupo/direta.

    Mensagens `from_me` (enviadas pela conta conectada, inclusive pelo celular)
    entram como enviadas (`out`). Deduplica pelo id externo para nao repetir a
    mesma mensagem quando o webhook reenvia ou quando o proprio sistema ja salvou
    o envio. Para midia, tenta baixar o arquivo. Nunca cria contato privado para
    o participante de um grupo."""
    from_me = bool(ctx.get('from_me'))
    external_message_id = (external_message_id or '').strip()
    if external_message_id and Message.objects.filter(
        conversation=conversation, external_message_id=external_message_id
    ).exists():
        return None

    is_media = message_type in MEDIA_TYPES
    media = media or {}
    is_group = bool(ctx.get('is_group'))
    direction = 'out' if from_me else 'in'
    status = 'sent' if from_me else 'received'

    if is_group:
        msg_phone = ctx.get('sender_id') or ''
    else:
        msg_phone = normalize_phone(ctx.get('chat_id') or '') or (
            conversation.contact.phone if conversation.contact_id else ''
        )

    message = Message.objects.create(
        conversation=conversation,
        direction=direction,
        message_type=message_type,
        text=text or '',
        phone=msg_phone,
        sender_name=(ctx.get('sender_name') or '').strip(),
        sender_id=ctx.get('sender_id') or '',
        participant_id=ctx.get('participant_id') or '',
        is_group=is_group,
        from_me=from_me,
        external_message_id=external_message_id,
        status=status,
        media_url=(media.get('media_url') or '') if is_media else '',
        media_mimetype=(media.get('media_mimetype') or '') if is_media else '',
        media_status='pending' if is_media else 'none',
        raw_payload=payload if isinstance(payload, dict) else None,
    )
    if is_media:
        _try_download_media(message, media)
    update_conversation_summary(conversation, _summary_text(message_type, text), direction)
    return message


def ingest_wapi_payload(payload):
    """Ponto unico de entrada de uma mensagem recebida da W-API.

    Detecta grupo vs direta, resolve a conversa certa e cria a mensagem (texto,
    reacao ou midia). Retorna a Message criada, ou None quando nao ha o que salvar
    (payload sem chat_id, sem conteudo, ou duplicada)."""
    parsed = parse_wapi_webhook_payload(payload)
    ctx = normalize_wapi_message_context(payload)

    ingest_logger.info(
        '[WAPI WEBHOOK] chat_id=%s chat_type=%s is_group=%s sender_id=%s '
        'participant_id=%s from_me=%s source=%s',
        ctx.get('chat_id') or '-',
        ctx.get('chat_type'),
        ctx.get('is_group'),
        ctx.get('sender_id') or '-',
        ctx.get('participant_id') or '-',
        ctx.get('from_me'),
        ctx.get('source') or '-',
    )

    if not ctx.get('chat_id'):
        return None

    conversation = resolve_conversation_for_context(ctx)
    if conversation is None:
        return None

    media_info = parse_wapi_media(payload)
    message_type = media_info['message_type']
    external_message_id = parsed.get('message_id', '')

    if message_type == 'text':
        text = parsed.get('message_text', '')
        if not text:
            return None
        return save_incoming_message(
            conversation, ctx, message_type='text', text=text,
            external_message_id=external_message_id, payload=payload,
        )

    if message_type == 'reaction':
        return save_incoming_message(
            conversation, ctx, message_type='reaction',
            text=media_info.get('reaction', ''),
            external_message_id=external_message_id, payload=payload,
        )

    # imagem/audio/video/documento/sticker/gif/location/contact/unknown
    return save_incoming_message(
        conversation, ctx, message_type=message_type,
        text=media_info.get('caption') or parsed.get('message_text', ''),
        external_message_id=external_message_id, payload=payload,
        media={
            'media_url': media_info.get('media_url'),
            'media_mimetype': media_info.get('media_mimetype'),
            'media_key': media_info.get('media_key'),
            'direct_path': media_info.get('direct_path'),
        },
    )


def save_outgoing_text_message(conversation, text, external_message_id='', status='sent'):
    return save_outgoing_message(conversation, 'text', text=text, external_message_id=external_message_id, status=status)


def convert_audio_to_ogg(uploaded_file):
    """Converte o audio enviado (ex.: webm/opus do Chrome) para ogg/opus, formato
    aceito pela W-API. Requer ffmpeg instalado. Retorna um ContentFile chamado
    'audio.ogg' ou None se nao for possivel converter."""
    if not shutil.which('ffmpeg'):
        media_logger.warning('ffmpeg nao encontrado; nao foi possivel converter o audio para ogg.')
        return None
    tmpdir = tempfile.mkdtemp(prefix='beezap_audio_')
    try:
        in_path = os.path.join(tmpdir, 'in')
        out_path = os.path.join(tmpdir, 'out.ogg')
        with open(in_path, 'wb') as dst:
            for chunk in uploaded_file.chunks():
                dst.write(chunk)
        proc = subprocess.run(
            ['ffmpeg', '-y', '-i', in_path, '-vn', '-c:a', 'libopus', '-b:a', '32000', out_path],
            capture_output=True, timeout=90,
        )
        if proc.returncode != 0 or not os.path.exists(out_path):
            media_logger.warning('ffmpeg falhou ao converter audio (rc=%s).', proc.returncode)
            return None
        with open(out_path, 'rb') as src:
            data = src.read()
        return ContentFile(data, name='audio.ogg')
    except Exception:
        media_logger.exception('Erro convertendo audio para ogg.')
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def save_outgoing_media_message(conversation, message_type, uploaded_file, caption='', mimetype=''):
    """Cria a mensagem enviada de midia e salva o arquivo em MEDIA/whatsapp/outgoing/
    com nome unico. Status/media_status ficam 'pending' ate a W-API confirmar."""
    ext = os.path.splitext(getattr(uploaded_file, 'name', '') or '')[1].lower()[:10]
    message = Message(
        conversation=conversation,
        direction='out',
        message_type=message_type,
        text=caption or '',
        phone=conversation.recipient,
        is_group=conversation.is_group,
        status='sent',
        media_mimetype=mimetype or '',
        media_status='pending',
    )
    # Nome unico (nunca reaproveita o nome do usuario -> evita traversal/sobrescrita).
    message.media_file.save(f'outgoing/{uuid.uuid4().hex}{ext}', uploaded_file, save=True)
    update_conversation_summary(conversation, _summary_text(message_type, caption), 'out')
    return message


def save_outgoing_message(conversation, message_type='text', text='', external_message_id='',
                          status='sent', media_url='', media_mimetype=''):
    """Registra a mensagem enviada pelo atendente na conversa."""
    message = Message.objects.create(
        conversation=conversation,
        direction='out',
        message_type=message_type,
        text=text or '',
        phone=conversation.recipient,
        is_group=conversation.is_group,
        external_message_id=external_message_id or '',
        status=status,
        media_url=media_url or '',
        media_mimetype=media_mimetype or '',
        media_status='ok' if message_type in MEDIA_TYPES else 'none',
    )
    update_conversation_summary(conversation, _summary_text(message_type, text), 'out')
    return message
