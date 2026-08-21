"""Telas do PROPRIO CLIENTE sobre a empresa dele: aba Marca (logo e cor),
aba Meus dados e a exportacao (portabilidade).

Quem exporta e o cliente, nunca o master: um ZIP com todas as conversas
seria ler tudo de uma vez (`_deny_master_export`).
"""

from .common import (
    Company,
    CompanyBrandForm,
    Contact,
    Conversation,
    FileResponse,
    HttpResponseForbidden,
    Message,
    _delete_company_media_files,
    _remove_company_logo_file,
    block_readonly,
    brand_logger,
    build_company_export,
    build_nav_items,
    build_settings_tabs,
    export_filename,
    is_master,
    is_read_only,
    login_required,
    messages,
    redirect,
    render,
    request_company,
    require_POST,
    require_feature,
)


@login_required
def company_brand_view(request):
    """Aba MARCA (Configuracoes do cliente): o ADM cadastra o LOGO e a COR da
    propria empresa — o que aparece no topo do menu.

    Por que e do cliente: logo e cor sao identidade do negocio dele, nao
    credencial. Antes so o gestor master alcancava isso (tela Clientes), entao
    trocar de logo virava pedido de suporte. Nenhum dado cadastral (nome, CNPJ,
    identificador) entra aqui — isso continua com o master.

    O gestor master leva 403: `require_feature('settings')` devolve False para
    qualquer feature da empresa (docs/CONTEXTO.md secao 16). O perfil `leitor` nao
    salva (bloqueio no POST).
    """
    forbidden = require_feature(request, 'settings')
    if forbidden:
        return forbidden

    company = request_company(request)
    form = CompanyBrandForm(instance=company)

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked

        action = (request.POST.get('action') or 'save').strip()

        if action == 'remove-logo':
            if company.logo:
                _remove_company_logo_file(company)
                company.logo = None
                company.save(update_fields=['logo'])
                messages.success(request, 'Logo removido. Voltamos a mostrar as iniciais da empresa.')
            else:
                messages.info(request, 'Esta empresa ainda não tem logo cadastrado.')
            return redirect('company-brand')

        anterior = company.logo.name if company.logo else ''
        form = CompanyBrandForm(request.POST, request.FILES, instance=company)
        if form.is_valid():
            # Arquivo NOVO chegando: apaga o antigo do disco antes de trocar o valor.
            novo_arquivo = request.FILES.get('logo')
            if novo_arquivo and anterior:
                _remove_company_logo_file(Company.objects.get(pk=company.pk))
            form.save()
            messages.success(request, 'Marca atualizada. O menu já mostra a mudança.')
            return redirect('company-brand')
        messages.error(request, 'Confira os campos destacados.')

    return render(
        request,
        'accounts/company_brand.html',
        {
            'nav_items': build_nav_items(request.user, 'Configurações', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'settings_tabs': build_settings_tabs('marca', company=company),
            'company': company,
            'form': form,
            'read_only': is_read_only(request.user),
            'logo_max_mb': CompanyBrandForm.LOGO_MAX_MB,
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

    company = request_company(request)
    if company is None:
        return HttpResponseForbidden('Sem empresa vinculada.')
    bundle = build_company_export(company)
    return FileResponse(
        bundle, as_attachment=True, filename=export_filename(company),
        content_type='application/zip',
    )


def _deny_master_export(request):
    """403 para o gestor master nas telas de dados do cliente (inclusive no suporte)."""
    if is_master(request.user):
        return HttpResponseForbidden(
            'A exportação é do cliente. O gestor master administra as empresas, '
            'mas não acessa os dados de atendimento delas.'
        )
    return None
