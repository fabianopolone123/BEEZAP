"""Tag `{% asset %}` — cache-busting de CSS/JS sem numero na mao.

Por que existe: o padrao do projeto era incrementar um `?v=N` na mao em cada link de
CSS. Na pratica isso nunca ficava certo, porque o MESMO arquivo e carregado por varios
templates. No pente fino do projeto, `dashboard.css` estava com `?v=6` em 8 templates
e **sem versao nenhuma** em outros 7 (attendants, chatbot_settings, contacts,
openai_settings, permissions, sectors, wapi_settings); `attendants.css` tinha versao em
1 e faltava em 5; `login.css`, `password_recovery.css` e `wapi_settings.css` nao tinham
versao nenhuma.

Como o Nginx serve a pasta `static/` direto, isso significa que editar
`dashboard.css` e subir para `?v=7` limpava o cache de 8 telas e deixava as outras 7
com o arquivo ANTIGO no navegador — exatamente o sintoma "mudei o CSS e nao aparece",
que o projeto ja tinha documentado como armadilha. E cada bump exigia editar 8
arquivos, o tipo de tarefa que sempre passa incompleta.

A tag resolve a raiz: a versao vem da **data de modificacao do proprio arquivo**.
Editar o CSS **e** publicar a nova versao passam a ser a mesma acao, sem ninguem
precisar lembrar de nada:

    {% load beeonboard_assets %}
    <link rel="stylesheet" href="{% asset 'css/dashboard.css' %}">
    -> /static/css/dashboard.css?v=1a2b3c4d

O valor e calculado uma vez por processo (`_CACHE`). Em producao os workers do gunicorn
sao reciclados em todo deploy (ver docs/DEPLOY.md), entao a versao acompanha o
`git pull` sozinha. Em desenvolvimento, `DEBUG=True` desliga o cache e o valor
acompanha cada salvamento.

Arquivo inexistente cai no `{% static %}` puro, sem levantar erro: link quebrado nao
pode derrubar a pagina inteira.
"""

import hashlib
import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# {caminho relativo: sufixo de versao} — uma leitura de disco por arquivo, por processo.
_CACHE = {}


def _versao_do_arquivo(caminho_relativo):
    """Sufixo curto derivado da data de modificacao e do tamanho do arquivo."""
    caminho_absoluto = finders.find(caminho_relativo)
    if not caminho_absoluto:
        return ''
    if isinstance(caminho_absoluto, (list, tuple)):
        caminho_absoluto = caminho_absoluto[0]
    try:
        info = os.stat(caminho_absoluto)
    except OSError:
        return ''
    marca = f'{info.st_mtime_ns}-{info.st_size}'
    return hashlib.blake2s(marca.encode('utf-8'), digest_size=4).hexdigest()


@register.simple_tag
def asset(caminho_relativo):
    """URL do estatico com a versao do arquivo (ex.: `css/dashboard.css`)."""
    url = static(caminho_relativo)
    if settings.DEBUG:
        versao = _versao_do_arquivo(caminho_relativo)
    else:
        if caminho_relativo not in _CACHE:
            _CACHE[caminho_relativo] = _versao_do_arquivo(caminho_relativo)
        versao = _CACHE[caminho_relativo]
    if not versao:
        return url
    separador = '&' if '?' in url else '?'
    return f'{url}{separador}v={versao}'
