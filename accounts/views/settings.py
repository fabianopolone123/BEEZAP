"""Telas de CONFIGURACAO: Inteligencia (IA), Atendimento (chatbot + modo),
WhatsApp/W-API, Permissoes e Atendentes.

O que e TECNICO (credencial da W-API, API Key do GPT) e do gestor master;
o que e conteudo do negocio (textos do chatbot, modo de atendimento,
setores, atendentes, permissoes) e do ADM da empresa. Ver docs/CONTEXTO.md
secao 16.
"""

from .common import (
    ALL_FEATURE_KEYS,
    Attendant,
    AttendantForm,
    Conversation,
    ConversationViewScope,
    DEFAULT_CONFIRMATION_MESSAGE,
    DEFAULT_GREETING,
    DEFAULT_HANDOFF_MESSAGE,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_INVALID_MESSAGE,
    DEFAULT_MENU_INTRO,
    EDITABLE_ROLES,
    GroupAccess,
    HttpResponseForbidden,
    IntegrityError,
    JsonResponse,
    MENU_FEATURES,
    MenuBotConfiguration,
    MenuBotConfigurationForm,
    MenuOption,
    OpenAiConfiguration,
    OpenAiConfigurationForm,
    ReceptionModeForm,
    RoleMenuPermission,
    Sector,
    SectorForm,
    User,
    UserConversationView,
    UserMenuPermission,
    WapiConfiguration,
    WapiConfigurationForm,
    WapiSendTextForm,
    WapiWebhookEvent,
    _fmt_int,
    allowed_keys_for,
    block_readonly,
    build_menu_text,
    build_nav_items,
    build_service_status,
    build_settings_tabs,
    build_wapi_webhook_url,
    current_company,
    deny_readonly_json,
    effective_view_scope,
    get_object_or_404,
    gpt_test_connection,
    history_full_for,
    id_valido,
    is_master,
    json,
    login_required,
    messages,
    redirect,
    render,
    request_company,
    require_POST,
    require_feature,
    require_master,
    require_master_in_company,
    require_master_in_company_json,
    resolved_instructions,
    reverse,
    role_allowed_keys,
    send_text_message,
    serialize_wapi_event,
    split_name_parts,
    transaction,
    user_can_access,
)


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
            # `select_related` nao faz nada junto com `count()` — so a contagem.
            'companies_using_ai': MenuBotConfiguration.objects.filter(
                mode=MenuBotConfiguration.MODE_AI, company__is_active=True
            ).count(),
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
    setor por linha). Ignora linhas sem rotulo; numera na ordem enviada.

    TUDO DENTRO DE UMA TRANSACAO: a funcao apaga as opcoes antigas antes de criar as
    novas, entao uma falha no meio deixaria o menu do cliente VAZIO — e o chatbot
    passaria a mandar um menu sem nenhuma opcao para o cliente final dele.
    """
    labels = post.getlist('option_label')
    sector_ids = post.getlist('option_sector')
    with transaction.atomic():
        config.options.all().delete()
        order = 0
        for label, sector_id in zip(labels, sector_ids):
            label = (label or '').strip()
            if not label:
                continue
            order += 1
            sector = (
                Sector.objects.filter(
                    company_id=config.company_id, pk=id_valido(sector_id)
                ).first()
                if sector_id else None
            )
            MenuOption.objects.create(
                config=config, order=order, label=label, sector=sector
            )


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
        # Nao existe mais nada a espelhar na configuracao da plataforma: o `mode` de
        # cada empresa E a fonte unica da verdade (o antigo
        # `OpenAiConfiguration.enabled` era escrito aqui e nunca lido por ninguem).
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
            user_id = id_valido(request.POST.get('user_id'))
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
            user_id = id_valido(request.POST.get('user_id'))
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
            user_id = id_valido(request.POST.get('user_id'))
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
            user_id = id_valido(request.POST.get('user_id'))
            UserConversationView.objects.filter(
                user_id=user_id, user__company=company
            ).delete()
            messages.success(request, 'Personalizacao de visualizacao removida (voltou ao padrao do setor).')
            return redirect(f'{reverse("permissions")}?tab=visualizacao&user={user_id}')

        if form_type == 'profile-role':
            user_id = id_valido(request.POST.get('user_id'))
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
            gid = id_valido(request.POST.get('group_id'))
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
            gid = id_valido(request.POST.get('group_id'))
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

    selected_id = id_valido(request.GET.get('user'))
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
        # `.all()` USA o cache do prefetch_related acima; `.values_list()` o IGNORA e
        # emite consulta nova (duas por grupo), anulando o prefetch.
        sec_ids = {s.id for s in access.sectors.all()} if access else set()
        usr_ids = {u.id for u in access.users.all()} if access else set()
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
    """Ultimos eventos recebidos, para o painel da tela WhatsApp se atualizar sozinho.

    Mesma guarda da tela (master DENTRO do painel do cliente) e mesmo corte de
    privacidade do serializer: so tipo, direcao e data/hora — nunca o texto da
    mensagem, o telefone ou o nome do contato. Ver `serialize_wapi_event`.
    """
    forbidden_response = require_master_in_company_json(request)
    if forbidden_response:
        return forbidden_response

    events = WapiWebhookEvent.objects.filter(company=current_company(request))[:5]
    return JsonResponse({
        'ok': True,
        'events': [serialize_wapi_event(event) for event in events],
    })


def _liberar_conversas_do_atendente(attendant):
    """Devolve para a FILA as conversas que estavam com essa pessoa.

    A tela Conversas monta as filas pelo VINCULO, nao pelo status: sem atendente e
    nao encerrada = "Aguardando"; com atendente = "Conversando". Se o vinculo ficar
    de pe, a conversa de quem saiu continua "Conversando" com alguem que nao entra
    mais no sistema — nao aparece em fila nenhuma e ninguem a assume.

    Na exclusao o `on_delete=SET_NULL` do campo ja faria isso; na inativacao nada
    faria. Aqui os dois caminhos passam pela mesma regra. As MENSAGENS nao se perdem:
    quem escreveu fica gravado em texto (`sender_name`), sem chave estrangeira.
    """
    return (
        Conversation.objects
        .filter(assigned_attendant=attendant)
        .exclude(status='closed')
        .update(assigned_attendant=None)
    )


def _texto_com_conversas(frase, liberadas):
    """Acrescenta o efeito colateral a mensagem, so quando houve algum."""
    if not liberadas:
        return frase
    if liberadas == 1:
        return f'{frase} 1 conversa voltou para a fila.'
    return f'{frase} {liberadas} conversas voltaram para a fila.'


def _acao_de_acesso_do_atendente(request, company, action):
    """Inativar/reativar e EXCLUIR atendente — acoes do ADM da empresa.

    Ficam fora do formulario da tela porque nao editam cadastro: mexem no ACESSO da
    pessoa. Cadastrar e editar seguem valendo para quem tem o botao Atendentes; tirar
    alguem do sistema e de quem administra os perfis — a mesma divisao da aba Perfis,
    em Permissoes.

    Guardas, todas no servidor (esconder o botao nao basta, o POST pode ser forjado):
    escopo de empresa, so ADM, nunca em si mesmo e nunca no ultimo administrador
    ativo.
    """
    if request.user.role != User.Role.ADM:
        messages.error(request, 'Somente o administrador inativa ou exclui um atendente.')
        return redirect('attendants')

    attendant = Attendant.objects.select_related('user').filter(
        company=company, pk=id_valido(request.POST.get('attendant_id'))
    ).first()
    if attendant is None:
        # Vale tambem para id de atendente de OUTRO cliente: aqui ele nao existe.
        messages.error(request, 'Atendente nao encontrado.')
        return redirect('attendants')

    pessoa = attendant.user
    nome = attendant.name or pessoa.email
    # Ninguem se tranca fora do sistema pela propria tela (mesma regra da aba Perfis).
    if pessoa.id == request.user.id:
        messages.error(request, 'Voce nao pode inativar nem excluir o seu proprio acesso.')
        return redirect('attendants')
    # A empresa nunca pode ficar sem administrador ativo — sem isso ninguem mais
    # alcanca Permissoes, Setores e esta tela.
    if pessoa.role == User.Role.ADM and pessoa.is_active:
        outros_adms = (
            User.objects
            .filter(company=company, role=User.Role.ADM, is_active=True)
            .exclude(pk=pessoa.pk)
            .exists()
        )
        if not outros_adms:
            messages.error(request, 'Deve existir pelo menos um administrador ativo.')
            return redirect('attendants')

    if action == 'delete':
        with transaction.atomic():
            liberadas = _liberar_conversas_do_atendente(attendant)
            # Apaga o USUARIO, nao so o atendente: `Attendant.user` e OneToOne com
            # CASCADE, entao a conta cai junto. Apagar apenas o atendente deixaria
            # alguem capaz de entrar no sistema — e, se fosse adm, o sinal de
            # provisionamento recriaria o atendente no proximo save.
            pessoa.delete()
        messages.success(request, _texto_com_conversas(f'{nome} foi excluido.', liberadas))
        return redirect('attendants')

    if pessoa.is_active:
        with transaction.atomic():
            liberadas = _liberar_conversas_do_atendente(attendant)
            pessoa.is_active = False
            pessoa.save(update_fields=['is_active'])
        messages.success(
            request,
            _texto_com_conversas(f'{nome} ficou inativo e nao entra mais no sistema.', liberadas),
        )
    else:
        pessoa.is_active = True
        pessoa.save(update_fields=['is_active'])
        messages.success(request, f'{nome} voltou a ter acesso.')
    return redirect('attendants')


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
        # Inativar/reativar e excluir nao passam pelo formulario de cadastro.
        action = (request.POST.get('action') or '').strip()
        if action in ('toggle-active', 'delete'):
            return _acao_de_acesso_do_atendente(request, company, action)
        attendant_id = id_valido(request.POST.get('attendant_id'))
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
            # Quem inativa/exclui e o ADM; para os outros o botao nem aparece.
            'can_manage_access': request.user.role == User.Role.ADM,
            'form': form,
            'show_modal': show_modal,
            'modal_mode': modal_mode,
            'nav_items': build_nav_items(request.user, 'Atendentes', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )

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
        # TRANSACAO: a organizacao e um conjunto de `set()` que se substituem. Uma
        # falha no meio deixava metade dos setores com a lista nova e metade com a
        # antiga — e o atendente podia acabar fora de toda fila, sem ninguem notar.
        with transaction.atomic():
            for sector_id_str, attendant_ids in sectors_data.items():
                sector_id = id_valido(sector_id_str)
                if sector_id is None:
                    continue
                sector_obj = Sector.objects.filter(company=company, pk=sector_id).first()
                if not sector_obj:
                    continue
                if not isinstance(attendant_ids, list):
                    continue
                valid_ids = list(
                    Attendant.objects
                    .filter(company=company, pk__in=[
                        i for i in (id_valido(a) for a in attendant_ids) if i
                    ])
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
