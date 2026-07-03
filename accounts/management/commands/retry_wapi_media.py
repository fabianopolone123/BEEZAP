"""Recupera midias recebidas que ficaram sem arquivo local.

Uso:
    python manage.py retry_wapi_media

Re-chama o download-media da W-API (que gera um fileLink novo) para as mensagens
recebidas de midia que ficaram so com link remoto expirado — tipico dos audios
que nao davam play. Baixa e salva o arquivo localmente.
"""
from django.core.management.base import BaseCommand

from wapi.services import retry_incoming_media_downloads


class Command(BaseCommand):
    help = 'Re-baixa midias recebidas que ficaram sem arquivo local (ex.: audios sem play).'

    def handle(self, *args, **options):
        result = retry_incoming_media_downloads()
        self.stdout.write(self.style.SUCCESS(
            f"Recuperacao concluida: {result['recovered']} de {result['total']} "
            f"midia(s) baixada(s) localmente."
        ))
