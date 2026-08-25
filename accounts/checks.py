"""System checks do BEEonBOARD (rodam em `manage.py check` e no deploy).

Objetivo: falhar cedo/avisar quando falta uma dependencia de SISTEMA que nao vem
pelo pip, para nao descobrir so em producao com o envio quebrando.
"""
import shutil

from django.conf import settings
from django.core.checks import Warning, register


@register()
def ffmpeg_available_check(app_configs, **kwargs):
    """Avisa (nao bloqueia) quando o ffmpeg nao esta no PATH.

    O BEEonBOARD usa ffmpeg para converter o audio gravado no navegador (.webm -> .ogg)
    e imagens nao suportadas pela W-API (webp/gif/bmp/heic... -> .jpg) antes de
    enviar. Sem ele, esses envios falham (JPG/PNG, video, documento e texto seguem
    funcionando)."""
    if shutil.which('ffmpeg'):
        return []
    return [
        Warning(
            'ffmpeg nao encontrado no PATH.',
            hint='Instale com: sudo apt install -y ffmpeg (Linux). O envio de audio '
                 'gravado e de imagens webp/gif/bmp/heic pela conversa depende dele. '
                 'Ver requirements.txt e docs/DEPLOY.md.',
            id='beezap.W001',
        )
    ]


@register()
def wapi_env_credentials_check(app_configs, **kwargs):
    """Avisa quando o `.env` ainda tem credencial de W-API e ja existe mais de um
    cliente.

    As variaveis `WAPI_INSTANCE_ID`/`WAPI_TOKEN` sao heranca da epoca de um cliente
    unico. Hoje a credencial de cada empresa fica no BANCO (tela WhatsApp), e o
    fallback para o ambiente vale SO para a empresa padrao — justamente para um
    cliente novo sem credencial nao acabar enviando pelo WhatsApp de outro (ver
    `WapiConfiguration.usa_credencial_do_ambiente`).

    Com o fallback restrito, essas variaveis deixaram de ser necessarias: manter
    credencial no `.env` num ambiente multiempresa so confunde quem for diagnosticar
    um envio.
    """
    if not (settings.WAPI_INSTANCE_ID or settings.WAPI_TOKEN):
        return []
    try:
        from .models import Company
        empresas = Company.objects.count()
    except Exception:
        # Banco ainda nao migrado (ex.: primeiro `migrate`): nada a checar.
        return []
    if empresas <= 1:
        return []
    return [
        Warning(
            'O .env tem credencial de W-API (WAPI_INSTANCE_ID/WAPI_TOKEN) e o sistema '
            'ja atende mais de um cliente.',
            hint='A credencial de cada empresa fica no banco (tela WhatsApp/W-API). O '
                 'fallback para o ambiente vale so para a empresa padrao. Esvazie '
                 'essas variaveis no .env para nao confundir diagnostico de envio.',
            id='beezap.W002',
        )
    ]

@register()
def webpush_vapid_keys_check(app_configs, **kwargs):
    """Avisa quando as chaves VAPID nao estao no ambiente.

    Sem elas o aviso de nova mensagem (Web Push) fica INERTE: ninguem recebe pop-up
    com a aba em segundo plano — e nada na tela indicaria isso, porque a inscricao do
    navegador falha em silencio. Em desenvolvimento e normal nao ter; em producao e
    perda de funcionalidade, entao vale o aviso no `check` do deploy.
    """
    from accounts.webpush import vapid_configured

    if vapid_configured():
        return []
    return [
        Warning(
            'Chaves VAPID de Web Push ausentes: o aviso de nova mensagem esta desligado.',
            hint='Gere o par e ponha WEBPUSH_VAPID_PUBLIC_KEY / '
                 'WEBPUSH_VAPID_PRIVATE_KEY no .env (ver config/settings.py e '
                 'docs/DEPLOY.md). Sem elas o pop-up nao chega com a aba em segundo plano.',
            id='beezap.W003',
        )
    ]


# A partir daqui a busca por CONTEUDO (a tela Pesquisar) comeca a pesar. O numero nao
# e chute: medido em banco sintetico, o `LIKE '%termo%'` cresce LINEAR — 500 mil
# mensagens = ~63 ms, 1 milhao = ~130 ms, 2 milhoes = ~254 ms (por busca). Ate 500 mil
# ninguem percebe; dali para cima a conta piora sozinha.
LIMITE_MENSAGENS_BUSCA = 500_000


@register()
def search_volume_check(app_configs, **kwargs):
    """Avisa quando o volume de mensagens chega no ponto de repensar a busca.

    A tela Pesquisar (seccao 5.5 do CONTEXTO) procura dentro do texto das mensagens
    com `icontains`, que no SQLite e VARREDURA — indice comum nao serve para
    `LIKE '%termo%'`. Isso e barato hoje e vai ficando caro sozinho, e o pior jeito de
    descobrir seria um cliente reclamando que a busca demora.

    O aviso existe para a decisao chegar ANTES do problema, com as duas saidas ja
    escritas: FTS5 do SQLite (busca por PALAVRA, ~150x mais rapido, mas para de achar
    pedaco no meio da palavra) ou migrar para PostgreSQL, que tem busca de texto nativa
    e integrada ao Django. Nao ha o que "otimizar" com indice comum — esse e o erro
    natural de quem ve a busca lenta.
    """
    try:
        from .models import Message
        total = Message.objects.count()
    except Exception:
        # Banco ainda nao migrado (ex.: primeiro `migrate`): nada a checar.
        return []
    if total < LIMITE_MENSAGENS_BUSCA:
        return []
    return [
        Warning(
            'A tela Pesquisar pode ficar lenta: %s mensagens no banco.' % f'{total:,}'.replace(',', '.'),
            hint='A busca por conteudo e uma varredura (LIKE), que cresce linear com o '
                 'volume. Indice comum NAO resolve. As duas saidas estao na secao 5.5 do '
                 'docs/CONTEXTO.md: FTS5 do SQLite (rapido, mas indexa palavra inteira) '
                 'ou migrar para PostgreSQL (busca de texto nativa, integrada ao Django).',
            id='beezap.W004',
        )
    ]
