import json
import logging
import secrets
from hmac import compare_digest
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    AutomationAiTestForm,
    AutomationRuleForm,
    AttendantForm,
    InitialPasswordChangeForm,
    LoginForm,
    PasswordRecoveryCodeForm,
    PasswordRecoveryNewPasswordForm,
    PasswordRecoveryRequestForm,
    SectorForm,
    WapiConfigurationForm,
    WapiSendTextForm,
)
from .models import (
    Attendant,
    AutomationRule,
    PasswordResetCode,
    Sector,
    User,
    WapiConfiguration,
    WapiWebhookEvent,
)
from ai_engine.services import generate_ai_reply, generate_ai_reply_with_rules
from wapi.client import send_text_message
from wapi.parser import parse_wapi_webhook_payload


PASSWORD_RECOVERY_CODE_ID_KEY = 'password_recovery_code_id'
PASSWORD_RECOVERY_EMAIL_KEY = 'password_recovery_email'
PASSWORD_RECOVERY_VERIFIED_ID_KEY = 'password_recovery_verified_id'
PASSWORD_RECOVERY_GENERIC_MESSAGE = 'Se os dados estiverem corretos, enviaremos um codigo para o WhatsApp cadastrado.'

wapi_webhook_logger = logging.getLogger('beezap.wapi.webhook')


def mask_phone_for_log(phone):
    """Mantem apenas o final do telefone nos logs para nao expor o numero completo."""
    digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
    if not digits:
        return '-'
    return '***' + digits[-4:] if len(digits) > 4 else '***'


ROLE_RANK = {
    'leitor': 1,
    'usuario': 2,
    'adm': 3,
}

NAV_ITEMS = [
    {'label': 'Dashboard', 'required': 'leitor', 'url_name': 'dashboard'},
    {'label': 'Conversas', 'required': 'leitor', 'url_name': 'conversations'},
    {'label': 'Atendimentos', 'required': 'leitor', 'url_name': None},
    {'label': 'Contatos', 'required': 'leitor', 'url_name': None},
    {'label': 'Atendentes', 'required': 'adm', 'url_name': 'attendants'},
    {'label': 'Setores', 'required': 'adm', 'url_name': 'sectors'},
    {'label': 'Campanhas', 'required': 'usuario', 'url_name': None},
    {'label': 'Relatorios', 'required': 'leitor', 'url_name': None},
    {'label': 'Automacao', 'required': 'adm', 'url_name': 'automation-ai'},
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


def build_login_context(
    request,
    form=None,
    recovery_step='request',
    recovery_open=False,
    recovery_request_form=None,
    recovery_code_form=None,
    recovery_password_form=None,
):
    return {
        'form': form or LoginForm(),
        'recovery_open': recovery_open,
        'recovery_step': recovery_step,
        'recovery_request_form': recovery_request_form or PasswordRecoveryRequestForm(
            initial={'email': request.session.get(PASSWORD_RECOVERY_EMAIL_KEY, '')}
        ),
        'recovery_code_form': recovery_code_form or PasswordRecoveryCodeForm(),
        'recovery_password_form': recovery_password_form or PasswordRecoveryNewPasswordForm(),
    }


def render_login(request, **context):
    return render(request, 'accounts/login.html', build_login_context(request, **context))


def get_user_recovery_phone(user):
    try:
        return Attendant.normalize_phone(user.attendant_profile.phone)
    except Attendant.DoesNotExist:
        return ''


def clear_password_recovery_session(request):
    for key in (
        PASSWORD_RECOVERY_CODE_ID_KEY,
        PASSWORD_RECOVERY_EMAIL_KEY,
        PASSWORD_RECOVERY_VERIFIED_ID_KEY,
    ):
        request.session.pop(key, None)


def create_and_send_password_recovery_code(user, phone):
    code = f'{secrets.randbelow(1000000):06d}'
    now = timezone.now()
    PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    reset_code = PasswordResetCode.objects.create(
        user=user,
        code_hash=make_password(code),
        expires_at=now + timedelta(minutes=10),
    )
    message = (
        f'Seu codigo de recuperacao de senha do BEEZAP e: {code}\n\n'
        'Este codigo expira em 10 minutos.'
    )
    result = send_text_message(phone=phone, message=message)
    return reset_code if result.success else None


def request_password_recovery_code(request, email):
    request.session[PASSWORD_RECOVERY_EMAIL_KEY] = email
    request.session.pop(PASSWORD_RECOVERY_CODE_ID_KEY, None)
    request.session.pop(PASSWORD_RECOVERY_VERIFIED_ID_KEY, None)

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if not user:
        return

    phone = get_user_recovery_phone(user)
    if not phone:
        return

    reset_code = create_and_send_password_recovery_code(user, phone)
    if reset_code:
        request.session[PASSWORD_RECOVERY_CODE_ID_KEY] = reset_code.id


def create_wapi_webhook_event(payload):
    parsed_payload = parse_wapi_webhook_payload(payload)
    return WapiWebhookEvent.objects.create(
        raw_payload=payload if isinstance(payload, dict) else {},
        **parsed_payload,
    )


def is_valid_wapi_webhook_token(request):
    config = WapiConfiguration.get_solo()
    expected_token = config.resolved_webhook_token().strip()
    if not expected_token:
        # Sem token configurado o recebimento fica aberto (protecao opcional).
        # A W-API chama apenas a URL publica, sem enviar cabecalhos proprios,
        # entao exigir token aqui bloquearia todas as mensagens reais.
        return True

    received_token = (
        request.headers.get('X-BEEZAP-WEBHOOK-TOKEN', '').strip()
        or request.GET.get('token', '').strip()
    )
    return bool(received_token) and compare_digest(received_token, expected_token)


def build_wapi_webhook_url(request):
    # Exibe a URL sob o prefixo /beezap/, que e como o app e publicado no VPS.
    return request.build_absolute_uri(reverse('wapi-webhook-beezap'))


def require_admin_json(request):
    if request.user.role != 'adm':
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)
    return None


