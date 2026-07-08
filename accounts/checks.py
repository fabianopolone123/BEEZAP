"""System checks do BEEZAP (rodam em `manage.py check` e no deploy).

Objetivo: falhar cedo/avisar quando falta uma dependencia de SISTEMA que nao vem
pelo pip, para nao descobrir so em producao com o envio quebrando.
"""
import shutil

from django.core.checks import Warning, register


@register()
def ffmpeg_available_check(app_configs, **kwargs):
    """Avisa (nao bloqueia) quando o ffmpeg nao esta no PATH.

    O BEEZAP usa ffmpeg para converter o audio gravado no navegador (.webm -> .ogg)
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
