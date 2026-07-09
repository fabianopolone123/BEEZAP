"""Converte eventos W-API ja recebidos em conversas/mensagens reais.

Uso:
    python manage.py sync_wapi_events_to_conversations

Reprocessa o payload bruto de cada evento pela mesma ingestao do webhook, que
detecta grupo vs direta e resolve a conversa certa. A deduplicacao pelo id
externo (dentro da conversa) evita repetir mensagens ja registradas.
"""
from django.core.management.base import BaseCommand

from accounts.models import WapiWebhookEvent
from wapi.services import ingest_wapi_payload


class Command(BaseCommand):
    help = 'Cria conversas/mensagens reais a partir dos eventos W-API ja recebidos.'

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        events = WapiWebhookEvent.objects.order_by('received_at')

        for event in events:
            payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
            try:
                # trigger_ai=False: reprocessar eventos antigos NAO deve acionar a
                # IA (evita responder mensagens historicas).
                message = ingest_wapi_payload(payload, trigger_ai=False)
            except Exception:
                message = None
            if message:
                created += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Sincronizacao concluida: {created} mensagens criadas, {skipped} ignoradas.'
        ))
