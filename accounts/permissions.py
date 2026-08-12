"""Permissoes de menu por perfil (e por usuario) — controla quais botoes da barra
lateral cada perfil ve E acessa.

O ADMINISTRADOR tem sempre acesso total (nao e editavel, para nunca se trancar
fora do sistema) DENTRO DA EMPRESA dele. Os demais perfis (`usuario`, `leitor`)
tem um conjunto padrao (abaixo) que o admin pode ajustar na tela Permissoes; alem
disso, um usuario especifico pode ter uma personalizacao propria
(UserMenuPermission) que sobrepoe o padrao do perfil.

O GESTOR MASTER (dono da plataforma, ver `accounts/tenancy.py`) e um caso
separado: ele NAO opera o atendimento de ninguem. O menu dele tem as telas da
PLATAFORMA — "Clientes" (cadastro das empresas) e "Inteligência (IA)" (a
configuracao do GPT, que e uma so para todos os clientes) — e, quando ele entra no
painel de um cliente (modo suporte), as telas de configuracao daquele cliente.
Nenhuma feature de atendimento fica liberada para ele e ele nao enxerga conversa
nenhuma.

O que e TECNICO nao fica com o cliente: as credenciais da W-API (instancia e token
de cada empresa) e a API Key do GPT sao do master. O cliente configura apenas o
chatbot de menu e escolhe o modo de primeiro atendimento (desligado / chatbot / IA).

As "features" abaixo sao os botoes reais do menu. `permissions` (a propria tela) e
exclusiva do admin; `clients` e a tela de IA sao exclusivas do master. Nenhuma
delas entra na matriz de toggles.
"""

# Botoes reais do menu, na ordem de exibicao. Cada um tem um icone (emoji) para a
# tela de Permissoes ficar visual/didatica.
MENU_FEATURES = [
    {'key': 'dashboard',     'label': 'Dashboard',      'url_name': 'dashboard',     'icon': '🏠'},
    {'key': 'conversations', 'label': 'Conversas',      'url_name': 'conversations', 'icon': '💬'},
    {'key': 'contacts',      'label': 'Contatos',       'url_name': 'contacts',      'icon': '👥'},
    {'key': 'attendants',    'label': 'Atendentes',     'url_name': 'attendants',    'icon': '🎧'},
    {'key': 'sectors',       'label': 'Setores',        'url_name': 'sectors',       'icon': '🗂️'},
    # Para o CLIENTE, Configuracoes = a tela Atendimento (chatbot de menu + o
    # seletor de modo). As telas TECNICAS (WhatsApp/W-API e Inteligencia (IA)) sao do
    # gestor master — ver MASTER_ONLY_ITEMS e docs/CONTEXTO.md secao 16.
    {'key': 'settings',      'label': 'Configurações',  'url_name': 'atendimento',   'icon': '⚙️'},
]
ALL_FEATURE_KEYS = [f['key'] for f in MENU_FEATURES]

# Item exclusivo do admin (fora da matriz de toggles).
PERMISSIONS_ITEM = {'label': 'Permissões', 'url_name': 'permissions'}

# Itens exclusivos do GESTOR MASTER (fora da matriz de toggles — nenhum perfil de
# cliente pode recebe-los).
#
# `Clientes` = cadastro/administracao das empresas.
# `Inteligência (IA)` = configuracao do GPT, que e UMA para toda a plataforma (a API
# Key e do master, que paga a conta da OpenAI). Cada empresa so decide SE usa IA,
# chatbot ou nada, no seletor de modo da tela Atendimento dela.
CLIENTS_ITEM = {'label': 'Clientes', 'url_name': 'clients'}
AI_ITEM = {'label': 'Inteligência (IA)', 'url_name': 'openai-settings'}
MASTER_ONLY_ITEMS = [CLIENTS_ITEM, AI_ITEM]

# MODO SUPORTE: o que o master pode acessar quando "entra no painel" de um cliente.
# Sao apenas as telas de CONFIGURACAO — o master ajusta o WhatsApp (credenciais da
# W-API daquele cliente), o chatbot, os setores, os atendentes e as permissoes dele.
# (A tela de IA nao entra aqui: e da plataforma, nao do cliente.)
#
# `conversations` e `contacts` ficam DE FORA de proposito: sao os dados pessoais dos
# clientes finais da empresa, e a regra do projeto e que o master administra sem ler
# o atendimento de ninguem (ver accounts/tenancy.py). `dashboard` tambem fica fora
# (indicadores do movimento do cliente).
MASTER_SUPPORT_KEYS = {'settings', 'sectors', 'attendants', 'permissions'}

# Perfis que aparecem na tela para edicao (o admin e sempre acesso total).
EDITABLE_ROLES = [
    {'role': 'usuario', 'label': 'Usuário'},
    {'role': 'leitor', 'label': 'Leitor'},
]

