"""Aviso de nova mensagem por WEB PUSH (RFC 8291/8292).

Por que Web Push e nao o poll da tela: o pop-up dependia de um `setInterval` de 6s
em `static/js/conversations.js` comparando o contador de nao lidas. O Chrome
estrangula timer de aba em segundo plano para 1x por minuto — medido em producao, as
chamadas ao `conversas/lista/` caem de 6s para 60s no instante em que a aba sai da
frente. Resultado: o aviso chegava com ate um minuto de atraso e, se a pessoa
voltasse para a aba antes do tique, a deteccao rodava com a janela em foco e o codigo
mostrava o TOAST interno em vez do pop-up — o aviso do sistema simplesmente nao vinha.

Aqui quem avisa e o SERVIDOR, no momento em que a mensagem entra pelo webhook. Chega
com a aba em segundo plano e mesmo com o navegador fechado, porque nao passa por
timer nenhum: quem entrega e o servico de push do proprio navegador.

Privacidade: o conteudo vai CIFRADO ponta a ponta (aes128gcm com as chaves que o
navegador gerou, guardadas em `PushSubscription`). O servico de push transporta e nao
consegue ler o texto da mensagem.

Configuracao: par de chaves VAPID no `.env` (`WEBPUSH_VAPID_PUBLIC_KEY` /
`WEBPUSH_VAPID_PRIVATE_KEY` / `WEBPUSH_VAPID_SUBJECT`). Sem elas o modulo fica
inerte e o `manage.py check` avisa (`beezap.W002`) — ver docs/DEPLOY.md.
"""

import json
import logging
import threading

from django.conf import settings
from django.utils import timezone

push_logger = logging.getLogger('beezap.push')

# Limite do texto que vai no corpo do aviso. O sistema operacional corta bem antes
# disso; o corte aqui evita mandar uma mensagem inteira para fora sem necessidade.
PREVIEW_MAX = 120


def vapid_configured():
    """As chaves VAPID estao no ambiente? Sem elas nao ha como assinar o envio."""
    return bool(
        getattr(settings, 'WEBPUSH_VAPID_PUBLIC_KEY', '')
        and getattr(settings, 'WEBPUSH_VAPID_PRIVATE_KEY', '')
    )


