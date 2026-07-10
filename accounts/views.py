import base64
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
from hmac import compare_digest
from datetime import timedelta
from urllib.parse import urlsplit

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
    AttendantForm,
    InitialPasswordChangeForm,
    LoginForm,
    MenuBotConfigurationForm,
    OpenAiConfigurationForm,
    PasswordRecoveryCodeForm,
    PasswordRecoveryNewPasswordForm,
    PasswordRecoveryRequestForm,
    ReceptionModeForm,
    SectorForm,
    WapiConfigurationForm,
    WapiSendTextForm,
)
from .models import (
    Attendant,
    Contact,
    Conversation,
    GroupAccess,
    MenuBotConfiguration,
    MenuOption,
    Message,
    OpenAiConfiguration,
    PasswordResetCode,
    RoleMenuPermission,
    Sector,
    User,
    UserMenuPermission,
    WapiConfiguration,
    WapiWebhookEvent,
)
from gpt.client import test_connection as gpt_test_connection
from wapi.client import (
    send_audio_message,
    send_document_message,
    send_image_message,
    send_text_message,
    send_video_message,
)
from wapi.formatting import markdown_to_whatsapp
from wapi.parser import parse_wapi_webhook_payload
from wapi.services import (
    SYSTEM_CLOSE_TEXT,
    convert_audio_to_ogg,
    ensure_wapi_image,
    document_filename,
    ingest_wapi_payload,
    retry_conversation_media_async,
    save_outgoing_media_message,
    save_outgoing_text_message,
    save_system_message,
    sync_group_names,
)


PASSWORD_RECOVERY_CODE_ID_KEY = 'password_recovery_code_id'
PASSWORD_RECOVERY_EMAIL_KEY = 'password_recovery_email'
PASSWORD_RECOVERY_VERIFIED_ID_KEY = 'password_recovery_verified_id'
PASSWORD_RECOVERY_GENERIC_MESSAGE = 'Se os dados estiverem corretos, enviaremos um codigo para o WhatsApp cadastrado.'

wapi_webhook_logger = logging.getLogger('beezap.wapi.webhook')


def _fmt_int(value):
    """Formata inteiro com separador de milhar no estilo pt-br (ex.: 1.234.567)."""
    try:
        return f'{int(value or 0):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return '0'


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


# Abas da area de Configuracoes (barra horizontal no topo das telas de config).
# WhatsApp (W-API) e Atendimento (chatbot de menu + IA). A aba Atendimento tem duas
# sub-abas: Chatbot e Inteligencia (IA).
def build_settings_tabs(active_tab, active_subtab=''):
    return {
        'active_tab': active_tab,
        'active_subtab': active_subtab,
        'reception_mode': MenuBotConfiguration.get_solo().mode,
    }


def build_nav_items(user, active_label):
    """Itens do menu conforme as PERMISSOES do usuario (ver accounts/permissions.py)."""
    from .permissions import nav_items_for
    return nav_items_for(user, active_label)


def require_feature(request, key):
    """Retorna 403 se o usuario nao pode acessar a feature/botao `key` (o admin
    sempre pode). Retorna None quando o acesso e permitido."""
    from .permissions import user_can_access
    if not user_can_access(request.user, key):
        return HttpResponseForbidden('Acesso restrito.')
    return None


def deny_conversation_json(request, conversation):
    """Retorna 403 JSON se o usuario nao pode ver a conversa; senao None."""
    from .permissions import can_see_conversation
    if not can_see_conversation(request.user, conversation):
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)
    return None


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
    event = WapiWebhookEvent.objects.create(
        raw_payload=payload if isinstance(payload, dict) else {},
        **parsed_payload,
    )

    # Integra com Conversas reais: detecta grupo vs direta, resolve a conversa
    # certa e cria a mensagem (texto/reacao/midia). Falha aqui nunca deve derrubar
    # o webhook — o evento bruto ja foi salvo acima em WapiWebhookEvent.
    try:
        ingest_wapi_payload(payload)
    except Exception:
        wapi_webhook_logger.exception('Falha ao criar conversa a partir do webhook W-API.')

    return event


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
    # Com FORCE_SCRIPT_NAME=/beezap, reverse ja gera /beezap/webhook/wapi/.
    # Sem prefixo (local), gera /webhook/wapi/.
    return request.build_absolute_uri(reverse('wapi-webhook'))


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


def _format_hms(seconds):
    seconds = int(max(0, seconds or 0))
    return f'{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'


# Paleta para o gráfico de setores (donut) e legenda.
_DASHBOARD_PALETTE = ['#21c25e', '#2d6cdf', '#f4b740', '#e5484d', '#7c3aed', '#0d8d43', '#14b8a6', '#ef7d1a']