# Conjunto PADRAO por perfil (usado quando nao ha configuracao salva no banco).
DEFAULT_ROLE_KEYS = {
    'adm': list(ALL_FEATURE_KEYS),
    'usuario': ['conversations', 'contacts'],
    'leitor': ['conversations', 'contacts'],
}


def role_default_keys(role):
    return list(DEFAULT_ROLE_KEYS.get(role, ['conversations']))


def is_read_only(user):
    """Perfil SOMENTE LEITURA (`leitor`): enxerga as telas liberadas em "Botoes do
    perfil", mas NAO pode executar nenhuma acao que altere dados — enviar mensagem,
    assumir/encerrar/transferir conversa, nomear contato, cadastrar/editar/excluir
    contato/atendente/setor, salvar configuracoes, etc. So visualiza."""
    return getattr(user, 'role', None) == 'leitor'


def role_allowed_keys(role, company=None):
    """Conjunto de botoes de um PERFIL DENTRO DE UMA EMPRESA (config salva ou
    padrao). adm = tudo. Cada empresa tem a sua propria linha por perfil, por isso
    a consulta e sempre filtrada pela empresa."""
    if role == 'adm':
        return set(ALL_FEATURE_KEYS)
    if company is None:
        return set(role_default_keys(role))
    from .models import RoleMenuPermission
    row = RoleMenuPermission.objects.filter(company=company, role=role).first()
    if row is not None:
        return set(row.allowed_keys or [])
    return set(role_default_keys(role))


def allowed_keys_for(user):
    """Conjunto EFETIVO de botoes de um usuario: adm = tudo; senao a personalizacao
    do usuario (se houver) ou o padrao do perfil. O MASTER nao tem feature de
    atendimento (o botao dele, Clientes, fica fora desta matriz)."""
    if not getattr(user, 'is_authenticated', False):
        return set()
    if user.role == 'master':
        return set()
    if user.role == 'adm':
        return set(ALL_FEATURE_KEYS)
    from .models import UserMenuPermission
    override = UserMenuPermission.objects.filter(user=user).first()
    if override is not None:
        return set(override.allowed_keys or [])
    return role_allowed_keys(user.role, getattr(user, 'company', None))