def serialize_wapi_event(event):
    received_at = timezone.localtime(event.received_at)
    return {
        'id': event.id,
        'event_type': event.event_type or '-',
        'phone': event.phone or '-',
        'contact_name': event.contact_name or '-',
        'message_text': event.short_text or '-',
        'received_at': received_at.strftime('%d/%m/%Y %H:%M'),
        'status_label': event.status_label,
    }


def must_change_initial_password(user):
    if not user.is_authenticated:
        return False
    try:
        return user.attendant_profile.must_change_password
    except Attendant.DoesNotExist:
        return False


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
            if must_change_initial_password(user):
                return redirect('change-initial-password')
            return redirect('dashboard')
        messages.error(request, 'E-mail ou senha invalidos.')

    return render_login(request, form=form)


def password_recovery_request_view(request):
    if request.method != 'POST':
        return redirect('login')

    form = PasswordRecoveryRequestForm(request.POST)
    if form.is_valid():
        request_password_recovery_code(request, form.cleaned_data['email'].strip().lower())
        messages.info(request, PASSWORD_RECOVERY_GENERIC_MESSAGE)
        return render_login(request, recovery_step='code', recovery_open=True, recovery_request_form=form)

    messages.error(request, 'Nao foi possivel concluir a recuperacao de senha. Tente novamente.')
    return render_login(request, recovery_step='request', recovery_open=True, recovery_request_form=form)


def password_recovery_resend_view(request):
    if request.method != 'POST':
        return redirect('login')

    email = request.session.get(PASSWORD_RECOVERY_EMAIL_KEY, '')
    if email:
        request_password_recovery_code(request, email)
    messages.info(request, PASSWORD_RECOVERY_GENERIC_MESSAGE)
    return render_login(request, recovery_step='code', recovery_open=True)


def password_recovery_verify_code_view(request):
    if request.method != 'POST':
        return redirect('login')

    form = PasswordRecoveryCodeForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Codigo invalido ou expirado. Verifique e tente novamente.')
        return render_login(request, recovery_step='code', recovery_open=True, recovery_code_form=form)

    reset_code = PasswordResetCode.objects.filter(
        pk=request.session.get(PASSWORD_RECOVERY_CODE_ID_KEY),
        used_at__isnull=True,
    ).select_related('user').first()

    if not reset_code or not reset_code.is_available:
        messages.error(request, 'Codigo invalido ou expirado. Verifique e tente novamente.')
        return render_login(request, recovery_step='code', recovery_open=True, recovery_code_form=form)

    if reset_code.matches(form.cleaned_data['code']):
        request.session[PASSWORD_RECOVERY_VERIFIED_ID_KEY] = reset_code.id
        messages.info(request, 'Codigo confirmado. Crie sua nova senha.')
        return render_login(request, recovery_step='password', recovery_open=True)

    reset_code.attempts += 1
    update_fields = ['attempts']
    if reset_code.attempts >= 5:
        reset_code.used_at = timezone.now()
        update_fields.append('used_at')
        request.session.pop(PASSWORD_RECOVERY_CODE_ID_KEY, None)
        messages.error(request, 'Muitas tentativas. Solicite um novo codigo.')
    else:
        messages.error(request, 'Codigo invalido ou expirado. Verifique e tente novamente.')
    reset_code.save(update_fields=update_fields)
    return render_login(request, recovery_step='code', recovery_open=True, recovery_code_form=form)


