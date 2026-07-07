"""Remove mensagens de tipo 'unknown' (Tipo de mensagem nao suportado).

Uso:
    python manage.py cleanup_unknown_messages           # so lista (dry-run)
    python manage.py cleanup_unknown_messages --delete    # apaga de fato

Quase sempre sao mensagens de SISTEMA do WhatsApp (ex.: distribuicao de chave de
criptografia em grupos) sem conteudo para o usuario, que apareciam como "Tipo de
mensagem nao suportado". Remove essas mensagens e apaga conversas que ficarem
vazias, atualizando o resumo das afetadas.
"""
from django.core.management.base import BaseCommand

from accounts.models import Conversation, Message


class Command(BaseCommand):
    help = 'Lista/remove mensagens de tipo unknown ("Tipo de mensagem nao suportado").'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help='Apaga as mensagens encontradas (sem esta flag, apenas lista).',
        )

    def handle(self, *args, **options):
        do_delete = options['delete']
        unknown = Message.objects.filter(message_type='unknown')
        total = unknown.count()

        if not total:
            self.stdout.write(self.style.SUCCESS('Nenhuma mensagem "unknown" encontrada.'))
            return

        affected_conv_ids = set(unknown.values_list('conversation_id', flat=True))
        self.stdout.write(f'Encontrada(s) {total} mensagem(ns) "unknown" '
                          f'em {len(affected_conv_ids)} conversa(s).')

        if not do_delete:
            self.stdout.write(self.style.WARNING(
                'Dry-run: nada foi apagado. Rode de novo com --delete para remover.'
            ))
            return

        unknown.delete()

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
            f'Removida(s) {total} mensagem(ns); {emptied} conversa(s) vazia(s) apagada(s).'
        ))