def user_can_access(user, key, in_company=False):
    """O usuario pode acessar a feature/botao `key`?

    `in_company` = o gestor master esta no MODO SUPORTE (entrou no painel de um
    cliente). Nesse modo ele acessa apenas as telas de configuracao
    (`MASTER_SUPPORT_KEYS`) — nunca Conversas/Contatos.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    role = getattr(user, 'role', None)
    # Telas da PLATAFORMA (gestao de clientes e configuracao do GPT): so o master, e
    # nenhum perfil de cliente as acessa.
    if key in ('clients', 'platform_ai'):
        return role == 'master'
    if role == 'master':
        return in_company and key in MASTER_SUPPORT_KEYS
    if key == 'permissions':
        return role == 'adm'
    if role == 'adm':
        return True
    return key in allowed_keys_for(user)


def nav_items_for(user, active_label, in_company=False):
    """Itens do menu que o usuario pode ver, no formato esperado pelo template."""
    role = getattr(user, 'role', None)
    # O master tem um menu proprio: a gestao das empresas clientes e, quando esta no
    # painel de um cliente (modo suporte), as telas de configuracao dele.
    if role == 'master':
        items = [
            {
                'label': item['label'],
                'url_name': item['url_name'],
                'href': item['url_name'],
                'active': item['label'] == active_label,
            }
            for item in MASTER_ONLY_ITEMS
        ]
        if in_company:
            items += [
                {
                    'label': f['label'],
                    'url_name': f['url_name'],
                    'href': f['url_name'],
                    'active': f['label'] == active_label,
                }
                for f in MENU_FEATURES if f['key'] in MASTER_SUPPORT_KEYS
            ]
            items.append({
                'label': PERMISSIONS_ITEM['label'],
                'url_name': PERMISSIONS_ITEM['url_name'],
                'href': PERMISSIONS_ITEM['url_name'],
                'active': PERMISSIONS_ITEM['label'] == active_label,
            })
        return items
    allowed = allowed_keys_for(user)
    is_adm = role == 'adm'
    items = []
    for f in MENU_FEATURES:
        if is_adm or f['key'] in allowed:
            items.append({
                'label': f['label'],
                'url_name': f['url_name'],
                'href': f['url_name'],
                'active': f['label'] == active_label,
            })
    if is_adm:
        items.append({
            'label': PERMISSIONS_ITEM['label'],
            'url_name': PERMISSIONS_ITEM['url_name'],
            'href': PERMISSIONS_ITEM['url_name'],
            'active': PERMISSIONS_ITEM['label'] == active_label,
        })
    return items


def first_landing_url_name(user):
    """Primeiro botao acessivel — para onde mandar o usuario apos o login quando
    ele nao tem acesso ao Dashboard."""
    if getattr(user, 'role', None) == 'master':
        return CLIENTS_ITEM['url_name']
    for f in MENU_FEATURES:
        if user_can_access(user, f['key']):
            return f['url_name']
    return 'conversations'


# ─────────────────────────────────────────────────────────────────────────────
# Visibilidade das CONVERSAS (quem ve quais chats) + escopo do historico.
# ─────────────────────────────────────────────────────────────────────────────

def user_sector_ids(user):
    """IDs dos setores dos quais o usuario faz parte (via perfil de atendente)."""
    from .models import Sector
    return list(Sector.objects.filter(attendants__user=user).values_list('id', flat=True))


# Alcance de visualizacao (ver models.ConversationViewScope). Ordem crescente de
# permissividade — usada para resolver o efetivo quando o usuario esta em varios
# setores (vence o MAIS permissivo).
VIEW_SCOPE_RANK = {'own': 0, 'sector_open': 1, 'sector_all': 2, 'all': 3}
DEFAULT_VIEW_SCOPE = 'sector_open'  # comportamento historico (padrao de fabrica)


def effective_view_scope(user):
    """Alcance EFETIVO de conversas do usuario. Admin = 'all'. Personalizacao do
    usuario (UserConversationView.view_scope) > o MAIS PERMISSIVO entre os setores
    dele > padrao de fabrica ('sector_open')."""
    if not getattr(user, 'is_authenticated', False):
        return DEFAULT_VIEW_SCOPE
    if user.role == 'adm':
        return 'all'
    from .models import Sector, UserConversationView
    override = UserConversationView.objects.filter(user=user).first()
    if override is not None and override.view_scope:
        return override.view_scope
    scopes = list(
        Sector.objects.filter(attendants__user=user)
        .values_list('view_scope', flat=True)
    )
    if scopes:
        return max(scopes, key=lambda s: VIEW_SCOPE_RANK.get(s, 0))
    return DEFAULT_VIEW_SCOPE


def visible_conversations_q(user):
    """Q das conversas que um usuario NAO-admin pode ver, conforme o alcance efetivo
    (effective_view_scope):
    - `own`         → so as DIRETAS atribuidas a ele (qualquer status);
    - `sector_open` → atribuidas a ele OU do(s) setor(es) dele E ainda NAO fechada
                      (comportamento historico: cada um so ve os PROPRIOS finalizados);
    - `sector_all`  → atribuidas a ele OU do(s) setor(es) dele (inclui finalizadas de
                      outros do setor);
    - `all`         → todas as conversas diretas, de qualquer setor.

    GRUPOS: independem do alcance — seguem sempre a liberacao individual da aba
    Grupos (GroupAccess: por setor OU por usuario). (O admin ve tudo, ver
    visible_conversations.)"""
    from django.db.models import Q
    sector_ids = user_sector_ids(user)
    scope = effective_view_scope(user)
    assigned = Q(assigned_attendant__user=user)
    if scope == 'all':
        direct = Q(chat_type='private')
    elif scope == 'sector_all':
        direct = Q(chat_type='private') & (assigned | Q(sector_id__in=sector_ids))
    elif scope == 'own':
        direct = Q(chat_type='private') & assigned
    else:  # 'sector_open' (padrao)
        direct = Q(chat_type='private') & (
            assigned | (Q(sector_id__in=sector_ids) & ~Q(status='closed'))
        )
    group = Q(chat_type='group') & (
        Q(access__sectors__id__in=sector_ids) | Q(access__users=user)
    )
    return direct | group


def visible_conversations(user, queryset):
    """Filtra um queryset de Conversation pelo que o usuario pode ver. Admin ve tudo
    DA EMPRESA dele; o gestor master nao ve conversa nenhuma (ele administra os
    clientes, nao le o atendimento deles — ver accounts/tenancy.py)."""
    if not getattr(user, 'is_authenticated', False):
        return queryset.none()
    if user.role == 'master':
        return queryset.none()
    company = getattr(user, 'company', None)
    if company is None:
        return queryset.none()
    queryset = queryset.filter(company=company)
    if user.role == 'adm':
        return queryset
    return queryset.filter(visible_conversations_q(user)).distinct()


def can_see_conversation(user, conversation):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.role == 'master':
        return False
    from .models import Conversation
    return visible_conversations(
        user, Conversation.objects.filter(pk=conversation.pk)
    ).exists()


def history_full_for(user):
    """O usuario ve a conversa INTEIRA (True) ou so o atendimento atual (False)?
    Admin sempre ve tudo. Personalizacao do usuario
    (UserConversationView.view_full_history, se definida) > algum setor dele com
    "ver conversa inteira" ligado > padrao (False). Configurado na aba
    "Visualização de conversas" em Permissoes."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.role == 'adm':
        return True
    from .models import Sector, UserConversationView
    override = UserConversationView.objects.filter(user=user).first()
    if override is not None and override.view_full_history is not None:
        return bool(override.view_full_history)
    return Sector.objects.filter(attendants__user=user, view_full_history=True).exists()