def build_dashboard_context():
    """Métricas reais do dashboard a partir do banco (conversas/mensagens/setores)."""
    from django.db.models import Count

    today = timezone.localdate()
    start_7 = today - timedelta(days=6)
    convs = Conversation.objects.all()

    ativas = convs.exclude(status='closed').count()
    novas = convs.filter(created_at__date__gte=start_7).count()
    finalizadas = convs.filter(status='closed').count()

    # Tempo médio de resposta: 1a resposta do atendente após a 1a mensagem do cliente
    # (considera atendimentos com atividade nos últimos 30 dias).
    deltas = []
    recent = convs.filter(last_message_at__date__gte=today - timedelta(days=30)).prefetch_related('messages')
    for conv in recent:
        msgs = sorted(
            [m for m in conv.messages.all() if m.message_type != 'system'],
            key=lambda m: m.created_at,
        )
        first_in = next((m for m in msgs if m.direction == 'in'), None)
        if not first_in:
            continue
        first_out = next(
            (m for m in msgs if m.direction == 'out' and m.created_at >= first_in.created_at), None
        )
        if first_out:
            deltas.append((first_out.created_at - first_in.created_at).total_seconds())
    tempo_medio = _format_hms(sum(deltas) / len(deltas)) if deltas else '--:--:--'

    stats = [
        {'label': 'Conversas ativas', 'value': _fmt_int(ativas)},
        {'label': 'Novas conversas', 'value': _fmt_int(novas)},
        {'label': 'Atendimentos finalizados', 'value': _fmt_int(finalizadas)},
        {'label': 'Tempo médio de resposta', 'value': tempo_medio},
    ]

    # Atendimentos por dia (últimos 7 dias, pela data da última mensagem).
    day_counts = []
    for i in range(7):
        d = start_7 + timedelta(days=i)
        day_counts.append((d, convs.filter(last_message_at__date=d).count()))
    max_v = max((c for _, c in day_counts), default=0) or 1
    # Area de plotagem (viewBox 0 0 720 250): eixo Y a esquerda, base em baixo.
    left, right, top, bottom = 48, 700, 25, 210
    step = (right - left) / 6
    chart_points = []
    for i, (d, c) in enumerate(day_counts):
        x = left + step * i
        y = bottom - (c / max_v) * (bottom - top)
        chart_points.append({'x': round(x, 1), 'y': round(y, 1), 'label': d.strftime('%d/%m'), 'value': c})
    chart_polyline = ' '.join(f"{p['x']},{p['y']}" for p in chart_points)
    # Poligono da area sob a linha (fecha na base).
    chart_area = f'{left},{bottom} ' + chart_polyline + f' {right},{bottom}'
    # Linhas de grade horizontais com os valores do eixo Y (0, metade, maximo).
    chart_gridlines = [
        {'y': round(bottom - frac * (bottom - top), 1), 'value': round(max_v * frac)}
        for frac in (0, 0.5, 1.0)
    ]

    # Atendimentos por setor (donut + legenda).
    sector_rows = list(
        convs.filter(sector__isnull=False)
        .values('sector__name').annotate(n=Count('id')).order_by('-n')
    )
    total_sector = sum(r['n'] for r in sector_rows)
    segments, sector_legend, acc = [], [], 0.0
    for i, r in enumerate(sector_rows):
        pct = (r['n'] / total_sector * 100) if total_sector else 0
        start, acc = acc, acc + pct
        color = _DASHBOARD_PALETTE[i % len(_DASHBOARD_PALETTE)]
        segments.append(f'{color} {start:.2f}% {acc:.2f}%')
        sector_legend.append({'name': r['sector__name'], 'pct': round(pct), 'color': color})
    donut_gradient = f"conic-gradient({', '.join(segments)})" if segments else '#e2e8f0'

    # Atendimentos em andamento (abertos, em atendimento humano).
    andamento = []
    for conv in (convs.filter(status='open')
                 .select_related('contact', 'assigned_attendant', 'sector')
                 .order_by('-last_message_at')[:12]):
        andamento.append({
            'cliente': conv.display_title,
            'setor': conv.sector.name if conv.sector_id else '—',
            'atendente': conv.assigned_attendant.name if conv.assigned_attendant_id else '—',
            'tempo': _format_conv_time(conv.last_message_at),
            'ultima': conv.last_message_text or '',
        })

    return {
        'stats': stats,
        'chart_points': chart_points,
        'chart_polyline': chart_polyline,
        'chart_area': chart_area,
        'chart_gridlines': chart_gridlines,
        'donut_gradient': donut_gradient,
        'sector_legend': sector_legend,
        'andamento': andamento,
    }


@login_required
def dashboard_view(request):
    # Quem nao tem o botao Dashboard cai na primeira tela disponivel (ex.: Conversas).
    from .permissions import user_can_access, first_landing_url_name
    if not user_can_access(request.user, 'dashboard'):
        return redirect(first_landing_url_name(request.user))

    context = build_dashboard_context()
    context.update({
        'role': request.user.role,
        'role_label': request.user.get_role_display(),
        'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        'nav_items': build_nav_items(request.user, 'Dashboard'),
        'today_str': timezone.localdate().strftime('%d/%m/%Y'),
    })
    return render(request, 'accounts/dashboard.html', context)


@login_required
def openai_settings_view(request):
    """Sub-aba Inteligencia (IA) da area Atendimento: cadastra a API Key do GPT,
    escolhe o modelo, edita o prompt e testa a conexao. A ATIVACAO (ligar a IA) e
    feita pelo seletor de modo no topo da area Atendimento, nao mais aqui. Apenas ADM."""
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden

    from gpt.attendant import (
        DEFAULT_INSTRUCTIONS,
        attendants_context_text,
        resolved_instructions,
        sectors_context_text,
    )

    config = OpenAiConfiguration.get_solo()
    menubot = MenuBotConfiguration.get_solo()
    config_form = OpenAiConfigurationForm(
        request.POST if request.POST.get('form_type') == 'config' else None,
        initial={
            'model': config.resolved_model(),
            'instructions': config.instructions,
            'max_turns': config.max_turns,
            'fallback_sector': config.fallback_sector_id,
        },
    )

    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        if form_type == 'config' and config_form.is_valid():
            new_key = config_form.cleaned_data['api_key'].strip()
            if new_key:
                config.api_key = new_key
            config.model = (config_form.cleaned_data['model'] or 'gpt-4.1-nano').strip()
            config.instructions = (config_form.cleaned_data['instructions'] or '').strip()
            config.max_turns = config_form.cleaned_data['max_turns'] or 3
            config.fallback_sector = config_form.cleaned_data['fallback_sector']
            config.save()
            messages.success(request, 'Configuracao da inteligencia salva com sucesso.')
            return redirect('openai-settings')

        if form_type == 'test':
            if not config.has_api_key:
                messages.error(request, 'Cadastre a API Key do GPT antes de testar.')
            else:
                result = gpt_test_connection()
                if result.success:
                    messages.success(
                        request,
                        'Conexao com o GPT funcionando (modelo %s).' % (result.model or config.resolved_model()),
                    )
                else:
                    messages.error(request, result.error or 'Nao foi possivel falar com o GPT.')
            return redirect('openai-settings')

        if form_type == 'reset-usage':
            config.reset_usage()
            messages.success(request, 'Contador de tokens zerado.')
            return redirect('openai-settings')

    return render(
        request,
        'accounts/openai_settings.html',
        {
            'config_form': config_form,
            'config': config,
            'nav_items': build_nav_items(request.user, 'Configurações'),
            'settings_tabs': build_settings_tabs('atendimento', 'ia'),
            'mode_form': ReceptionModeForm(initial={'mode': menubot.mode}),
            'ai_active': menubot.mode == MenuBotConfiguration.MODE_AI,
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'api_key_configured': config.has_api_key,
            'usage_total_tokens': _fmt_int(config.total_tokens),
            'usage_prompt_tokens': _fmt_int(config.total_prompt_tokens),
            'usage_completion_tokens': _fmt_int(config.total_completion_tokens),
            'usage_requests': _fmt_int(config.total_requests),
            # Pre-visualizacao do que e enviado a IA (contexto auto-gerido).
            'preview_instructions': resolved_instructions(config),
            'preview_sectors': sectors_context_text(),
            'preview_attendants': attendants_context_text(),
            # Diagnostico: conteudo completo da ultima chamada real ao GPT.
            'last_request': config.last_request,
            'last_response': config.last_response,
            'last_exchange_at': config.last_exchange_at,
            'default_instructions': DEFAULT_INSTRUCTIONS,
        },
    )


