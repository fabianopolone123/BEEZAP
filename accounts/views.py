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
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    AttendantForm,
    CompanyAdminForm,
    MasterUserForm,
    CompanyForm,
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
    Company,
    CompanyAiUsage,
    Contact,
    Conversation,
    ConversationViewScope,
    GroupAccess,
    MenuBotConfiguration,
    MenuOption,
    Message,
    OpenAiConfiguration,
    PasswordResetCode,
    RoleMenuPermission,
    Sector,
    User,
    UserConversationView,
    UserMenuPermission,
    WapiConfiguration,
    WapiWebhookEvent,
)
from gpt.client import test_connection as gpt_test_connection
from wapi.client import (
    check_connection as wapi_check_connection,
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
    resolve_webhook_company,
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
def build_service_status(company):
    """Status simples do WhatsApp e da IA para MOSTRAR AO CLIENTE.

    Sem nenhuma credencial: apenas "esta configurado?" e "esta disponivel?". As
    credenciais (instancia/token da W-API por empresa e a API Key do GPT da
    plataforma) sao do gestor master.
    """
    wapi = WapiConfiguration.for_company(company)
    whatsapp_ok = bool(
        wapi.resolved_instance_id().strip() and wapi.resolved_token().strip()
    )
    ai_ok = OpenAiConfiguration.get_solo().has_api_key
    return {
        'whatsapp_ok': whatsapp_ok,
        'whatsapp_label': 'WhatsApp conectado' if whatsapp_ok else 'WhatsApp ainda não configurado',
        'whatsapp_help': (
            'As mensagens do seu WhatsApp chegam normalmente aqui.' if whatsapp_ok else
            'Fale com o administrador da plataforma para ligar o seu WhatsApp.'
        ),
        'ai_ok': ai_ok,
        'ai_label': 'Inteligência (IA) disponível' if ai_ok else 'Inteligência (IA) indisponível',
        'ai_help': (
            'Você pode escolher a IA como primeiro atendimento.' if ai_ok else
            'A IA ainda não foi liberada pelo administrador da plataforma.'
        ),
    }


def build_settings_tabs(active_tab, active_subtab='', company=None):
    return {
        'active_tab': active_tab,
        'active_subtab': active_subtab,
        # O modo de primeiro atendimento e DA EMPRESA (multiempresa).
        'reception_mode': MenuBotConfiguration.for_company(company).mode if company else '',
    }


def set_active_company(request, company):
    """Entra/sai do painel de um cliente (modo suporte do master)."""
    from .tenancy import set_active_company as _set_active_company
    return _set_active_company(request, company)


def current_company(request):
    """Empresa da requisicao (ver accounts/tenancy.py)."""
    from .tenancy import current_company as _current_company
    return _current_company(request)


def require_master_in_company(request):
    """Telas TECNICAS de um cliente (ex.: credenciais da W-API): so o gestor master,
    e so quando ele esta DENTRO do painel daquele cliente (modo suporte).

    Retorna um redirect amigavel quando o master ainda nao escolheu a empresa, e 403
    para qualquer outro perfil — o cliente nao mexe em credencial.
    """
    from .tenancy import is_master
    if not is_master(request.user):
        return HttpResponseForbidden(
            'As configurações do WhatsApp são feitas pelo administrador da plataforma.'
        )
    if current_company(request) is None:
        messages.info(
            request,
            'Escolha um cliente e use "Entrar no painel" para configurar o WhatsApp dele.',
        )
        return redirect('clients')
    return None


def master_in_company(request):
    """O gestor master esta no MODO SUPORTE (entrou no painel de um cliente)?

    Nesse modo ele alcanca SO a tela WhatsApp daquele cliente (credenciais da
    W-API) — nada do negocio da empresa e nunca Conversas/Contatos. Ver
    accounts/permissions.WHATSAPP_ITEM e accounts/tenancy.py.
    """
    from .tenancy import current_company, is_master
    return is_master(request.user) and current_company(request) is not None


def build_nav_items(user, active_label, request=None):
    """Itens do menu conforme as PERMISSOES do usuario (ver accounts/permissions.py)."""
    from .permissions import nav_items_for
    in_company = master_in_company(request) if request is not None else False
    return nav_items_for(user, active_label, in_company=in_company)


def require_master(request):
    """Retorna 403 se quem chamou nao e o gestor master; senao None."""
    from .tenancy import require_master as _require_master
    return _require_master(request)


def request_company(request):
    """EMPRESA CLIENTE da requisicao — a dona de tudo o que for criado/consultado.

    Normalmente e a empresa do usuario logado (ver accounts/tenancy.py). A
    retaguarda para a empresa padrao existe para nunca gravar um registro sem
    empresa (o campo e obrigatorio) caso um usuario antigo esteja sem vinculo.
    """
    from .tenancy import current_company
    return current_company(request) or Company.get_default()


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


def deny_readonly_json(request):
    """Retorna 403 JSON se o usuario e SOMENTE LEITURA (perfil leitor); senao None.
    Usado nos endpoints AJAX que alteram dados (enviar, assumir, encerrar, etc.)."""
    from .permissions import is_read_only
    if is_read_only(request.user):
        return JsonResponse(
            {'ok': False, 'error': 'Seu perfil e somente leitura: voce pode visualizar, mas nao executar acoes.'},
            status=403,
        )
    return None


def block_readonly(request):
    """Retorna 403 (pagina) se o usuario e SOMENTE LEITURA; senao None. Usado no
    tratamento de POST das telas de formulario (contatos, atendentes, setores,
    configuracoes)."""
    from .permissions import is_read_only
    if is_read_only(request.user):
        return HttpResponseForbidden('Seu perfil e somente leitura.')
    return None


def _current_attendant_name(request):
    """Nome de quem esta enviando (mostrado em GRUPO, que e um numero so)."""
    attendant = getattr(request.user, 'attendant_profile', None)
    if attendant and attendant.name:
        return attendant.name
    return request.user.get_full_name() or request.user.email


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
    """WhatsApp que recebe o codigo de recuperacao.

    Quem tem perfil de atendente usa o telefone de la; o GESTOR MASTER nao tem
    empresa nem `Attendant`, entao usa o `recovery_phone` cadastrado na tela Gestores.
    """
    try:
        phone = user.attendant_profile.phone
    except Attendant.DoesNotExist:
        phone = user.recovery_phone
    return Attendant.normalize_phone(phone)


def clear_password_recovery_session(request):
    for key in (
        PASSWORD_RECOVERY_CODE_ID_KEY,
        PASSWORD_RECOVERY_EMAIL_KEY,
        PASSWORD_RECOVERY_VERIFIED_ID_KEY,
    ):
        request.session.pop(key, None)


def create_and_send_password_recovery_code(user, phone):
    # O codigo vai pelo WhatsApp DA EMPRESA da pessoa. O gestor master nao tem
    # empresa, entao usa a instancia da empresa padrao (a da propria plataforma).
    company = user.company or Company.get_default()
    code = f'{secrets.randbelow(1000000):06d}'
    now = timezone.now()
    PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    reset_code = PasswordResetCode.objects.create(
        user=user,
        code_hash=make_password(code),
        expires_at=now + timedelta(minutes=10),
    )
    message = (
        f'Seu codigo de recuperacao de senha do BEEonBOARD e: {code}\n\n'
        'Este codigo expira em 10 minutos.'
    )
    result = send_text_message(phone=phone, message=message, company=company)
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


def create_wapi_webhook_event(payload, company):
    """Registra o evento BRUTO do webhook na empresa dona e alimenta as Conversas.

    A empresa vem resolvida pela view (identificador na URL, `instanceId` do payload
    ou empresa padrao — ver `resolve_webhook_company`).
    """
    parsed_payload = parse_wapi_webhook_payload(payload)
    event = WapiWebhookEvent.objects.create(
        company=company,
        raw_payload=payload if isinstance(payload, dict) else {},
        **parsed_payload,
    )

    # Integra com Conversas reais: detecta grupo vs direta, resolve a conversa
    # certa e cria a mensagem (texto/reacao/midia). Falha aqui nunca deve derrubar
    # o webhook — o evento bruto ja foi salvo acima em WapiWebhookEvent.
    try:
        ingest_wapi_payload(payload, company=company)
    except Exception:
        wapi_webhook_logger.exception('Falha ao criar conversa a partir do webhook W-API.')

    return event


def is_valid_wapi_webhook_token(request, company):
    """Valida o token de webhook DA EMPRESA (cada cliente tem o seu, opcional)."""
    config = WapiConfiguration.for_company(company)
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


def build_wapi_webhook_url(request, company=None):
    """URL de webhook que o cliente deve cadastrar na W-API.

    MULTIEMPRESA: com empresa, devolve a URL PROPRIA dela
    (`.../webhook/wapi/<empresa>/`) — e assim que o sistema sabe de quem e cada
    mensagem quando ha mais de um cliente. Com FORCE_SCRIPT_NAME=/beeonboard, o reverse
    ja inclui o prefixo.
    """
    if company is not None:
        return request.build_absolute_uri(
            reverse('wapi-webhook-company', args=[company.slug])
        )
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


def build_dashboard_context(company):
    """Métricas reais do dashboard a partir do banco (conversas/mensagens/setores).

    MULTIEMPRESA: todos os numeros sao SOMENTE da empresa informada — nenhum
    indicador mistura clientes."""
    from django.db.models import Count

    today = timezone.localdate()
    start_7 = today - timedelta(days=6)
    convs = Conversation.objects.filter(company=company)

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
    # Coordenadas em PORCENTAGEM (0-100). O SVG (linha/area) e as legendas HTML usam
    # a mesma referencia, entao ficam alinhados. Faixa util: x 6..94, y 14..86 (deixa
    # margem em cima para o numero e embaixo para a data — nada e cortado).
    BASELINE = 86.0
    chart_points = []
    for i, (d, c) in enumerate(day_counts):
        left = 6 + (i / 6) * 88
        topp = 14 + (1 - c / max_v) * (BASELINE - 14)
        chart_points.append({
            'left': round(left, 2), 'top': round(topp, 2),
            'label': d.strftime('%d/%m'), 'value': c,
        })
    chart_polyline = ' '.join(f"{p['left']},{p['top']}" for p in chart_points)
    chart_area = (
        f"{chart_points[0]['left']},{BASELINE} " + chart_polyline
        + f" {chart_points[-1]['left']},{BASELINE}"
    )
    # Linhas de grade horizontais (topo, meio, base) — sem numeros no eixo.
    chart_gridlines = [14.0, (14.0 + BASELINE) / 2, BASELINE]

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

    context = build_dashboard_context(request_company(request))
    context.update({
        'role': request.user.role,
        'role_label': request.user.get_role_display(),
        'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        'nav_items': build_nav_items(request.user, 'Dashboard', request),
        'today_str': timezone.localdate().strftime('%d/%m/%Y'),
    })
    return render(request, 'accounts/dashboard.html', context)


@login_required
def openai_settings_view(request):
    """Tela INTELIGENCIA (IA) — configuracao da PLATAFORMA, exclusiva do gestor
    master (as empresas clientes nem enxergam esta tela).

    Aqui ficam a API Key do GPT, o modelo, o prompt/persona, o limite de respostas,
    o teste de conexao e o consumo acumulado de tokens. E UMA configuracao para
    todos os clientes: quem paga a conta da OpenAI e o master. Cada empresa apenas
    decide SE usa IA, chatbot de menu ou nada, no seletor de modo da tela
    Atendimento dela (`MenuBotConfiguration.mode`).
    """
    forbidden = require_master(request)
    if forbidden:
        return forbidden

    from gpt.attendant import DEFAULT_INSTRUCTIONS, resolved_instructions

    config = OpenAiConfiguration.get_solo()
    config_form = OpenAiConfigurationForm(
        request.POST if request.POST.get('form_type') == 'config' else None,
        initial={
            'model': config.resolved_model(),
            'instructions': config.instructions,
            'max_turns': config.max_turns,
        },
    )

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
        form_type = request.POST.get('form_type')
        if form_type == 'config' and config_form.is_valid():
            new_key = config_form.cleaned_data['api_key'].strip()
            if new_key:
                config.api_key = new_key
            config.model = (config_form.cleaned_data['model'] or 'gpt-4.1-nano').strip()
            config.instructions = (config_form.cleaned_data['instructions'] or '').strip()
            config.max_turns = config_form.cleaned_data['max_turns'] or 3
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
            'nav_items': build_nav_items(request.user, 'Inteligência (IA)', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'api_key_configured': config.has_api_key,
            'usage_total_tokens': _fmt_int(config.total_tokens),
            'usage_prompt_tokens': _fmt_int(config.total_prompt_tokens),
            'usage_completion_tokens': _fmt_int(config.total_completion_tokens),
            'usage_requests': _fmt_int(config.total_requests),
            # Quantas empresas estao com a IA ligada (o master ve o alcance da chave).
            'companies_using_ai': MenuBotConfiguration.objects.filter(
                mode=MenuBotConfiguration.MODE_AI, company__is_active=True
            ).select_related('company').count(),
            # Pre-visualizacao do prompt (os setores/atendentes sao anexados na hora
            # da conversa, com os dados DA EMPRESA daquele atendimento).
            'preview_instructions': resolved_instructions(config),
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

    # O chatbot de cada empresa tem os SEUS textos, opcoes, tentativas e fallback.
    company = request_company(request)
    config = MenuBotConfiguration.for_company(company)
    config_form = MenuBotConfigurationForm(
        request.POST if request.POST.get('form_type') == 'chatbot' else None,
        company=company,
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

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
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

    sectors = list(Sector.objects.filter(company=company).order_by('name'))
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
            'nav_items': build_nav_items(request.user, 'Configurações', request),
            'settings_tabs': build_settings_tabs('atendimento', 'chatbot', company),
            'mode_form': ReceptionModeForm(initial={'mode': config.mode}),
            'menu_active': config.mode == MenuBotConfiguration.MODE_MENU,
            # Card de STATUS para o cliente: ele precisa saber se o WhatsApp esta
            # ligado e se a IA esta disponivel, mas NUNCA ve Instance ID, token ou
            # API Key (isso e do master). Ver docs/CONTEXTO.md secao 16.
            'service_status': build_service_status(company),
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
        sector = (
            Sector.objects.filter(company=config.company_id, pk=sector_id).first()
            if sector_id else None
        )
        MenuOption.objects.create(config=config, order=order, label=label, sector=sector)


@login_required
@require_POST
def atendimento_set_mode_view(request):
    """Salva o MODO mestre de primeiro atendimento (desligado/chatbot/IA) e volta
    para a sub-aba de origem. Apenas ADM."""
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden
    blocked = block_readonly(request)
    if blocked:
        return blocked
    company = request_company(request)
    config = MenuBotConfiguration.for_company(company)
    form = ReceptionModeForm(request.POST)
    if form.is_valid():
        config.mode = form.cleaned_data['mode']
        config.save(update_fields=['mode', 'updated_at'])
        # `OpenAiConfiguration.enabled` e vestigial (a ativacao real vem do `mode`
        # de cada empresa), mas como a config do GPT e UMA da plataforma, ele nao
        # pode ser desligado so porque UM cliente saiu da IA: reflete se ALGUMA
        # empresa ativa esta usando a IA.
        ai = OpenAiConfiguration.get_solo()
        ai.enabled = MenuBotConfiguration.objects.filter(
            mode=MenuBotConfiguration.MODE_AI, company__is_active=True
        ).exists()
        ai.save(update_fields=['enabled', 'updated_at'])
        messages.success(request, 'Modo de atendimento atualizado.')
    # A tela de IA saiu da area do cliente (virou da plataforma), então o retorno e
    # sempre para o Atendimento.
    return redirect('atendimento')


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
        role_allowed_keys, allowed_keys_for, history_full_for, effective_view_scope,
    )
    from .tenancy import is_master
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    # MULTIEMPRESA: esta tela decide quem ve o que, então TODA consulta aqui e
    # restrita a empresa de quem esta logado — pessoas, setores e grupos de outro
    # cliente nunca aparecem nem podem ser alvo de um POST forjado.
    company = request_company(request)
    company_users = User.objects.filter(company=company)
    # A aba GRUPOS lista os grupos de WhatsApp do cliente pelo NOME — isso e conteudo
    # do atendimento, nao configuracao de plataforma; quem libera grupo e o
    # Administrador da empresa. Hoje o master ja nem chega aqui (esta tela inteira e
    # do ADM — ver require_feature acima), mas a checagem fica como segunda barreira.
    show_groups_tab = not is_master(request.user)

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        # Esconder a aba nao basta: o master tambem nao pode MEXER nos grupos do
        # cliente por um POST forjado (renomear, remover ou liberar acesso).
        if not show_groups_tab and form_type in ('groups', 'group-name', 'group-remove'):
            if is_ajax:
                return JsonResponse(
                    {'ok': False, 'error': 'O gestor master nao administra os grupos do cliente.'},
                    status=403,
                )
            return HttpResponseForbidden('O gestor master nao administra os grupos do cliente.')

        if form_type == 'roles':
            for entry in EDITABLE_ROLES:
                role = entry['role']
                chosen = [k for k in ALL_FEATURE_KEYS
                          if request.POST.get(f'role__{role}__{k}') == 'on']
                RoleMenuPermission.objects.update_or_create(
                    company=request_company(request),
                    role=role,
                    defaults={'allowed_keys': chosen},
                )
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, 'Permissoes dos perfis salvas.')
            return redirect('permissions')

        if form_type == 'user':
            user_id = (request.POST.get('user_id') or '').strip()
            target = company_users.filter(pk=user_id).first() if user_id else None
            if not target or target.role == 'adm':
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Selecione um usuario valido.'}, status=400)
                messages.error(request, 'Selecione um usuario valido.')
                return redirect('permissions')
            chosen = [k for k in ALL_FEATURE_KEYS
                      if request.POST.get(f'userkey__{k}') == 'on']
            UserMenuPermission.objects.update_or_create(
                user=target,
                defaults={'allowed_keys': chosen},
            )
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, f'Permissoes de {target.email} salvas.')
            return redirect(f'{reverse("permissions")}?tab=botoes&user={target.id}')

        if form_type == 'user-reset':
            user_id = (request.POST.get('user_id') or '').strip()
            UserMenuPermission.objects.filter(
                user_id=user_id, user__company=company
            ).delete()
            messages.success(request, 'Personalizacao removida (voltou ao padrao do perfil).')
            return redirect(f'{reverse("permissions")}?tab=botoes&user={user_id}')

        # ----- Aba "Visualização de conversas" -----
        valid_scopes = {c.value for c in ConversationViewScope}

        if form_type == 'view-sectors':
            for sector in Sector.objects.filter(company=company):
                scope = (request.POST.get(f'sector__{sector.id}__scope') or '').strip()
                if scope not in valid_scopes:
                    scope = ConversationViewScope.SECTOR_OPEN
                full = request.POST.get(f'sector__{sector.id}__full_history') == 'on'
                Sector.objects.filter(company=company, pk=sector.id).update(
                    view_scope=scope, view_full_history=full
                )
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, 'Visualizacao por setor salva.')
            return redirect(f'{reverse("permissions")}?tab=visualizacao')

        if form_type == 'view-user':
            user_id = (request.POST.get('user_id') or '').strip()
            target = company_users.filter(pk=user_id).exclude(role='adm').first() if user_id else None
            if not target:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Selecione um usuario valido.'}, status=400)
                messages.error(request, 'Selecione um usuario valido.')
                return redirect(f'{reverse("permissions")}?tab=visualizacao')
            scope_raw = (request.POST.get('user_scope') or '').strip()
            scope = scope_raw if scope_raw in valid_scopes else None  # '' = herdar do setor
            fh_raw = (request.POST.get('user_full_history') or 'inherit').strip()
            full_history = None if fh_raw == 'inherit' else (fh_raw == 'yes')
            if scope is None and full_history is None:
                # Sem nenhuma personalizacao: remove a linha (volta a herdar do setor).
                UserConversationView.objects.filter(user=target).delete()
            else:
                UserConversationView.objects.update_or_create(
                    user=target,
                    defaults={'view_scope': scope, 'view_full_history': full_history},
                )
            if is_ajax:
                return JsonResponse({'ok': True})
            messages.success(request, f'Visualizacao de {target.email} salva.')
            return redirect(f'{reverse("permissions")}?tab=visualizacao&user={target.id}')

        if form_type == 'view-user-reset':
            user_id = (request.POST.get('user_id') or '').strip()
            UserConversationView.objects.filter(
                user_id=user_id, user__company=company
            ).delete()
            messages.success(request, 'Personalizacao de visualizacao removida (voltou ao padrao do setor).')
            return redirect(f'{reverse("permissions")}?tab=visualizacao&user={user_id}')

        if form_type == 'profile-role':
            user_id = (request.POST.get('user_id') or '').strip()
            new_role = (request.POST.get('role') or '').strip()
            valid_roles = {User.Role.ADM, User.Role.USUARIO, User.Role.LEITOR}
            target = company_users.filter(pk=user_id).first() if user_id else None
            if not target or new_role not in valid_roles:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Selecione um perfil valido.'}, status=400)
                messages.error(request, 'Selecione um perfil valido.')
                return redirect('permissions')
            # Nao deixa o admin mudar o proprio perfil (evita se trancar fora).
            if target.id == request.user.id:
                if is_ajax:
                    return JsonResponse(
                        {'ok': False, 'error': 'Voce nao pode alterar o seu proprio perfil.'}, status=400
                    )
                messages.error(request, 'Voce nao pode alterar o seu proprio perfil.')
                return redirect('permissions')
            # Nao deixa o sistema ficar sem nenhum administrador.
            if target.role == User.Role.ADM and new_role != User.Role.ADM:
                # Cada empresa precisa manter ao menos um administrador proprio.
                admin_count = company_users.filter(role=User.Role.ADM, is_active=True).count()
                if admin_count <= 1:
                    if is_ajax:
                        return JsonResponse(
                            {'ok': False, 'error': 'Deve existir pelo menos um administrador.'}, status=400
                        )
                    messages.error(request, 'Deve existir pelo menos um administrador.')
                    return redirect('permissions')
            target.role = new_role
            target.save(update_fields=['role'])  # dispara o sinal (provisiona atendente se virar adm)
            role_label = target.get_role_display()
            name = target.get_full_name() or getattr(getattr(target, 'attendant_profile', None), 'name', '') or target.email
            if is_ajax:
                return JsonResponse({'ok': True, 'message': f'{name} agora e {role_label}.'})
            messages.success(request, f'{name} agora e {role_label}.')
            return redirect('permissions')

        if form_type == 'group-name':
            gid = (request.POST.get('group_id') or '').strip()
            name = (request.POST.get('name') or '').strip()
            conv = (
                Conversation.objects.filter(company=company, pk=gid, chat_type='group').first()
                if gid else None
            )
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
                deleted, _ = Conversation.objects.filter(
                    company=company, pk=gid, chat_type='group'
                ).delete()
            if is_ajax:
                return JsonResponse({'ok': bool(deleted)})
            messages.success(request, 'Grupo removido da lista.')
            return redirect(f'{reverse("permissions")}?tab=grupos')

        if form_type == 'groups':
            group_ids = (
                Conversation.objects
                .filter(company=company, chat_type='group')
                .values_list('id', flat=True)
            )
            valid_sector_ids = set(
                Sector.objects.filter(company=company).values_list('id', flat=True)
            )
            attendant_user_ids = set(
                company_users.filter(attendant_profile__isnull=False).values_list('id', flat=True)
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
    roles_ctx = []
    for entry in EDITABLE_ROLES:
        keys = role_allowed_keys(entry['role'], company)
        roles_ctx.append({
            'role': entry['role'],
            'label': entry['label'],
            'features': [
                {**f, 'checked': f['key'] in keys} for f in MENU_FEATURES
            ],
        })

    users = list(
        company_users.exclude(role='adm').filter(is_active=True).order_by('email')
    )
    override_ids = set(
        UserMenuPermission.objects
        .filter(user__company=company)
        .values_list('user_id', flat=True)
    )
    users_ctx = [
        {'id': u.id, 'email': u.email, 'name': u.get_full_name() or u.email,
         'role_label': u.get_role_display(), 'custom': u.id in override_ids}
        for u in users
    ]

    selected_id = (request.GET.get('user') or '').strip()
    selected = company_users.filter(pk=selected_id).exclude(role='adm').first() if selected_id else None
    selected_ctx = None
    if selected:
        keys = allowed_keys_for(selected)
        selected_ctx = {
            'id': selected.id,
            'email': selected.email,
            'name': selected.get_full_name() or selected.email,
            'role_label': selected.get_role_display(),
            'custom': selected.id in override_ids,
            'features': [{**f, 'checked': f['key'] in keys} for f in MENU_FEATURES],
        }

    # ----- Aba Grupos -----
    sectors = list(Sector.objects.filter(company=company).order_by('name'))
    attendant_users = list(
        company_users.filter(attendant_profile__isnull=False, is_active=True)
        .select_related('attendant_profile').order_by('email')
    )
    groups = (
        Conversation.objects.filter(company=company, chat_type='group')
        .prefetch_related('access__sectors', 'access__users')
        .order_by('name', 'external_id')
    ) if show_groups_tab else []
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

    # ----- Aba Perfis (papel de cada pessoa) -----
    def _initials(name, email):
        base = (name or '').strip()
        if base:
            parts = [p for p in base.split() if p]
            if len(parts) == 1:
                return parts[0][:2].upper()
            return (parts[0][:1] + parts[-1][:1]).upper()
        return (email or '?')[:2].upper()

    people_qs = (
        company_users.filter(is_active=True)
        .select_related('attendant_profile')
        .order_by('first_name', 'email')
    )
    people_ctx = []
    for u in people_qs:
        attendant = getattr(u, 'attendant_profile', None)
        real_name = u.get_full_name() or (attendant.name if attendant else '')
        people_ctx.append({
            'id': u.id,
            'name': real_name or u.email,
            'email': u.email,
            'initials': _initials(real_name, u.email),
            'role': u.role,
            'is_self': u.id == request.user.id,
        })

    role_options = [
        {'value': User.Role.ADM, 'label': 'Administrador', 'icon': '👑'},
        {'value': User.Role.USUARIO, 'label': 'Usuário', 'icon': '🎧'},
        {'value': User.Role.LEITOR, 'label': 'Leitor', 'icon': '👁️'},
    ]

    # ----- Aba "Visualização de conversas" -----
    # Niveis de alcance em ORDEM (menos -> mais visualizacao); o slider usa o indice.
    scope_levels = [{'value': c.value, 'label': c.label} for c in ConversationViewScope]
    scope_order = [c['value'] for c in scope_levels]
    scope_label_by_value = {c['value']: c['label'] for c in scope_levels}

    def scope_level_index(value):
        return scope_order.index(value) if value in scope_order else scope_order.index('sector_open')

    view_sectors_ctx = []
    for s in sectors:
        view_sectors_ctx.append({
            'id': s.id,
            'name': s.name,
            'is_general': s.is_general,
            'full_history': s.view_full_history,
            'scope_value': s.view_scope,
            'scope_level': scope_level_index(s.view_scope),
            'scope_label': scope_label_by_value.get(s.view_scope, ''),
        })
    view_selected_ctx = None
    if selected:
        ov = UserConversationView.objects.filter(user=selected).first()  # selected ja e da empresa
        ov_scope = ov.view_scope if ov else None
        ov_full = ov.view_full_history if ov else None
        if ov_full is None:
            fh_value = 'inherit'
        else:
            fh_value = 'yes' if ov_full else 'no'
        view_selected_ctx = {
            'id': selected.id,
            'name': selected.get_full_name() or selected.email,
            'custom': bool(ov and ov.is_customized),
            'effective_scope': effective_view_scope(selected),
            'effective_full_history': history_full_for(selected),
            'inherit_scope': ov_scope is None,
            # Se herda, o slider comeca no efetivo (so referencia); senao no override.
            'scope_value': ov_scope or '',
            'scope_level': scope_level_index(ov_scope or effective_view_scope(selected)),
            'scope_label': scope_label_by_value.get(ov_scope, '') if ov_scope else '',
            'full_history_value': fh_value,
        }

    tab = request.GET.get('tab')
    active_tab = tab if tab in ('people', 'botoes', 'grupos', 'visualizacao') else 'people'
    if active_tab == 'grupos' and not show_groups_tab:
        active_tab = 'people'

    return render(
        request,
        'accounts/permissions.html',
        {
            'show_groups_tab': show_groups_tab,
            'nav_items': build_nav_items(request.user, 'Permissões', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'features': MENU_FEATURES,
            'roles': roles_ctx,
            'users': users_ctx,
            'selected_user': selected_ctx,
            'groups': groups_ctx,
            'has_sectors': bool(sectors),
            'people': people_ctx,
            'role_options': role_options,
            'view_sectors': view_sectors_ctx,
            'view_selected': view_selected_ctx,
            'scope_levels': scope_levels,
            'active_tab': active_tab,
        },
    )


@login_required
def wapi_settings_view(request):
    """Tela WhatsApp (W-API) de UMA empresa cliente — exclusiva do gestor master.

    Cada cliente tem a SUA instancia e o SEU token da W-API (parte tecnica, com
    credencial), então quem configura e o master, entrando no painel do cliente. O
    cliente nao acessa esta tela; ele ve apenas um aviso de status na tela
    Atendimento (ver `atendimento_view`).
    """
    forbidden = require_master_in_company(request)
    if forbidden:
        return forbidden

    # Cada empresa tem a SUA instancia/token da W-API e o SEU webhook.
    company = request_company(request)
    config = WapiConfiguration.for_company(company)
    config_form = WapiConfigurationForm(
        request.POST if request.POST.get('form_type') == 'config' else None,
        initial={'instance_id': config.instance_id},
    )
    send_form = WapiSendTextForm(
        request.POST if request.POST.get('form_type') == 'send-test' else None,
    )

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
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
                company=company,
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
            'webhook_url': build_wapi_webhook_url(request, company),
            'latest_webhook_events': WapiWebhookEvent.objects.filter(company=company)[:5],
            'nav_items': build_nav_items(request.user, 'Configurações', request),
            'settings_tabs': build_settings_tabs('whatsapp', company=company),
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

    events = WapiWebhookEvent.objects.filter(company=request_company(request))[:5]
    return JsonResponse({
        'ok': True,
        'events': [serialize_wapi_event(event) for event in events],
    })


@login_required
def attendants_view(request):
    forbidden = require_feature(request, 'attendants')
    if forbidden:
        return forbidden

    company = request_company(request)
    attendants = Attendant.objects.select_related('user').filter(company=company)
    form = AttendantForm()
    modal_mode = 'create'
    show_modal = False
    editing_attendant = None

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
        attendant_id = request.POST.get('attendant_id')
        if attendant_id:
            # Escopo da empresa: id de atendente de outro cliente da 404.
            editing_attendant = get_object_or_404(
                Attendant, pk=attendant_id, company=company
            )
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
                        # NAO mexe no perfil (role) aqui: o papel de cada pessoa e
                        # definido na tela Permissoes (aba Perfis). Editar os dados do
                        # atendente nao deve rebaixar/alterar o perfil escolhido la.
                        user.save()

                        editing_attendant.name = name
                        editing_attendant.phone = phone
                        editing_attendant.save()
                        messages.success(request, 'Atendente atualizado com sucesso.')
                    else:
                        # O atendente novo nasce na MESMA empresa de quem cadastrou.
                        user = User.objects.create_user(
                            email=email,
                            password='1234',
                            role=User.Role.USUARIO,
                            first_name=first_name,
                            last_name=last_name,
                            company=company,
                        )
                        Attendant.objects.create(
                            company=company,
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
            'nav_items': build_nav_items(request.user, 'Atendentes', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


@login_required
def change_initial_password_view(request):
    # O gestor master nao tem `Attendant`: a marca dele fica no proprio User.
    try:
        attendant = request.user.attendant_profile
    except Attendant.DoesNotExist:
        attendant = None

    if not (request.user.must_change_password or (attendant and attendant.must_change_password)):
        return redirect('dashboard')

    form = InitialPasswordChangeForm(request.POST or None, user=request.user)
    if request.method == 'POST':
        if form.is_valid():
            request.user.set_password(form.cleaned_data['new_password'])
            request.user.must_change_password = False
            request.user.save(update_fields=['password', 'must_change_password'])
            if attendant is not None:
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


def _serialize_conversation_item(conversation, current_user=None):
    sector_name = conversation.sector.name if conversation.sector_id else ''
    attendant_name = conversation.assigned_attendant.name if conversation.assigned_attendant_id else ''
    # "comigo" (destaque azul / "Em conversa com você"): atribuida a mim e AINDA ATIVA.
    # Finalizada NAO conta como "comigo" (nao fica azul).
    mine = bool(
        current_user is not None
        and conversation.status != 'closed'
        and conversation.assigned_attendant_id
        and conversation.assigned_attendant.user_id == current_user.id
    )
    queue_label = ''
    if conversation.status == 'closed':
        queue_label = 'Finalizado'
    elif conversation.status == 'pending' and sector_name and not attendant_name:
        queue_label = f'Aguardando {sector_name}'
    elif mine:
        queue_label = 'Em conversa com você'
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
        'mine': mine,
        'queue_label': queue_label,
    }


# Mencao no texto do WhatsApp: "@<numero/LID>" (o app resolve para o nome).
_MENTION_RE = re.compile(r'@(\d{7,})')


def _digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _build_name_map(conversation):
    """Mapa {digitos: nome} dos participantes do grupo, para exibir o remetente e
    resolver mencoes (@numero). Fonte UNICA: Contato CADASTRADO. O pushName do
    WhatsApp NAO entra aqui — sem contato cadastrado o numero fica visivel (e
    clicavel, para cadastrar na hora); so nome cadastrado aparece como nome."""
    numbers = set()  # numeros relevantes (remetentes + mencionados)
    rows = conversation.messages.values_list('sender_id', 'text')
    for sender_id, text in rows:
        digits = _digits(sender_id)
        if digits:
            numbers.add(digits)
        for mentioned in _MENTION_RE.findall(text or ''):
            numbers.add(mentioned)
    names = {}
    if numbers:
        contacts = Contact.objects.filter(
            company=conversation.company, phone__in=numbers
        ).values_list('phone', 'name')
        for phone, cname in contacts:
            if cname and cname.strip():
                names[phone] = cname.strip()
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
    if message.direction == 'out':
        # Enviada: mostra quem mandou (atendente) — usado no GRUPO (numero unico).
        sender_display = message.sender_name
    elif name_map is None:
        sender_display = message.sender_name
    else:
        sender_display = name_map.get(_digits(message.sender_id), '')
    return {
        'id': message.id,
        'type': 'sent' if message.direction == 'out' else 'received',
        'kind': message.message_type,
        'text': _resolve_mentions(message.text, name_map),
        'time': timezone.localtime(message.created_at).strftime('%H:%M'),
        'date': timezone.localtime(message.created_at).strftime('%d/%m/%Y'),
        'status': message.status,
        'media_url': message.resolved_media_url,
        'media_mimetype': message.media_mimetype,
        'media_status': message.media_status,
        # Nome real do arquivo (documento) para baixar com nome/extensao corretos.
        'filename': document_filename(message) if message.message_type == 'document' else '',
        # Em grupo, o front mostra o nome de quem enviou (so o do Contato CADASTRADO).
        # Sem cadastro fica vazio e o front exibe o numero (sender_id) clicavel.
        'is_group': message.is_group,
        'from_me': message.from_me,
        'sender_name': sender_display,
        'sender_id': message.sender_id,
    }


def _serialize_contact_info(conversation, current_user=None):
    contact = conversation.contact
    attendant = conversation.assigned_attendant
    is_group = conversation.is_group
    created_source = contact.created_at if contact else conversation.created_at
    mine = bool(
        current_user is not None
        and conversation.status != 'closed'
        and conversation.assigned_attendant_id
        and conversation.assigned_attendant.user_id == current_user.id
    )
    return {
        'name': conversation.display_title,
        'initials': conversation.display_initials,
        'phone': contact.phone if contact else '',
        # Nome REALMENTE cadastrado no Contato (vazio quando ninguem cadastrou ainda).
        # `name` acima cai para o numero nesse caso, entao o front usa este campo para
        # saber se ainda falta cadastrar (e para nao pre-preencher o modal com o numero).
        'contact_name': (contact.name or '').strip() if contact else '',
        'is_group': is_group,
        'chat_type': conversation.chat_type,
        'status': conversation.status,  # cru (open/pending/closed) — o front decide os botoes
        'mine': mine,                   # atribuida ao usuario logado (esconde "Assumir")
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
    ('em-atendimento', 'Conversando'),
    ('aguardando', 'Aguardando'),
    ('finalizadas', 'Finalizadas'),
)


def _filter_conversations_by_status(queryset, status):
    if status == 'nao-lidas':
        return queryset.filter(unread_count__gt=0)
    if status == 'em-atendimento':
        return queryset.filter(assigned_attendant__isnull=False).exclude(status='closed')
    if status == 'aguardando':
        # Fila de atendimento: so conversas DIRETAS (grupo nao entra em "aguardando").
        return queryset.filter(
            assigned_attendant__isnull=True, chat_type='private'
        ).exclude(status='closed')
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
    from .permissions import visible_conversations, is_read_only
    role = request.user.role
    read_only = is_read_only(request.user)
    conversations = visible_conversations(
        request.user,
        Conversation.objects.select_related('contact', 'assigned_attendant', 'sector'),
    )
    counts = _conversation_counts(conversations)
    # "Aguardando" saiu dos chips e virou um badge pulsante ao lado dos botoes do topo
    # (ver template/JS). O count continua vindo de `counts['aguardando']`.
    filter_chips = [
        {'key': slug, 'label': label, 'count': counts.get(slug, 0), 'active': slug == 'todas'}
        for slug, label in CONVERSATION_FILTERS if slug != 'aguardando'
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
            'nav_items': build_nav_items(request.user, 'Conversas', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'conversations': [_serialize_conversation_item(c, request.user) for c in conversations],
            'filter_chips': filter_chips,
            'type_tabs': type_tabs,
            'waiting_count': counts.get('aguardando', 0),
            'read_only': read_only,
        },
    )


@login_required
def company_data_view(request):
    """Aba MEUS DADOS (Configuracoes do cliente): portabilidade da empresa.

    Explica o que vem no ZIP e oferece o download. Quem exporta e o cliente — os
    dados sao dele. O gestor master NAO exporta: ele administra sem ler o
    atendimento (docs/CONTEXTO.md secao 16), e um ZIP com todas as conversas seria
    justamente ler tudo de uma vez.
    """
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden
    blocked = _deny_master_export(request)
    if blocked:
        return blocked

    company = request_company(request)
    from .models import Contact, Conversation, Message
    return render(
        request,
        'accounts/company_data.html',
        {
            'nav_items': build_nav_items(request.user, 'Configurações', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'settings_tabs': build_settings_tabs('dados', company=company),
            'company': company,
            'resumo': {
                'contatos': Contact.objects.filter(company=company).count(),
                'conversas': Conversation.objects.filter(company=company).count(),
                'mensagens': Message.objects.filter(conversation__company=company).count(),
                'arquivos': Message.objects.filter(conversation__company=company)
                                   .exclude(media_file='').exclude(media_file__isnull=True).count(),
            },
        },
    )


@login_required
@require_POST
def company_export_view(request):
    """Baixa o ZIP com os dados da empresa de quem esta logado.

    Nunca recebe um id de empresa por parametro: a empresa vem de quem esta logado,
    entao nao existe "exportar a empresa do vizinho" nem por URL forjada.
    """
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden
    blocked = _deny_master_export(request)
    if blocked:
        return blocked
    readonly = block_readonly(request)
    if readonly:
        return readonly

    from .export import build_company_export, export_filename
    company = request_company(request)
    if company is None:
        return HttpResponseForbidden('Sem empresa vinculada.')
    bundle = build_company_export(company)
    return FileResponse(
        bundle, as_attachment=True, filename=export_filename(company),
        content_type='application/zip',
    )


def _delete_company_media_files(company):
    """Apaga do disco as midias das conversas da empresa. Devolve quantas saiu.

    Chamado antes de excluir a empresa: o `delete()` em cascata limpa o banco, mas
    deixaria os arquivos orfaos no servidor — dado pessoal de cliente final que
    ninguem mais consegue nem ver nem remover pela interface.
    """
    from .models import Message
    removidos = 0
    arquivos = (
        Message.objects.filter(conversation__company=company)
        .exclude(media_file='').exclude(media_file__isnull=True)
    )
    for message in arquivos.iterator(chunk_size=500):
        try:
            message.media_file.delete(save=False)
            removidos += 1
        except (FileNotFoundError, OSError, ValueError):
            continue
    return removidos


def _deny_master_export(request):
    """403 para o gestor master nas telas de dados do cliente (inclusive no suporte)."""
    from .tenancy import is_master
    if is_master(request.user):
        return HttpResponseForbidden(
            'A exportação é do cliente. O gestor master administra as empresas, '
            'mas não acessa os dados de atendimento delas.'
        )
    return None


def build_company_metrics(company):
    """Indicadores de UMA empresa cliente para o gestor master — SO NUMEROS.

    A regra do produto e que o master administra os clientes sem ler o atendimento
    deles. Entao aqui nao entra nada de conteudo: nenhum texto de mensagem, nome de
    contato, nome de grupo ou arquivo. Sao contagens, datas e o estado do canal —
    o suficiente para saber o tamanho do cliente, se ele esta usando o sistema e se
    o WhatsApp dele esta de pe.
    """
    from .models import (
        Attendant, CompanyAiUsage, Contact, Conversation, MenuBotConfiguration,
        Message, Sector, WapiConfiguration, WapiWebhookEvent,
    )

    now = timezone.now()
    last_7 = now - timedelta(days=7)
    last_30 = now - timedelta(days=30)

    convs = Conversation.objects.filter(company=company)
    msgs = Message.objects.filter(conversation__company=company).exclude(message_type='system')
    incoming = msgs.filter(direction='in')
    outgoing = msgs.filter(direction='out')

    last_in = incoming.order_by('-created_at').values_list('created_at', flat=True).first()
    last_out = outgoing.order_by('-created_at').values_list('created_at', flat=True).first()
    last_event = (
        WapiWebhookEvent.objects.filter(company=company)
        .order_by('-received_at').values_list('received_at', flat=True).first()
    )

    users = User.objects.filter(company=company)
    wapi_config = WapiConfiguration.for_company(company)
    menu_config = MenuBotConfiguration.for_company(company)

    # Consumo de IA DESTA empresa (medicao, sem limite nem bloqueio). A chave do GPT
    # e uma so, da plataforma, entao e este contador por empresa/mes que diz quem
    # gastou — ver CompanyAiUsage.
    ia_mes = CompanyAiUsage.month_totals(company)
    ia_mes_anterior = CompanyAiUsage.month_totals(
        company, *CompanyAiUsage.previous_reference()
    )
    ia_acumulado = CompanyAiUsage.all_time_totals(company)

    return {
        'company': company,
        'mensagens': {
            'enviadas': outgoing.count(),
            'recebidas': incoming.count(),
            'enviadas_7d': outgoing.filter(created_at__gte=last_7).count(),
            'recebidas_7d': incoming.filter(created_at__gte=last_7).count(),
            'enviadas_30d': outgoing.filter(created_at__gte=last_30).count(),
            'recebidas_30d': incoming.filter(created_at__gte=last_30).count(),
            # Respostas do atendimento automatico (IA ou chatbot de menu).
            'automaticas': outgoing.filter(is_ai=True).count(),
            'com_arquivo': msgs.exclude(media_file='').exclude(media_file__isnull=True).count(),
            'ultima_recebida': timezone.localtime(last_in) if last_in else None,
            'ultima_enviada': timezone.localtime(last_out) if last_out else None,
        },
        'conversas': {
            'total': convs.count(),
            'ativas': convs.exclude(status='closed').count(),
            'aguardando': convs.filter(status='pending').count(),
            'finalizadas': convs.filter(status='closed').count(),
            'grupos': convs.filter(chat_type='group').count(),
            'novas_7d': convs.filter(created_at__gte=last_7).count(),
        },
        'equipe': {
            'usuarios': users.count(),
            'usuarios_ativos': users.filter(is_active=True).count(),
            'administradores': users.filter(role=User.Role.ADM, is_active=True).count(),
            'atendentes': Attendant.objects.filter(company=company).count(),
            'setores': Sector.objects.filter(company=company).count(),
            'contatos': Contact.objects.filter(company=company).count(),
        },
        'canal': {
            # Nunca o Instance ID nem o token — so se existe credencial cadastrada.
            'configurado': bool(
                wapi_config.resolved_instance_id().strip() and wapi_config.resolved_token().strip()
            ),
            'ultimo_evento': timezone.localtime(last_event) if last_event else None,
            'eventos': WapiWebhookEvent.objects.filter(company=company).count(),
            'modo': menu_config.mode,
            'modo_label': menu_config.get_mode_display(),
        },
        'ia': {
            # A API Key e o modelo sao da plataforma; aqui e so o CONSUMO da empresa.
            'ativa': menu_config.mode == MenuBotConfiguration.MODE_AI,
            'mes': ia_mes,
            'mes_anterior': ia_mes_anterior,
            'acumulado': ia_acumulado,
        },
    }


@login_required
def client_metrics_view(request, company_id):
    """Tela METRICAS DO CLIENTE (exclusiva do gestor master).

    Mostra o tamanho e a atividade da empresa em NUMEROS — mensagens, conversas,
    equipe, saude do canal e quando foi a ultima mensagem. Conteudo de conversa,
    contato, grupo e arquivo continua fora do alcance do master, aqui e em qualquer
    outra tela (ver accounts/permissions.py e docs/CONTEXTO.md secao 16).
    """
    forbidden = require_master(request)
    if forbidden:
        return forbidden
    company = get_object_or_404(Company, pk=company_id)
    return render(
        request,
        'accounts/client_metrics.html',
        {
            'nav_items': build_nav_items(request.user, 'Clientes', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'metrics': build_company_metrics(company),
        },
    )


@login_required
@require_POST
def client_connection_check_view(request, company_id):
    """Testa AGORA a conexao do WhatsApp daquele cliente (botao da tela de metricas).

    Fica atras de um botao de proposito: verificar na abertura da tela faria uma
    chamada externa por empresa toda vez que o master abrisse a lista.
    """
    forbidden = require_master(request)
    if forbidden:
        return JsonResponse({'ok': False, 'error': 'Área restrita ao gestor master.'}, status=403)
    company = get_object_or_404(Company, pk=company_id)
    health = wapi_check_connection(company=company)
    return JsonResponse({
        'ok': True,
        'configured': health.configured,
        'connected': health.connected,
        'label': health.label,
        'detail': health.detail,
    })


def build_platform_metrics():
    """Indicadores de TODOS os clientes juntos, para o gestor master — SO NUMEROS.

    E a visao de cima: uma linha por empresa (canal, conversas, mensagens, respostas
    automaticas e consumo de IA do mes) mais os totais da plataforma. Serve para
    responder "quem esta usando o sistema e quanto" sem abrir cliente por cliente.

    Mesma regra de privacidade da tela de Metricas do cliente (docs/CONTEXTO.md
    secao 16): contagens e datas, nunca conteudo de conversa, contato ou arquivo, e
    nunca credencial (Instance ID, token ou API Key).

    As consultas sao AGREGADAS por empresa (uma consulta por assunto, nao uma por
    cliente), entao a tela nao fica mais lenta conforme a carteira cresce.

    A SAUDE do WhatsApp aqui e so "credencial cadastrada" + "ultimo evento
    recebido": consultar a W-API de verdade e uma chamada externa POR EMPRESA e
    continua atras do botao "Testar conexao" da tela de cada cliente.
    """
    now = timezone.now()
    last_30 = now - timedelta(days=30)
    ano, mes = CompanyAiUsage.reference(now)

    companies = list(Company.objects.all())

    def _por_empresa(queryset, chave='company'):
        return {linha[chave]: linha for linha in queryset}

    conversas = _por_empresa(
        Conversation.objects.values('company').annotate(
            total=Count('id'),
            ativas=Count('id', filter=~Q(status='closed')),
            aguardando=Count('id', filter=Q(status='pending')),
        )
    )
    mensagens = _por_empresa(
        Message.objects.exclude(message_type='system')
        .values('conversation__company').annotate(
            total=Count('id'),
            ultimos_30d=Count('id', filter=Q(created_at__gte=last_30)),
            automaticas=Count('id', filter=Q(direction='out', is_ai=True)),
            ultima=Max('created_at'),
        ),
        chave='conversation__company',
    )
    eventos = _por_empresa(
        WapiWebhookEvent.objects.values('company').annotate(ultimo=Max('received_at'))
    )
    usuarios = _por_empresa(
        User.objects.filter(company__isnull=False).values('company').annotate(
            ativos=Count('id', filter=Q(is_active=True)),
        )
    )
    atendentes = _por_empresa(
        Attendant.objects.values('company').annotate(total=Count('id'))
    )
    consumo_mes = {
        linha.company_id: linha
        for linha in CompanyAiUsage.objects.filter(year=ano, month=mes)
    }
    consumo_total = _por_empresa(
        CompanyAiUsage.objects.values('company').annotate(
            tokens=Sum('total_tokens'), chamadas=Sum('total_requests'),
        )
    )
    # Uma linha por empresa nas duas tabelas de configuracao — cabe em memoria.
    wapi_configs = {c.company_id: c for c in WapiConfiguration.objects.all()}
    menu_configs = {c.company_id: c for c in MenuBotConfiguration.objects.all()}

    linhas = []
    for company in companies:
        conv = conversas.get(company.id, {})
        msg = mensagens.get(company.id, {})
        uso_mes = consumo_mes.get(company.id)
        uso_total = consumo_total.get(company.id, {})
        wapi_config = wapi_configs.get(company.id)
        menu_config = menu_configs.get(company.id)
        ultima_msg = msg.get('ultima')
        ultimo_evento = eventos.get(company.id, {}).get('ultimo')
        linhas.append({
            'company': company,
            'canal_configurado': bool(
                wapi_config
                and wapi_config.resolved_instance_id().strip()
                and wapi_config.resolved_token().strip()
            ),
            'modo': menu_config.mode if menu_config else MenuBotConfiguration.MODE_OFF,
            'modo_label': menu_config.get_mode_display() if menu_config else 'Desligado',
            'ultimo_evento': timezone.localtime(ultimo_evento) if ultimo_evento else None,
            'conversas_total': conv.get('total', 0),
            'conversas_ativas': conv.get('ativas', 0),
            'conversas_aguardando': conv.get('aguardando', 0),
            'mensagens_total': msg.get('total', 0),
            'mensagens_30d': msg.get('ultimos_30d', 0),
            'automaticas': msg.get('automaticas', 0),
            'ultima_mensagem': timezone.localtime(ultima_msg) if ultima_msg else None,
            'usuarios_ativos': usuarios.get(company.id, {}).get('ativos', 0),
            'atendentes': atendentes.get(company.id, {}).get('total', 0),
            'ia_tokens_mes': uso_mes.total_tokens if uso_mes else 0,
            'ia_chamadas_mes': uso_mes.total_requests if uso_mes else 0,
            'ia_ultimo_uso': (
                timezone.localtime(uso_mes.last_used_at)
                if uso_mes and uso_mes.last_used_at else None
            ),
            'ia_tokens_total': uso_total.get('tokens') or 0,
            'ia_chamadas_total': uso_total.get('chamadas') or 0,
        })

    # Ordem: empresa ativa primeiro, depois quem mais consumiu IA no mes e quem tem
    # mais movimento — assim o master bate o olho em quem esta pesando na conta.
    linhas.sort(key=lambda linha: (
        not linha['company'].is_active,
        -linha['ia_tokens_mes'],
        -linha['mensagens_30d'],
        linha['company'].display_name.casefold(),
    ))

    plataforma = OpenAiConfiguration.get_solo()
    return {
        'mes_label': CompanyAiUsage.month_label(ano, mes),
        'linhas': linhas,
        'totais': {
            'clientes': len(companies),
            'clientes_ativos': sum(1 for c in companies if c.is_active),
            'clientes_com_ia': sum(
                1 for linha in linhas
                if linha['modo'] == MenuBotConfiguration.MODE_AI and linha['company'].is_active
            ),
            'canais_configurados': sum(1 for linha in linhas if linha['canal_configurado']),
            'conversas_ativas': sum(linha['conversas_ativas'] for linha in linhas),
            'conversas_aguardando': sum(linha['conversas_aguardando'] for linha in linhas),
            'mensagens_30d': sum(linha['mensagens_30d'] for linha in linhas),
            'automaticas': sum(linha['automaticas'] for linha in linhas),
            'ia_tokens_mes': sum(linha['ia_tokens_mes'] for linha in linhas),
            'ia_chamadas_mes': sum(linha['ia_chamadas_mes'] for linha in linhas),
            'ia_tokens_total': sum(linha['ia_tokens_total'] for linha in linhas),
        },
        # Contador da PLATAFORMA (a conta que o master paga). Pode ser MAIOR que a
        # soma por empresa: ele inclui os testes de conexao (que nao tem empresa) e
        # tudo o que foi gasto antes de existir a medicao por cliente.
        'plataforma': {
            'tem_chave': plataforma.has_api_key,
            'modelo': plataforma.resolved_model(),
            'tokens': plataforma.total_tokens,
            'chamadas': plataforma.total_requests,
            'desde': timezone.localtime(plataforma.usage_since) if plataforma.usage_since else None,
            'ultimo_uso': timezone.localtime(plataforma.last_used_at) if plataforma.last_used_at else None,
        },
    }


@login_required
def platform_metrics_view(request):
    """Tela METRICAS (exclusiva do gestor master): todos os clientes num lugar so.

    Mostra, por empresa, o estado do canal de WhatsApp, o tamanho do atendimento e
    o consumo de IA do mes — mais os totais da plataforma. Sem limite e sem
    bloqueio: e medicao, para o master saber quem usa o sistema e quanto gasta.

    Continua valendo a regra de que o master nao le o atendimento: aqui nao aparece
    texto de mensagem, nome de contato, nome de grupo, arquivo nem credencial.
    """
    forbidden = require_master(request)
    if forbidden:
        return forbidden
    return render(
        request,
        'accounts/platform_metrics.html',
        {
            'nav_items': build_nav_items(request.user, 'Métricas', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'metrics': build_platform_metrics(),
        },
    )


@login_required
def clients_view(request):
    """Tela CLIENTES (exclusiva do gestor master): cadastra e administra as EMPRESAS
    que usam o sistema.

    Cada empresa cadastrada aqui e uma "instancia" do BEEonBOARD: tem os seus setores,
    atendentes, contatos, conversas e as suas proprias credenciais de W-API/GPT. Os
    dados cadastrais e o logo definidos aqui aparecem na barra lateral do cliente
    (ver accounts/context_processors.py).

    A EMPRESA PADRAO (dona de tudo o que existia antes do multiempresa) nao pode ser
    excluida nem desativada.
    """
    forbidden = require_master(request)
    if forbidden:
        return forbidden

    editing = None
    form = None
    show_modal = False

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        company_id = (request.POST.get('company_id') or '').strip()
        target = Company.objects.filter(pk=company_id).first() if company_id else None

        if action == 'delete':
            if target is None:
                messages.error(request, 'Empresa não encontrada.')
            elif target.is_default:
                messages.error(request, 'A empresa padrão não pode ser excluída.')
            elif (request.POST.get('confirm_name') or '').strip().casefold() \
                    != target.display_name.strip().casefold():
                # Exclusao apaga conversas e midias e NAO tem volta. Digitar o nome
                # e a trava contra o clique errado na empresa errada.
                messages.error(
                    request,
                    'Para excluir, digite o nome exato da empresa na confirmação. '
                    'Nada foi apagado.',
                )
                return redirect('clients')
            elif target.is_active:
                # Encerrar com seguranca tem ordem: o cliente exporta, o master
                # desativa (o WhatsApp para de receber) e so entao exclui.
                messages.error(
                    request,
                    f'Desative "{target.display_name}" antes de excluir. Assim o '
                    'atendimento para primeiro e o cliente tem tempo de exportar os dados.',
                )
                return redirect('clients')
            else:
                name = target.display_name
                # Apagar a empresa em cascata remove as linhas do banco, mas o Django
                # NAO apaga o arquivo em disco: sem isto, as fotos e os documentos do
                # cliente ficariam no servidor para sempre depois de ele sair.
                removidos = _delete_company_media_files(target)
                target.delete()
                messages.success(
                    request,
                    f'A empresa "{name}" foi excluída ({removidos} arquivo(s) de mídia apagados).',
                )
            return redirect('clients')

        if action == 'toggle-active':
            if target is None:
                messages.error(request, 'Empresa não encontrada.')
            elif target.is_default:
                messages.error(request, 'A empresa padrão não pode ser desativada.')
            else:
                target.is_active = not target.is_active
                target.save(update_fields=['is_active', 'updated_at'])
                estado = 'reativada' if target.is_active else 'desativada'
                messages.success(request, f'A empresa "{target.display_name}" foi {estado}.')
            return redirect('clients')

        if action == 'create-admin':
            # Primeiro ACESSO da empresa: cria o Administrador dela. Dai em diante e
            # ele quem cadastra atendentes, setores e configuracoes do cliente.
            if target is None:
                messages.error(request, 'Empresa não encontrada.')
                return redirect('clients')
            admin_form = CompanyAdminForm(request.POST)
            if admin_form.is_valid():
                try:
                    with transaction.atomic():
                        new_admin = User.objects.create_user(
                            email=admin_form.cleaned_data['email'],
                            password=admin_form.cleaned_data['password'],
                            role=User.Role.ADM,
                            company=target,
                        )
                        first_name, last_name = split_name_parts(admin_form.cleaned_data['name'])
                        new_admin.first_name = first_name
                        new_admin.last_name = last_name
                        new_admin.save(update_fields=['first_name', 'last_name'])
                        # O sinal ja provisionou o Attendant do admin (ver
                        # accounts/signals.py); aqui ajustamos nome/telefone e
                        # OBRIGAMOS a troca da senha no primeiro acesso.
                        from .signals import ensure_admin_attendant
                        attendant = ensure_admin_attendant(new_admin)
                        if attendant is not None:
                            attendant.name = admin_form.cleaned_data['name']
                            attendant.phone = admin_form.cleaned_data['phone']
                            attendant.must_change_password = True
                            attendant.save(update_fields=[
                                'name', 'phone', 'must_change_password', 'updated_at',
                            ])
                        # Garante o setor Geral da empresa nova.
                        Sector.ensure_general(target)
                    messages.success(
                        request,
                        f'Acesso criado para "{target.display_name}". '
                        f'{new_admin.email} entra como Administrador e troca a senha no primeiro acesso.',
                    )
                    return redirect('clients')
                except IntegrityError:
                    messages.error(request, 'Este e-mail já está em uso no sistema.')
                    return redirect('clients')
            # Erro de validacao: mostra a primeira mensagem (padrao toast do projeto).
            first_error = next(iter(admin_form.errors.values()))[0]
            messages.error(request, first_error)
            return redirect('clients')

        if action == 'enter':
            # MODO SUPORTE: o master passa a operar as telas de CONFIGURACAO deste
            # cliente (nunca Conversas/Contatos — ver accounts/permissions.py).
            if target is None:
                messages.error(request, 'Empresa não encontrada.')
                return redirect('clients')
            set_active_company(request, target)
            messages.success(
                request,
                f'Você está no painel de "{target.display_name}". '
                'Dá para ajustar as configurações do cliente; as conversas dele continuam privadas.',
            )
            return redirect('wapi-settings')

        if action == 'leave':
            set_active_company(request, None)
            messages.success(request, 'Você saiu do painel do cliente.')
            return redirect('clients')

        # Cadastro (sem company_id) ou edicao (com company_id).
        editing = target
        form = CompanyForm(request.POST, request.FILES, instance=editing)
        if form.is_valid():
            company = form.save()
            if editing is None:
                messages.success(request, f'A empresa "{company.display_name}" foi cadastrada.')
            else:
                messages.success(request, f'Os dados de "{company.display_name}" foram salvos.')
            return redirect('clients')
        show_modal = True
        messages.error(request, 'Não foi possível salvar. Verifique os campos destacados.')

    term = (request.GET.get('q') or '').strip()
    companies = Company.objects.all()
    if term:
        companies = companies.filter(
            Q(name__icontains=term)
            | Q(legal_name__icontains=term)
            | Q(document__icontains=term)
            | Q(city__icontains=term)
        )
    # Contadores reais por empresa (o master ve o tamanho de cada cliente, sem
    # entrar no conteudo das conversas).
    companies = companies.annotate(
        users_count=Count('users', distinct=True),
        conversations_count=Count('conversations', distinct=True),
    )

    # Quem ja tem ACESSO de administrador em cada empresa (para a tela mostrar se
    # o cliente ja consegue entrar ou se ainda falta criar o primeiro acesso).
    admins_by_company = {}
    for u in User.objects.filter(role=User.Role.ADM, company__isnull=False).order_by('email'):
        admins_by_company.setdefault(u.company_id, []).append(
            {'email': u.email, 'name': u.get_full_name() or u.email, 'active': u.is_active}
        )
    for c in companies:
        c.admin_list = admins_by_company.get(c.id, [])

    # Abrir o modal de edicao direto pelo link da lista (?editar=<id>).
    edit_id = (request.GET.get('editar') or '').strip()
    if form is None and edit_id:
        editing = Company.objects.filter(pk=edit_id).first()
        if editing is not None:
            form = CompanyForm(instance=editing)
            show_modal = True

    return render(
        request,
        'accounts/clients.html',
        {
            'companies': companies,
            'form': form or CompanyForm(),
            'editing': editing,
            'show_modal': show_modal,
            'admin_form': CompanyAdminForm(),
            'active_company': current_company(request),
            'search_term': term,
            'total_companies': Company.objects.count(),
            'active_companies': Company.objects.filter(is_active=True).count(),
            'nav_items': build_nav_items(request.user, 'Clientes', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


@login_required
def masters_view(request):
    """Tela GESTORES (exclusiva do gestor master): quem administra a PLATAFORMA.

    Ate aqui o master so nascia pelo shell do servidor (`create_user(role=MASTER)`),
    o que deixava o dono da plataforma sem sucessor e sem substituto em ferias. Agora
    um master cadastra outro, com senha inicial e WhatsApp — o telefone e obrigatorio
    porque e o unico caminho de recuperacao de senha de quem nao tem empresa.

    Travas (todas no backend, nao so na tela):
      - **nunca sobrar zero master ativo**: a plataforma ficaria sem dono e sem
        ninguem para cadastrar cliente ou credencial;
      - **ninguem se desativa nem se exclui**, para nao se trancar fora;
      - **excluir exige estar desativado antes** (mesma ordem da tela Clientes).
    """
    forbidden = require_master(request)
    if forbidden:
        return forbidden

    form = None
    masters = User.objects.filter(role=User.Role.MASTER)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        target_id = (request.POST.get('master_id') or '').strip()
        target = masters.filter(pk=target_id).first() if target_id else None
        is_self = target is not None and target.pk == request.user.pk

        if action == 'create':
            form = MasterUserForm(request.POST)
            if form.is_valid():
                first_name, last_name = split_name_parts(form.cleaned_data['name'])
                # company=None e o que coloca a pessoa ACIMA das empresas (ver
                # accounts/tenancy.py); master nao tem perfil de atendente.
                User.objects.create_user(
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data['password'],
                    role=User.Role.MASTER,
                    first_name=first_name,
                    last_name=last_name,
                    recovery_phone=form.cleaned_data['phone'],
                    must_change_password=True,
                )
                messages.success(
                    request,
                    f'Gestor "{form.cleaned_data["name"]}" criado. Ele troca a senha no primeiro acesso.',
                )
                return redirect('masters')
            messages.error(request, 'Não foi possível criar o gestor. Verifique os dados.')

        elif target is None:
            messages.error(request, 'Gestor não encontrado.')
            return redirect('masters')

        elif action == 'toggle-active':
            if is_self:
                messages.error(request, 'Você não pode desativar a sua própria conta.')
            elif target.is_active and _active_masters_besides(target) == 0:
                messages.error(
                    request,
                    'Este é o único gestor ativo. Crie outro antes de desativar este — '
                    'sem gestor ativo ninguém administra a plataforma.',
                )
            else:
                target.is_active = not target.is_active
                target.save(update_fields=['is_active'])
                estado = 'reativado' if target.is_active else 'desativado'
                messages.success(request, f'Gestor "{target.get_full_name() or target.email}" {estado}.')
            return redirect('masters')

        elif action == 'reset-password':
            nova = (request.POST.get('password') or '').strip()
            if len(nova) < 8:
                messages.error(request, 'A senha inicial precisa de pelo menos 8 caracteres.')
            else:
                target.set_password(nova)
                # Senha definida por outra pessoa e sempre provisoria.
                target.must_change_password = True
                target.save(update_fields=['password', 'must_change_password'])
                messages.success(
                    request,
                    f'Senha de "{target.get_full_name() or target.email}" redefinida. '
                    'Ele troca no próximo acesso.',
                )
            return redirect('masters')

        elif action == 'save-phone':
            phone = Attendant.normalize_phone(request.POST.get('phone'))
            if len(phone) < 10:
                messages.error(request, 'Informe o WhatsApp com DDD (ex.: 5511999999999).')
            else:
                target.recovery_phone = phone
                target.save(update_fields=['recovery_phone'])
                messages.success(request, 'WhatsApp de recuperação atualizado.')
            return redirect('masters')

        elif action == 'delete':
            if is_self:
                messages.error(request, 'Você não pode excluir a sua própria conta.')
            elif target.is_active:
                messages.error(
                    request,
                    'Desative o gestor antes de excluir — assim ninguém perde o acesso por engano.',
                )
            else:
                nome = target.get_full_name() or target.email
                target.delete()
                messages.success(request, f'Gestor "{nome}" excluído.')
            return redirect('masters')

    masters_ctx = [
        {
            'id': m.id,
            'name': m.get_full_name() or m.email,
            'email': m.email,
            'phone': m.recovery_phone,
            'formatted_phone': _format_recovery_phone(m.recovery_phone),
            'is_active': m.is_active,
            'is_self': m.pk == request.user.pk,
            'must_change_password': m.must_change_password,
            'last_login': timezone.localtime(m.last_login).strftime('%d/%m/%Y %H:%M') if m.last_login else '',
        }
        for m in masters.order_by('email')
    ]

    return render(
        request,
        'accounts/masters.html',
        {
            'masters': masters_ctx,
            'form': form or MasterUserForm(),
            'show_modal': form is not None,
            'total_masters': len(masters_ctx),
            'active_masters': sum(1 for m in masters_ctx if m['is_active']),
            'nav_items': build_nav_items(request.user, 'Gestores', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


def _active_masters_besides(user):
    """Quantos OUTROS gestores master ativos existem (a trava do 'ultimo master')."""
    return User.objects.filter(
        role=User.Role.MASTER, is_active=True,
    ).exclude(pk=user.pk).count()


def _format_recovery_phone(digits):
    digits = digits or ''
    if len(digits) == 13:  # 55 + DDD + 9 digitos
        return f'+{digits[:2]} ({digits[2:4]}) {digits[4:9]}-{digits[9:]}'
    if len(digits) == 11:
        return f'({digits[:2]}) {digits[2:7]}-{digits[7:]}'
    return digits or '-'


@login_required
def contacts_view(request):
    """Lista/gerencia os contatos (nome + telefone). Os nomes salvos aqui aparecem
    no lugar do numero nas mensagens de grupo (remetente e mencoes)."""
    forbidden = require_feature(request, 'contacts')
    if forbidden:
        return forbidden
    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
        # Toda acao desta tela acontece DENTRO da empresa de quem esta logado: um id
        # de contato de outro cliente simplesmente nao e encontrado.
        company = request_company(request)
        action = (request.POST.get('action') or '').strip()
        if action == 'delete':
            Contact.objects.filter(
                company=company, pk=(request.POST.get('contact_id') or '').strip()
            ).delete()
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
                contact = Contact.objects.filter(company=company, pk=contact_id).first()
                if contact:
                    contact.name = name
                    contact.phone = phone
                    contact.save(update_fields=['name', 'phone', 'updated_at'])
                    messages.success(request, 'Contato atualizado.')
            else:
                Contact.objects.create(company=company, name=name, phone=phone)
                messages.success(request, 'Contato adicionado.')
        except IntegrityError:
            messages.error(request, 'Ja existe um contato com esse telefone.')
        return redirect('contacts')

    from .permissions import is_read_only
    term = (request.GET.get('q') or '').strip()
    company = request_company(request)
    contacts = Contact.objects.filter(company=company)
    if term:
        contacts = contacts.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    return render(
        request,
        'accounts/contacts.html',
        {
            'contacts': contacts,
            'search_term': term,
            'total_contacts': Contact.objects.filter(company=company).count(),
            'nav_items': build_nav_items(request.user, 'Contatos', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'read_only': is_read_only(request.user),
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
        'conversations': [_serialize_conversation_item(c, request.user) for c in queryset],
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

    from wapi.services import SYSTEM_NEW_SERVICE_TEXT
    messages_qs = conversation.messages.all()
    # Escopo do historico: quem nao tem "conversa inteira" ve so o ATENDIMENTO atual,
    # a partir da ultima divisoria "Novo atendimento iniciado" (NAO a de "Encerrado" —
    # senao um chat finalizado, ou recem-encaminhado pela IA, mostraria so a divisoria).
    # Assim o atendente ve toda a conversa do atendimento (cliente + IA/menu), inclusive
    # nos Finalizados.
    if not history_full_for(request.user):
        last_start = (
            conversation.messages
            .filter(message_type='system', text=SYSTEM_NEW_SERVICE_TEXT)
            .order_by('-created_at').first()
        )
        if last_start:
            messages_qs = messages_qs.filter(created_at__gte=last_start.created_at)
    # Transferencia so pode oferecer setores/atendentes DA EMPRESA da conversa.
    sectors = Sector.objects.filter(company=conversation.company)
    attendants = Attendant.objects.select_related('user').filter(
        company=conversation.company, user__is_active=True
    )
    name_map = _build_name_map(conversation) if conversation.is_group else None

    # Abas "Conversa privada" (o que EU atendi) x "Conversa do setor" (tudo o que vejo):
    # separa os ATENDIMENTOS (segmentos entre as divisorias "Novo atendimento iniciado")
    # por dono. Um segmento e MEU se eu enviei alguma mensagem nele (comparo o nome do
    # atendente que respondeu) ou se a conversa esta atribuida a mim (segmento atual).
    # So faz sentido em conversa DIRETA. Marca cada mensagem com o segmento, se e minha
    # e o SETOR do atendimento (resolvido por segmento).
    msg_objs = list(messages_qs)
    mine_name = _current_attendant_name(request)
    my_att_id = getattr(getattr(request.user, 'attendant_profile', None), 'id', None)
    sector_name_by_id = {s.id: s.name for s in sectors}
    seg_of = {}
    seg_mine = {}
    seg_sector = {}  # idx -> id do setor do atendimento (ultimo setor nao-nulo do segmento)
    seg_idx = 0
    for m in msg_objs:
        if m.message_type == 'system' and m.text == SYSTEM_NEW_SERVICE_TEXT:
            seg_idx += 1
        seg_of[m.id] = seg_idx
        seg_mine.setdefault(seg_idx, False)
        seg_sector.setdefault(seg_idx, None)
        if m.direction == 'out' and not m.is_ai and m.sender_name and m.sender_name == mine_name:
            seg_mine[seg_idx] = True
        if m.sector_id:
            seg_sector[seg_idx] = m.sector_id  # ultimo setor visto no segmento
    if seg_mine:
        # O atendimento ATUAL (ultimo segmento): se atribuido a mim, e meu; e o setor
        # atual da conversa vale se o segmento ainda nao tem setor carimbado.
        if my_att_id and conversation.assigned_attendant_id == my_att_id:
            seg_mine[seg_idx] = True
        if not seg_sector.get(seg_idx) and conversation.sector_id:
            seg_sector[seg_idx] = conversation.sector_id
    # Mostra as abas por dono quando ha MAIS DE UM atendimento no historico visivel
    # (mesmo que sejam todos meus) — assim o filtro fica descobrivel. So em direta.
    owner_tabs = (not conversation.is_group) and len(seg_mine) >= 2
    # Setores presentes no historico (para o seletor da aba "Conversa do setor").
    present_sector_ids = [sid for sid in dict.fromkeys(seg_sector.values()) if sid]
    conv_sectors = [{'id': sid, 'name': sector_name_by_id.get(sid, 'Setor')}
                    for sid in present_sector_ids]

    data_messages = []
    for m in msg_objs:
        d = _serialize_message(m, name_map)
        idx = seg_of.get(m.id, 0)
        d['seg'] = idx
        d['seg_mine'] = bool(seg_mine.get(idx))
        d['seg_sector'] = seg_sector.get(idx) or ''
        data_messages.append(d)

    return JsonResponse({
        'ok': True,
        'contact': _serialize_contact_info(conversation, request.user),
        'messages': data_messages,
        'owner_tabs': owner_tabs,
        'conv_sectors': conv_sectors,
        'sectors': [{'id': s.id, 'name': s.name} for s in sectors],
        'attendants': [{'id': a.id, 'name': a.name} for a in attendants],
    })


@login_required
@require_POST
def conversation_send_view(request, conversation_id):
    conversation = get_object_or_404(
        Conversation.objects.select_related('contact'), pk=conversation_id
    )
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'ok': False, 'error': 'Digite uma mensagem para enviar.'}, status=400)
    # O atendente cola texto em Markdown; converte para a formatacao nativa do WhatsApp
    # (negrito/italico/listas/citacao) preservando as quebras de linha.
    text = markdown_to_whatsapp(text)

    if not (conversation.recipient or '').strip():
        return JsonResponse(
            {'ok': False, 'error': 'Nao foi possivel enviar: conversa sem destino.'}, status=400
        )

    # Credenciais DA EMPRESA da conversa (cada cliente tem a sua instancia).
    config = WapiConfiguration.for_company(conversation.company)
    if not config.resolved_instance_id().strip() or not config.resolved_token().strip():
        return JsonResponse(
            {'ok': False, 'error': 'Configure a W-API antes de enviar mensagens.'}, status=400
        )

    sender_name = _current_attendant_name(request)
    # Em GRUPO (numero unico), prefixa o NOME do atendente no corpo da mensagem, para
    # os participantes saberem quem falou. No nosso chat guardamos o texto SEM o prefixo
    # (o nome ja aparece acima do balao), para nao duplicar.
    outgoing_text = f'*{sender_name}*\n{text}' if conversation.is_group else text

    # Reutiliza o mesmo servico de envio da tela de teste da W-API.
    # Em grupo, recipient e o JID (@g.us) — nunca o participante individual.
    result = send_text_message(
        phone=conversation.recipient, message=outgoing_text,
        company=conversation.company,
    )
    if not result.success:
        # Erro tecnico ja foi logado com seguranca no servico; aqui vai o texto amigavel.
        return JsonResponse({
            'ok': False,
            'error': result.error or 'Nao foi possivel enviar a mensagem. Verifique a conexao do WhatsApp e tente novamente.',
        }, status=502)

    message = save_outgoing_text_message(
        conversation, text, external_message_id=result.message_id or '', status='sent',
        sender_name=sender_name,
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


# ---------------------------------------------------------------------------
# Midia (arquivos das conversas) — acesso controlado
#
# Foto, audio, video e documento sao CONTEUDO DO CLIENTE. Por isso o Nginx nao
# serve mais /beeonboard/media/whatsapp/ direto (ver docs/DEPLOY.md): o arquivo so sai
# por `message_media_view`, que aplica as mesmas regras da conversa — empresa +
# alcance. Uma empresa nao alcanca o arquivo da outra, e o gestor master tambem
# nao, porque `can_see_conversation` e False para ele.
#
# A UNICA excecao e o link assinado (`media_public_view`): a W-API roda na nuvem e
# baixa pela URL a midia que NOS enviamos, entao esse link precisa ser publico. Ele
# e assinado, aponta para uma unica mensagem e expira em minutos.
# ---------------------------------------------------------------------------

MEDIA_LINK_SALT = 'beezap.midia.publica'
MEDIA_LINK_MAX_AGE = 15 * 60  # 15 min — sobra para a W-API baixar e nada alem disso


def _media_link_token(message):
    """Token assinado (curta duracao) que libera UMA mensagem para a W-API baixar."""
    return signing.dumps({'m': message.pk}, salt=MEDIA_LINK_SALT)


def _serve_media_file(message, as_attachment=False):
    """Entrega o arquivo local da mensagem. Quem pode pedir e decidido por quem chama."""
    if not message.media_file:
        raise Http404('Arquivo nao disponivel.')
    try:
        handle = message.media_file.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404('Arquivo nao disponivel.')
    filename = ''
    if message.message_type == 'document':
        filename = document_filename(message)
    filename = filename or os.path.basename(message.media_file.name)
    response = FileResponse(
        handle,
        content_type=message.media_mimetype or 'application/octet-stream',
        as_attachment=as_attachment,
        filename=filename,
    )
    # Conteudo de cliente: nunca em cache compartilhado.
    response['Cache-Control'] = 'private, max-age=0, no-store'
    return response


@login_required
def message_media_view(request, message_id):
    """Serve o arquivo de uma mensagem para quem pode ver aquela conversa.

    Mesma regra da tela: `can_see_conversation` ja filtra por EMPRESA e depois pelo
    alcance do usuario. Logo: outra empresa recebe 403, um atendente sem alcance
    recebe 403 e o gestor master tambem — ele administra os clientes, nao le o
    atendimento deles.
    """
    from .permissions import can_see_conversation
    message = get_object_or_404(
        Message.objects.select_related('conversation'), pk=message_id
    )
    if not can_see_conversation(request.user, message.conversation):
        return HttpResponseForbidden('Voce nao tem acesso a este arquivo.')
    return _serve_media_file(message)


def media_public_view(request, token):
    """Link PUBLICO e TEMPORARIO de um arquivo — existe so para a W-API.

    A W-API (nuvem) baixa pela URL a midia que enviamos, entao esse link nao pode
    exigir login. Em troca ele e assinado (`signing`), vale para UMA mensagem e
    expira em `MEDIA_LINK_MAX_AGE`. Sem token valido, 404.
    """
    try:
        data = signing.loads(token, salt=MEDIA_LINK_SALT, max_age=MEDIA_LINK_MAX_AGE)
    except signing.BadSignature:
        raise Http404('Link expirado ou invalido.')
    message = get_object_or_404(Message, pk=data.get('m'))
    return _serve_media_file(message)


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
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
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

    # Credenciais DA EMPRESA da conversa (cada cliente tem a sua instancia).
    config = WapiConfiguration.for_company(conversation.company)
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
        conversation, media_type, uploaded, caption=caption, mimetype=mimetype,
        sender_name=_current_attendant_name(request),
    )

    # Link ASSINADO e temporario para a W-API baixar o arquivo (ver MEDIA_LINK_SALT).
    # Nao usamos mais a URL crua do /media/: ela ficaria acessivel para sempre e para
    # qualquer um que adivinhasse o caminho.
    public_url = request.build_absolute_uri(
        reverse('media-public', args=[_media_link_token(message)])
    )
    # A W-API (nuvem) baixa a midia pela URL. Em producao (dominio publico) isso
    # funciona; em ambiente local (localhost/IP privado) ela nao alcanca a URL e o
    # envio falha -> nesse caso mandamos a midia em base64 (data URI), aceito pela API.
    if _host_reachable_by_wapi(urlsplit(public_url).hostname):
        media_payload = public_url
    else:
        media_payload = _media_file_to_data_uri(message.media_file, mimetype)
    # Em grupo, destino e o JID (@g.us) — nunca o participante individual.
    phone = conversation.recipient

    # Toda mídia sai pela instância da W-API DA EMPRESA da conversa.
    company = conversation.company
    if media_type == 'image':
        result = send_image_message(phone, media_payload, caption=caption or None, company=company)
    elif media_type == 'audio':
        result = send_audio_message(phone, media_payload, company=company)
    elif media_type == 'video':
        result = send_video_message(phone, media_payload, caption=caption or None, company=company)
    else:
        # A W-API exige a extensao do documento; usa a do nome e cai no mapa por mimetype.
        doc_ext = os.path.splitext(uploaded.name or '')[1].lstrip('.').lower() \
            or WAPI_DOC_EXT_BY_MIME.get(mimetype, '')
        result = send_document_message(
            phone, media_payload, file_name=uploaded.name,
            caption=caption or None, extension=doc_ext, company=company,
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
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
    # Sincroniza os grupos DA EMPRESA de quem pediu (instancia propria da W-API).
    result = sync_group_names(request_company(request))
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
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
    number = _digits(request.POST.get('number'))
    name = (request.POST.get('name') or '').strip()
    if not number or not name:
        return JsonResponse({'ok': False, 'error': 'Informe o numero e o nome.'}, status=400)
    contact, _created = Contact.objects.get_or_create(
        company=request_company(request), phone=number, defaults={'name': name}
    )

    if contact.name != name:
        contact.name = name
        contact.save(update_fields=['name', 'updated_at'])
    return JsonResponse({'ok': True, 'number': number, 'name': name})


@login_required
@require_POST
def conversation_transfer_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    update_fields = {'updated_at'}

    # A transferencia so aceita atendente/setor DA MESMA EMPRESA da conversa: mesmo
    # que alguem forje um id de outro cliente, o filtro nao encontra e nada muda.
    company = conversation.company
    if 'attendant_id' in request.POST:
        attendant_id = (request.POST.get('attendant_id') or '').strip()
        conversation.assigned_attendant = (
            Attendant.objects.filter(company=company, pk=attendant_id).first()
            if attendant_id else None
        )
        update_fields.add('assigned_attendant')
    if 'sector_id' in request.POST:
        sector_id = (request.POST.get('sector_id') or '').strip()
        conversation.sector = (
            Sector.objects.filter(company=company, pk=sector_id).first()
            if sector_id else None
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

    return JsonResponse({'ok': True, 'contact': _serialize_contact_info(conversation, request.user)})


@login_required
@require_POST
def conversation_take_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
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

    return JsonResponse({'ok': True, 'contact': _serialize_contact_info(conversation, request.user)})


@login_required
@require_POST
def conversation_close_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    # Divisoria no chat marcando o fim do atendimento (o chat permanece; padrao
    # WhatsApp = um unico chat por pessoa com todo o historico).
    save_system_message(conversation, SYSTEM_CLOSE_TEXT)
    conversation.status = 'closed'
    # MANTEM o atendente que fechou (para ele continuar vendo em "Finalizados");
    # so limpa o setor. A proxima mensagem do cliente reabre e zera atendente/setor
    # (_reopen_for_new_service), voltando para a recepcao/fila.
    conversation.sector = None
    conversation.ai_turns = 0
    conversation.save(update_fields=['status', 'sector', 'ai_turns', 'updated_at'])

    return JsonResponse({'ok': True, 'contact': _serialize_contact_info(conversation, request.user)})


@login_required
def sectors_view(request):
    forbidden = require_feature(request, 'sectors')
    if forbidden:
        return forbidden

    company = request_company(request)
    sectors = Sector.objects.prefetch_related('attendants__user').filter(company=company)
    attendants = Attendant.objects.select_related('user').filter(
        company=company, user__is_active=True
    )

    form = SectorForm(company=company)
    show_modal = False
    modal_mode = 'create'
    editing_sector = None

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
        action = request.POST.get('action', '')
        sector_id_str = request.POST.get('sector_id', '').strip()

        if action == 'delete' and sector_id_str:
            try:
                sector_obj = Sector.objects.get(company=company, pk=int(sector_id_str))
                if sector_obj.is_general:
                    messages.error(request, 'O setor Geral é padrão e não pode ser excluído.')
                else:
                    sector_obj.delete()
                    messages.success(request, 'Setor removido com sucesso.')
            except (Sector.DoesNotExist, ValueError):
                messages.error(request, 'Setor não encontrado.')
            return redirect('sectors')

        if sector_id_str:
            try:
                editing_sector = Sector.objects.get(company=company, pk=int(sector_id_str))
                modal_mode = 'edit'
            except (Sector.DoesNotExist, ValueError):
                messages.error(request, 'Setor não encontrado.')
                return redirect('sectors')

        form = SectorForm(request.POST, instance=editing_sector, company=company)
        show_modal = True

        # Captura ANTES de o form mutar a instancia (form.save altera o .name).
        was_general = bool(editing_sector and editing_sector.is_general)

        if form.is_valid():
            obj = form.save(commit=False)
            # Setor novo nasce na empresa de quem cadastrou (o campo e obrigatorio).
            if not obj.company_id:
                obj.company = company
            # O nome do setor Geral padrao nao pode ser alterado (o sistema depende
            # dele; ver Sector.ensure_general). A descricao pode ser editada.
            renamed_general = (
                was_general
                and obj.name.strip().lower() != Sector.GENERAL_SECTOR_NAME.lower()
            )
            if renamed_general:
                obj.name = Sector.GENERAL_SECTOR_NAME
            obj.save()
            if renamed_general:
                messages.info(request, 'O nome do setor Geral não pode ser alterado.')
            else:
                messages.success(
                    request,
                    'Setor atualizado com sucesso.' if editing_sector else 'Setor cadastrado com sucesso.',
                )
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
            'nav_items': build_nav_items(request.user, 'Setores', request),
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
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Dados inválidos.'}, status=400)

    sectors_data = data.get('sectors', {})
    if not isinstance(sectors_data, dict):
        return JsonResponse({'ok': False, 'error': 'Dados inválidos.'}, status=400)

    # Tudo aqui e restrito a EMPRESA de quem esta logado: setor ou atendente de
    # outro cliente nao e encontrado e e simplesmente ignorado.
    company = request_company(request)
    try:
        for sector_id_str, attendant_ids in sectors_data.items():
            try:
                sector_id = int(sector_id_str)
            except (ValueError, TypeError):
                continue
            sector_obj = Sector.objects.filter(company=company, pk=sector_id).first()
            if not sector_obj:
                continue
            if not isinstance(attendant_ids, list):
                continue
            valid_ids = list(
                Attendant.objects
                .filter(company=company, pk__in=attendant_ids)
                .values_list('id', flat=True)
            )
            sector_obj.attendants.set(valid_ids)
        # O admin faz parte de TODOS os setores DA EMPRESA dele: re-inclui apos o
        # set() do arrastar-e-soltar (senao seria removido das filas nao listadas).
        admins = list(Attendant.objects.filter(company=company, user__role='adm'))
        if admins:
            for sector_obj in Sector.objects.filter(company=company):
                sector_obj.attendants.add(*admins)
    except Exception:
        return JsonResponse(
            {'ok': False, 'error': 'Não foi possível salvar a organização. Tente novamente.'},
            status=500,
        )

    return JsonResponse({'ok': True})


@csrf_exempt
def wapi_webhook_view(request, company_slug=''):
    """Recebe as mensagens da W-API.

    MULTIEMPRESA: cada cliente cadastra na W-API a URL PROPRIA dele
    (`webhook/wapi/<empresa>/`). A URL antiga, sem identificador, continua
    funcionando: nesse caso a empresa e descoberta pelo `instanceId` do payload e,
    se nada casar, cai na empresa padrao (instalacao de um unico cliente).
    """
    if request.method != 'POST':
        # GET/HEAD respondem JSON amigavel (405) para facilitar o diagnostico.
        return JsonResponse({'ok': False, 'error': 'Metodo nao permitido.'}, status=405)

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

    # Identifica a EMPRESA dona da mensagem antes de qualquer coisa: o token de
    # webhook e as credenciais sao dela, nao do sistema.
    company = resolve_webhook_company(company_slug, payload)
    if company is None:
        wapi_webhook_logger.warning(
            'Webhook W-API recusado: empresa nao identificada ou inativa (%s).',
            company_slug or 'sem identificador',
        )
        return JsonResponse({'ok': False, 'error': 'Empresa nao encontrada.'}, status=404)

    if not is_valid_wapi_webhook_token(request, company):
        wapi_webhook_logger.warning('Webhook W-API recusado: token invalido (%s).', company.slug)
        return JsonResponse({'ok': False, 'error': 'Token de webhook invalido.'}, status=403)

    try:
        event = create_wapi_webhook_event(payload, company)
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
