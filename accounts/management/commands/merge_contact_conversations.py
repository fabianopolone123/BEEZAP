"""Unifica conversas picotadas: um unico chat por pessoa/grupo (padrao WhatsApp).

Antes, ao encerrar um atendimento, a proxima mensagem do mesmo contato criava uma
NOVA `Conversation`. Este comando junta essas conversas separadas do mesmo contato
(ou grupo/LID) em um unico chat, na ordem cronologica, inserindo uma divisoria
"Novo atendimento iniciado" no limite de cada atendimento antigo.

Uso (no VPS, faca backup do db.sqlite3 antes):
    python manage.py merge_contact_conversations            # dry-run (so mostra)
    python manage.py merge_contact_conversations --apply    # aplica de verdade

Read-only por padrao; so altera o banco com --apply.
"""
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Conversation, Message
from wapi.services import SYSTEM_NEW_SERVICE_TEXT, _summary_text


class Command(BaseCommand):
    help = 'Unifica conversas separadas do mesmo contato/grupo em um unico chat.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica a unificacao (sem isso, e apenas dry-run).')

    def _groups(self):
        """Agrupa conversas que representam o MESMO chat.

        - com contato: por contact_id.
        - sem contato (grupo / @lid): por (external_id, chat_type).
        Retorna apenas grupos com mais de uma conversa (os que precisam unificar)."""
        by_key = defaultdict(list)
        for conv in Conversation.objects.all():
            if conv.contact_id:
                key = ('contact', conv.contact_id)
            elif conv.external_id:
                key = ('external', conv.chat_type, conv.external_id)
            else:
                continue  # sem chave de agrupamento -> deixa como esta
            by_key[key].append(conv)
        return {k: v for k, v in by_key.items() if len(v) > 1}

    def handle(self, *args, **options):
        apply = options['apply']
        groups = self._groups()

        if not groups:
            self.stdout.write(self.style.SUCCESS('Nada a unificar: ja ha no maximo 1 conversa por contato/grupo.'))
            return

        total_merged = 0
        for key, convs in groups.items():
            convs.sort(key=lambda c: c.created_at)  # mais antiga = canonica
            canonical = convs[0]
            extras = convs[1:]
            label = canonical.display_title
            self.stdout.write(self.style.HTTP_INFO(
                f'=== {label!r}: unificando {len(convs)} conversas na #{canonical.id} '
                f'(absorvendo {[c.id for c in extras]}) ==='
            ))
            if not apply:
                total_merged += len(extras)
                continue

            with transaction.atomic():
                for seg in extras:
                    first = seg.messages.order_by('created_at', 'id').first()
                    divider_time = (first.created_at - timedelta(microseconds=1)) if first else seg.created_at
                    divider = Message.objects.create(
                        conversation=canonical, direction='out',
                        message_type='system', text=SYSTEM_NEW_SERVICE_TEXT, status='sent',
                    )
                    # created_at tem auto_now_add; ajustamos via update para ordenar certo.
                    Message.objects.filter(pk=divider.pk).update(created_at=divider_time)
                    Message.objects.filter(conversation=seg).exclude(pk=divider.pk).update(conversation=canonical)
                    seg.delete()

                # Estado atual do chat vem do atendimento mais recente (ultima conversa).
                newest_conv = convs[-1]
                canonical.status = newest_conv.status
                canonical.sector_id = newest_conv.sector_id
                canonical.assigned_attendant_id = newest_conv.assigned_attendant_id
                canonical.unread_count = sum(c.unread_count or 0 for c in convs)
                if not canonical.name and newest_conv.name:
                    canonical.name = newest_conv.name
                # Resumo (ultima mensagem real, ignorando divisorias).
                last_msg = (
                    canonical.messages.exclude(message_type='system')
                    .order_by('-created_at', '-id').first()
                )
                if last_msg:
                    canonical.last_message_text = _summary_text(last_msg.message_type, last_msg.text)
                    canonical.last_message_at = last_msg.created_at
                canonical.save()
            total_merged += len(extras)

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f'Unificacao concluida: {total_merged} conversas absorvidas em {len(groups)} chats.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: {total_merged} conversas seriam absorvidas em {len(groups)} chats. '
                f'Rode com --apply para aplicar (faca backup do db.sqlite3 antes).'
            ))
