"""Contexto de template disponivel em TODAS as telas.

`brand` leva a identidade visual de quem esta logado para a barra lateral (ver
`templates/accounts/_sidebar.html`), sem que cada view precise montar isso:

- usuario de uma empresa cliente → logo e nome DA EMPRESA dele (o que o gestor
  master cadastrou na tela Clientes). Sem logo cadastrado, aparecem as iniciais da
  empresa num circulo com a cor de destaque dela;
- gestor master → a marca do BEEonBOARD com o rotulo "Gestão de clientes".

Registrado em `config/settings.py` (TEMPLATES → context_processors).
"""

from .tenancy import current_company, is_master

# Marca da propria plataforma (BEEonBOARD), usada para o master e como retaguarda.
PLATFORM_NAME = 'BEEonBOARD'
PLATFORM_SUBTITLE = 'Central de atendimento'
MASTER_SUBTITLE = 'Gestão de clientes'


def branding(request):
    """Identidade visual da empresa de quem esta logado (ou do BEEonBOARD)."""
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return {}

    if is_master(user):
        # MODO SUPORTE: quando o master entra no painel de um cliente, a barra
        # lateral mostra de quem e o painel para ele nao se confundir.
        support_company = current_company(request)
        return {
            'brand': {
                'company': support_company,
                'name': support_company.display_name if support_company else PLATFORM_NAME,
                'subtitle': (f'Suporte · {MASTER_SUBTITLE}' if support_company
                             else MASTER_SUBTITLE),
                'logo_url': support_company.logo_url if support_company else '',
                'initials': support_company.initials if support_company else '',
                'accent': support_company.accent_color if support_company else '',
                'accent_text': support_company.accent_text_color if support_company else '',
                'is_master': True,
                'support_company': support_company,
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
                'accent_text': '',
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
            'accent_text': company.accent_text_color,
            'is_master': False,
        }
    }
