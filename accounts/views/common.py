"""Base compartilhada das views: imports, guardas e helpers.

Tudo o que mais de uma tela usa mora aqui — em especial as GUARDAS, que sao a
espinha do controle de acesso do sistema:

  `require_feature` / `require_feature_json`  o botao do menu bloqueia a URL
  `require_master` / `require_master_in_company`  telas da plataforma e o modo suporte
  `deny_master_json`                          o master nao opera atendimento
  `deny_readonly_json` / `block_readonly`     o perfil leitor so visualiza
  `deny_conversation_json`                    alcance da conversa
  `request_company` / `current_company`       escopo de empresa (multiempresa)
  `id_valido`                                 id de formulario sem virar 500

Ao criar tela ou endpoint novo, comece pela guarda — nao deixe para depois.
"""

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
from django.db.models import Count, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# NAO ha ciclo de import aqui: `permissions.py`, `tenancy.py` e `signals.py` importam
# `models` apenas DENTRO das funcoes, e nenhum deles importa `views`. Os imports
# tardios espalhados pelo arquivo (34 deles) eram vestigio de um ciclo que ja nao
# existe — custavam uma linha de ruido por uso e escondiam as dependencias reais do
# modulo.
from ..permissions import (
    ALL_FEATURE_KEYS,
    EDITABLE_ROLES,
    MENU_FEATURES,
    allowed_keys_for,
    can_see_conversation,
    effective_view_scope,
    first_landing_url_name,
    history_full_for,
    is_read_only,
    nav_groups_for,
    role_allowed_keys,
    user_can_access,
    visible_contacts,
    visible_conversations,
)
from ..tenancy import (
    current_company as tenancy_current_company,
    deny_master_json as tenancy_deny_master_json,
    is_master,
    require_master as tenancy_require_master,
    set_active_company as tenancy_set_active_company,
)
from ..signals import ensure_admin_attendant
from ..forms import (
    AttendantForm,
    CompanyAdminForm,
    CompanyBrandForm,
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
from ..models import (
    Attendant,
    Company,
    CompanyAiUsage,
    Contact,
    ContactSectorAccess,
    Conversation,
    ConversationViewScope,
    GroupAccess,
    MenuBotConfiguration,
    MenuOption,
    Message,
    OpenAiConfiguration,
    PasswordResetCode,
    PushSubscription,
    RoleMenuPermission,
    Sector,
    User,
    UserConversationView,
    UserMenuPermission,
    WapiConfiguration,
    WapiWebhookEvent,
)
from ..webpush import vapid_configured
from gpt.attendant import DEFAULT_INSTRUCTIONS, resolved_instructions
from gpt.client import test_connection as gpt_test_connection
from chatbot.handler import (
    DEFAULT_CONFIRMATION_MESSAGE,
    DEFAULT_GREETING,
    DEFAULT_HANDOFF_MESSAGE,
    DEFAULT_INVALID_MESSAGE,
    DEFAULT_MENU_INTRO,
    build_menu_text,
)
from ..export import build_company_export, export_filename
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
    SYSTEM_NEW_SERVICE_TEXT,
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
brand_logger = logging.getLogger('beezap.marca')


def id_valido(value):
    """Devolve o id inteiro do POST, ou None quando nao e numero.

    `Model.objects.filter(pk='abc')` levanta `ValueError` — 500 na cara do usuario
    em vez de "nao encontrado". Todo lugar que le id de formulario passa por aqui.
    """
    texto = (str(value) if value is not None else '').strip()
    if not texto or not texto.isdigit():
        return None
    try:
        return int(texto)
    except (TypeError, ValueError):
        return None


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
    return tenancy_set_active_company(request, company)


def current_company(request):
    """Empresa da requisicao (ver accounts/tenancy.py)."""
    return tenancy_current_company(request)


def require_master_in_company(request):
    """Telas TECNICAS de um cliente (ex.: credenciais da W-API): so o gestor master,
    e so quando ele esta DENTRO do painel daquele cliente (modo suporte).

    Retorna um redirect amigavel quando o master ainda nao escolheu a empresa, e 403
    para qualquer outro perfil — o cliente nao mexe em credencial.
    """
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
    return is_master(request.user) and current_company(request) is not None


def build_nav_items(user, active_label, request=None):
    """Itens do menu AGRUPADOS, conforme as permissoes (accounts/permissions.py).

    Devolve grupos, nao uma lista simples: a barra lateral separa o que e da
    PLATAFORMA do que e do CLIENTE em que o master entrou, com rotulo em cada grupo
    (ver `nav_groups_for`). Para quem nao e master vem um grupo unico e sem rotulo,
    entao a barra continua identica.
    """
    in_company = master_in_company(request) if request is not None else False
    nome_do_cliente = ''
    if in_company:
        empresa = current_company(request)
        nome_do_cliente = empresa.display_name if empresa is not None else ''
    return nav_groups_for(
        user, active_label, in_company=in_company,
        support_company_name=nome_do_cliente,
    )


def require_master(request):
    """Retorna 403 se quem chamou nao e o gestor master; senao None."""
    return tenancy_require_master(request)


def request_company(request):
    """EMPRESA CLIENTE da requisicao — a dona de tudo o que for criado/consultado.

    Normalmente e a empresa do usuario logado (ver accounts/tenancy.py). A
    retaguarda para a empresa padrao existe para nunca gravar um registro sem
    empresa (o campo e obrigatorio) caso um usuario antigo esteja sem vinculo.
    """
    return current_company(request) or Company.get_default()


def require_feature(request, key):
    """Retorna 403 se o usuario nao pode acessar a feature/botao `key` (o admin
    sempre pode). Retorna None quando o acesso e permitido."""
    if not user_can_access(request.user, key):
        return HttpResponseForbidden('Acesso restrito.')
    return None


def require_feature_json(request, key):
    """Versao JSON de `require_feature`, para os endpoints AJAX.

    Existe porque o gate de feature estava so nas TELAS: um usuario com o botao
    Conversas (ou Contatos) removido pelo ADM levava 403 na tela e continuava sendo
    atendido pelos endpoints que a alimentam. A promessa do modulo de permissoes e
    que esconder o botao BLOQUEIA a URL — inclusive a URL de dados.
    """
    if not user_can_access(request.user, key):
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)
    return None


