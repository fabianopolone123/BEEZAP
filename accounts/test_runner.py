"""Runner de testes do projeto — troca o hash de senha por um rapido.

Por que existe: a suite cria usuario em quase todo teste, e o PBKDF2 do Django
(proposital e lentamente caro) dominava o tempo total — 371 testes levavam mais de
10 MINUTOS. Uma suite que demora tanto deixa de ser rodada antes do commit, que e
exatamente o que a regra fixa do projeto pede (ver CLAUDE.md).

A troca vale SO durante os testes: o `PASSWORD_HASHERS` de producao continua o
padrao do Django, entao nenhuma senha real e guardada com hash fraco.

Registrado em `config/settings.py` (TEST_RUNNER), entao o comando continua sendo o
mesmo: `python manage.py test`.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class FastPasswordHasherRunner(DiscoverRunner):
    """DiscoverRunner padrao + hash de senha barato durante os testes."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.PASSWORD_HASHERS = [
            'django.contrib.auth.hashers.MD5PasswordHasher',
        ]
