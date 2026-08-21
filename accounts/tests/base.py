"""Base dos testes: imports comuns e o helper de empresa.

`default_company()` esta aqui porque quase todo teste precisa dele: com o
multiempresa, setor/atendente/contato/conversa pertencem OBRIGATORIAMENTE a uma
empresa, e os usuarios operacionais tambem.
"""

import json as _json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.auth.hashers import check_password
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from wapi.parser import (
    is_group_jid,
    is_ignorable_jid,
    is_status_or_broadcast,
    normalize_phone,
    normalize_wapi_message_context,
)

from ..models import Attendant, PasswordResetCode, User


def default_company():
    """Empresa cliente usada pelos testes.

    Com o multiempresa, setor/atendente/contato/conversa pertencem OBRIGATORIAMENTE
    a uma empresa, e os usuarios operacionais tambem. Os testes usam a empresa
    padrao (a mesma que a migration 0031 cria).
    """
    from ..models import Company
    return Company.get_default()
