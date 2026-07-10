"""Permissoes de menu por perfil (e por usuario) — controla quais botoes da barra
lateral cada perfil ve E acessa.

O ADMINISTRADOR tem sempre acesso total (nao e editavel, para nunca se trancar
fora do sistema). Os demais perfis (`usuario`, `leitor`) tem um conjunto padrao
(abaixo) que o admin pode ajustar na tela Permissoes; alem disso, um usuario
especifico pode ter uma personalizacao propria (UserMenuPermission) que sobrepoe o
padrao do perfil.

As "features" abaixo sao os botoes reais do menu. `permissions` (a propria tela) e
exclusiva do admin e nao entra na matriz de toggles.
"""

# Botoes reais do menu, na ordem de exibicao. Cada um tem um icone (emoji) para a
# tela de Permissoes ficar visual/didatica.
MENU_FEATURES = [
    {'key': 'dashboard',     'label': 'Dashboard',      'url_name': 'dashboard',     'icon': '🏠'},
    {'key': 'conversations', 'label': 'Conversas',      'url_name': 'conversations', 'icon': '💬'},
    {'key': 'contacts',      'label': 'Contatos',       'url_name': 'contacts',      'icon': '👥'},
    {'key': 'attendants',    'label': 'Atendentes',     'url_name': 'attendants',    'icon': '🎧'},
    {'key': 'sectors',       'label': 'Setores',        'url_name': 'sectors',       'icon': '🗂️'},
    {'key': 'settings',      'label': 'Configurações',  'url_name': 'wapi-settings', 'icon': '⚙️'},
]
ALL_FEATURE_KEYS = [f['key'] for f in MENU_FEATURES]

# Item exclusivo do admin (fora da matriz de toggles).
PERMISSIONS_ITEM = {'label': 'Permissões', 'url_name': 'permissions'}

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


def role_allowed_keys(role):
    """Conjunto de botoes de um PERFIL (config salva ou padrao). adm = tudo."""
    if role == 'adm':
        return set(ALL_FEATURE_KEYS)
    from .models import RoleMenuPermission
    row = RoleMenuPermission.objects.filter(role=role).first()
    if row is not None:
        return set(row.allowed_keys or [])
    return set(role_default_keys(role))


def allowed_keys_for(user):
    """Conjunto EFETIVO de botoes de um usuario: adm = tudo; senao a personalizacao
    do usuario (se houver) ou o padrao do perfil."""
    if not getattr(user, 'is_authenticated', False):
        return set()
    if user.role == 'adm':
        return set(ALL_FEATURE_KEYS)
    from .models import UserMenuPermission
    override = UserMenuPermission.objects.filter(user=user).first()
    if override is not None:
        return set(override.allowed_keys or [])
    return role_allowed_keys(user.role)


def user_can_access(user, key):
    """O usuario pode acessar a feature/botao `key`?"""
    if not getattr(user, 'is_authenticated', False):
        return False
    if key == 'permissions':
        return getattr(user, 'role', None) == 'adm'
    if getattr(user, 'role', None) == 'adm':
        return True
    return key in allowed_keys_for(user)


def nav_items_for(user, active_label):
    """Itens do menu que o usuario pode ver, no formato esperado pelo template."""
    allowed = allowed_keys_for(user)
    is_adm = getattr(user, 'role', None) == 'adm'
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


def visible_conversations_q(user):
    """Q das conversas que um usuario NAO-admin pode ver:
    - diretas: atribuidas a ele OU (do(s) setor(es) dele E ainda NAO fechada);
    - grupos: liberados para o(s) setor(es) dele OU para ele (GroupAccess).

    Regra dos FINALIZADOS: uma conversa fechada so aparece para quem a ATENDEU
    (por atribuicao), NAO para o setor inteiro — cada atendente ve so os seus
    finalizados. (O admin ve tudo, ver visible_conversations.)"""
    from django.db.models import Q
    sector_ids = user_sector_ids(user)
    direct = Q(chat_type='private') & (
        Q(assigned_attendant__user=user)
        | (Q(sector_id__in=sector_ids) & ~Q(status='closed'))
    )
    group = Q(chat_type='group') & (
        Q(access__sectors__id__in=sector_ids) | Q(access__users=user)
    )
    return direct | group


def visible_conversations(user, queryset):
    """Filtra um queryset de Conversation pelo que o usuario pode ver. Admin = tudo."""
    if not getattr(user, 'is_authenticated', False):
        return queryset.none()
    if user.role == 'adm':
        return queryset
    return queryset.filter(visible_conversations_q(user)).distinct()


def can_see_conversation(user, conversation):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.role == 'adm':
        return True
    from .models import Conversation
    return visible_conversations(
        user, Conversation.objects.filter(pk=conversation.pk)
    ).exists()


def history_full_for(user):
    """O usuario ve a conversa INTEIRA (True) ou so o atendimento atual (False)?
    Admin sempre ve tudo. Personalizacao do usuario > perfil > padrao (False)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.role == 'adm':
        return True
    from .models import RoleMenuPermission, UserMenuPermission
    override = UserMenuPermission.objects.filter(user=user).first()
    if override is not None:
        return bool(override.full_history)
    row = RoleMenuPermission.objects.filter(role=user.role).first()
    if row is not None:
        return bool(row.full_history)
    return False
