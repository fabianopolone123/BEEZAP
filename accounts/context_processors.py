"""Contexto de template disponivel em TODAS as telas.

`brand` leva a identidade visual de quem esta logado para a barra lateral (ver
`templates/accounts/_sidebar.html`), sem que cada view precise montar isso:

- usuario de uma empresa cliente → logo e nome DA EMPRESA dele (o que o gestor
  master cadastrou na tela Clientes). Sem logo cadastrado, aparecem as iniciais da
  empresa num circulo com a cor de destaque dela;
- gestor master → a marca do BEEZap com o rotulo "Gestão de clientes".

Registrado em `config/settings.py` (TEMPLATES → context_processors).
"""

from .tenancy import current_company, is_master

# Marca do proprio BEEZAP, usada para o master e como retaguarda.
PLATFORM_NAME = 'BEEZap'
PLATFORM_SUBTITLE = 'Central de atendimento'
MASTER_SUBTITLE = 'Gestão de clientes'


def branding(request):
    """Identidade visual da empresa de quem esta logado (ou do BEEZap)."""
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return {}

    if is_master(user):
        return {
            'brand': {
                'company': None,
                'name': PLATFORM_NAME,
                'subtitle': MASTER_SUBTITLE,
                'logo_url': '',
                'initials': '',
                'accent': '',
                'is_master': True,
            }
        }

    company = current_company(request)
    if company is None:
        return {
            'brand': {
                'company': None,
                'name': PLATFORM_NAME,
                'subtitle': PLATFORM_SUBTITLE,
                'logo_url': '',
                'initials': '',
                'accent': '',
                'is_master': False,
            }
        }

    return {
        'brand': {
            'company': company,
            'name': company.display_name,
            'subtitle': PLATFORM_SUBTITLE,
            'logo_url': company.logo_url,
            'initials': company.initials,
            'accent': company.accent_color,
            'is_master': False,
        }
    }
