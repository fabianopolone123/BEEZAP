"""Telas exclusivas do GESTOR MASTER: Clientes, Gestores e as duas de
Metricas (um cliente e a carteira inteira).

Aqui o master ve NUMEROS e DATAS dos clientes — nunca texto de mensagem,
nome de contato, nome de grupo, arquivo ou credencial. E a contrapartida
de ele nao ler o atendimento: ver docs/CONTEXTO.md secao 16.
"""

from .common import (
    Attendant,
    Coalesce,
    Company,
    CompanyAdminForm,
    CompanyAiUsage,
    CompanyForm,
    Contact,
    Conversation,
    Count,
    IntegrityError,
    JsonResponse,
    MasterUserForm,
    Max,
    MenuBotConfiguration,
    Message,
    OpenAiConfiguration,
    OuterRef,
    Q,
    Sector,
    Subquery,
    Sum,
    User,
    WapiConfiguration,
    WapiWebhookEvent,
    _delete_company_media_files,
    _remove_company_logo_file,
    build_nav_items,
    current_company,
    ensure_admin_attendant,
    get_object_or_404,
    id_valido,
    login_required,
    messages,
    redirect,
    render,
    require_POST,
    require_master,
    set_active_company,
    split_name_parts,
    timedelta,
    timezone,
    transaction,
    wapi_check_connection,
)


def build_company_metrics(company):
    """Indicadores de UMA empresa cliente para o gestor master — SO NUMEROS.

    A regra do produto e que o master administra os clientes sem ler o atendimento
    deles. Entao aqui nao entra nada de conteudo: nenhum texto de mensagem, nome de
    contato, nome de grupo ou arquivo. Sao contagens, datas e o estado do canal —
    o suficiente para saber o tamanho do cliente, se ele esta usando o sistema e se
    o WhatsApp dele esta de pe.
    """
    now = timezone.now()
    last_7 = now - timedelta(days=7)
    last_30 = now - timedelta(days=30)

    convs = Conversation.objects.filter(company=company)
    msgs = Message.objects.filter(conversation__company=company).exclude(message_type='system')
    incoming = msgs.filter(direction='in')
    outgoing = msgs.filter(direction='out')

    last_in = incoming.order_by('-created_at').values_list('created_at', flat=True).first()
    last_out = outgoing.order_by('-created_at').values_list('created_at', flat=True).first()
    eventos = WapiWebhookEvent.objects.filter(company=company).aggregate(
        ultimo=Max('received_at'), total=Count('id'),
    )
    last_event = eventos['ultimo']

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

    # UMA agregacao por assunto, em vez de um `.count()` por indicador. Antes esta
    # funcao disparava cerca de 25 consultas — e cada uma sobre `Message`, que e a
    # maior tabela do sistema. O jeito certo ja existia ao lado, em
    # `build_platform_metrics`: `Count('id', filter=Q(...))`.
    resumo_msgs = msgs.aggregate(
        enviadas=Count('id', filter=Q(direction='out')),
        recebidas=Count('id', filter=Q(direction='in')),
        enviadas_7d=Count('id', filter=Q(direction='out', created_at__gte=last_7)),
        recebidas_7d=Count('id', filter=Q(direction='in', created_at__gte=last_7)),
        enviadas_30d=Count('id', filter=Q(direction='out', created_at__gte=last_30)),
        recebidas_30d=Count('id', filter=Q(direction='in', created_at__gte=last_30)),
        # Respostas do atendimento automatico (IA ou chatbot de menu).
        automaticas=Count('id', filter=Q(direction='out', is_ai=True)),
        com_arquivo=Count(
            'id', filter=~Q(media_file='') & Q(media_file__isnull=False)
        ),
    )
    resumo_convs = convs.aggregate(
        total=Count('id'),
        ativas=Count('id', filter=~Q(status='closed')),
        aguardando=Count('id', filter=Q(status='pending')),
        finalizadas=Count('id', filter=Q(status='closed')),
        grupos=Count('id', filter=Q(chat_type='group')),
        novas_7d=Count('id', filter=Q(created_at__gte=last_7)),
    )
    resumo_users = users.aggregate(
        usuarios=Count('id'),
        usuarios_ativos=Count('id', filter=Q(is_active=True)),
        administradores=Count('id', filter=Q(role=User.Role.ADM, is_active=True)),
    )

    return {
        'company': company,
        'mensagens': {
            **resumo_msgs,
            'ultima_recebida': timezone.localtime(last_in) if last_in else None,
            'ultima_enviada': timezone.localtime(last_out) if last_out else None,
        },
        'conversas': resumo_convs,
        'equipe': {
            **resumo_users,
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
            'eventos': eventos['total'],
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
        company_id = id_valido(request.POST.get('company_id'))
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
                #
                # Os arquivos saem ANTES e o `delete()` vem numa transacao: se o
                # delete falhar, a empresa continua no banco sem os arquivos — ruim,
                # mas recuperavel; a ordem inversa deixaria arquivos orfaos sem
                # nenhuma linha apontando para eles, que nao da para limpar depois.
                removidos = _delete_company_media_files(target)
                with transaction.atomic():
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
        # Logo ANTES de o form mutar a instancia (o ModelForm ja atribui o arquivo
        # novo no `is_valid()`), para conseguir apagar o antigo do disco.
        logo_anterior = bool(editing and editing.logo)
        form = CompanyForm(request.POST, request.FILES, instance=editing)
        if form.is_valid():
            # Arquivo novo chegando: apaga o antigo. Sem isto, cada troca de logo
            # pela tela do master deixava um arquivo orfao no servidor — a aba Marca
            # do cliente ja fazia essa limpeza, esta tela nao.
            if request.FILES.get('logo') and logo_anterior:
                _remove_company_logo_file(Company.objects.get(pk=editing.pk))
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
    #
    # SUBQUERY, nao dois Count no mesmo annotate: dois `Count` sobre relacoes
    # DIFERENTES na mesma consulta fazem o banco multiplicar as linhas (usuarios x
    # conversas por empresa). O `distinct=True` corrigia o numero, nao o custo — uma
    # empresa com 20 usuarios e 10 mil conversas virava 200 mil linhas varridas.
    def _contagem_por_empresa(model, campo='company'):
        return Subquery(
            model.objects.filter(**{campo: OuterRef('pk')})
            .order_by().values(campo)
            .annotate(n=Count('id')).values('n')[:1]
        )

    companies = companies.annotate(
        users_count=Coalesce(_contagem_por_empresa(User), 0),
        conversations_count=Coalesce(_contagem_por_empresa(Conversation), 0),
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
    edit_id = id_valido(request.GET.get('editar'))
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
        target_id = id_valido(request.POST.get('master_id'))
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
