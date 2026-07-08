"""Diagnostico: inspeciona os EVENTOS BRUTOS do webhook (WapiWebhookEvent).

Uso (no VPS):
    python manage.py inspect_wapi_events --hours 6
    python manage.py inspect_wapi_events --name Joao --full
    python manage.py inspect_wapi_events --contains albumMessage --full

Diferenca para `inspect_wapi_messages`: aquele mostra apenas mensagens JA criadas
(tabela Message); este mostra TODO webhook recebido (tabela WapiWebhookEvent),
inclusive os que o `ingest_wapi_payload` DESCARTOU (status, canal, tipo nao
suportado, duplicada). Serve para descobrir por que uma mensagem recebida no
celular NAO apareceu no sistema (ex.: um album de fotos que chegou como
`albumMessage` e caiu como tipo nao suportado).

Para cada evento mostra: o que o parser atual decide (grupo/direta, tipo, status),
as chaves do conteudo, o veredito do ingest (CRIARIA a mensagem ou DESCARTARIA, e
por que) e se ha uma Message com o mesmo id externo (ou seja, se virou mensagem).
O payload e o do webhook recebido — nao contem o token da W-API.
"""
import json

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts.models import Message, WapiWebhookEvent
from wapi.parser import (
    is_ignorable_jid,
    is_status_or_broadcast,
    normalize_wapi_message_context,
    parse_wapi_media,
    parse_wapi_webhook_payload,
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


def _ingest_verdict(payload):
    """Replica a decisao do `ingest_wapi_payload` sem gravar nada: retorna
    (acao, motivo) explicando se a mensagem seria CRIADA ou DESCARTADA."""
    if not isinstance(payload, dict):
        return 'DESCARTA', 'payload nao e objeto'
    ctx = normalize_wapi_message_context(payload)
    chat_id = ctx.get('chat_id') or ''
    if not chat_id:
        return 'DESCARTA', 'sem chat_id'
    if is_ignorable_jid(chat_id) or is_status_or_broadcast(payload):
        return 'DESCARTA', 'status/canal/transmissao'
    media = parse_wapi_media(payload)
    mtype = media.get('message_type')
    if mtype == 'unknown':
        return 'DESCARTA', 'tipo nao suportado (unknown)'
    if mtype == 'text':
        parsed = parse_wapi_webhook_payload(payload)
        if not parsed.get('message_text'):
            return 'DESCARTA', 'texto vazio'
    return 'CRIA', f'tipo={mtype}'


class Command(BaseCommand):
    help = 'Inspeciona os eventos brutos do webhook W-API (inclusive os descartados).'

    def add_arguments(self, parser):
        parser.add_argument('--name', help='Filtra por nome/telefone (contact_name/phone).')
        parser.add_argument('--contains', help='Filtra por texto presente no payload cru (ex.: albumMessage).')
        parser.add_argument('--hours', type=int, help='Somente eventos das ultimas N horas.')
        parser.add_argument('--limit', type=int, default=20, help='Quantidade (padrao 20).')
        parser.add_argument('--full', action='store_true', help='Imprime o payload inteiro.')

    def handle(self, *args, **options):
        qs = WapiWebhookEvent.objects.all().order_by('-received_at')

        if options.get('name'):
            name = options['name']
            qs = qs.filter(Q(contact_name__icontains=name) | Q(phone__icontains=name))
        if options.get('hours'):
            since = timezone.now() - timezone.timedelta(hours=options['hours'])
            qs = qs.filter(received_at__gte=since)

        contains = (options.get('contains') or '').lower()
        events = []
        for event in qs.iterator():
            if contains and contains not in json.dumps(event.raw_payload or {}).lower():
                continue
            events.append(event)
            if len(events) >= options['limit']:
                break

        if not events:
            self.stdout.write(self.style.WARNING('Nenhum evento encontrado com esse filtro.'))
            return

        self.stdout.write(f'{len(events)} evento(s) (mais recente primeiro):\n')
        for event in events:
            payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
            ctx = normalize_wapi_message_context(payload)
            media = parse_wapi_media(payload)
            parsed = parse_wapi_webhook_payload(payload)
            action, reason = _ingest_verdict(payload)

            # Uma Message com o mesmo id externo indica que o evento virou mensagem.
            msg_id = parsed.get('message_id') or ''
            created_msg = None
            if msg_id:
                created_msg = Message.objects.filter(external_message_id=msg_id).first()

            style = self.style.SUCCESS if action == 'CRIA' else self.style.ERROR
            self.stdout.write(style(
                f'=== evento #{event.id} | {timezone.localtime(event.received_at):%Y-%m-%d %H:%M:%S} '
                f'| {action}: {reason} ==='
            ))
            self.stdout.write(
                f'  parser: chat_id={ctx.get("chat_id")!r} chat_type={ctx.get("chat_type")} '
                f'is_group={ctx.get("is_group")} tipo_detectado={media.get("message_type")}'
            )
            self.stdout.write(
                f'  status_or_broadcast={is_status_or_broadcast(payload)} '
                f'ignorable_jid={is_ignorable_jid(ctx.get("chat_id") or "")}'
            )
            self.stdout.write(f'  content_keys={wapi_content_keys(payload)}')
            self.stdout.write(f'  payload_top_keys={sorted(payload.keys())}')
            self.stdout.write(
                f'  message_id={msg_id!r} '
                + (f'-> Message #{created_msg.id} (virou mensagem)' if created_msg
                   else '-> nenhuma Message com esse id (nao apareceu no chat)')
            )
            if options['full']:
                pretty = json.dumps(_shorten(payload), indent=2, ensure_ascii=False)
                self.stdout.write(pretty)
            self.stdout.write('')
