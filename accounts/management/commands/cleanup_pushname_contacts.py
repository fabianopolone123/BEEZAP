"""Limpa o nome dos contatos que foram batizados AUTOMATICAMENTE com o pushName do
WhatsApp, para a conversa voltar a mostrar o NUMERO ate alguem cadastrar o nome.

Uso:
    python manage.py cleanup_pushname_contacts           # so lista (dry-run)
    python manage.py cleanup_pushname_contacts --apply    # limpa de fato

Antes, a primeira mensagem de uma conversa direta criava o Contato com o nome que
vem do WhatsApp (pushName). Hoje o contato nasce SEM nome (ver
`wapi.services.get_or_create_contact`), mas os contatos criados antes continuam com
aquele nome. Este comando desfaz APENAS esses casos.

Como decide se o nome veio do WhatsApp: o nome do contato tem de ser IGUAL (ignorando
maiusculas/minusculas e espacos) ao `sender_name` de alguma mensagem RECEBIDA daquele
mesmo numero — isto e, ao pushName registrado no historico. Nome digitado a mao por
uma pessoa nao bate com nenhum pushName e por isso e PRESERVADO.
"""
from django.core.management.base import BaseCommand

from accounts.models import Contact, Message


def _digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _norm(value):
    return ' '.join((value or '').split()).casefold()


class Command(BaseCommand):
    help = ('Lista/limpa nomes de contatos que vieram do pushName do WhatsApp '
            '(a conversa volta a mostrar o numero).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Limpa os nomes encontrados (sem esta flag, apenas lista).',
        )

    def handle(self, *args, **options):
        do_apply = options['apply']

        named = list(Contact.objects.exclude(name='').order_by('phone'))
        if not named:
            self.stdout.write(self.style.SUCCESS('Nenhum contato com nome cadastrado.'))
            return

        # pushNames vistos no historico, por numero do remetente: {digitos: {nomes}}.
        pushnames = {}
        rows = (
            Message.objects
            .filter(direction='in')
            .exclude(sender_name='')
            .values_list('sender_id', 'sender_name')
        )
        for sender_id, sender_name in rows:
            digits = _digits(sender_id)
            if digits:
                pushnames.setdefault(digits, set()).add(_norm(sender_name))

        from_pushname = [
            c for c in named
            if _norm(c.name) in pushnames.get(_digits(c.phone), ())
        ]

        kept = len(named) - len(from_pushname)
        if not from_pushname:
            self.stdout.write(self.style.SUCCESS(
                f'Nenhum contato com nome vindo do WhatsApp. {kept} nome(s) cadastrado(s) preservado(s).'
            ))
            return

        self.stdout.write(f'{len(from_pushname)} contato(s) com nome vindo do WhatsApp '
                          f'(voltariam a aparecer pelo numero):')
        for c in from_pushname[:50]:
            self.stdout.write(f'  {c.phone} — {c.name!r}')
        if len(from_pushname) > 50:
            self.stdout.write(f'  ... e mais {len(from_pushname) - 50}.')
        self.stdout.write(f'{kept} nome(s) cadastrado(s) a mao serao preservado(s).')

        if not do_apply:
            self.stdout.write(self.style.WARNING(
                'Dry-run: nada foi alterado. Rode de novo com --apply para limpar '
                '(faca backup do db.sqlite3 antes).'
            ))
            return

        for c in from_pushname:
            c.name = ''
            c.save(update_fields=['name', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'{len(from_pushname)} contato(s) voltaram a aparecer pelo numero; '
            f'{kept} nome(s) cadastrado(s) preservado(s).'
        ))
