"""Atualiza o nome real dos grupos consultando a W-API.

Uso:
    python manage.py sync_wapi_group_names                  # todas as empresas ativas
    python manage.py sync_wapi_group_names --empresa acme   # apenas uma empresa

Busca a lista de grupos da conta conectada (GET /v1/group/get-all-groups) e
preenche `Conversation.name` das conversas de grupo pelo JID (`external_id`),
para nao ficar exibindo "Grupo <jid>".

MULTIEMPRESA: cada empresa cliente tem a SUA instancia da W-API, entao a busca e
feita uma vez por empresa e cada nome so atualiza as conversas dela.
"""
from django.core.management.base import BaseCommand

from accounts.models import Company
from wapi.services import sync_group_names


class Command(BaseCommand):
    help = 'Sincroniza o nome dos grupos com a W-API (por empresa cliente).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--empresa', default='',
            help='Identificador (slug) da empresa. Sem isto, roda para todas as ativas.',
        )

    def handle(self, *args, **options):
        slug = (options.get('empresa') or '').strip()
        if slug:
            companies = list(Company.objects.filter(slug=slug))
            if not companies:
                self.stdout.write(self.style.ERROR(f'Empresa "{slug}" nao encontrada.'))
                return
        else:
            companies = list(Company.objects.filter(is_active=True).order_by('name'))
            if not companies:
                self.stdout.write(self.style.WARNING('Nenhuma empresa ativa cadastrada.'))
                return

        for company in companies:
            result = sync_group_names(company)
            if not result.get('ok'):
                self.stdout.write(self.style.WARNING(
                    f'[{company.name}] Nao foi possivel buscar os grupos na W-API. '
                    'Verifique a configuracao/conexao dessa empresa.'
                ))
                continue
            self.stdout.write(self.style.SUCCESS(
                f"[{company.name}] {result['updated']} grupo(s) atualizado(s) "
                f"de {result['total_groups']} encontrado(s) na W-API."
            ))
