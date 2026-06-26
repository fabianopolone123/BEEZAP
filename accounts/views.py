from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import LoginForm, SettingsForm


ROLE_RANK = {
    'leitor': 1,
    'usuario': 2,
    'adm': 3,
}

NAV_ITEMS = [
    {'label': 'Dashboard', 'required': 'leitor', 'url_name': 'dashboard', 'active': False},
    {'label': 'Conversas', 'required': 'leitor', 'url_name': 'dashboard', 'active': False},
    {'label': 'Atendimentos', 'required': 'leitor', 'url_name': 'dashboard', 'active': False},
    {'label': 'Contatos', 'required': 'leitor', 'url_name': 'dashboard', 'active': False},
    {'label': 'Setores', 'required': 'usuario', 'url_name': 'dashboard', 'active': False},
    {'label': 'Campanhas', 'required': 'usuario', 'url_name': 'dashboard', 'active': False},
    {'label': 'Relatórios', 'required': 'leitor', 'url_name': 'dashboard', 'active': False},
    {'label': 'Automação', 'required': 'usuario', 'url_name': 'dashboard', 'active': False},
    {'label': 'Configurações', 'required': 'adm', 'url_name': 'settings', 'active': False},
]


def _visible_nav_items(role, active_label):
    role_rank = ROLE_RANK.get(role, 1)
    items = []
    for item in NAV_ITEMS:
        if role_rank >= ROLE_RANK[item['required']]:
            items.append({
                **item,
                'url': item['url_name'],
                'active': item['label'] == active_label,
            })
    return items


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = LoginForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        password = form.cleaned_data['password']
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        messages.error(request, 'E-mail ou senha inválidos.')

    return render(request, 'accounts/login.html', {'form': form})


@login_required
def dashboard_view(request):
    role = request.user.role
    role_rank = ROLE_RANK.get(role, 1)

    quick_actions = [
        {'label': 'Nova conversa', 'required': 'usuario', 'tone': 'primary'},
        {'label': 'Fila de atendimento', 'required': 'leitor', 'tone': 'secondary'},
        {'label': 'Relatórios', 'required': 'leitor', 'tone': 'secondary'},
        {'label': 'Configurações', 'required': 'adm', 'tone': 'locked'},
    ]

    visible_actions = [
        item for item in quick_actions if role_rank >= ROLE_RANK[item['required']]
    ]

    stats = [
        {'label': 'Conversas ativas', 'value': '152', 'delta': '+12%', 'delta_class': 'positive'},
        {'label': 'Novas conversas', 'value': '98', 'delta': '+10%', 'delta_class': 'positive'},
        {'label': 'Atendimentos finalizados', 'value': '235', 'delta': '+15%', 'delta_class': 'positive'},
        {'label': 'Tempo médio de resposta', 'value': '00:01:28', 'delta': '-8%', 'delta_class': 'negative'},
    ]

    table_rows = [
        ['João Silva', 'Vendas', 'Maria Santos', '00:03:25', 'Quero saber mais sobre o plano...'],
        ['Ana Paula', 'Suporte', 'Carlos Lima', '00:01:47', 'Preciso de ajuda com meu pedido.'],
        ['Ricardo Oliveira', 'Financeiro', 'Juliana Costa', '00:02:18', 'Como funciona o pagamento?'],
    ]

    return render(
        request,
        'accounts/dashboard.html',
        {
            'role': role,
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'nav_items': _visible_nav_items(role, 'Dashboard'),
            'quick_actions': visible_actions,
            'stats': stats,
            'table_rows': table_rows,
        },
    )


@login_required
def settings_view(request):
    role = request.user.role
    role_rank = ROLE_RANK.get(role, 1)
    if role_rank < ROLE_RANK['adm']:
        return redirect('dashboard')

    initial_data = {
        'company_name': 'BEEZap',
        'workspace_name': 'Central de Atendimento',
        'support_email': 'suporte@beezap.com',
        'support_phone': '(11) 99999-9999',
        'business_hours': 'Seg a Sex, 08:00 às 18:00',
        'default_sector': 'Suporte',
        'welcome_message': 'Olá! Como podemos ajudar você hoje?',
        'auto_assignment': 'sim',
        'notification_email': 'alertas@beezap.com',
        'primary_color': '#21c25e',
    }
    form = SettingsForm(request.POST or None, initial=initial_data)

    if request.method == 'POST' and form.is_valid():
        messages.success(request, 'Configurações enviadas para validação.')

    sections = [
        {'title': 'Identificação da operação', 'fields': [form['company_name'], form['workspace_name']]},
        {'title': 'Canais e suporte', 'fields': [form['support_email'], form['support_phone'], form['business_hours']]},
        {'title': 'Fluxo de atendimento', 'fields': [form['default_sector'], form['welcome_message'], form['auto_assignment']]},
        {'title': 'Ajustes visuais e alertas', 'fields': [form['notification_email'], form['primary_color']]},
    ]

    return render(
        request,
        'accounts/settings.html',
        {
            'role': role,
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'nav_items': _visible_nav_items(role, 'Configurações'),
            'form': form,
            'sections': sections,
        },
    )


def logout_view(request):
    logout(request)
    return redirect('login')
