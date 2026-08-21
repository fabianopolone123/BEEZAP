"""Contexto de template disponivel em TODAS as telas.

`brand` leva a identidade visual de quem esta logado para a barra lateral (ver
`templates/accounts/_sidebar.html`), sem que cada view precise montar isso:

- usuario de uma empresa cliente → logo e nome DA EMPRESA dele (o que o gestor
  master cadastrou na tela Clientes, ou o proprio ADM na aba Marca). Sem logo,
  aparecem as iniciais da empresa num circulo com a cor de destaque dela;
- gestor master → SEMPRE a marca do BEEonBOARD.

**Por que o master nunca "veste" a marca do cliente.** Antes, ao entrar no painel de
um cliente (modo suporte), a barra lateral trocava logo, nome e cor de destaque pelos
do cliente. O efeito era o contrario do pretendido: batendo o olho, a tela dizia "voce
e a PPM" em vez de "voce, master, esta olhando a PPM" — e o master podia se confundir
sobre de quem era a conta em que estava mexendo. Hoje a identidade da barra lateral e
sempre a dele, e o cliente aparece como CONTEXTO (`brand.support_company`), no seletor
do topo da barra e na faixa de modo suporte do `base.html`.

Registrado em `config/settings.py` (TEMPLATES → context_processors).
"""

from .tenancy import current_company, is_master

# Marca da propria plataforma (BEEonBOARD), usada para o master e como retaguarda.
PLATFORM_NAME = 'BEEonBOARD'
PLATFORM_SUBTITLE = 'Central de atendimento'
MASTER_SUBTITLE = 'Gestão de clientes'


def _empresas_para_troca(company_atual):
    """Clientes que o master pode abrir pelo seletor da barra lateral.

    Existe para trocar de cliente em UM clique. Antes era preciso sair do painel,
    voltar para a tela Clientes e entrar no outro — tres passos para uma coisa que
    o master faz o tempo todo.

    Só empresas ATIVAS: entrar no painel de uma empresa desativada nao faz sentido
    (o webhook dela nem recebe mais). Uma consulta leve, so para o master.
    """
    from .models import Company

    empresas = (
        Company.objects
        .filter(is_active=True)
        .only('id', 'name', 'legal_name', 'slug', 'is_default')
        .order_by('name')
    )
    atual_id = company_atual.pk if company_atual is not None else None
    return [
        {
            'id': empresa.pk,
            'nome': empresa.display_name,
            'is_default': empresa.is_default,
            'ativa': empresa.pk == atual_id,
        }
        for empresa in empresas
    ]


def branding(request):
    """Identidade visual de quem esta logado (ou do BEEonBOARD)."""
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return {}

    if is_master(user):
        # MODO SUPORTE: o master entrou no painel de um cliente. A marca continua
        # sendo a DELE; o cliente vai como contexto (ver o docstring do modulo).
        support_company = current_company(request)
        return {
            'brand': {
                'company': None,
                'name': PLATFORM_NAME,
                'subtitle': MASTER_SUBTITLE,
                'logo_url': '',
                'initials': '',
                'accent': '',
                'accent_text': '',
                'is_master': True,
                'support_company': support_company,
                'switch_companies': _empresas_para_troca(support_company),
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
                'support_company': None,
                'switch_companies': [],
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
            'support_company': None,
            'switch_companies': [],
        }
    }
