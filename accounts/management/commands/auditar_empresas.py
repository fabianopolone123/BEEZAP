"""Confere se o vinculo de EMPRESA esta coerente em todo o banco (multiempresa).

O isolamento entre clientes e por VINCULO: todo registro operacional aponta para a
`Company` dona, e as consultas filtram por ela. As views ja filtram por empresa em cada
ponto, entao na pratica nao aparece registro cruzado — mas nao existia nada que
PROVASSE isso. Este comando prova.

O que ele procura sao referencias que atravessam a fronteira de empresa:

- `Attendant` cuja empresa difere da empresa do `User` (o sinal que provisiona o
  atendente usa `get_or_create(user=...)`, entao se alguem mudar a empresa do usuario o
  atendente antigo continua com a empresa antiga);
- `Conversation` cujo `contact`, `sector` ou `assigned_attendant` e de outra empresa;
- `Message` cujo `sector` e de outra empresa que a da conversa;
- `MenuOption` / `MenuBotConfiguration.fallback_sector` apontando para setor de outra
  empresa;
- `GroupAccess` liberando um grupo para setor/usuario de outra empresa;
- `UserMenuPermission` / `UserConversationView` de usuario sem empresa;
- registro operacional com empresa INATIVA (nao e erro, mas vale ver).

Somente LEITURA: o comando nunca corrige nada. Ele diz o que esta torto e cabe a uma
decisao consciente arrumar — corrigir automaticamente vinculo de empresa e o tipo de
coisa que pode mover dado de cliente para o lugar errado.

    python manage.py auditar_empresas
    python manage.py auditar_empresas --detalhe
"""

from django.core.management.base import BaseCommand
from django.db.models import F, Q


class Command(BaseCommand):
    help = 'Audita a coerencia do vinculo de empresa em todo o banco (somente leitura).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--detalhe', action='store_true',
            help='Lista os ids de cada problema encontrado (ate 20 por item).',
        )

    def handle(self, *args, **options):
        from accounts.models import (
            Attendant, Company, Conversation, GroupAccess, MenuBotConfiguration,
            MenuOption, Message, User, UserConversationView, UserMenuPermission,
        )

        detalhe = options['detalhe']
        problemas = []

        def conferir(rotulo, queryset, dica=''):
            total = queryset.count()
            problemas.append((rotulo, total, queryset, dica))

        # --- atendente com empresa diferente da do usuario ---
        conferir(
            'Attendant com empresa diferente da do User',
            Attendant.objects.exclude(company_id=F('user__company_id')),
            'o sinal usa get_or_create(user=...), entao mudar a empresa do usuario '
            'nao move o atendente. Corrija o Attendant.company a mao.',
        )

        # --- conversa apontando para fora da empresa ---
        conferir(
            'Conversation com contato de outra empresa',
            Conversation.objects.filter(contact__isnull=False)
            .exclude(contact__company_id=F('company_id')),
        )
        conferir(
            'Conversation com setor de outra empresa',
            Conversation.objects.filter(sector__isnull=False)
            .exclude(sector__company_id=F('company_id')),
        )
        conferir(
            'Conversation com atendente de outra empresa',
            Conversation.objects.filter(assigned_attendant__isnull=False)
            .exclude(assigned_attendant__company_id=F('company_id')),
        )

        # --- mensagem carimbada com setor de outra empresa ---
        conferir(
            'Message com setor de outra empresa',
            Message.objects.filter(sector__isnull=False)
            .exclude(sector__company_id=F('conversation__company_id')),
        )

        # --- chatbot apontando para setor de outra empresa ---
        conferir(
            'MenuOption com setor de outra empresa',
            MenuOption.objects.filter(sector__isnull=False)
            .exclude(sector__company_id=F('config__company_id')),
        )
        conferir(
            'Chatbot com setor de fallback de outra empresa',
            MenuBotConfiguration.objects.filter(fallback_sector__isnull=False)
            .exclude(fallback_sector__company_id=F('company_id')),
        )

        # --- liberacao de grupo cruzando empresa ---
        conferir(
            'GroupAccess liberando grupo para setor de outra empresa',
            GroupAccess.objects.exclude(
                sectors__company_id=F('conversation__company_id')
            ).filter(sectors__isnull=False).distinct(),
        )
        conferir(
            'GroupAccess liberando grupo para usuario de outra empresa',
            GroupAccess.objects.exclude(
                users__company_id=F('conversation__company_id')
            ).filter(users__isnull=False).distinct(),
        )

        # --- usuario operacional sem empresa (so o master pode) ---
        conferir(
            'Usuario operacional SEM empresa (so o master pode ficar sem)',
            User.objects.filter(company__isnull=True).exclude(role=User.Role.MASTER),
            'sem empresa, visible_conversations devolve vazio: a pessoa nao ve nada.',
        )
        conferir(
            'Master COM empresa (o master fica acima das empresas)',
            User.objects.filter(role=User.Role.MASTER, company__isnull=False),
        )

        # --- personalizacao orfa ---
        conferir(
            'UserMenuPermission de usuario sem empresa',
            UserMenuPermission.objects.filter(user__company__isnull=True),
        )
        conferir(
            'UserConversationView de usuario sem empresa',
            UserConversationView.objects.filter(user__company__isnull=True),
        )

        # --- empresa padrao ---
        padroes = Company.objects.filter(is_default=True).count()
        if padroes != 1:
            problemas.append((
                'Empresas marcadas como padrao (deveria ser exatamente 1)',
                padroes, Company.objects.filter(is_default=True),
                'a empresa padrao e o destino do webhook sem identificador.',
            ))

        # ------------------------------------------------------------- relatorio
        self.stdout.write('Auditoria de vinculo de empresa (somente leitura)\n')
        encontrados = 0
        for rotulo, total, queryset, dica in problemas:
            if total:
                encontrados += total
                self.stdout.write(self.style.WARNING(f'  [{total}] {rotulo}'))
                if dica:
                    self.stdout.write(f'        {dica}')
                if detalhe:
                    ids = list(queryset.values_list('pk', flat=True)[:20])
                    self.stdout.write(f'        ids: {ids}')
            else:
                self.stdout.write(f'  [ok] {rotulo}')

        self.stdout.write('')
        if encontrados:
            self.stdout.write(self.style.WARNING(
                f'{encontrados} registro(s) com vinculo de empresa incoerente. '
                'O comando NAO corrige nada de proposito: mover dado de cliente e '
                'decisao consciente. Rode com --detalhe para ver os ids.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Nenhuma incoerencia: todo registro aponta para a empresa certa.'
            ))
