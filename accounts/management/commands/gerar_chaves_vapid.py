"""Gera o par de chaves VAPID do aviso de nova mensagem (Web Push).

Uso (uma vez, no servidor):
    python manage.py gerar_chaves_vapid

Copie as duas linhas para o `.env` e reinicie o servico. A chave PRIVADA nunca vai
para o Git e nunca sai do servidor; a publica vai no JavaScript de qualquer forma.

Existe como comando porque a receita a mao e obscura (ponto X9.62 nao comprimido para
a publica, inteiro de 32 bytes para a privada, tudo em base64url) e errar nisso quebra
o aviso sem dizer o porque.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Gera o par de chaves VAPID (Web Push) para colar no .env.'

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.primitives import serialization
            from py_vapid import Vapid01
            from py_vapid.utils import b64urlencode, num_to_bytes
        except ImportError:
            self.stdout.write(self.style.ERROR(
                'pywebpush nao instalado. Rode: pip install -r requirements.txt'
            ))
            return

        vapid = Vapid01()
        vapid.generate_keys()
        publica = b64urlencode(vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        ))
        privada = b64urlencode(
            num_to_bytes(vapid.private_key.private_numbers().private_value, 32)
        )

        self.stdout.write(self.style.SUCCESS('Par de chaves VAPID gerado. Cole no .env:'))
        self.stdout.write('')
        self.stdout.write(f'WEBPUSH_VAPID_PUBLIC_KEY={publica}')
        self.stdout.write(f'WEBPUSH_VAPID_PRIVATE_KEY={privada}')
        self.stdout.write('WEBPUSH_VAPID_SUBJECT=mailto:seu-email@dominio.com')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            'A chave PRIVADA e segredo: nao versione, nao mande por chat. '
            'Trocar o par depois OBRIGA todo mundo a se inscrever de novo '
            '(as inscricoes antigas param de aceitar o envio).'
        ))
