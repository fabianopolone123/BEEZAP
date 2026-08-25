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
painel de um cliente (modo suporte), SO a tela "WhatsApp" daquela empresa
(instancia e token da W-API). Nenhuma feature do negocio do cliente (setores,
atendentes, permissoes, chatbot) fica liberada para ele e ele nao enxerga conversa
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
# `Gestores` = quem administra a PLATAFORMA (os proprios masters). Um master cria
# outro por ali, com senha inicial e WhatsApp de recuperacao.
CLIENTS_ITEM = {'label': 'Clientes', 'url_name': 'clients'}
# `Metricas` = todos os clientes num lugar so (canal, atendimento e consumo de IA do
# mes). So numeros e datas: o master mede o uso sem ler o atendimento de ninguem.
PLATFORM_METRICS_ITEM = {'label': 'Métricas', 'url_name': 'platform-metrics'}
AI_ITEM = {'label': 'Inteligência (IA)', 'url_name': 'openai-settings'}
MASTERS_ITEM = {'label': 'Gestores', 'url_name': 'masters'}
MASTER_ONLY_ITEMS = [CLIENTS_ITEM, PLATFORM_METRICS_ITEM, AI_ITEM, MASTERS_ITEM]

# MODO SUPORTE: o que o master alcanca quando "entra no painel" de um cliente.
#
# SO A TELA WHATSAPP (instancia e token da W-API daquela empresa) — a unica parte
# TECNICA, com credencial, que nao pode ficar na mao do cliente. Junto com ela o
# master mantem os botoes da PLATAFORMA (Clientes e Inteligencia (IA), que valem
# para todos), e nada mais.
#
# Setores, Atendentes, Permissoes e Atendimento (chatbot + modo) sao o NEGOCIO da
# empresa e ficam com o ADM dela; Conversas, Contatos e Dashboard nunca estiveram
# abertos, porque sao os dados pessoais dos clientes finais. A regra do projeto e
# que o master administra a plataforma sem operar (nem ler) o atendimento de
# ninguem — ver accounts/tenancy.py.
#
# A tela WhatsApp NAO passa pela matriz de features: quem a protege e
# `views.require_master_in_company` (ser master E estar dentro do painel). Por isso
# nenhuma `key` de MENU_FEATURES fica liberada para o master.
WHATSAPP_ITEM = {'label': 'WhatsApp', 'url_name': 'wapi-settings'}

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


def user_can_access(user, key):
    """O usuario pode acessar a feature/botao `key`?"""
    if not getattr(user, 'is_authenticated', False):
        return False
    role = getattr(user, 'role', None)
    # Telas da PLATAFORMA (gestao de clientes e dos proprios gestores): so o master,
    # e nenhum perfil de cliente as acessa. A tela de IA nao entra nesta matriz —
    # quem a protege e `views.require_master` (era a chave `platform_ai`, que nenhuma
    # view chegou a usar).
    if key in ('clients', 'masters'):
        return role == 'master'
    # O master nao tem NENHUMA feature da empresa, nem dentro do painel do cliente
    # (modo suporte): a unica tela que ele alcanca la e a do WhatsApp, protegida por
    # `views.require_master_in_company`. Ver WHATSAPP_ITEM.
    if role == 'master':
        return False
    if key == 'permissions':
        return role == 'adm'
    if role == 'adm':
        return True
    return key in allowed_keys_for(user)


def nav_items_for(user, active_label, in_company=False):
    """Itens do menu que o usuario pode ver, no formato esperado pelo template."""
    role = getattr(user, 'role', None)
    # O master tem um menu proprio: as telas da PLATAFORMA (Clientes e a IA, que vale
    # para todas as empresas) e, quando esta no painel de um cliente (modo suporte),
    # apenas o WhatsApp daquela empresa. Nenhum botao do negocio do cliente.
    if role == 'master':
        menu = list(MASTER_ONLY_ITEMS)
        if in_company:
            menu.append(WHATSAPP_ITEM)
        return [
            {
                'label': item['label'],
                'url_name': item['url_name'],
                'href': item['url_name'],
                'active': item['label'] == active_label,
            }
            for item in menu
        ]
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


