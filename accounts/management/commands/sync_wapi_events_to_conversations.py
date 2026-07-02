"""Converte eventos W-API ja recebidos em conversas/mensagens reais.

Uso:
    python manage.py sync_wapi_events_to_conversations

So processa eventos de texto recebido (com telefone e mensagem, nao enviados
pelo proprio numero). Evita duplicar mensagens que ja tenham o mesmo id externo.
"""
from django.core.management.base import BaseCommand

from accounts.models import Message, WapiWebhookEvent
from wapi.services import save_incoming_text_message


class Command(BaseCommand):
    help = 'Cria conversas/mensagens reais a partir dos eventos W-API ja recebidos.'

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        events = WapiWebhookEvent.objects.filter(from_me=False).order_by('received_at')

        for event in events:
            if not event.phone or not event.message_text:
                skipped += 1
                continue
            # Evita duplicar quando o evento tem id externo ja registrado.
            if event.message_id and Message.objects.filter(external_message_id=event.message_id).exists():
                skipped += 1
                continue

            message = save_incoming_text_message(
                phone=event.phone,
                text=event.message_text,
                sender_name=event.contact_name,
                external_message_id=event.message_id,
                payload=event.raw_payload,
            )
            if message:
                created += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Sincronizacao concluida: {created} mensagens criadas, {skipped} ignoradas.'
        ))
