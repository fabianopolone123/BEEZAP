from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # Registra os system checks do BEEZAP (ex.: aviso de ffmpeg ausente).
        from . import checks  # noqa: F401