@login_required
def atendimento_view(request):
    """Sub-aba Chatbot da area Atendimento: configura o chatbot de menu (saudacao,
    opcoes numeradas -> setor, tentativas, fallback) e mostra a previa do menu.
    O seletor de modo (desligado/chatbot/IA) fica no topo. Apenas ADM."""
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden

    from chatbot.handler import (
        DEFAULT_CONFIRMATION_MESSAGE,
        DEFAULT_GREETING,
        DEFAULT_HANDOFF_MESSAGE,
        DEFAULT_INVALID_MESSAGE,
        DEFAULT_MENU_INTRO,
        build_menu_text,
    )

    config = MenuBotConfiguration.get_solo()
    config_form = MenuBotConfigurationForm(
        request.POST if request.POST.get('form_type') == 'chatbot' else None,
        initial={
            'greeting': config.greeting,
            'menu_intro': config.menu_intro,
            'confirmation_message': config.confirmation_message,
            'invalid_message': config.invalid_message,
            'handoff_message': config.handoff_message,
            'max_attempts': config.max_attempts,
            'fallback_sector': config.fallback_sector_id,
        },
    )

    if request.method == 'POST' and request.POST.get('form_type') == 'chatbot' and config_form.is_valid():
        config.greeting = (config_form.cleaned_data['greeting'] or '').strip()
        config.menu_intro = (config_form.cleaned_data['menu_intro'] or '').strip()
        config.confirmation_message = (config_form.cleaned_data['confirmation_message'] or '').strip()
        config.invalid_message = (config_form.cleaned_data['invalid_message'] or '').strip()
        config.handoff_message = (config_form.cleaned_data['handoff_message'] or '').strip()
        config.max_attempts = config_form.cleaned_data['max_attempts'] or 3
        config.fallback_sector = config_form.cleaned_data['fallback_sector']
        config.save()
        _save_menu_options(config, request.POST)
        messages.success(request, 'Configuracao do chatbot salva com sucesso.')
        return redirect('atendimento')

    sectors = list(Sector.objects.all().order_by('name'))
    return render(
        request,
        'accounts/chatbot_settings.html',
        {
            'config_form': config_form,
            'config': config,
            'options': config.ordered_options(),
            'sectors': sectors,
            # Setores em JSON para o preenchimento automatico (JS monta as opcoes).
            'sectors_json': [{'id': s.id, 'name': s.name} for s in sectors],
            'nav_items': build_nav_items(request.user, 'Configurações'),
            'settings_tabs': build_settings_tabs('atendimento', 'chatbot'),
            'mode_form': ReceptionModeForm(initial={'mode': config.mode}),
            'menu_active': config.mode == MenuBotConfiguration.MODE_MENU,
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'menu_preview': build_menu_text(config),
            'defaults': {
                'greeting': DEFAULT_GREETING,
                'menu_intro': DEFAULT_MENU_INTRO,
                'confirmation_message': DEFAULT_CONFIRMATION_MESSAGE,
                'invalid_message': DEFAULT_INVALID_MESSAGE,
                'handoff_message': DEFAULT_HANDOFF_MESSAGE,
            },
        },
    )


def _save_menu_options(config, post):
    """Reconstroi as opcoes do menu a partir dos arrays do formulario (rotulo +
    setor por linha). Ignora linhas sem rotulo; numera na ordem enviada."""
    labels = post.getlist('option_label')
    sector_ids = post.getlist('option_sector')
    config.options.all().delete()
    order = 0
    for label, sector_id in zip(labels, sector_ids):
        label = (label or '').strip()
        if not label:
            continue
        order += 1
        sector = Sector.objects.filter(pk=sector_id).first() if sector_id else None
        MenuOption.objects.create(config=config, order=order, label=label, sector=sector)


@login_required
@require_POST
def atendimento_set_mode_view(request):
    """Salva o MODO mestre de primeiro atendimento (desligado/chatbot/IA) e volta
    para a sub-aba de origem. Apenas ADM."""
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden
    config = MenuBotConfiguration.get_solo()
    form = ReceptionModeForm(request.POST)
    if form.is_valid():
        config.mode = form.cleaned_data['mode']
        config.save(update_fields=['mode', 'updated_at'])
        # Mantem o interruptor antigo da IA coerente com o modo (compatibilidade).
        ai = OpenAiConfiguration.get_solo()
        ai.enabled = (config.mode == MenuBotConfiguration.MODE_AI)
        ai.save(update_fields=['enabled', 'updated_at'])
        messages.success(request, 'Modo de atendimento atualizado.')
    dest = request.POST.get('next')
    return redirect('openai-settings' if dest == 'ia' else 'atendimento')


