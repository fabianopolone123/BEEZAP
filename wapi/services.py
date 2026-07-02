"""Regras de negocio das Conversas reais alimentadas pela W-API.

Concentra a criacao de contato/conversa/mensagem a partir de mensagens
recebidas pelo webhook e de mensagens enviadas pelo sistema.
"""
from django.utils import timezone

from accounts.models import Contact, Conversation, Message
from wapi.parser import normalize_phone


def get_or_create_contact(phone, name=''):
    phone = normalize_phone(phone)
    if not phone:
        return None
    name = (name or '').strip()
    contact, _created = Contact.objects.get_or_create(phone=phone, defaults={'name': name})
    # Atualiza o nome apenas se veio um nome novo e o contato ainda nao tem nome.
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


def save_incoming_text_message(phone, text, sender_name='', external_message_id='', payload=None):
    """Cria contato/conversa (se necessario) e registra a mensagem recebida."""
    contact = get_or_create_contact(phone, sender_name)
    if contact is None:
        return None
    conversation = get_or_create_open_conversation(contact)
    message = Message.objects.create(
        conversation=conversation,
        direction='in',
        text=text or '',
        phone=contact.phone,
        sender_name=(sender_name or '').strip(),
        external_message_id=external_message_id or '',
        status='received',
        raw_payload=payload if isinstance(payload, dict) else None,
    )
    update_conversation_summary(conversation, text, 'in')
    return message


def save_outgoing_text_message(conversation, text, external_message_id='', status='sent'):
    """Registra a mensagem enviada pelo atendente na conversa."""
    message = Message.objects.create(
        conversation=conversation,
        direction='out',
        text=text or '',
        phone=conversation.contact.phone,
        external_message_id=external_message_id or '',
        status=status,
    )
    update_conversation_summary(conversation, text, 'out')
    return message
