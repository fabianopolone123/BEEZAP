"""Regras de negocio das Conversas reais alimentadas pela W-API.

Concentra a criacao de contato/conversa/mensagem a partir de mensagens
recebidas pelo webhook e de mensagens enviadas pelo sistema, incluindo midia
(imagem/audio/video/documento/sticker/gif) com download seguro.
"""
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from urllib import request

from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.models import Contact, Conversation, Message
from wapi.parser import (
    is_ignorable_jid,
    is_status_or_broadcast,
    normalize_phone,
    normalize_wapi_message_context,
    parse_wapi_media,
    parse_wapi_webhook_payload,
    strip_jid,
    wapi_content_keys,
)

ingest_logger = logging.getLogger('beezap.wapi.webhook')


def _group_key(group_id):
    """Chave de comparacao de grupo por digitos (ignora sufixo @g.us e formato)."""
    return ''.join(ch for ch in strip_jid(group_id) if ch.isdigit())


def _iter_group_items(groups_response):
    """Extrai a lista de grupos de formatos possiveis da W-API (lista ou dict)."""
    if isinstance(groups_response, list):
        return groups_response
    if isinstance(groups_response, dict):
        for key in ('groups', 'data', 'result', 'items', 'chats'):
            value = groups_response.get(key)
            if isinstance(value, list):
                return value
        data = groups_response.get('data')
        if isinstance(data, dict):
            for key in ('groups', 'items', 'result'):
                value = data.get(key)
                if isinstance(value, list):
                    return value
    return []


def _group_item_id(item):
    for key in ('id', 'groupId', 'remoteJid', 'jid', 'wid', 'chatId'):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for inner in ('_serialized', 'id', 'user'):
                inner_value = value.get(inner)
                if isinstance(inner_value, str) and inner_value:
                    return inner_value
    return ''


def _group_item_name(item):
    for key in ('name', 'subject', 'title', 'groupName', 'pushName'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def build_group_name_map(groups_response):
    """Monta {chave_digitos: nome} a partir da resposta de get-all-groups."""
    mapping = {}
    for item in _iter_group_items(groups_response):
        if not isinstance(item, dict):
            continue
        key = _group_key(_group_item_id(item))
        name = _group_item_name(item)
        if key and name:
            mapping[key] = name
    return mapping


def get_all_groups_safe():
    """Busca os grupos na W-API sem nunca derrubar o fluxo (retorna None em falha)."""
    from wapi.client import get_all_groups
    try:
        return get_all_groups()
    except Exception:
        ingest_logger.exception('Falha ao buscar grupos na W-API.')
        return None


def resolve_group_name(group_id, groups_response=None):
    """Descobre o nome real de um grupo pelo JID, consultando a W-API se preciso."""
    if not group_id:
        return ''
    if groups_response is None:
        groups_response = get_all_groups_safe()
    if not groups_response:
        return ''
    return build_group_name_map(groups_response).get(_group_key(group_id), '')


def sync_group_names():
    """Atualiza Conversation.name de todas as conversas de grupo a partir da W-API.

    Retorna um resumo {ok, updated, total_groups} para comando/endpoint."""
    groups_response = get_all_groups_safe()
    if not groups_response:
        return {'ok': False, 'updated': 0, 'total_groups': 0}
    mapping = build_group_name_map(groups_response)
    updated = 0
    for conversation in Conversation.objects.filter(chat_type='group'):
        name = mapping.get(_group_key(conversation.external_id))
        if name and name != conversation.name:
            conversation.name = name
            conversation.save(update_fields=['name', 'updated_at'])
            updated += 1
    return {'ok': True, 'updated': updated, 'total_groups': len(mapping)}

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
    'audio/mp4': 'm4a', 'audio/aac': 'aac', 'audio/amr': 'amr',
    'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/webm': 'webm',
    'video/mp4': 'mp4', 'video/3gpp': '3gp', 'video/webm': 'webm', 'video/quicktime': 'mov',
    # Documentos (Office, PDF, texto e compactados). Sem estes, docx/xlsx/etc.
    # caiam para ".bin" ao serem salvos/baixados.
    'application/pdf': 'pdf',
    'application/msword': 'doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.oasis.opendocument.text': 'odt',
    'application/vnd.oasis.opendocument.spreadsheet': 'ods',
    'application/vnd.oasis.opendocument.presentation': 'odp',
    'application/rtf': 'rtf', 'text/rtf': 'rtf',
    'text/plain': 'txt', 'text/csv': 'csv',
    'application/zip': 'zip', 'application/x-7z-compressed': '7z',
    'application/vnd.rar': 'rar', 'application/x-rar-compressed': 'rar',
}