def _send_one(subscription, payload):
    """Envia para UMA inscricao. Devolve 'ok', 'gone' (inscricao morta) ou 'erro'.

    O import do `pywebpush` e local de proposito: se a dependencia faltar no servidor,
    o sistema continua funcionando sem o pop-up em vez de quebrar o webhook — a mesma
    postura do ffmpeg ausente.
    """
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        push_logger.warning(
            'pywebpush nao instalado: aviso de nova mensagem desligado. '
            'Rode pip install -r requirements.txt.'
        )
        return 'erro'

    try:
        webpush(
            subscription_info={
                'endpoint': subscription.endpoint,
                'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
            vapid_claims={'sub': settings.WEBPUSH_VAPID_SUBJECT},
            # 10 min: aviso de atendimento perde o sentido depois disso, e sem TTL o
            # servico de push guardaria para entregar muito depois.
            ttl=600,
        )
        return 'ok'
    except WebPushException as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        # 404/410 = o navegador descartou a inscricao (limpou dados, desinstalou).
        # Nao e erro: e para remover a linha, senao ela e tentada para sempre.
        if status in (404, 410):
            return 'gone'
        push_logger.warning('Push falhou (HTTP %s) na inscricao %s.', status, subscription.pk)
        return 'erro'
    except Exception:
        push_logger.exception('Push falhou na inscricao %s.', subscription.pk)
        return 'erro'


def send_to_users(users, payload):
    """Manda o mesmo aviso para todas as inscricoes desses usuarios.

    Devolve quantos envios deram certo. Inscricao morta e APAGADA no caminho.
    """
    from .models import PushSubscription

    if not vapid_configured():
        return 0
    ids = [u.pk for u in users]
    if not ids:
        return 0

    enviados = 0
    mortas = []
    ok_ids = []
    for sub in PushSubscription.objects.filter(user_id__in=ids).select_related('user'):
        resultado = _send_one(sub, payload)
        if resultado == 'ok':
            enviados += 1
            ok_ids.append(sub.pk)
        elif resultado == 'gone':
            mortas.append(sub.pk)
    if mortas:
        PushSubscription.objects.filter(pk__in=mortas).delete()
    if ok_ids:
        PushSubscription.objects.filter(pk__in=ok_ids).update(last_sent_at=timezone.now())
    return enviados


def recipients_for(conversation):
    """Quem pode ver ESTA conversa e tem inscricao de push.

    "Quem e avisado?" e a MESMA pergunta de "quem pode abrir a conversa", entao a
    resposta sai de `can_see_conversation` — a autoridade que ja existe. Sem passar por
    ela, o aviso vazaria conteudo de atendimento para quem a tela esconde: alcance por
    setor, perfil personalizado e, principalmente, o gestor master, que administra mas
    NUNCA le o atendimento de ninguem (e ja e recusado ali dentro).

    A empresa entra na consulta antes de tudo: o aviso nunca sai da empresa dona da
    conversa.
    """
    from .models import PushSubscription, User

    inscritos = PushSubscription.objects.values_list('user_id', flat=True)
    candidatos = (
        User.objects
        .filter(is_active=True, company_id=conversation.company_id, pk__in=inscritos)
        .exclude(role=User.Role.MASTER)
    )
    from .permissions import can_see_conversation
    return [u for u in candidatos if can_see_conversation(u, conversation)]


def build_payload(message):
    """Monta o que aparece no pop-up: titulo, corpo e para onde o clique leva."""
    from django.urls import reverse

    conversation = message.conversation
    titulo = conversation.display_title or 'Nova mensagem'
    texto = (message.text or '').strip()
    if not texto:
        # Midia/reacao nao tem texto: usa o resumo que a lista tambem mostra.
        texto = (conversation.last_message_text or '').strip() or 'Nova mensagem recebida.'
    # Em GRUPO, quem falou importa tanto quanto o que foi dito.
    if conversation.is_group and (message.sender_name or '').strip():
        texto = f'{message.sender_name.strip()}: {texto}'
    if len(texto) > PREVIEW_MAX:
        texto = texto[:PREVIEW_MAX - 1].rstrip() + '…'
    return {
        'title': titulo,
        'body': texto,
        # O clique abre a conversa certa; o service worker foca a aba se ja existir.
        'url': f"{reverse('conversations')}?conversa={conversation.pk}",
        'conversation_id': conversation.pk,
        # Agrupa os avisos da MESMA conversa (5 mensagens seguidas nao viram 5 pop-ups).
        'tag': f'beeonboard-conv-{conversation.pk}',
    }


def notify_new_message_async(message):
    """Dispara o aviso de nova mensagem EM BACKGROUND. Devolve True se a thread subiu.

    Em background pelo mesmo motivo do download de midia: isto roda DENTRO da
    requisicao do webhook, num servico com `--workers 2 --timeout 60`. Cada inscricao
    e uma chamada HTTPS ao servico de push do navegador; com varios atendentes
    conectados, enviar inline seguraria o worker e atrasaria as proximas mensagens.

    So mensagem RECEBIDA avisa: o que a propria conta enviou (`from_me`) nao e novidade
    para ninguem.
    """
    if message is None or message.pk is None:
        return False
    if message.direction != 'in' or message.from_me:
        return False
    if not vapid_configured():
        return False

    def _worker():
        from django.db import connection
        try:
            destinatarios = recipients_for(message.conversation)
            if destinatarios:
                send_to_users(destinatarios, build_payload(message))
        except Exception:
            push_logger.exception('Falha ao avisar nova mensagem (msg=%s).', message.pk)
        finally:
            connection.close()  # nao deixar conexao de banco pendurada na thread

    threading.Thread(
        target=_worker, name='push-msg-%s' % message.pk, daemon=True
    ).start()
    return True
