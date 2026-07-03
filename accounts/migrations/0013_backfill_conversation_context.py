"""Backfill seguro do contexto de grupo/direta nas conversas e mensagens antigas.

- Conversas antigas ficam como `private` (default) e recebem `external_id` a partir
  do telefone do contato vinculado. Se alguma tiver telefone terminando em "@g.us"
  (nao esperado no historico), e marcada como grupo.
- Mensagens antigas de saida (`direction='out'`) recebem `from_me=True`; recebidas
  ficam `from_me=False` (default). Nenhum registro e apagado.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Conversation = apps.get_model('accounts', 'Conversation')
    Message = apps.get_model('accounts', 'Message')

    for conversation in Conversation.objects.select_related('contact').all():
        phone = (conversation.contact.phone if conversation.contact_id else '') or ''
        chat_type = 'group' if phone.lower().endswith('@g.us') else 'private'
        updates = {}
        if not conversation.external_id and phone:
            updates['external_id'] = phone
        if conversation.chat_type != chat_type:
            updates['chat_type'] = chat_type
        if updates:
            for field, value in updates.items():
                setattr(conversation, field, value)
            conversation.save(update_fields=list(updates.keys()) + ['updated_at'])

    # Mensagens de saida antigas foram enviadas pela conta conectada.
    Message.objects.filter(direction='out').update(from_me=True)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_conversation_chat_type_conversation_external_id_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
