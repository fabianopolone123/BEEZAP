from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


User = get_user_model()


class EmailBackend(ModelBackend):
    """Login por e-mail, sem diferenciar maiusculas."""

    def authenticate(self, request, email=None, password=None, **kwargs):
        if email is None:
            email = kwargs.get(User.USERNAME_FIELD)
        if not email or not password:
            return None
        # `MultipleObjectsReturned` era um 500 na tela de LOGIN: `email` e unico no
        # banco de forma sensivel a caixa, entao `Joao@x.com` e `joao@x.com` podiam
        # coexistir (conta criada pelo shell ou pelo admin) e a busca `iexact`
        # encontrava as duas. Hoje `User.save()` normaliza para minusculo, e aqui
        # ficamos tolerantes ao que ja estiver gravado: tenta cada candidata.
        candidatos = list(User.objects.filter(email__iexact=email).order_by('pk'))
        for user in candidatos:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None
