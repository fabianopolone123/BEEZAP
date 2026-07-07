"""Remove mensagens de STATUS/transmissao do WhatsApp que entraram como conversa.

Uso:
    python manage.py cleanup_status_messages           # so lista (dry-run)
    python manage.py cleanup_status_messages --delete   # apaga de fato

Atualizacoes de Status ('stories', JID status@broadcast) chegavam como se fossem
mensagens diretas do autor. Este comando apaga apenas essas mensagens (mantendo
as mensagens reais do contato) e, se uma conversa ficar sem nenhuma mensagem,
remove a conversa. Atualiza o resumo (ultima mensagem) das conversas afetadas.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import Conversation, Message
from wapi.parser import is_status_or_broadcast


class Command(BaseCommand):
    help = 'Lista/remove mensagens de status (status@broadcast) que entraram como conversa.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help='Apaga as mensagens de status encontradas (sem esta flag, apenas lista).',
        )

    def handle(self, *args, **options):
        do_delete = options['delete']

        # Filtro amplo pelo texto do payload; confirma com o parser. Status do
        # W-API Lite trazem "status" (chat.id) e "statusSourceType", nem sempre
        # a palavra "broadcast".
        candidates = (
            Message.objects
            .filter(Q(raw_payload__icontains='status') | Q(raw_payload__icontains='broadcast'))
            .exclude(raw_payload__isnull=True)
        )
        status_msgs = [m for m in candidates if is_status_or_broadcast(m.raw_payload)]

        if not status_msgs:
            self.stdout.write(self.style.SUCCESS('Nenhuma mensagem de status encontrada.'))
            return

        affected_conv_ids = {m.conversation_id for m in status_msgs}
        self.stdout.write(f'Encontrada(s) {len(status_msgs)} mensagem(ns) de status '
                          f'em {len(affected_conv_ids)} conversa(s):')
        for m in status_msgs[:50]:
            preview = (m.text or '')[:40]
            self.stdout.write(f'  msg #{m.id} conv #{m.conversation_id} '
                              f'tipo={m.message_type} texto={preview!r}')
        if len(status_msgs) > 50:
            self.stdout.write(f'  ... e mais {len(status_msgs) - 50}.')

        if not do_delete:
            self.stdout.write(self.style.WARNING(
                'Dry-run: nada foi apagado. Rode de novo com --delete para remover.'
            ))
            return

        removed = len(status_msgs)
        for m in status_msgs:
            m.delete()

        # Atualiza resumo/remove conversas que ficaram vazias.
        emptied = 0
        for conv in Conversation.objects.filter(id__in=affected_conv_ids):
            last = conv.messages.order_by('-created_at').first()
            if last is None:
                conv.delete()
                emptied += 1
            else:
                conv.last_message_text = last.text or ''
                conv.last_message_at = last.created_at
                conv.save(update_fields=['last_message_text', 'last_message_at', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'Removida(s) {removed} mensagem(ns) de status; {emptied} conversa(s) vazia(s) apagada(s).'
        ))
