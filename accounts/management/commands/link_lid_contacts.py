"""Vincula o Contato do TELEFONE REAL as conversas diretas chaveadas por `@lid`.

Uso:
    python manage.py link_lid_contacts            # so lista (dry-run)
    python manage.py link_lid_contacts --apply     # vincula de fato

A W-API Lite entrega a conversa direta com um identificador interno (`@lid`) no chat,
mas manda o telefone de verdade no remetente (`sender.id`) de cada mensagem RECEBIDA —
que o parser ja guarda em `Message.sender_id`. As conversas criadas ANTES desse
tratamento ficaram sem contato e por isso exibiam o pushName do WhatsApp. Este comando
resolve o telefone dessas conversas pelo historico e anexa o Contato:

- a conversa passa a aparecer pelo NUMERO (clicar cadastra o nome — ver
  `wapi.services.get_or_create_contact`);
- a pessoa fica UNIFICADA com os grupos e a tela Contatos (mesmo telefone = mesmo
  Contato), entao cadastrar o nome uma vez vale em todo lugar;
- a conversa continua chaveada pelo `@lid` (`external_id`), que e o destino de envio
  exigido pela W-API — nenhum chat e dividido nem unido.

Nome de contato ja cadastrado nunca e alterado. Numeros da propria instancia
(`connectedPhone`) nunca viram contato.
"""
from django.core.management.base import BaseCommand

from accounts.models import Conversation
from wapi.parser import normalize_phone
from wapi.services import get_or_create_contact


class Command(BaseCommand):
    help = ('Lista/vincula o Contato do telefone real as conversas diretas chaveadas '
            'por @lid (que apareciam pelo pushName).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Vincula os contatos encontrados (sem esta flag, apenas lista).',
        )

    def handle(self, *args, **options):
        do_apply = options['apply']

        pending = list(
            Conversation.objects
            .filter(chat_type='private', contact__isnull=True)
            .order_by('id')
        )
        if not pending:
            self.stdout.write(self.style.SUCCESS(
                'Nenhuma conversa direta sem contato. Nada a fazer.'
            ))
            return

        resolved = []    # (conversation, phone)
        unresolved = []  # conversation
        for conv in pending:
            rows = (
                conv.messages
                .filter(direction='in')
                .exclude(sender_id='')
                .values_list('sender_id', 'raw_payload')
            )
            counts = {}
            connected = set()
            for sender_id, payload in rows:
                phone = normalize_phone(sender_id)
                if phone:
                    counts[phone] = counts.get(phone, 0) + 1
                if isinstance(payload, dict):
                    ours = normalize_phone(payload.get('connectedPhone') or '')
                    if ours:
                        connected.add(ours)
            # Mais frequente entre os remetentes recebidos, nunca o nosso proprio numero.
            options_ = [(n, p) for p, n in counts.items() if p not in connected]
            if options_:
                options_.sort(reverse=True)
                resolved.append((conv, options_[0][1]))
            else:
                unresolved.append(conv)

        self.stdout.write(f'{len(pending)} conversa(s) direta(s) sem contato.')
        if resolved:
            self.stdout.write(f'{len(resolved)} com telefone resolvido pelo historico:')
            for conv, phone in resolved:
                title = conv.name or conv.external_id
                self.stdout.write(f'  {conv.external_id} ({title!r}) -> telefone {phone}')
        if unresolved:
            self.stdout.write(self.style.WARNING(
                f'{len(unresolved)} sem telefone no historico (ficam como estao; '
                f'resolvem sozinhas na proxima mensagem recebida):'
            ))
            for conv in unresolved:
                self.stdout.write(f'  {conv.external_id} ({(conv.name or "-")!r})')

        if not resolved:
            return
        if not do_apply:
            self.stdout.write(self.style.WARNING(
                'Dry-run: nada foi alterado. Rode de novo com --apply para vincular '
                '(faca backup do db.sqlite3 antes).'
            ))
            return

        linked = 0
        reused = 0
        for conv, phone in resolved:
            # O contato e resolvido dentro da MESMA empresa da conversa.
            contact = get_or_create_contact(phone, conv.company)
            if contact is None:
                continue
            if contact.name:
                reused += 1  # ja tinha nome cadastrado (ex.: nomeado em um grupo)
            conv.contact = contact
            conv.save(update_fields=['contact', 'updated_at'])
            linked += 1

        msg = f'{linked} conversa(s) vinculada(s) ao contato do telefone real.'
        if reused:
            msg += f' {reused} ja tinha(m) nome cadastrado e passa(m) a exibi-lo.'
        self.stdout.write(self.style.SUCCESS(msg))