def deny_master_json(request):
    """Retorna 403 JSON quando o gestor master chama um endpoint de ATENDIMENTO.

    Segunda tranca, independente do alcance de conversas: o master administra os
    clientes e nao opera (nem le) o atendimento deles, nem dentro do painel do
    cliente. Ver accounts/tenancy.deny_master_json e docs/CONTEXTO.md secao 16.
    """
    return tenancy_deny_master_json(request)


def deny_conversation_json(request, conversation):
    """Retorna 403 JSON se o usuario nao pode ver a conversa; senao None."""
    if not can_see_conversation(request.user, conversation):
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)
    return None


def deny_readonly_json(request):
    """Retorna 403 JSON se o usuario e SOMENTE LEITURA (perfil leitor); senao None.
    Usado nos endpoints AJAX que alteram dados (enviar, assumir, encerrar, etc.)."""
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


def require_master_in_company_json(request):
    """Versao JSON de `require_master_in_company`, para o endpoint que alimenta a
    tela WhatsApp de um cliente.

    A tela e do master DENTRO do painel do cliente; o endpoint precisa da MESMA
    guarda. Antes ele exigia `role == 'adm'`, entao devolvia 403 justamente para a
    unica pessoa que abre a tela — o painel de eventos nunca atualizava e o
    JavaScript engolia o erro a cada 5 segundos, silenciosamente.
    """
    if not is_master(request.user) or current_company(request) is None:
        return JsonResponse({'ok': False, 'error': 'Acesso restrito.'}, status=403)
    return None


def serialize_wapi_event(event):
    """Evento do webhook para a tela WhatsApp — SEM conteudo de atendimento.

    Quem abre essa tela e o GESTOR MASTER, que administra os clientes sem ler o
    atendimento deles (docs/CONTEXTO.md secao 16). Antes iam daqui o texto da
    mensagem (`short_text`), o telefone e o nome do contato — exatamente o que a
    regra do produto proibe, na unica tela que so ele alcanca.

    O que o master precisa saber e apenas "esta chegando mensagem, e quando": entao
    ficam o tipo do evento, o tipo do conteudo, a direcao e a data/hora.
    """
    received_at = timezone.localtime(event.received_at)
    return {
        'id': event.id,
        'event_type': event.event_type or '-',
        'message_type': event.message_type or '-',
        'direction': 'Enviada' if event.from_me else 'Recebida',
        'received_at': received_at.strftime('%d/%m/%Y %H:%M'),
    }


def must_change_initial_password(user):
    if not user.is_authenticated:
        return False
    try:
        return user.attendant_profile.must_change_password
    except Attendant.DoesNotExist:
        return False



# ---------------------------------------------------------------------------
# Helpers usados por MAIS DE UMA tela.
#
# Ficam aqui para o grafo de modulos continuar PLANO: todo modulo de view depende
# apenas de `common`, nunca de outro modulo de view. Sem isso havia dependencia
# lateral (contacts -> conversations, dashboard -> conversations, master ->
# company), que e por onde um ciclo de import entra quando alguem mexe.
# ---------------------------------------------------------------------------

def _delete_company_media_files(company):
    """Apaga do disco TODOS os arquivos da empresa. Devolve quantos saíram.

    Chamado antes de excluir a empresa: o `delete()` em cascata limpa o banco, mas
    deixaria os arquivos orfaos no servidor — dado pessoal de cliente final que
    ninguem mais consegue nem ver nem remover pela interface.

    Inclui as midias das conversas E o LOGO da empresa. O logo ficava para tras: o
    `_delete_company_media_files` so percorria `Message.media_file`, entao o arquivo
    de `media/empresas/logos/` sobrava no disco para sempre depois de o cliente sair.
    """
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
    if company.logo:
        _remove_company_logo_file(company)
        removidos += 1
    return removidos

def _digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())

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

def _remove_company_logo_file(company):
    """Apaga o ARQUIVO do logo do disco (o `delete()` do campo nao e chamado quando
    so trocamos o valor no banco).

    Sem isto, cada troca de logo deixaria o arquivo antigo orfao no servidor para
    sempre — ninguem mais o alcanca pela interface. Nunca derruba a requisicao: se o
    arquivo ja nao existe, seguimos em frente.
    """
    logo = getattr(company, 'logo', None)
    if not logo:
        return
    try:
        logo.delete(save=False)
    except Exception:
        brand_logger.warning('Nao foi possivel apagar o arquivo do logo da empresa %s.', company.pk)