@login_required
def permissions_view(request):
    """Tela Permissoes (so ADM): define quais botoes do menu cada PERFIL ve/acessa
    e permite personalizar um USUARIO especifico. O Administrador tem sempre acesso
    total (nao editavel)."""
    forbidden = require_feature(request, 'permissions')
    if forbidden:
        return forbidden

    from .permissions import (
        EDITABLE_ROLES, MENU_FEATURES, ALL_FEATURE_KEYS,
        role_allowed_keys, allowed_keys_for, history_full_for,
    )
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        if form_type == 'roles':
            for entry in EDITABLE_ROLES:
                role = entry['role']
                chosen = [k for k in ALL_FEATURE_KEYS
                          if request.POST.get(f'role__{role}__{k}') == 'on']
                RoleMenuPermission.objects.update_or_create(
                    role=role,
                    defaults={
                        'allowed_keys': chosen,
                        'full_history': request.POST.get(f'role__{role}__full_history') == 'on',
                    },
                )
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, 'Permissoes dos perfis salvas.')
            return redirect('permissions')

        if form_type == 'user':
            user_id = (request.POST.get('user_id') or '').strip()
            target = User.objects.filter(pk=user_id).first() if user_id else None
            if not target or target.role == 'adm':
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Selecione um usuario valido.'}, status=400)
                messages.error(request, 'Selecione um usuario valido.')
                return redirect('permissions')
            chosen = [k for k in ALL_FEATURE_KEYS
                      if request.POST.get(f'userkey__{k}') == 'on']
            UserMenuPermission.objects.update_or_create(
                user=target,
                defaults={
                    'allowed_keys': chosen,
                    'full_history': request.POST.get('userkey__full_history') == 'on',
                },
            )
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, f'Permissoes de {target.email} salvas.')
            return redirect(f'{reverse("permissions")}?user={target.id}')

        if form_type == 'user-reset':
            user_id = (request.POST.get('user_id') or '').strip()
            UserMenuPermission.objects.filter(user_id=user_id).delete()
            messages.success(request, 'Personalizacao removida (voltou ao padrao do perfil).')
            return redirect('permissions')

        if form_type == 'group-name':
            gid = (request.POST.get('group_id') or '').strip()
            name = (request.POST.get('name') or '').strip()
            conv = Conversation.objects.filter(pk=gid, chat_type='group').first() if gid else None
            if conv is not None:
                conv.name = name
                conv.save(update_fields=['name', 'updated_at'])
            if is_ajax:
                return JsonResponse({'ok': conv is not None})
            return redirect(f'{reverse("permissions")}?tab=grupos')

        if form_type == 'group-remove':
            gid = (request.POST.get('group_id') or '').strip()
            deleted = 0
            if gid:
                deleted, _ = Conversation.objects.filter(pk=gid, chat_type='group').delete()
            if is_ajax:
                return JsonResponse({'ok': bool(deleted)})
            messages.success(request, 'Grupo removido da lista.')
            return redirect(f'{reverse("permissions")}?tab=grupos')

        if form_type == 'groups':
            group_ids = Conversation.objects.filter(chat_type='group').values_list('id', flat=True)
            valid_sector_ids = set(Sector.objects.values_list('id', flat=True))
            attendant_user_ids = set(
                User.objects.filter(attendant_profile__isnull=False).values_list('id', flat=True)
            )
            for gid in group_ids:
                sec_ids = [int(s) for s in request.POST.getlist(f'group__{gid}__sector')
                           if s.isdigit() and int(s) in valid_sector_ids]
                usr_ids = [int(u) for u in request.POST.getlist(f'group__{gid}__user')
                           if u.isdigit() and int(u) in attendant_user_ids]
                access, _ = GroupAccess.objects.get_or_create(conversation_id=gid)
                access.sectors.set(sec_ids)
                access.users.set(usr_ids)
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, 'Acessos aos grupos salvos.')
            return redirect(f'{reverse("permissions")}?tab=grupos')

    # ----- GET -----
    def role_history(role):
        row = RoleMenuPermission.objects.filter(role=role).first()
        return bool(row.full_history) if row else False

    roles_ctx = []
    for entry in EDITABLE_ROLES:
        keys = role_allowed_keys(entry['role'])
        roles_ctx.append({
            'role': entry['role'],
            'label': entry['label'],
            'full_history': role_history(entry['role']),
            'features': [
                {**f, 'checked': f['key'] in keys} for f in MENU_FEATURES
            ],
        })

    users = list(
        User.objects.exclude(role='adm').filter(is_active=True).order_by('email')
    )
    override_ids = set(
        UserMenuPermission.objects.values_list('user_id', flat=True)
    )
    users_ctx = [
        {'id': u.id, 'email': u.email, 'name': u.get_full_name() or u.email,
         'role_label': u.get_role_display(), 'custom': u.id in override_ids}
        for u in users
    ]

    selected_id = (request.GET.get('user') or '').strip()
    selected = User.objects.filter(pk=selected_id).exclude(role='adm').first() if selected_id else None
    selected_ctx = None
    if selected:
        keys = allowed_keys_for(selected)
        selected_ctx = {
            'id': selected.id,
            'email': selected.email,
            'name': selected.get_full_name() or selected.email,
            'role_label': selected.get_role_display(),
            'custom': selected.id in override_ids,
            'full_history': history_full_for(selected),
            'features': [{**f, 'checked': f['key'] in keys} for f in MENU_FEATURES],
        }

    # ----- Aba Grupos -----
    sectors = list(Sector.objects.all().order_by('name'))
    attendant_users = list(
        User.objects.filter(attendant_profile__isnull=False, is_active=True)
        .select_related('attendant_profile').order_by('email')
    )
    groups = (
        Conversation.objects.filter(chat_type='group')
        .prefetch_related('access__sectors', 'access__users')
        .order_by('name', 'external_id')
    )
    groups_ctx = []
    for g in groups:
        access = getattr(g, 'access', None)
        sec_ids = set(access.sectors.values_list('id', flat=True)) if access else set()
        usr_ids = set(access.users.values_list('id', flat=True)) if access else set()
        groups_ctx.append({
            'id': g.id,
            'title': g.display_title,
            'name': g.name,
            'jid': g.external_id,
            'sectors': [{'id': s.id, 'name': s.name, 'checked': s.id in sec_ids} for s in sectors],
            'users': [{'id': u.id, 'name': (u.attendant_profile.name or u.email),
                       'checked': u.id in usr_ids} for u in attendant_users],
        })

    active_tab = 'grupos' if request.GET.get('tab') == 'grupos' else 'perfis'

    return render(
        request,
        'accounts/permissions.html',
        {
            'nav_items': build_nav_items(request.user, 'Permissões'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'features': MENU_FEATURES,
            'roles': roles_ctx,
            'users': users_ctx,
            'selected_user': selected_ctx,
            'groups': groups_ctx,
            'has_sectors': bool(sectors),
            'active_tab': active_tab,
        },
    )


@login_required
def wapi_settings_view(request):
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden

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
            'latest_webhook_events': WapiWebhookEvent.objects.all()[:5],
            'nav_items': build_nav_items(request.user, 'Configurações'),
            'settings_tabs': build_settings_tabs('whatsapp'),
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

    events = WapiWebhookEvent.objects.all()[:5]
    return JsonResponse({
        'ok': True,
        'events': [serialize_wapi_event(event) for event in events],
    })


@login_required
def attendants_view(request):
    forbidden = require_feature(request, 'attendants')
    if forbidden:
        return forbidden

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
                        # Nao rebaixa um administrador que tambem atua como atendente.
                        if user.role != User.Role.ADM:
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
            'nav_items': build_nav_items(request.user, 'Atendentes'),
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


def _format_conv_time(dt):
    if not dt:
        return ''
    local = timezone.localtime(dt)
    today = timezone.localdate()
    if local.date() == today:
        return local.strftime('%H:%M')
    if local.date() == today - timedelta(days=1):
        return 'Ontem'
    return local.strftime('%d/%m/%Y')


def _serialize_conversation_item(conversation):
    sector_name = conversation.sector.name if conversation.sector_id else ''
    attendant_name = conversation.assigned_attendant.name if conversation.assigned_attendant_id else ''
    queue_label = ''
    if conversation.status == 'pending' and sector_name and not attendant_name:
        queue_label = f'Aguardando {sector_name}'
    elif attendant_name:
        queue_label = f'Com {attendant_name}'
    elif sector_name:
        queue_label = sector_name
    return {
        'id': conversation.id,
        'name': conversation.display_title,
        'initials': conversation.display_initials,
        'preview': conversation.last_message_text or '',
        'time': _format_conv_time(conversation.last_message_at),
        'unread': conversation.unread_count or 0,
        'status': conversation.status,
        'status_label': conversation.status_label,
        'chat_type': conversation.chat_type,
        'is_group': conversation.is_group,
        'sector': sector_name,
        'attendant': attendant_name,
        'queue_label': queue_label,
    }


# Mencao no texto do WhatsApp: "@<numero/LID>" (o app resolve para o nome).
_MENTION_RE = re.compile(r'@(\d{7,})')


def _digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _build_name_map(conversation):
    """Mapa {digitos: nome} dos participantes do grupo, para exibir o remetente e
    resolver mencoes (@numero). Fonte: pushName com que a pessoa enviou; um
    Contato salvo manualmente (mesmo numero) tem PRIORIDADE sobre o pushName."""
    names = {}       # digitos -> pushName valido
    numbers = set()  # numeros relevantes (remetentes + mencionados)
    rows = conversation.messages.values_list('sender_id', 'sender_name', 'text')
    for sender_id, sender_name, text in rows:
        digits = _digits(sender_id)
        if digits:
            numbers.add(digits)
            name = (sender_name or '').strip()
            if name and any(ch.isalnum() for ch in name) and digits not in names:
                names[digits] = name
        for mentioned in _MENTION_RE.findall(text or ''):
            numbers.add(mentioned)
    if numbers:
        for phone, cname in Contact.objects.filter(phone__in=numbers).values_list('phone', 'name'):
            if cname and cname.strip():
                names[phone] = cname.strip()  # Contato salvo vence o pushName
    return names


def _resolve_mentions(text, name_map):
    """Substitui "@<numero>" por "@<nome>" quando conhecemos o participante."""
    if not text or '@' not in text or not name_map:
        return text or ''

    def repl(match):
        name = name_map.get(match.group(1))
        return '@' + name if name else match.group(0)

    return _MENTION_RE.sub(repl, text)


def _serialize_message(message, name_map=None):
    if name_map is None:
        sender_display = message.sender_name
    else:
        sender_display = name_map.get(_digits(message.sender_id), '')
    return {
        'id': message.id,
        'type': 'sent' if message.direction == 'out' else 'received',
        'kind': message.message_type,
        'text': _resolve_mentions(message.text, name_map),
        'time': timezone.localtime(message.created_at).strftime('%H:%M'),
        'status': message.status,
        'media_url': message.resolved_media_url,
        'media_mimetype': message.media_mimetype,
        'media_status': message.media_status,
        # Nome real do arquivo (documento) para baixar com nome/extensao corretos.
        'filename': document_filename(message) if message.message_type == 'document' else '',
        # Em grupo, o front mostra o nome de quem enviou (nome resolvido: Contato
        # salvo > pushName). Se vazio, o front exibe o numero (sender_id) clicavel.
        'is_group': message.is_group,
        'from_me': message.from_me,
        'sender_name': sender_display,
        'sender_id': message.sender_id,
    }


def _serialize_contact_info(conversation):
    contact = conversation.contact
    attendant = conversation.assigned_attendant
    is_group = conversation.is_group
    created_source = contact.created_at if contact else conversation.created_at
    return {
        'name': conversation.display_title,
        'initials': conversation.display_initials,
        'phone': contact.phone if contact else '',
        'is_group': is_group,
        'chat_type': conversation.chat_type,
        'status_label': conversation.status_label,
        'sector_id': conversation.sector_id,
        'sector': conversation.sector.name if conversation.sector else 'Nao definido',
        'attendant_id': attendant.id if attendant else None,
        'attendant': attendant.name if attendant else 'Nao definido',
        'created_at': timezone.localtime(created_source).strftime('%d/%m/%Y %H:%M'),
    }


CONVERSATION_FILTERS = (
    ('todas', 'Todas'),
    ('nao-lidas', 'Nao lidas'),
    ('em-atendimento', 'Em atendimento'),
    ('aguardando', 'Aguardando'),
    ('finalizadas', 'Finalizadas'),
)


def _filter_conversations_by_status(queryset, status):
    if status == 'nao-lidas':
        return queryset.filter(unread_count__gt=0)
    if status == 'em-atendimento':
        return queryset.filter(assigned_attendant__isnull=False).exclude(status='closed')
    if status == 'aguardando':
        return queryset.filter(assigned_attendant__isnull=True).exclude(status='closed')
    if status == 'finalizadas':
        return queryset.filter(status='closed')
    return queryset  # 'todas'


def _search_conversations(queryset, term):
    term = (term or '').strip()
    if not term:
        return queryset
    return queryset.filter(
        Q(contact__name__icontains=term)
        | Q(contact__phone__icontains=term)
        | Q(name__icontains=term)
        | Q(last_message_text__icontains=term)
    )


CONVERSATION_TYPE_FILTERS = (
    ('todas', 'Todas'),
    ('diretas', 'Diretas'),
    ('grupos', 'Grupos'),
)


def _filter_conversations_by_type(queryset, tipo):
    if tipo == 'diretas':
        return queryset.filter(chat_type='private')
    if tipo == 'grupos':
        return queryset.filter(chat_type='group')
    return queryset  # 'todas'


def _conversation_counts(base=None):
    # Totais reais por status; usa o mesmo filtro da listagem para nunca divergir.
    base = base if base is not None else Conversation.objects.all()
    return {slug: _filter_conversations_by_status(base, slug).count() for slug, _ in CONVERSATION_FILTERS}


def _conversation_type_counts(base=None):
    base = base if base is not None else Conversation.objects.all()
    return {slug: _filter_conversations_by_type(base, slug).count() for slug, _ in CONVERSATION_TYPE_FILTERS}


@login_required
def conversations_view(request):
    forbidden = require_feature(request, 'conversations')
    if forbidden:
        return forbidden
    from .permissions import visible_conversations
    role = request.user.role
    conversations = visible_conversations(
        request.user,
        Conversation.objects.select_related('contact', 'assigned_attendant', 'sector'),
    )
    counts = _conversation_counts(conversations)
    filter_chips = [
        {'key': slug, 'label': label, 'count': counts.get(slug, 0), 'active': slug == 'todas'}
        for slug, label in CONVERSATION_FILTERS
    ]
    type_counts = _conversation_type_counts(conversations)
    type_tabs = [
        {'key': slug, 'label': label, 'count': type_counts.get(slug, 0), 'active': slug == 'todas'}
        for slug, label in CONVERSATION_TYPE_FILTERS
    ]
    return render(
        request,
        'accounts/conversations.html',
        {
            'role': role,
            'nav_items': build_nav_items(request.user, 'Conversas'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'conversations': [_serialize_conversation_item(c) for c in conversations],
            'filter_chips': filter_chips,
            'type_tabs': type_tabs,
        },
    )


@login_required
def contacts_view(request):
    """Lista/gerencia os contatos (nome + telefone). Os nomes salvos aqui aparecem
    no lugar do numero nas mensagens de grupo (remetente e mencoes)."""
    forbidden = require_feature(request, 'contacts')
    if forbidden:
        return forbidden
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'delete':
            Contact.objects.filter(pk=(request.POST.get('contact_id') or '').strip()).delete()
            messages.success(request, 'Contato removido.')
            return redirect('contacts')

        contact_id = (request.POST.get('contact_id') or '').strip()
        name = (request.POST.get('name') or '').strip()
        phone = _digits(request.POST.get('phone'))
        if not name or not phone:
            messages.error(request, 'Informe o nome e o telefone do contato.')
            return redirect('contacts')
        try:
            if contact_id:
                contact = Contact.objects.filter(pk=contact_id).first()
                if contact:
                    contact.name = name
                    contact.phone = phone
                    contact.save(update_fields=['name', 'phone', 'updated_at'])
                    messages.success(request, 'Contato atualizado.')
            else:
                Contact.objects.create(name=name, phone=phone)
                messages.success(request, 'Contato adicionado.')
        except IntegrityError:
            messages.error(request, 'Ja existe um contato com esse telefone.')
        return redirect('contacts')

    term = (request.GET.get('q') or '').strip()
    contacts = Contact.objects.all()
    if term:
        contacts = contacts.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    return render(
        request,
        'accounts/contacts.html',
        {
            'contacts': contacts,
            'search_term': term,
            'total_contacts': Contact.objects.count(),
            'nav_items': build_nav_items(request.user, 'Contatos'),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


@login_required
def conversation_list_view(request):
    status = (request.GET.get('status') or 'todas').strip()
    tipo = (request.GET.get('tipo') or 'todas').strip()
    from .permissions import visible_conversations
    term = (request.GET.get('q') or '').strip()
    base = visible_conversations(
        request.user,
        Conversation.objects.select_related('contact', 'assigned_attendant', 'sector'),
    )
    queryset = _filter_conversations_by_type(base, tipo)
    queryset = _filter_conversations_by_status(queryset, status)
    queryset = _search_conversations(queryset, term)
    return JsonResponse({
        'ok': True,
        'counts': _conversation_counts(base),
        'type_counts': _conversation_type_counts(base),
        'conversations': [_serialize_conversation_item(c) for c in queryset],
    })


@login_required
def conversation_messages_view(request, conversation_id):
    conversation = get_object_or_404(
        Conversation.objects.select_related('contact', 'assigned_attendant', 'sector'),
        pk=conversation_id,
    )
    from .permissions import can_see_conversation, history_full_for
    if not can_see_conversation(request.user, conversation):
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)
    # Ao abrir a conversa, zera as nao lidas.
    if conversation.unread_count:
        conversation.unread_count = 0
        conversation.save(update_fields=['unread_count', 'updated_at'])

    # So ao ABRIR a conversa (retry=1), nao no poll: tenta rebaixar em background
    # as midias que falharam na chegada. A midia recuperada aparece sozinha no
    # proximo ciclo do poll, sem travar a abertura.
    if request.GET.get('retry'):
        retry_conversation_media_async(conversation.id)

    messages_qs = conversation.messages.all()
    # Escopo do historico: quem nao tem "conversa inteira" ve so o atendimento atual
    # (mensagens a partir da ultima divisoria de sistema).
    if not history_full_for(request.user):
        last_divider = (
            conversation.messages.filter(message_type='system')
            .order_by('-created_at').first()
        )
        if last_divider:
            messages_qs = messages_qs.filter(created_at__gte=last_divider.created_at)
    sectors = Sector.objects.all()
    attendants = Attendant.objects.select_related('user').filter(user__is_active=True)
    name_map = _build_name_map(conversation) if conversation.is_group else None

    return JsonResponse({
        'ok': True,
        'contact': _serialize_contact_info(conversation),
        'messages': [_serialize_message(m, name_map) for m in messages_qs],
        'sectors': [{'id': s.id, 'name': s.name} for s in sectors],
        'attendants': [{'id': a.id, 'name': a.name} for a in attendants],
    })


@login_required
@require_POST
def conversation_send_view(request, conversation_id):
    conversation = get_object_or_404(
        Conversation.objects.select_related('contact'), pk=conversation_id
    )
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'ok': False, 'error': 'Digite uma mensagem para enviar.'}, status=400)
    # O atendente cola texto em Markdown; converte para a formatacao nativa do WhatsApp
    # (negrito/italico/listas/citacao) preservando as quebras de linha. Guardamos e
    # enviamos a MESMA versao convertida, para o historico refletir o que foi enviado.
    text = markdown_to_whatsapp(text)

    if not (conversation.recipient or '').strip():
        return JsonResponse(
            {'ok': False, 'error': 'Nao foi possivel enviar: conversa sem destino.'}, status=400
        )

    config = WapiConfiguration.get_solo()
    if not config.resolved_instance_id().strip() or not config.resolved_token().strip():
        return JsonResponse(
            {'ok': False, 'error': 'Configure a W-API antes de enviar mensagens.'}, status=400
        )

    # Reutiliza o mesmo servico de envio da tela de teste da W-API.
    # Em grupo, recipient e o JID (@g.us) — nunca o participante individual.
    result = send_text_message(phone=conversation.recipient, message=text)
    if not result.success:
        # Erro tecnico ja foi logado com seguranca no servico; aqui vai o texto amigavel.
        return JsonResponse({
            'ok': False,
            'error': result.error or 'Nao foi possivel enviar a mensagem. Verifique a conexao do WhatsApp e tente novamente.',
        }, status=502)

    message = save_outgoing_text_message(
        conversation, text, external_message_id=result.message_id or '', status='sent'
    )
    return JsonResponse({'ok': True, 'message': _serialize_message(message)})


WAPI_MEDIA_SEND_TYPES = ('image', 'audio', 'video', 'document')
WAPI_DOC_MIMETYPES = {
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain',
    'text/csv',
}
# A W-API exige a extensao do arquivo no envio de documento ("A extensao do arquivo
# e obrigatoria."). Usamos a extensao do nome enviado; este mapa e o fallback quando
# o nome vem sem extensao.
WAPI_DOC_EXT_BY_MIME = {
    'application/pdf': 'pdf',
    'application/msword': 'doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'text/plain': 'txt',
    'text/csv': 'csv',
}


def _media_category_ok(media_type, mimetype):
    if media_type == 'image':
        return mimetype.startswith('image/')
    if media_type == 'audio':
        return mimetype.startswith('audio/')
    if media_type == 'video':
        return mimetype.startswith('video/')
    if media_type == 'document':
        return mimetype in WAPI_DOC_MIMETYPES
    return False


def _host_reachable_by_wapi(host):
    """A W-API roda na nuvem: so consegue baixar a midia se a URL apontar para um
    host publico. localhost / IP privado / .local (tipico do ambiente local com
    runserver) nao sao acessiveis de fora -> nesses casos enviamos base64."""
    host = (host or '').split(':')[0].strip().lower()
    if not host or host == 'localhost' or host.endswith('.local'):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # E um dominio (ex.: beezap.exemplo.com) -> assume publico/acessivel.
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _media_file_to_data_uri(field_file, mimetype):
    """Le os bytes do arquivo salvo e devolve um data URI base64 aceito pela W-API
    (ex.: data:image/jpeg;base64,....). Usado quando a URL publica nao e acessivel."""
    field_file.open('rb')
    try:
        raw = field_file.read()
    finally:
        field_file.close()
    encoded = base64.b64encode(raw).decode('ascii')
    return f'data:{mimetype or "application/octet-stream"};base64,{encoded}'


@login_required
@require_POST
def conversation_send_media_view(request, conversation_id):
    conversation = get_object_or_404(
        Conversation.objects.select_related('contact'), pk=conversation_id
    )
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    media_type = (request.POST.get('media_type') or '').strip()
    caption = (request.POST.get('caption') or '').strip()
    uploaded = request.FILES.get('file')

    if media_type not in WAPI_MEDIA_SEND_TYPES:
        return JsonResponse({'ok': False, 'error': 'Tipo de arquivo nao suportado.'}, status=400)
    if not (conversation.recipient or '').strip():
        return JsonResponse({'ok': False, 'error': 'Nao foi possivel enviar: conversa sem destino.'}, status=400)
    if not uploaded or not uploaded.size:
        return JsonResponse({'ok': False, 'error': 'Selecione um arquivo valido.'}, status=400)

    max_bytes = settings.WAPI_MEDIA_MAX_MB * 1024 * 1024
    if uploaded.size > max_bytes:
        return JsonResponse(
            {'ok': False, 'error': f'Arquivo muito grande (limite {settings.WAPI_MEDIA_MAX_MB} MB).'},
            status=400,
        )

    mimetype = (uploaded.content_type or '').split(';')[0].strip().lower()
    if not mimetype:
        mimetype = (mimetypes.guess_type(uploaded.name or '')[0] or '').lower()
    if not _media_category_ok(media_type, mimetype):
        return JsonResponse({'ok': False, 'error': 'Arquivo nao compativel com o tipo escolhido.'}, status=400)

    config = WapiConfiguration.get_solo()
    if not config.resolved_instance_id().strip() or not config.resolved_token().strip():
        return JsonResponse({'ok': False, 'error': 'Configure a W-API antes de enviar mensagens.'}, status=400)

    # A W-API so aceita audio em .mp3/.ogg. Audio gravado no navegador vem em
    # .webm (Chrome); convertemos para .ogg (opus) com ffmpeg.
    if media_type == 'audio' and mimetype not in ('audio/ogg', 'audio/mpeg', 'audio/mp3') \
            and not (uploaded.name or '').lower().endswith(('.ogg', '.mp3')):
        converted = convert_audio_to_ogg(uploaded)
        if converted is None:
            return JsonResponse(
                {'ok': False, 'error': 'Nao foi possivel preparar o audio. Grave em .ogg/.mp3 ou instale o ffmpeg no servidor.'},
                status=400,
            )
        uploaded = converted
        mimetype = 'audio/ogg'

    # A W-API exige que a URL da imagem termine em .png/.jpeg/.jpg. Garante a
    # extensao aceita e converte formatos nao suportados (webp/gif/bmp/heic...) p/ JPEG.
    if media_type == 'image':
        uploaded, mimetype = ensure_wapi_image(uploaded, mimetype)
        if uploaded is None:
            return JsonResponse(
                {'ok': False, 'error': 'Nao foi possivel preparar a imagem. Envie um JPG ou PNG, ou instale o ffmpeg no servidor.'},
                status=400,
            )

    # Salva o arquivo localmente e cria a mensagem (pendente).
    message = save_outgoing_media_message(
        conversation, media_type, uploaded, caption=caption, mimetype=mimetype
    )

    # URL publica que a W-API consegue baixar (respeita o prefixo /beezap/ via MEDIA_URL).
    public_url = request.build_absolute_uri(message.media_file.url)
    # A W-API (nuvem) baixa a midia pela URL. Em producao (dominio publico) isso
    # funciona; em ambiente local (localhost/IP privado) ela nao alcanca a URL e o
    # envio falha -> nesse caso mandamos a midia em base64 (data URI), aceito pela API.
    if _host_reachable_by_wapi(urlsplit(public_url).hostname):
        media_payload = public_url
    else:
        media_payload = _media_file_to_data_uri(message.media_file, mimetype)
    # Em grupo, destino e o JID (@g.us) — nunca o participante individual.
    phone = conversation.recipient

    if media_type == 'image':
        result = send_image_message(phone, media_payload, caption=caption or None)
    elif media_type == 'audio':
        result = send_audio_message(phone, media_payload)
    elif media_type == 'video':
        result = send_video_message(phone, media_payload, caption=caption or None)
    else:
        # A W-API exige a extensao do documento; usa a do nome e cai no mapa por mimetype.
        doc_ext = os.path.splitext(uploaded.name or '')[1].lstrip('.').lower() \
            or WAPI_DOC_EXT_BY_MIME.get(mimetype, '')
        result = send_document_message(
            phone, media_payload, file_name=uploaded.name,
            caption=caption or None, extension=doc_ext,
        )

    if result.success:
        message.status = 'sent'
        message.media_status = 'ok'
        message.external_message_id = result.message_id or ''
    else:
        message.status = 'failed'
        message.media_status = 'unavailable'
    message.save(update_fields=['status', 'media_status', 'external_message_id'])

    response = {'ok': result.success, 'message': _serialize_message(message)}
    if not result.success:
        response['error'] = result.error or 'Nao foi possivel enviar o arquivo. Tente novamente.'
    return JsonResponse(response)


@login_required
@require_POST
def conversation_sync_groups_view(request):
    """Busca os grupos na W-API e atualiza os nomes das conversas de grupo."""
    result = sync_group_names()
    if not result.get('ok'):
        return JsonResponse(
            {'ok': False, 'error': 'Nao foi possivel sincronizar os grupos. Verifique a conexao do WhatsApp.'},
            status=502,
        )
    return JsonResponse({
        'ok': True,
        'updated': result['updated'],
        'total_groups': result.get('total_groups', 0),
    })


@login_required
@require_POST
def conversation_name_contact_view(request):
    """Nomeia um numero (remetente de grupo ou mencionado) criando/atualizando um
    Contato. O nome passa a aparecer no lugar do numero nas mensagens."""
    number = _digits(request.POST.get('number'))
    name = (request.POST.get('name') or '').strip()
    if not number or not name:
        return JsonResponse({'ok': False, 'error': 'Informe o numero e o nome.'}, status=400)
    contact, _created = Contact.objects.get_or_create(phone=number, defaults={'name': name})
    if contact.name != name:
        contact.name = name
        contact.save(update_fields=['name', 'updated_at'])
    return JsonResponse({'ok': True, 'number': number, 'name': name})


@login_required
@require_POST
def conversation_transfer_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    update_fields = {'updated_at'}

    if 'attendant_id' in request.POST:
        attendant_id = (request.POST.get('attendant_id') or '').strip()
        conversation.assigned_attendant = (
            Attendant.objects.filter(pk=attendant_id).first() if attendant_id else None
        )
        update_fields.add('assigned_attendant')
    if 'sector_id' in request.POST:
        sector_id = (request.POST.get('sector_id') or '').strip()
        conversation.sector = (
            Sector.objects.filter(pk=sector_id).first() if sector_id else None
        )
        update_fields.add('sector')

    if conversation.assigned_attendant_id:
        conversation.status = 'open'
    elif conversation.sector_id:
        conversation.status = 'pending'
    else:
        conversation.status = 'open'
    update_fields.add('status')
    conversation.save(update_fields=list(update_fields))

    return JsonResponse({'ok': True, 'contact': _serialize_contact_info(conversation)})


@login_required
@require_POST
def conversation_take_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    attendant = getattr(request.user, 'attendant_profile', None)
    if attendant is None and request.user.role == User.Role.ADM:
        # O admin sempre pode assumir: provisiona o perfil de atendente na hora
        # (normalmente ja existe pelo sinal/backfill; isto e uma rede de seguranca).
        from .signals import ensure_admin_attendant
        attendant = ensure_admin_attendant(request.user)
    if attendant is None:
        return JsonResponse(
            {'ok': False, 'error': 'Esta conta nao possui perfil de atendente.'},
            status=400,
        )

    conversation.assigned_attendant = attendant
    conversation.status = 'open'
    conversation.save(update_fields=['assigned_attendant', 'status', 'updated_at'])

    return JsonResponse({'ok': True, 'contact': _serialize_contact_info(conversation)})


@login_required
@require_POST
def conversation_close_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    # Divisoria no chat marcando o fim do atendimento (o chat permanece; padrao
    # WhatsApp = um unico chat por pessoa com todo o historico).
    save_system_message(conversation, SYSTEM_CLOSE_TEXT)
    conversation.status = 'closed'
    conversation.assigned_attendant = None
    conversation.sector = None
    conversation.ai_turns = 0
    conversation.save(update_fields=['status', 'assigned_attendant', 'sector', 'ai_turns', 'updated_at'])

    return JsonResponse({'ok': True, 'contact': _serialize_contact_info(conversation)})


@login_required
def sectors_view(request):
    forbidden = require_feature(request, 'sectors')
    if forbidden:
        return forbidden

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
            'is_admin': att.user.role == User.Role.ADM,
        }
        for att in attendants
    }

    return render(
        request,
        'accounts/sectors.html',
        {
            'role': request.user.role,
            'nav_items': build_nav_items(request.user, 'Setores'),
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
    from .permissions import user_can_access
    if not user_can_access(request.user, 'sectors'):
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
        # O admin faz parte de TODOS os setores: re-inclui apos o set() do
        # arrastar-e-soltar (senao ele seria removido das filas que nao o listaram).
        admins = list(Attendant.objects.filter(user__role='adm'))
        if admins:
            for sector_obj in Sector.objects.all():
                sector_obj.attendants.add(*admins)
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

    # Log seguro para diagnostico da estrutura real: apenas nomes de chaves,
    # nunca valores, token ou payload completo.
    if isinstance(payload, dict):
        wapi_webhook_logger.info('Webhook W-API keys: %s', list(payload.keys()))
        data_node = payload.get('data')
        if isinstance(data_node, dict):
            wapi_webhook_logger.info('Webhook W-API data keys: %s', list(data_node.keys()))
            message_node = data_node.get('message')
            if isinstance(message_node, dict):
                wapi_webhook_logger.info('Webhook W-API data.message keys: %s', list(message_node.keys()))

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
