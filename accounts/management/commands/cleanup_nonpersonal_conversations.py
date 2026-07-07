"""Remove conversas DIRETAS criadas por engano a partir de JIDs nao-pessoais.

Uso:
    python manage.py cleanup_nonpersonal_conversations           # so lista (dry-run)
    python manage.py cleanup_nonpersonal_conversations --delete  # apaga de fato

Antes do fix de classificacao, um grupo/canal do WhatsApp (JID interno numerico
"120363...", ou sufixo @newsletter/@broadcast) podia chegar como conversa DIRETA,
criando um contato com "telefone" invalido. Este comando encontra essas conversas
privadas cujo identificador NAO e telefone de pessoa e (opcionalmente) as remove,
junto dos contatos-lixo que ficarem sem nenhuma conversa valida.
"""
from django.core.management.base import BaseCommand

from accounts.models import Contact, Conversation
from wapi.parser import is_group_jid, is_ignorable_jid


class Command(BaseCommand):
    help = 'Lista/remove conversas criadas por engano de JIDs nao-pessoais (canal/transmissao/grupo).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help='Apaga as conversas encontradas (sem esta flag, apenas lista).',
        )

    def _is_bogus(self, conversation):
        # Canal (@newsletter) / transmissao (@broadcast) nao sao atendimento,
        # independentemente do tipo com que foram gravados.
        if is_ignorable_jid(conversation.external_id):
            return True
        # Conversa DIRETA cujo identificador (ou telefone do contato) nao e de
        # pessoa: virou contato-lixo por engano.
        if conversation.chat_type == 'private':
            if is_group_jid(conversation.external_id):
                return True
            contact = conversation.contact
            return bool(contact and is_group_jid(contact.phone))
        return False

    def handle(self, *args, **options):
        do_delete = options['delete']
        bogus = [
            c for c in Conversation.objects.select_related('contact')
            if self._is_bogus(c)
        ]

        if not bogus:
            self.stdout.write(self.style.SUCCESS('Nenhuma conversa direta invalida encontrada.'))
            return

        self.stdout.write(f'Encontrada(s) {len(bogus)} conversa(s) direta(s) invalida(s):')
        for c in bogus:
            phone = c.contact.phone if c.contact else '-'
            self.stdout.write(f'  #{c.id} external_id={c.external_id!r} telefone={phone!r} '
                              f'nome={c.display_title!r}')

        if not do_delete:
            self.stdout.write(self.style.WARNING(
                'Dry-run: nada foi apagado. Rode de novo com --delete para remover.'
            ))
            return

        contact_ids = {c.contact_id for c in bogus if c.contact_id}
        removed = 0
        for c in bogus:
            c.delete()  # mensagens caem em cascata
            removed += 1

        # Remove contatos-lixo que ficaram sem nenhuma conversa.
        orphan_contacts = 0
        for contact in Contact.objects.filter(id__in=contact_ids):
            if is_group_jid(contact.phone) and not contact.conversations.exists():
                contact.delete()
                orphan_contacts += 1

        self.stdout.write(self.style.SUCCESS(
            f'Removida(s) {removed} conversa(s) e {orphan_contacts} contato(s)-lixo.'
        ))
