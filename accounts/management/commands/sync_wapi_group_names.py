"""Atualiza o nome real dos grupos consultando a W-API.

Uso:
    python manage.py sync_wapi_group_names

Busca a lista de grupos da conta conectada (GET /v1/group/get-all-groups) e
preenche `Conversation.name` das conversas de grupo pelo JID (`external_id`),
para nao ficar exibindo "Grupo <jid>".
"""
from django.core.management.base import BaseCommand

from wapi.services import sync_group_names


class Command(BaseCommand):
    help = 'Sincroniza o nome dos grupos com a W-API.'

    def handle(self, *args, **options):
        result = sync_group_names()
        if not result.get('ok'):
            self.stdout.write(self.style.WARNING(
                'Nao foi possivel buscar os grupos na W-API. Verifique a configuracao/conexao.'
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Sincronizacao concluida: {result['updated']} grupo(s) atualizado(s) "
            f"de {result['total_groups']} encontrado(s) na W-API."
        ))
