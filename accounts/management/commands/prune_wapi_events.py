"""Expurga eventos ANTIGOS do webhook da W-API.

Por que existe: `WapiWebhookEvent` guarda o payload BRUTO de todo evento recebido, e
nada nunca apagava nada. E a tabela que mais cresce do sistema, e o mesmo JSON ainda
fica duplicado em `Message.raw_payload` (que e o usado de verdade — o retry de midia e
o nome original do documento saem de la).

O evento bruto e util por DIAS, para diagnosticar "por que essa mensagem nao chegou".
Depois disso ele so ocupa espaco: o que a operacao precisa (tipo, telefone, data,
texto curto) ja esta nas colunas proprias da tabela.

Por isso o comando trabalha em dois niveis:

  1. **esvaziar o payload** dos eventos mais velhos que `--dias` (padrao 90),
     mantendo a linha e as colunas ja extraidas — o historico de "chegou mensagem tal
     dia" continua intacto e as Metricas nao mudam;
  2. **apagar a linha** dos mais velhos que `--dias-apagar` (padrao 365), quando nem o
     registro faz mais diferenca.

DRY-RUN POR PADRAO, como os outros `cleanup_*` do projeto: sem `--apply` ele apenas
diz o que faria.

    python manage.py prune_wapi_events
    python manage.py prune_wapi_events --dias 60 --apply
    python manage.py prune_wapi_events --empresa acme --apply
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Company, WapiWebhookEvent


class Command(BaseCommand):
    help = (
        'Esvazia o payload bruto (e opcionalmente apaga) eventos antigos do webhook '
        'da W-API. Dry-run por padrao; use --apply para valer.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=90,
            help='Esvazia o payload de eventos mais velhos que N dias (padrao 90).',
        )
        parser.add_argument(
            '--dias-apagar', type=int, default=365,
            help='Apaga a linha de eventos mais velhos que N dias (padrao 365). '
                 'Use 0 para nao apagar nada, so esvaziar o payload.',
        )
        parser.add_argument(
            '--empresa', default='',
            help='Identificador (slug) de uma empresa. Sem isto, vale para todas.',
        )
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica de verdade. Sem esta opcao, o comando so mostra o que faria.',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        dias_apagar = options['dias_apagar']
        slug = (options['empresa'] or '').strip()
        aplicar = options['apply']

        base = WapiWebhookEvent.objects.all()
        if slug:
            company = Company.objects.filter(slug=slug).first()
            if company is None:
                self.stderr.write(f'Empresa "{slug}" nao encontrada.')
                return
            base = base.filter(company=company)
            self.stdout.write(f'Empresa: {company.display_name}')

        agora = timezone.now()
        total = base.count()
        self.stdout.write(f'Eventos no escopo: {total}')

        # ------------------------------------------------ apagar as linhas velhas
        apagados = 0
        if dias_apagar and dias_apagar > 0:
            limite_apagar = agora - timedelta(days=dias_apagar)
            antigos = base.filter(received_at__lt=limite_apagar)
            apagados = antigos.count()
            if apagados and aplicar:
                # `_raw_delete` nao vale aqui: a linha nao tem dependentes, mas o
                # `delete()` normal ja e barato e mantem sinais/consistencia.
                antigos.delete()
            self.stdout.write(
                f'Linhas com mais de {dias_apagar} dias: {apagados}'
                + ('' if aplicar else ' (seriam apagadas)')
            )

        # ------------------------------------------- esvaziar o payload dos velhos
        limite_payload = agora - timedelta(days=dias)
        com_payload = (
            base.filter(received_at__lt=limite_payload)
            .exclude(raw_payload={})
        )
        esvaziados = com_payload.count()
        if esvaziados and aplicar:
            # `update` direto: nao carrega os payloads na memoria (e o que ocupa
            # espaco justamente por ser grande).
            com_payload.update(raw_payload={})
        self.stdout.write(
            f'Payloads com mais de {dias} dias: {esvaziados}'
            + ('' if aplicar else ' (seriam esvaziados)')
        )

        if aplicar:
            self.stdout.write(self.style.SUCCESS(
                f'Pronto. {apagados} linha(s) apagada(s) e {esvaziados} payload(s) esvaziado(s).'
            ))
            if 'sqlite' in str(WapiWebhookEvent.objects.db):
                self.stdout.write(
                    'Dica: no SQLite o arquivo so encolhe depois de um VACUUM '
                    '(`sqlite3 db.sqlite3 "VACUUM;"`, com o servico parado).'
                )
        else:
            self.stdout.write(
                'DRY-RUN: nada foi alterado. Rode com --apply para valer '
                '(faca backup do banco antes).'
            )
