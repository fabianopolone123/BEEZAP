"""Multiempresa (SaaS): a qual EMPRESA CLIENTE pertence quem esta usando o sistema.

O BEEZAP atende varias empresas na MESMA instalacao. Cada empresa (`Company`) tem
os seus setores, atendentes, contatos, conversas, mensagens e as suas proprias
credenciais de W-API e GPT. O isolamento e por VINCULO: todo dado operacional
aponta para a empresa dona, e as consultas filtram por ela.

Papeis:

- **Gestor master** (`User.Role.MASTER`): dono da plataforma. NAO pertence a
  nenhuma empresa (`user.company` nulo) e ve a tela "Clientes", onde cadastra e
  administra as empresas. Por decisao de privacidade (LGPD), o master **nao le as
  conversas** dos clientes — ele administra, configura e exporta.
- **Administrador / Usuario / Leitor**: pertencem a UMA empresa e so enxergam os
  dados dela. Nada muda no dia a dia deles.

Este modulo concentra as perguntas "quem e o master?" e "qual e a empresa da
requisicao?" para que as views nao repitam essa regra.
"""

from django.http import HttpResponseForbidden, JsonResponse


# Empresa em que o master esta "entrando" para dar suporte (guardada na sessao).
# O uso efetivo (botao "Entrar no painel") entra na Parte 2 do multiempresa.
ACTIVE_COMPANY_SESSION_KEY = 'active_company_id'


def is_master(user):
    """A pessoa e o GESTOR MASTER (dono da plataforma)?"""
    return getattr(user, 'role', None) == 'master'


def user_company(user):
    """Empresa a que a pessoa pertence (None para o master e para anonimos)."""
    if not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'company', None)


def current_company(request):
    """EMPRESA da requisicao — a que deve filtrar tudo o que aparece na tela.

    Para usuario comum: a empresa dele. Para o master: a empresa em que ele
    escolheu entrar (sessao), ou None quando ele esta na propria area de gestao.
    """
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return None
    if is_master(user):
        company_id = request.session.get(ACTIVE_COMPANY_SESSION_KEY)
        if not company_id:
            return None
        from .models import Company
        return Company.objects.filter(pk=company_id).first()
    return user_company(user)


def set_active_company(request, company):
    """Define (ou limpa, com None) a empresa em que o master esta atuando."""
    if company is None:
        request.session.pop(ACTIVE_COMPANY_SESSION_KEY, None)
    else:
        request.session[ACTIVE_COMPANY_SESSION_KEY] = company.pk


def scoped(queryset, company):
    """Filtra um queryset pela empresa. Sem empresa, nao devolve nada — a falta de
    empresa nunca pode virar "ver tudo"."""
    if company is None:
        return queryset.none()
    return queryset.filter(company=company)


def require_master(request):
    """Retorna 403 se quem chamou nao e o gestor master; senao None."""
    if not is_master(getattr(request, 'user', None)):
        return HttpResponseForbidden('Área restrita ao gestor master.')
    return None


def deny_master_json(request):
    """Retorna 403 JSON quando o master tenta uma acao operacional de cliente.

    O master administra as empresas, mas nao opera o atendimento delas (nao le nem
    responde conversas). Usado nos endpoints AJAX de conversa.
    """
    if is_master(getattr(request, 'user', None)):
        return JsonResponse(
            {'ok': False, 'error': 'O gestor master administra os clientes, mas não acessa as conversas deles.'},
            status=403,
        )
    return None
