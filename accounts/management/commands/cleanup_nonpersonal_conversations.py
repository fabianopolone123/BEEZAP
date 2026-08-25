"""Remove conversas criadas por engano a partir de chats nao-pessoais (canal etc.).

Uso:
    python manage.py cleanup_nonpersonal_conversations           # so lista (dry-run)
    python manage.py cleanup_nonpersonal_conversations --delete  # apaga de fato

Antes do fix de classificacao, um grupo/canal do WhatsApp (JID interno numerico
"120363...", ou sufixo @newsletter/@broadcast) podia chegar como conversa DIRETA,
criando um contato com "telefone" invalido. Este comando encontra essas conversas
privadas cujo identificador NAO e telefone de pessoa e (opcionalmente) as remove,
junto dos contatos-lixo que ficarem sem nenhuma conversa valida.

Pega tambem o CANAL QUE VIROU GRUPO: quando o id do canal chega "pelado" (sem
`@newsletter`), o formato nao o distingue de grupo — os dois usam o prefixo
`120363...` — e ele entrava como conversa de grupo, ficando para sempre com o nome
`Grupo <id>` (o `get-all-groups` nao lista canal). Aqui a deteccao usa o PAYLOAD
guardado das mensagens, onde a W-API diz `isGroup: false`. Grupo de verdade sempre
manda `isGroup: true`, entao nao ha risco de levar um grupo junto. A ingestao ja
descarta esses casos (ver `wapi.parser.is_channel_chat`); este comando limpa os que
entraram ANTES disso.
"""
from django.core.management.base import BaseCommand

from accounts.models import Contact, Conversation
from wapi.parser import (
    group_flag_from_payload,
    is_bare_internal_id,
    is_group_jid,
    is_ignorable_jid,
)


class Command(BaseCommand):
    help = 'Lista/remove conversas criadas por engano de JIDs nao-pessoais (canal/transmissao/grupo).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help='Apaga as conversas encontradas (sem esta flag, apenas lista).',
        )

    def _e_canal_pelado(self, conversation):
        """Conversa de GRUPO cujo id veio pelado e cujas mensagens dizem `isGroup:
        false` — ou seja, canal. Exige ao menos uma mensagem afirmando isso e NENHUMA
        afirmando o contrario, para nunca levar um grupo de verdade junto."""
        if conversation.chat_type != 'group':
            return False
        if not is_bare_internal_id(conversation.external_id):
            return False
        disse_canal = False
        for payload in conversation.messages.values_list('raw_payload', flat=True):
            flag = group_flag_from_payload(payload)
            if flag is True:
                return False
            if flag is False:
                disse_canal = True
        return disse_canal

    def _is_bogus(self, conversation):
        # Canal (@newsletter) / transmissao (@broadcast) nao sao atendimento,
        # independentemente do tipo com que foram gravados.
        if is_ignorable_jid(conversation.external_id):
            return True
        # Canal cujo id chegou sem sufixo, gravado como grupo (ver docstring).
        if self._e_canal_pelado(conversation):
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
            self.stdout.write(self.style.SUCCESS('Nenhuma conversa invalida encontrada.'))
            return

        self.stdout.write(f'Encontrada(s) {len(bogus)} conversa(s) invalida(s):')
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
