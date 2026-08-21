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
