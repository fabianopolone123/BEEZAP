"""Diagnostico: mostra o payload cru e a classificacao das mensagens recebidas.

Uso (no VPS):
    python manage.py inspect_wapi_messages --name Marcia
    python manage.py inspect_wapi_messages --name Marcia --full
    python manage.py inspect_wapi_messages --conv 45 --limit 5 --full

Serve para entender DE ONDE vem uma mensagem que caiu no lugar errado (ex.: um
status/transmissao entrando como conversa direta). Para cada mensagem, mostra os
dados salvos, o que o parser atual decide (grupo/direta, status/broadcast, tipo)
e as chaves do payload; com --full, imprime o payload inteiro (valores longos
sao truncados). O payload e do webhook recebido — nao contem o token da W-API.
"""
import json

from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import Message
from wapi.parser import (
    is_ignorable_jid,
    is_status_or_broadcast,
    normalize_wapi_message_context,
    parse_wapi_media,
    wapi_content_keys,
)


def _shorten(obj, maxlen=160, depth=0):
    """Copia a estrutura truncando strings longas (ex.: base64/jwt de midia)."""
    if depth > 8:
        return '...'
    if isinstance(obj, str):
        return obj if len(obj) <= maxlen else obj[:maxlen] + '...(%d chars)' % len(obj)
    if isinstance(obj, dict):
        return {k: _shorten(v, maxlen, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shorten(v, maxlen, depth + 1) for v in obj[:20]]
    return obj


class Command(BaseCommand):
    help = 'Mostra o payload cru e a classificacao de mensagens recebidas (diagnostico).'

    def add_arguments(self, parser):
        parser.add_argument('--name', help='Filtra por nome/telefone (contato, grupo ou remetente).')
        parser.add_argument('--conv', type=int, help='Filtra por ID da conversa.')
        parser.add_argument('--limit', type=int, default=10, help='Quantidade (padrao 10).')
        parser.add_argument('--full', action='store_true', help='Imprime o payload inteiro.')

    def handle(self, *args, **options):
        qs = Message.objects.select_related('conversation', 'conversation__contact').order_by('-created_at')

        name = options.get('name')
        if name:
            qs = qs.filter(
                Q(sender_name__icontains=name)
                | Q(phone__icontains=name)
                | Q(sender_id__icontains=name)
                | Q(conversation__name__icontains=name)
                | Q(conversation__contact__name__icontains=name)
                | Q(conversation__contact__phone__icontains=name)
            )
        if options.get('conv'):
            qs = qs.filter(conversation_id=options['conv'])

        messages = list(qs[:options['limit']])
        if not messages:
            self.stdout.write(self.style.WARNING('Nenhuma mensagem encontrada com esse filtro.'))
            return

        self.stdout.write(f'{len(messages)} mensagem(ns) (mais recente primeiro):\n')
        for m in messages:
            conv = m.conversation
            contact = conv.contact if conv else None
            self.stdout.write(self.style.HTTP_INFO(
                f'=== msg #{m.id} | {m.created_at:%Y-%m-%d %H:%M} | tipo={m.message_type} | dir={m.direction} ==='
            ))
            self.stdout.write(
                f'  conversa: #{conv.id} chat_type={conv.chat_type} external_id={conv.external_id!r} '
                f'name={conv.name!r} contato={(contact.name if contact else None)!r}/'
                f'{(contact.phone if contact else None)!r}'
            )
            self.stdout.write(
                f'  salvo: is_group={m.is_group} sender_name={m.sender_name!r} '
                f'sender_id={m.sender_id!r} participant_id={m.participant_id!r} from_me={m.from_me}'
            )

            payload = m.raw_payload
            if not isinstance(payload, dict):
                self.stdout.write(self.style.WARNING('  (sem raw_payload salvo)\n'))
                continue

            ctx = normalize_wapi_message_context(payload)
            media = parse_wapi_media(payload)
            self.stdout.write(
                f'  parser: chat_id={ctx.get("chat_id")!r} chat_type={ctx.get("chat_type")} '
                f'is_group={ctx.get("is_group")} source={ctx.get("source")!r}'
            )
            self.stdout.write(
                f'  veredito: status_or_broadcast={is_status_or_broadcast(payload)} '
                f'ignorable_jid={is_ignorable_jid(ctx.get("chat_id") or "")} '
                f'tipo_detectado={media.get("message_type")}'
            )
            self.stdout.write(f'  content_keys={wapi_content_keys(payload)}')
            self.stdout.write(f'  payload_top_keys={sorted(payload.keys())}')
            if options['full']:
                pretty = json.dumps(_shorten(payload), indent=2, ensure_ascii=False)
                self.stdout.write(pretty)
            self.stdout.write('')
