from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import LoginForm


ROLE_RANK = {
    'leitor': 1,
    'usuario': 2,
    'adm': 3,
}


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

    nav_items = [
        {'label': 'Dashboard', 'required': 'leitor', 'active': True},
        {'label': 'Conversas', 'required': 'leitor'},
        {'label': 'Atendimentos', 'required': 'leitor'},
        {'label': 'Contatos', 'required': 'leitor'},
        {'label': 'Setores', 'required': 'usuario'},
        {'label': 'Campanhas', 'required': 'usuario'},
        {'label': 'Relatórios', 'required': 'leitor'},
        {'label': 'Automação', 'required': 'usuario'},
        {'label': 'Configurações', 'required': 'adm'},
    ]

    quick_actions = [
        {'label': 'Nova conversa', 'required': 'usuario', 'tone': 'primary'},
        {'label': 'Fila de atendimento', 'required': 'leitor', 'tone': 'secondary'},
        {'label': 'Relatórios', 'required': 'leitor', 'tone': 'secondary'},
        {'label': 'Configurações', 'required': 'adm', 'tone': 'locked'},
    ]

    visible_nav_items = [
        item for item in nav_items if ROLE_RANK[role] >= ROLE_RANK[item['required']]
    ]
    visible_actions = [
        item for item in quick_actions if ROLE_RANK[role] >= ROLE_RANK[item['required']]
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
            'nav_items': visible_nav_items,
            'quick_actions': visible_actions,
            'stats': stats,
            'table_rows': table_rows,
        },
    )


def logout_view(request):
    logout(request)
    return redirect('login')