# Extensoes seguras para derivar do nome original do arquivo (documento).
_FILENAME_EXT_MAX = 8


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
        # Conversa de grupo nova: o webhook LITE quase nunca traz o nome, so o JID.
        # Buscamos o nome real na W-API uma unica vez (na criacao) para nao ficar
        # mostrando "Grupo <jid>". Se falhar, o fallback cuida da exibicao.
        name = ctx.get('display_name') or resolve_group_name(chat_id)
        return Conversation.objects.create(
            external_id=chat_id,
            chat_type='group',
            name=name or '',
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


def _ext_from_filename(filename):
    """Extensao a partir do nome original (ex.: 'contrato.docx' -> 'docx')."""
    name = (filename or '').strip()
    _, dot, ext = name.rpartition('.')
    if dot and 1 <= len(ext) <= _FILENAME_EXT_MAX and ext.isalnum():
        return ext.lower()
    return ''


def document_filename(message):
    """Nome ORIGINAL do documento (fileName do WhatsApp), lido do payload salvo.

    Fica separado da legenda: quando o documento vem com caption, `message.text`
    guarda a legenda, mas o download precisa do nome/extensao reais do arquivo.
    Cai para `message.text` quando o payload nao traz fileName (compatibilidade).
    """
    if message is None or message.message_type != 'document':
        return ''
    if isinstance(message.raw_payload, dict):
        # Enviado pelo BEEZAP: nome original guardado direto no envio.
        name = (message.raw_payload.get('beezap_filename') or '').strip()
        if name:
            return name
        # Recebido: o fileName vem dentro da estrutura do webhook da W-API.
        name = (parse_wapi_media(message.raw_payload).get('filename') or '').strip()
        if name:
            return name
    return (message.text or '').strip()


def _ext_for_media(message, mimetype):
    """Melhor extensao para salvar a midia: nome original do documento (se houver)
    -> mapa explicito por mimetype -> base de mimetypes do sistema -> 'bin'.

    Salvar com a extensao certa faz o arquivo baixar como .docx/.pdf/.xlsx (nao
    mais .bin) e servir com o Content-Type correto (imagem/audio/video tocam).
    Qualquer extensao vinda do nome original e aceita (nao depende do mapa)."""
    # 1) Documento: usa a extensao do nome ORIGINAL do arquivo (qualquer tipo).
    if message is not None and message.message_type == 'document':
        ext = _ext_from_filename(document_filename(message))
        if ext:
            return ext
    # 2) Mapa explicito por mimetype.
    key = (mimetype or '').split(';')[0].strip().lower()
    if key in _MIME_EXT:
        return _MIME_EXT[key]
    # 3) Base de mimetypes do Python (cobre tipos menos comuns).
    guessed = mimetypes.guess_extension(key) if key else None
    if guessed:
        return guessed.lstrip('.').lower()
    return 'bin'


def _download_to_media_file(message, file_link, mimetype):
    """Baixa o arquivo do fileLink e salva localmente em MEDIA (nao expira).

    Tenta 2x (o fileLink e temporario e a rede pode oscilar), recusa respostas que
    claramente nao sao midia (HTML/JSON de erro salvo como audio) e loga o motivo
    real da falha — sem expor o link/token — para diagnostico no journal."""
    for attempt in (1, 2):
        try:
            http_request = request.Request(
                file_link, headers={'User-Agent': 'Mozilla/5.0 (compatible; BEEZAP)'}
            )
            with request.urlopen(http_request, timeout=60) as response:
                data = response.read()
                content_type = (response.headers.get('Content-Type') or '').lower()
        except Exception as exc:  # URLError/HTTPError/SSL/ValueError/etc.
            media_logger.warning(
                'download-media: falha ao baixar fileLink (msg=%s tentativa=%s): %r',
                message.id, attempt, exc,
            )
            continue

        if not data:
            media_logger.warning('download-media: fileLink vazio (msg=%s tentativa=%s).', message.id, attempt)
            continue
        # Corpo de erro (HTML/JSON) nao e midia — nao salvar como audio/imagem.
        if 'text/html' in content_type or 'application/json' in content_type:
            media_logger.warning(
                'download-media: fileLink retornou %s (nao e midia; msg=%s bytes=%s).',
                content_type or '-', message.id, len(data),
            )
            continue
        try:
            message.media_file.save(
                f'wapi_{message.id}.{_ext_for_media(message, mimetype)}', ContentFile(data), save=False
            )
            media_logger.info(
                'download-media: salvo local (msg=%s bytes=%s ctype=%s).',
                message.id, len(data), content_type or '-',
            )
            return True
        except Exception:
            media_logger.exception('Falha ao salvar arquivo de midia local (msg=%s).', message.id)
            return False
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
        outcome = 'local'
    elif file_link:
        # Nao conseguiu salvar local; guarda o link (pode expirar -> play falha).
        message.media_url = file_link
        message.media_status = 'ok'
        outcome = 'remoto(link pode expirar)'
    else:
        message.media_status = 'unavailable'
        outcome = 'indisponivel(sem fileLink)'
    message.save(update_fields=['media_file', 'media_url', 'media_mimetype', 'media_status'])
    # Diagnostico seguro (sem link/token): ajuda a entender falhas de play de midia.
    media_logger.info(
        'download-media: tipo=%s mimetype=%s ext=%s resultado=%s',
        message.message_type, mimetype or '-', _ext_for_media(message, mimetype), outcome,
    )


def retry_media_download(message):
    """Re-baixa a midia de uma mensagem recebida usando o payload salvo.

    O endpoint download-media gera um fileLink NOVO a cada chamada, entao isso
    recupera midia cujo link remoto ja expirou (arquivo local ausente). Retorna
    True se a mensagem passou a ter arquivo salvo localmente."""
    if message.message_type not in MEDIA_TYPES or not isinstance(message.raw_payload, dict):
        return False
    media = parse_wapi_media(message.raw_payload)
    if not media.get('media_key') and not media.get('direct_path'):
        return False
    message.media_status = 'pending'
    _try_download_media(message, {
        'media_key': media.get('media_key'),
        'direct_path': media.get('direct_path'),
        'media_mimetype': media.get('media_mimetype') or message.media_mimetype,
        'media_url': media.get('media_url'),
    })
    return bool(message.media_file)


def retry_incoming_media_downloads():
    """Tenta recuperar todas as midias recebidas que ficaram sem arquivo local
    (ex.: audios que so tinham link remoto expirado). Retorna um resumo."""
    from django.db.models import Q
    pending = (
        Message.objects
        .filter(direction='in', message_type__in=MEDIA_TYPES)
        .filter(Q(media_file='') | Q(media_file__isnull=True))
        .exclude(raw_payload__isnull=True)
        .order_by('-created_at')
    )
    recovered = 0
    total = 0
    for message in pending:
        total += 1
        try:
            if retry_media_download(message):
                recovered += 1
        except Exception:
            media_logger.exception('Falha ao re-baixar midia (msg=%s).', message.id)
    return {'recovered': recovered, 'total': total}


# Evita disparar dois retries em paralelo para a mesma conversa (ex.: abrir,
# fechar e abrir de novo rapidamente).
_media_retry_lock = threading.Lock()
_media_retry_active = set()


def _conversation_pending_media(conversation_id, limit=None):
    """Midias recebidas desta conversa que ficaram SEM arquivo local (falharam
    no download da chegada) e ainda tem payload para tentar de novo."""
    from django.db.models import Q
    qs = (
        Message.objects
        .filter(conversation_id=conversation_id, direction='in',
                message_type__in=MEDIA_TYPES)
        .filter(Q(media_file='') | Q(media_file__isnull=True))
        .exclude(raw_payload__isnull=True)
        .order_by('-created_at')
    )
    return qs[:limit] if limit else qs


def retry_conversation_media_downloads(conversation_id, limit=8):
    """Tenta rebaixar ate `limit` midias 'unavailable' (sem arquivo local) de uma
    conversa. Sincrono — use retry_conversation_media_async para nao bloquear."""
    recovered = 0
    for message in _conversation_pending_media(conversation_id, limit=limit):
        try:
            if retry_media_download(message):
                recovered += 1
        except Exception:
            media_logger.exception('Falha ao re-baixar midia (msg=%s).', message.id)
    return recovered


def retry_conversation_media_async(conversation_id, limit=8):
    """Dispara, em background, o retry das midias que falharam nesta conversa.

    Chamado ao ABRIR a conversa (nao no poll), para nao virar loop automatico.
    Roda em thread para nao travar a abertura quando um link estiver morto (o
    download tem timeout longo); a midia recuperada aparece sozinha no proximo
    ciclo do poll. Evita rodar em paralelo para a mesma conversa e so gasta uma
    thread quando ha algo pendente."""
    if not _conversation_pending_media(conversation_id).exists():
        return False

    with _media_retry_lock:
        if conversation_id in _media_retry_active:
            return False
        _media_retry_active.add(conversation_id)

    def _worker():
        from django.db import connection
        try:
            retry_conversation_media_downloads(conversation_id, limit=limit)
        except Exception:
            media_logger.exception('Falha no retry de midia (conv=%s).', conversation_id)
        finally:
            connection.close()  # nao deixar conexao de banco pendurada na thread
            with _media_retry_lock:
                _media_retry_active.discard(conversation_id)

    threading.Thread(
        target=_worker, name='media-retry-%s' % conversation_id, daemon=True
    ).start()
    return True


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

    # Status do WhatsApp ('stories', JID status@broadcast) nao sao conversa: o
    # W-API pode mandar o autor como remetente e o status@broadcast em outro
    # campo, entao checamos o payload inteiro, alem do proprio chat_id.
    if is_ignorable_jid(ctx['chat_id']) or is_status_or_broadcast(payload):
        ingest_logger.info('[WAPI WEBHOOK] ignorado (status/canal/transmissao): %s', ctx['chat_id'])
        return None

    conversation = resolve_conversation_for_context(ctx)
    if conversation is None:
        return None

    media_info = parse_wapi_media(payload)
    message_type = media_info['message_type']
    external_message_id = parsed.get('message_id', '')

    # Tipos nao reconhecidos sao, em quase todos os casos, mensagens de SISTEMA do
    # WhatsApp (ex.: senderKeyDistributionMessage/protocolMessage em grupos) sem
    # conteudo para o usuario. Ignoramos em vez de poluir o chat com "Tipo de
    # mensagem nao suportado". O log guarda as chaves para diagnostico.
    if message_type == 'unknown':
        ingest_logger.info(
            '[WAPI WEBHOOK] ignorado (tipo nao suportado): chat=%s content=%s',
            ctx.get('chat_id') or '-', wapi_content_keys(payload),
        )
        return None

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


def _convert_image_to_jpeg(uploaded_file):
    """Converte a imagem enviada (webp/gif/bmp/heic/...) para JPEG via ffmpeg.
    Retorna um ContentFile chamado 'image.jpg' ou None se nao der para converter."""
    if not shutil.which('ffmpeg'):
        media_logger.warning('ffmpeg nao encontrado; nao foi possivel converter a imagem para JPEG.')
        return None
    tmpdir = tempfile.mkdtemp(prefix='beezap_img_')
    try:
        in_path = os.path.join(tmpdir, 'in')
        out_path = os.path.join(tmpdir, 'out.jpg')
        with open(in_path, 'wb') as dst:
            for chunk in uploaded_file.chunks():
                dst.write(chunk)
        # -frames:v 1 garante 1 quadro (ex.: webp/gif animado vira a 1a imagem).
        proc = subprocess.run(
            ['ffmpeg', '-y', '-i', in_path, '-frames:v', '1', out_path],
            capture_output=True, timeout=90,
        )
        if proc.returncode != 0 or not os.path.exists(out_path):
            media_logger.warning('ffmpeg falhou ao converter imagem (rc=%s).', proc.returncode)
            return None
        with open(out_path, 'rb') as src:
            data = src.read()
        return ContentFile(data, name='image.jpg')
    except Exception:
        media_logger.exception('Erro convertendo imagem para JPEG.')
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def ensure_wapi_image(uploaded_file, mimetype):
    """A W-API exige que a URL da imagem termine em .png/.jpeg/.jpg (senao HTTP 500
    'A URL da imagem deve ser nos formatos ...'). Garante isso:

    - PNG e JPEG passam direto, so forcando a extensao correta no nome do arquivo
      (cobre .jfif, print colado sem extensao, extensao errada, etc.);
    - outros formatos (webp/gif/bmp/heic/tiff/...) sao convertidos para JPEG.

    Retorna (arquivo, mimetype) pronto para salvar, ou (None, mimetype) se falhar."""
    mimetype = (mimetype or '').lower()
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    if mimetype == 'image/png' or (not mimetype and name.endswith('.png')):
        uploaded_file.name = 'image.png'
        return uploaded_file, 'image/png'
    if mimetype in ('image/jpeg', 'image/jpg') or (not mimetype and name.endswith(('.jpg', '.jpeg'))):
        uploaded_file.name = 'image.jpg'
        return uploaded_file, 'image/jpeg'
    converted = _convert_image_to_jpeg(uploaded_file)
    if converted is None:
        return None, mimetype
    return converted, 'image/jpeg'


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
    original_name = (getattr(uploaded_file, 'name', '') or '').strip()
    ext = os.path.splitext(original_name)[1].lower()[:10]
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
        # Documento: guarda o nome ORIGINAL (o arquivo em disco usa uuid), para o chat
        # exibir/baixar com o nome certo em vez do generico "Baixar documento".
        raw_payload=({'beezap_filename': original_name}
                     if message_type == 'document' and original_name else None),
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
