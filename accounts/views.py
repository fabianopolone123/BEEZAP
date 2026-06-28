from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AttendantForm, LoginForm, WapiConfigurationForm, WapiSendTextForm
from .models import Attendant, User, WapiConfiguration
from wapi.client import send_text_message


ROLE_RANK = {
    'leitor': 1,
    'usuario': 2,
    'adm': 3,
}

NAV_ITEMS = [
    {'label': 'Dashboard', 'required': 'leitor', 'url_name': 'dashboard'},
    {'label': 'Conversas', 'required': 'leitor', 'url_name': None},
    {'label': 'Atendimentos', 'required': 'leitor', 'url_name': None},
    {'label': 'Contatos', 'required': 'leitor', 'url_name': None},
    {'label': 'Atendentes', 'required': 'adm', 'url_name': 'attendants'},
    {'label': 'Setores', 'required': 'usuario', 'url_name': None},
    {'label': 'Campanhas', 'required': 'usuario', 'url_name': None},
    {'label': 'Relatorios', 'required': 'leitor', 'url_name': None},
    {'label': 'Automacao', 'required': 'usuario', 'url_name': None},
    {'label': 'Configuracoes', 'required': 'adm', 'url_name': 'wapi-settings'},
]


def build_nav_items(role, active_label):
    role_rank = ROLE_RANK.get(role, 1)
    items = []
    for item in NAV_ITEMS:
        if role_rank >= ROLE_RANK[item['required']]:
            items.append({
                **item,
                'active': item['label'] == active_label,
                'href': item['url_name'],
            })
    return items


def split_name_parts(full_name):
    parts = full_name.strip().split(maxsplit=1)
    if not parts:
        return '', ''
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ''
    return first_name, last_name


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
        messages.error(request, 'E-mail ou senha invalidos.')

    return render(request, 'accounts/login.html', {'form': form})


@login_required
def dashboard_view(request):
    role = request.user.role
    role_rank = ROLE_RANK.get(role, 1)

    quick_actions = [
        {'label': 'Nova conversa', 'required': 'usuario', 'tone': 'primary'},
        {'label': 'Fila de atendimento', 'required': 'leitor', 'tone': 'secondary'},
        {'label': 'Relatorios', 'required': 'leitor', 'tone': 'secondary'},
        {'label': 'Configuracoes', 'required': 'adm', 'tone': 'locked'},
    ]

    visible_actions = [
        item for item in quick_actions if role_rank >= ROLE_RANK[item['required']]
    ]

    stats = [
        {'label': 'Conversas ativas', 'value': '152', 'delta': '+12%', 'delta_class': 'positive'},
        {'label': 'Novas conversas', 'value': '98', 'delta': '+10%', 'delta_class': 'positive'},
        {'label': 'Atendimentos finalizados', 'value': '235', 'delta': '+15%', 'delta_class': 'positive'},
        {'label': 'Tempo medio de resposta', 'value': '00:01:28', 'delta': '-8%', 'delta_class': 'negative'},
    ]

    table_rows = [
        ['Joao Silva', 'Vendas', 'Maria Santos', '00:03:25', 'Quero saber mais sobre o plano...'],
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
            'nav_items': build_nav_items(role, 'Dashboard'),
            'quick_actions': visible_actions,
            'stats': stats,
            'table_rows': table_rows,
        },
    )


@login_required
def wapi_settings_view(request):
    if request.user.role != 'adm':
        return HttpResponseForbidden('Acesso restrito.')

    config = WapiConfiguration.get_solo()
    config_form = WapiConfigurationForm(
        request.POST if request.POST.get('form_type') == 'config' else None,
        initial={'instance_id': config.instance_id},
    )
    send_form = WapiSendTextForm(
        request.POST if request.POST.get('form_type') == 'send-test' else None,
    )

    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        if form_type == 'config' and config_form.is_valid():
            config.instance_id = config_form.cleaned_data['instance_id'].strip()
            new_token = config_form.cleaned_data['token'].strip()
            if new_token:
                config.token = new_token
            config.save()
            messages.success(request, 'Configuracao salva com sucesso.')
            return redirect('wapi-settings')

        if form_type == 'send-test' and send_form.is_valid():
            result = send_text_message(
                phone=send_form.cleaned_data['phone'].strip(),
                message=send_form.cleaned_data['message'].strip(),
            )
            if result.success:
                messages.success(request, 'Mensagem enviada com sucesso.')
            else:
                messages.error(
                    request,
                    result.error or 'Nao foi possivel enviar a mensagem. Verifique o telefone, o Instance ID e o Token.',
                )
            return redirect('wapi-settings')

    return render(
        request,
        'accounts/wapi_settings.html',
        {
            'config_form': config_form,
            'send_form': send_form,
            'config': config,
            'nav_items': build_nav_items(request.user.role, 'Configuracoes'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'token_configured': config.has_token,
        },
    )


@login_required
def attendants_view(request):
    if request.user.role != 'adm':
        return HttpResponseForbidden('Acesso restrito.')

    attendants = Attendant.objects.select_related('user').all()
    form = AttendantForm()
    modal_mode = 'create'
    show_modal = False
    editing_attendant = None

    if request.method == 'POST':
        attendant_id = request.POST.get('attendant_id')
        if attendant_id:
            editing_attendant = get_object_or_404(Attendant, pk=attendant_id)
            modal_mode = 'edit'
        form = AttendantForm(request.POST, attendant=editing_attendant)
        show_modal = True

        if form.is_valid():
            name = form.cleaned_data['name'].strip()
            email = form.cleaned_data['email']
            phone = form.cleaned_data['phone']
            first_name, last_name = split_name_parts(name)
            try:
                with transaction.atomic():
                    if editing_attendant:
                        user = editing_attendant.user
                        user.email = email
                        user.first_name = first_name
                        user.last_name = last_name
                        user.role = User.Role.USUARIO
                        user.save()

                        editing_attendant.name = name
                        editing_attendant.phone = phone
                        editing_attendant.save()
                        messages.success(request, 'Atendente atualizado com sucesso.')
                    else:
                        user = User.objects.create_user(
                            email=email,
                            password='1234',
                            role=User.Role.USUARIO,
                            first_name=first_name,
                            last_name=last_name,
                        )
                        Attendant.objects.create(
                            user=user,
                            name=name,
                            phone=phone,
                        )
                        messages.success(request, 'Atendente cadastrado com sucesso.')
                return redirect('attendants')
            except IntegrityError:
                form.add_error('email', 'Ja existe um atendente com este e-mail.')
                messages.error(request, 'Ja existe um atendente com este e-mail.')
            except Exception:
                messages.error(request, 'Nao foi possivel salvar o atendente. Verifique os dados e tente novamente.')
        elif form.errors.get('email'):
            messages.error(request, 'Ja existe um atendente com este e-mail.')
        else:
            messages.error(request, 'Nao foi possivel salvar o atendente. Verifique os dados e tente novamente.')

    return render(
        request,
        'accounts/attendants.html',
        {
            'attendants': attendants,
            'form': form,
            'show_modal': show_modal,
            'modal_mode': modal_mode,
            'nav_items': build_nav_items(request.user.role, 'Atendentes'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


def logout_view(request):
    logout(request)
    return redirect('login')
