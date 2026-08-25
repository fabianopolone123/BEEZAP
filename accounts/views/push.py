"""Aviso de nova mensagem (Web Push): service worker, inscricao e cancelamento.

O pop-up dependia de um timer de 6s na tela Conversas, e o Chrome estrangula timer de
aba em segundo plano para 1x por minuto — justamente quando o aviso importa. Aqui o
navegador se INSCREVE e passa a receber o aviso do servidor, sem timer no meio (ver
accounts/webpush.py).

Tres endpoints e um arquivo:
  `sw.js`            o service worker (servido pelo Django, ver abaixo)
  `push/chave/`      a chave publica VAPID que o navegador precisa para se inscrever
  `push/inscrever/`  guarda a inscricao do navegador
  `push/cancelar/`   apaga a inscricao (a pessoa desligou o aviso)
"""

from .common import (
    HttpResponse,
    JsonResponse,
    PushSubscription,
    json,
    login_required,
    require_POST,
    settings,
    vapid_configured,
)

# O service worker e servido por VIEW, nao como arquivo estatico. Motivo: o escopo de
# um service worker e limitado a PASTA dele. Em `static/js/sw.js` o escopo seria
# `/beeonboard/static/js/`, que nao cobre as telas do sistema; servindo em
# `/beeonboard/sw.js` o escopo passa a ser `/beeonboard/` inteiro. A alternativa
# (cabecalho `Service-Worker-Allowed` no Nginx) exigiria mexer na configuracao do
# servidor a cada deploy — aqui nao exige nada.
SERVICE_WORKER_JS = """/* BEEonBOARD — service worker do aviso de nova mensagem.
   Recebe o push do servidor e mostra a notificacao do sistema. Roda fora da pagina:
   funciona com a aba em segundo plano e com o navegador fechado. */

self.addEventListener('push', function (event) {
  var dados = {};
  try { dados = event.data ? event.data.json() : {}; } catch (e) { dados = {}; }
  var titulo = dados.title || 'Nova mensagem';
  var opcoes = {
    body: dados.body || 'Voce recebeu uma nova mensagem.',
    icon: dados.icon || '%(icone)s',
    badge: dados.icon || '%(icone)s',
    // Agrupa os avisos da MESMA conversa: cinco mensagens seguidas nao viram cinco
    // pop-ups empilhados, e `renotify` garante que o aviso novo volte a chamar.
    tag: dados.tag || 'beeonboard',
    renotify: true,
    data: {url: dados.url || '%(conversas)s', conversationId: dados.conversation_id || null}
  };
  event.waitUntil(self.registration.showNotification(titulo, opcoes));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var destino = (event.notification.data && event.notification.data.url) || '%(conversas)s';
  event.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(function (abas) {
      // Se a pessoa ja tem o sistema aberto, FOCA aquela aba em vez de abrir outra —
      // e avisa a pagina qual conversa abrir, para nao recarregar a tela inteira.
      for (var i = 0; i < abas.length; i++) {
        var aba = abas[i];
        if (aba.url.indexOf('%(conversas)s') !== -1) {
          aba.postMessage({tipo: 'abrir-conversa',
                           id: event.notification.data && event.notification.data.conversationId});
          return aba.focus();
        }
      }
      return self.clients.openWindow(destino);
    })
  );
});
"""


def service_worker_view(request):
    """Entrega o `sw.js` com escopo da aplicacao inteira (ver comentario acima)."""
    from django.templatetags.static import static
    from django.urls import reverse

    corpo = SERVICE_WORKER_JS % {
        'icone': static('images/logo-beeonboard.png'),
        'conversas': reverse('conversations'),
    }
    resposta = HttpResponse(corpo, content_type='application/javascript; charset=utf-8')
    # O navegador revalida o service worker a cada 24h no maximo; sem isto um sw.js
    # antigo poderia ficar preso em cache depois de um deploy.
    resposta['Cache-Control'] = 'no-cache'
    resposta['Service-Worker-Allowed'] = '/'
    return resposta


@login_required
def push_public_key_view(request):
    """A chave publica VAPID (o navegador precisa dela para se inscrever).

    E publica por definicao — vai no JavaScript de qualquer forma. A PRIVADA nunca
    sai do servidor.
    """
    return JsonResponse({
        'ok': vapid_configured(),
        'public_key': settings.WEBPUSH_VAPID_PUBLIC_KEY if vapid_configured() else '',
    })


@login_required
@require_POST
def push_subscribe_view(request):
    """Guarda a inscricao deste navegador para o usuario logado.

    A inscricao e sempre de QUEM ESTA LOGADO — o endpoint nao aceita id de usuario,
    entao ninguem inscreve o navegador no nome de outra pessoa.
    """
    try:
        dados = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Dados invalidos.'}, status=400)

    endpoint = (dados.get('endpoint') or '').strip()
    chaves = dados.get('keys') or {}
    p256dh = (chaves.get('p256dh') or '').strip()
    auth = (chaves.get('auth') or '').strip()
    if not endpoint or not p256dh or not auth:
        return JsonResponse({'ok': False, 'error': 'Inscricao incompleta.'}, status=400)

    # `endpoint` e unico no mundo: se este navegador ja estava inscrito (inclusive por
    # outra pessoa no mesmo computador), a linha passa a ser de quem esta logado agora.
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
            'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:200],
        },
    )
    return JsonResponse({'ok': True})


@login_required
@require_POST
def push_unsubscribe_view(request):
    """Apaga a inscricao deste navegador (a pessoa desligou o aviso)."""
    try:
        dados = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        dados = {}
    endpoint = (dados.get('endpoint') or '').strip()
    if endpoint:
        # So apaga inscricao DO PROPRIO usuario: endpoint de outra pessoa nao e alvo.
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({'ok': True})