def nav_groups_for(user, active_label, in_company=False, support_company_name=''):
    """Itens do menu AGRUPADOS, para a barra lateral.

    Existe por um problema de leitura, nao de permissao: quando o master entrava no
    painel de um cliente, o menu simplesmente GANHAVA um item ("WhatsApp") no meio
    dos itens da plataforma. Nada dizia que aquele item era daquele cliente e os
    outros eram da plataforma — o menu "mudava de forma" e o master perdia a
    referencia de onde estava.

    Agora o menu tem grupos ROTULADOS:

        PLATAFORMA          Clientes · Metricas · Inteligencia (IA) · Gestores
        CLIENTE · <nome>    WhatsApp

    Os itens da plataforma ficam SEMPRE no mesmo lugar, na mesma ordem. O que
    acontece ao entrar num painel e um segundo grupo APARECER, com o nome do cliente
    no rotulo. Para quem nao e master ha um grupo unico e sem rotulo — a barra fica
    exatamente como sempre foi.

    Devolve [{'label': str, 'items': [...]}]; `label` vazio = grupo sem titulo.
    """
    role = getattr(user, 'role', None)
    if role != 'master':
        return [{'label': '', 'items': nav_items_for(user, active_label)}]

    def _item(entrada):
        return {
            'label': entrada['label'],
            'url_name': entrada['url_name'],
            'href': entrada['url_name'],
            'active': entrada['label'] == active_label,
        }

    grupos = [{
        'label': 'Plataforma',
        'items': [_item(entrada) for entrada in MASTER_ONLY_ITEMS],
    }]
    if in_company:
        grupos.append({
            'label': 'Cliente · %s' % support_company_name if support_company_name
                     else 'Cliente',
            'items': [_item(WHATSAPP_ITEM)],
            'is_client': True,
        })
    return grupos


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


def sector_ids_for(user):
    """IDs dos setores em que a pessoa atua (pelo perfil de atendente dela)."""
    from .models import Sector
    return list(
        Sector.objects.filter(attendants__user=user).values_list('id', flat=True).distinct()
    )


def visible_contacts(user, queryset):
    """Filtra a AGENDA (tela Contatos) pelo que a pessoa pode ver.

    ESCOPO IMPORTANTE — esta funcao vale SO para a tela Contatos. Conversa,
    transferencia e a resolucao do nome que aparece no lugar do numero NAO passam por
    aqui e continuam com as regras proprias: um contato classificado em Vendas que
    escreve para o Comercial e atendido normalmente ali, ele so nao entra na AGENDA de
    quem nao tem acesso aquela carteira. O que e sensivel e a lista de clientes, nao o
    atendimento pontual — foi a decisao explicita do dono do produto.

    As regras, em ordem:
      - gestor master: nenhum contato (ele nao opera atendimento);
      - administrador: todos os contatos DA EMPRESA dele;
      - contato SEM setor: visivel para todos (e o estado de todo contato antigo, e
        por isso classificar e opcional — nada quebra no dia em que isto entra);
      - contato COM setor: visivel para quem atua num daqueles setores, mais o EXTRA
        liberado em Permissoes -> aba Contatos (`ContactSectorAccess`), por setor
        inteiro ou por pessoa.
    """
    from django.db.models import Q

    from .models import ContactSectorAccess

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

    meus_setores = sector_ids_for(user)
    # Carteiras liberadas para mim: pelo meu setor OU pelo meu nome.
    liberadas = set(
        ContactSectorAccess.objects
        .filter(sector__company=company)
        .filter(Q(sectors__id__in=meus_setores) | Q(users=user))
        .values_list('sector_id', flat=True)
    )
    alcance = set(meus_setores) | liberadas
    # `distinct` porque o contato pode estar em dois setores que eu alcanço.
    return queryset.filter(
        Q(sectors__isnull=True) | Q(sectors__id__in=alcance)
    ).distinct()


def can_see_contact(user, contact):
    """O usuario alcanca ESTE contato na agenda? (mesma regra de visible_contacts)"""
    from .models import Contact
    if not getattr(user, 'is_authenticated', False):
        return False
    return visible_contacts(user, Contact.objects.filter(pk=contact.pk)).exists()