def password_recovery_set_password_view(request):
    if request.method != 'POST':
        return redirect('login')

    reset_code = PasswordResetCode.objects.filter(
        pk=request.session.get(PASSWORD_RECOVERY_VERIFIED_ID_KEY),
        used_at__isnull=True,
    ).select_related('user').first()

    if not reset_code or not reset_code.is_available:
        clear_password_recovery_session(request)
        messages.error(request, 'Codigo invalido ou expirado. Verifique e tente novamente.')
        return render_login(request, recovery_step='request', recovery_open=True)

    form = PasswordRecoveryNewPasswordForm(request.POST, user=reset_code.user)
    if form.is_valid():
        reset_code.user.set_password(form.cleaned_data['new_password'])
        reset_code.user.save(update_fields=['password'])
        reset_code.invalidate()
        clear_password_recovery_session(request)
        messages.success(request, 'Senha alterada com sucesso. Faca login com sua nova senha.')
        return redirect('login')

    if form.errors.get('confirm_password'):
        messages.error(request, 'As senhas digitadas nao conferem.')
    elif form.errors.get('new_password'):
        messages.error(request, 'Escolha uma senha mais segura.')
    else:
        messages.error(request, 'Nao foi possivel concluir a recuperacao de senha. Tente novamente.')
    return render_login(request, recovery_step='password', recovery_open=True, recovery_password_form=form)


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
            new_webhook_token = config_form.cleaned_data['webhook_token'].strip()
            if new_webhook_token:
                config.webhook_token = new_webhook_token
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
            'webhook_url': build_wapi_webhook_url(request),
            'latest_webhook_events': WapiWebhookEvent.objects.all()[:10],
            'nav_items': build_nav_items(request.user.role, 'Configuracoes'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'token_configured': config.has_token,
            'webhook_token_configured': config.has_webhook_token,
        },
    )


@login_required
def wapi_webhook_events_view(request):
    """Lista os ultimos eventos reais recebidos, para atualizacao automatica na tela."""
    forbidden_response = require_admin_json(request)
    if forbidden_response:
        return forbidden_response

    events = WapiWebhookEvent.objects.all()[:10]
    return JsonResponse({
        'ok': True,
        'events': [serialize_wapi_event(event) for event in events],
    })


@login_required
def automation_ai_view(request):
    if request.user.role != 'adm':
        return HttpResponseForbidden('Acesso restrito.')

    form = AutomationAiTestForm(request.POST or None)
    ai_reply = ''
    rules_found = []
    use_rules = False
    rules_checked = False

    if request.method == 'POST':
        if form.is_valid():
            use_rules = form.cleaned_data.get('use_rules', False)
            if use_rules:
                rules_checked = True
                result = generate_ai_reply_with_rules(
                    message=form.cleaned_data['message'],
                    sector=form.cleaned_data.get('sector'),
                    model=form.cleaned_data['model'],
                    base_url=form.cleaned_data['ollama_url'],
                    timeout=form.cleaned_data['timeout'],
                )
                rules_found = result.rules
            else:
                result = generate_ai_reply(
                    message=form.cleaned_data['message'],
                    model=form.cleaned_data['model'],
                    base_url=form.cleaned_data['ollama_url'],
                    timeout=form.cleaned_data['timeout'],
                )
            ai_reply = result.reply
            if result.success:
                if result.no_rules_found:
                    messages.info(request, 'Nenhuma regra compativel foi encontrada.')
                else:
                    messages.success(request, 'Resposta gerada com sucesso.')
            else:
                messages.error(request, result.error)
        else:
            if form.errors.get('message'):
                messages.error(request, 'Digite uma mensagem para testar a IA.')
            else:
                messages.error(request, 'Nao foi possivel gerar resposta agora. Tente novamente.')

    return render(
        request,
        'accounts/automation_ai_settings.html',
        {
            'form': form,
            'ai_reply': ai_reply,
            'rules_found': rules_found,
            'use_rules': use_rules,
            'rules_checked': rules_checked,
            'nav_items': build_nav_items(request.user.role, 'Automacao'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


@login_required
def automation_rules_view(request):
    if request.user.role != 'adm':
        return HttpResponseForbidden('Acesso restrito.')

    query = request.GET.get('q', '').strip()
    sector_filter = request.GET.get('sector', '').strip()
    status_filter = request.GET.get('status', '').strip()
    sectors = Sector.objects.all()
    rules = AutomationRule.objects.select_related('sector').all()

    if query:
        rules = rules.filter(
            Q(title__icontains=query)
            | Q(keywords__icontains=query)
            | Q(customer_example__icontains=query)
            | Q(response_text__icontains=query)
        )

    if sector_filter == 'general':
        rules = rules.filter(sector__isnull=True)
    elif sector_filter:
        try:
            rules = rules.filter(sector_id=int(sector_filter))
        except ValueError:
            sector_filter = ''

    if status_filter == 'active':
        rules = rules.filter(is_active=True)
    elif status_filter == 'inactive':
        rules = rules.filter(is_active=False)
    else:
        status_filter = ''

    form = AutomationRuleForm()
    show_modal = request.GET.get('new') == '1'
    modal_mode = 'create'
    editing_rule = None

    edit_id = request.GET.get('edit', '').strip()
    if edit_id:
        editing_rule = AutomationRule.objects.filter(pk=edit_id).first()
        if editing_rule:
            form = AutomationRuleForm(instance=editing_rule)
            show_modal = True
            modal_mode = 'edit'
        else:
            messages.error(request, 'Regra nao encontrada.')
            return redirect('automation-rules')

    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        rule_id = request.POST.get('rule_id', '').strip()

        if action == 'deactivate':
            rule = AutomationRule.objects.filter(pk=rule_id).first()
            if rule:
                rule.is_active = False
                rule.save(update_fields=['is_active', 'updated_at'])
                messages.success(request, 'Regra inativada com sucesso.')
            else:
                messages.error(request, 'Regra nao encontrada.')
            return redirect('automation-rules')

        if rule_id:
            editing_rule = AutomationRule.objects.filter(pk=rule_id).first()
            if not editing_rule:
                messages.error(request, 'Regra nao encontrada.')
                return redirect('automation-rules')
            modal_mode = 'edit'

        form = AutomationRuleForm(request.POST, instance=editing_rule)
        show_modal = True
        if form.is_valid():
            form.save()
            if editing_rule:
                messages.success(request, 'Regra atualizada com sucesso.')
            else:
                messages.success(request, 'Regra cadastrada com sucesso.')
            return redirect('automation-rules')

        messages.error(request, 'Nao foi possivel salvar a regra. Verifique os dados e tente novamente.')

    return render(
        request,
        'accounts/automation_rules.html',
        {
            'rules': rules,
            'sectors': sectors,
            'form': form,
            'show_modal': show_modal,
            'modal_mode': modal_mode,
            'editing_rule': editing_rule,
            'filters': {
                'q': query,
                'sector': sector_filter,
                'status': status_filter,
            },
            'nav_items': build_nav_items(request.user.role, 'Automacao'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
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
                            must_change_password=True,
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


@login_required
def change_initial_password_view(request):
    try:
        attendant = request.user.attendant_profile
    except Attendant.DoesNotExist:
        return redirect('dashboard')

    if not attendant.must_change_password:
        return redirect('dashboard')

    form = InitialPasswordChangeForm(request.POST or None, user=request.user)
    if request.method == 'POST':
        if form.is_valid():
            request.user.set_password(form.cleaned_data['new_password'])
            request.user.save(update_fields=['password'])
            attendant.must_change_password = False
            attendant.save(update_fields=['must_change_password', 'updated_at'])
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Senha alterada com sucesso.')
            return redirect('dashboard')
        messages.error(request, 'Nao foi possivel alterar a senha. Verifique os dados e tente novamente.')

    return render(request, 'accounts/change_initial_password.html', {'form': form})


@login_required
def conversations_view(request):
    role = request.user.role

    conversations = [
        {'id': 1, 'name': 'João Silva',     'initials': 'JS', 'preview': 'Quero saber mais sobre os planos...', 'time': '09:42', 'unread': 2, 'active': True},
        {'id': 2, 'name': 'Maria Eduarda',  'initials': 'ME', 'preview': 'Ainda tem disponibilidade?',          'time': '09:40', 'unread': 1, 'active': False},
        {'id': 3, 'name': 'Carlos Lima',    'initials': 'CL', 'preview': 'Preciso de ajuda com meu pedido.',    'time': '09:36', 'unread': 0, 'active': False},
        {'id': 4, 'name': 'Juliana Costa',  'initials': 'JC', 'preview': 'Como funciona o pagamento?',          'time': '09:30', 'unread': 0, 'active': False},
        {'id': 5, 'name': 'Pedro Almeida',  'initials': 'PA', 'preview': 'Quando vence meu boleto?',            'time': '09:22', 'unread': 0, 'active': False},
        {'id': 6, 'name': 'Ana Paula',      'initials': 'AP', 'preview': 'Gostaria de agendar uma demonstração.', 'time': '09:15', 'unread': 0, 'active': False},
        {'id': 7, 'name': 'Ricardo Oliveira', 'initials': 'RO', 'preview': 'Vocês emitem nota fiscal?',         'time': '09:10', 'unread': 0, 'active': False},
        {'id': 8, 'name': 'Fernanda Rocha', 'initials': 'FR', 'preview': 'Tudo certo, obrigado!',               'time': 'Ontem', 'unread': 0, 'active': False},
    ]

    chat_messages = [
        {'type': 'received', 'text': 'Olá! Quero saber mais sobre os planos disponíveis.', 'time': '09:42', 'attachment': None},
        {'type': 'sent',     'text': 'Olá, João! Claro, posso te ajudar. 😊\nTemos 3 planos disponíveis.\nQuer que eu te envie mais detalhes?', 'time': '09:43', 'attachment': None},
        {'type': 'received', 'text': 'Sim, por favor.', 'time': '09:43', 'attachment': None},
        {'type': 'sent',     'text': None, 'time': '09:44', 'attachment': {'name': 'Planos_BEEZAP.pdf', 'size': 'PDF · 1,2 MB'}},
        {'type': 'received', 'text': 'Ótimo! Qual é o plano mais indicado para equipes pequenas?', 'time': '09:44', 'attachment': None},
        {'type': 'sent',     'text': 'Recomendamos o plano Profissional 👍\nAté 5 atendentes e integrações avançadas.', 'time': '09:44', 'attachment': None},
        {'type': 'received', 'text': 'Perfeito! E o pagamento, como funciona?', 'time': '09:45', 'attachment': None},
        {'type': 'sent',     'text': 'Aceitamos cartão, boleto e PIX.\nPosso gerar um boleto para você?', 'time': '09:46', 'attachment': None},
        {'type': 'received', 'text': 'Pode sim, por favor.', 'time': '09:46', 'attachment': None},
        {'type': 'sent',     'text': 'Pronto! Segue o boleto em anexo.', 'time': '09:47', 'attachment': None},
        {'type': 'sent',     'text': None, 'time': '09:47', 'attachment': {'name': 'Boleto_123456.pdf', 'size': 'PDF · 210 KB'}},
    ]

    contact = {
        'name':         'João Silva',
        'initials':     'JS',
        'phone':        '(11) 99999-8888',
        'email':        'joao.silva@email.com',
        'company':      'Silva Consultoria Ltda.',
        'tags':         ['Cliente', 'Interessado'],
        'responsible':  'Maria Santos',
        'last_contact': 'Hoje às 09:42',
        'notes':        'Interessado em plano para equipe pequena. Pediu boleto.',
    }

    return render(
        request,
        'accounts/conversations.html',
        {
            'role':          role,
            'nav_items':     build_nav_items(role, 'Conversas'),
            'role_label':    request.user.get_role_display(),
            'user_initial':  (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'conversations': conversations,
            'chat_messages': chat_messages,
            'contact':       contact,
        },
    )


@login_required
def sectors_view(request):
    if request.user.role != 'adm':
        return HttpResponseForbidden('Acesso restrito.')

    sectors = Sector.objects.prefetch_related('attendants__user').all()
    attendants = Attendant.objects.select_related('user').filter(user__is_active=True)

    form = SectorForm()
    show_modal = False
    modal_mode = 'create'
    editing_sector = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        sector_id_str = request.POST.get('sector_id', '').strip()

        if action == 'delete' and sector_id_str:
            try:
                sector_obj = Sector.objects.get(pk=int(sector_id_str))
                sector_obj.delete()
                messages.success(request, 'Setor removido com sucesso.')
            except (Sector.DoesNotExist, ValueError):
                messages.error(request, 'Setor não encontrado.')
            return redirect('sectors')

        if sector_id_str:
            try:
                editing_sector = Sector.objects.get(pk=int(sector_id_str))
                modal_mode = 'edit'
            except (Sector.DoesNotExist, ValueError):
                messages.error(request, 'Setor não encontrado.')
                return redirect('sectors')

        form = SectorForm(request.POST, instance=editing_sector)
        show_modal = True

        if form.is_valid():
            form.save()
            msg = 'Setor atualizado com sucesso.' if editing_sector else 'Setor cadastrado com sucesso.'
            messages.success(request, msg)
            return redirect('sectors')

        if 'name' in form.errors:
            err_text = ' '.join(str(e) for e in form.errors['name'])
            if 'já existe' in err_text.lower():
                messages.error(request, 'Já existe um setor com este nome.')
            else:
                messages.error(request, 'Não foi possível salvar o setor. Verifique os dados e tente novamente.')
        else:
            messages.error(request, 'Não foi possível salvar o setor. Verifique os dados e tente novamente.')

    sector_state = {
        str(s.id): list(s.attendants.values_list('id', flat=True))
        for s in sectors
    }

    attendants_data = {
        att.id: {
            'name': att.name,
            'email': att.user.email,
            'initials': att.name[0].upper() if att.name else '?',
        }
        for att in attendants
    }

    return render(
        request,
        'accounts/sectors.html',
        {
            'role': request.user.role,
            'nav_items': build_nav_items(request.user.role, 'Setores'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'sectors': sectors,
            'attendants': attendants,
            'form': form,
            'show_modal': show_modal,
            'modal_mode': modal_mode,
            'editing_sector': editing_sector,
            'sector_state': sector_state,
            'attendants_data': attendants_data,
        },
    )


@require_POST
def sectors_save_organization_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'Sessão expirada. Faça login novamente.'}, status=401)
    if request.user.role != 'adm':
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Dados inválidos.'}, status=400)

    sectors_data = data.get('sectors', {})
    if not isinstance(sectors_data, dict):
        return JsonResponse({'ok': False, 'error': 'Dados inválidos.'}, status=400)

    try:
        for sector_id_str, attendant_ids in sectors_data.items():
            try:
                sector_id = int(sector_id_str)
            except (ValueError, TypeError):
                continue
            sector_obj = Sector.objects.filter(pk=sector_id).first()
            if not sector_obj:
                continue
            if not isinstance(attendant_ids, list):
                continue
            valid_ids = list(
                Attendant.objects.filter(pk__in=attendant_ids).values_list('id', flat=True)
            )
            sector_obj.attendants.set(valid_ids)
    except Exception:
        return JsonResponse(
            {'ok': False, 'error': 'Não foi possível salvar a organização. Tente novamente.'},
            status=500,
        )

    return JsonResponse({'ok': True})


@csrf_exempt
def wapi_webhook_view(request):
    if request.method != 'POST':
        # GET/HEAD respondem JSON amigavel (405) para facilitar o diagnostico.
        return JsonResponse({'ok': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not is_valid_wapi_webhook_token(request):
        wapi_webhook_logger.warning('Webhook W-API recusado: token invalido.')
        return JsonResponse({'ok': False, 'error': 'Token de webhook invalido.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        wapi_webhook_logger.warning('Webhook W-API com corpo invalido; salvando payload vazio.')
        payload = {}

    try:
        event = create_wapi_webhook_event(payload)
    except Exception:
        # Nunca expor traceback para quem chama o webhook.
        wapi_webhook_logger.exception('Falha ao registrar evento de webhook W-API.')
        return JsonResponse({'ok': False, 'error': 'Nao foi possivel registrar o webhook.'}, status=500)

    # Log seguro: sem token, sem payload bruto e com telefone mascarado.
    wapi_webhook_logger.info(
        'Webhook W-API registrado: id=%s tipo=%s telefone=%s from_me=%s',
        event.id,
        event.event_type,
        mask_phone_for_log(event.phone),
        event.from_me,
    )

    return JsonResponse({'ok': True, 'message': 'Webhook recebido com sucesso.'})


def logout_view(request):
    logout(request)
    return redirect('login')
