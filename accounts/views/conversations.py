"""Tela Conversas e tudo o que a alimenta: lista, mensagens, envio, midia,
assumir/encerrar/transferir e nomear contato.

Duas regras valem em TODOS os endpoints daqui:
  - a mesma guarda da tela (`require_feature_json` + `deny_master_json`):
    esconder o botao tem que bloquear a URL de dados tambem;
  - carregamento em JANELA (`CONVERSATION_PAGE_SIZE`/`MESSAGE_PAGE_SIZE`),
    porque o poll repete a consulta a cada 6-12 segundos por aba aberta.
"""

from .common import (
    Attendant,
    Contact,
    Conversation,
    Count,
    FileResponse,
    Http404,
    HttpResponseForbidden,
    JsonResponse,
    Message,
    Q,
    SYSTEM_CLOSE_TEXT,
    SYSTEM_NEW_SERVICE_TEXT,
    Sector,
    User,
    WapiConfiguration,
    _current_attendant_name,
    _digits,
    _format_conv_time,
    base64,
    build_nav_items,
    can_see_conversation,
    convert_audio_to_ogg,
    deny_conversation_json,
    deny_master_json,
    deny_readonly_json,
    document_filename,
    ensure_admin_attendant,
    ensure_wapi_image,
    get_object_or_404,
    history_full_for,
    id_valido,
    ipaddress,
    is_read_only,
    login_required,
    markdown_to_whatsapp,
    messages,
    mimetypes,
    os,
    re,
    render,
    request_company,
    require_POST,
    require_feature,
    require_feature_json,
    retry_conversation_media_async,
    reverse,
    save_outgoing_media_message,
    save_outgoing_text_message,
    save_system_message,
    send_audio_message,
    send_document_message,
    send_image_message,
    send_text_message,
    send_video_message,
    settings,
    signing,
    sync_group_names,
    timedelta,
    timezone,
    urlsplit,
    visible_conversations,
)


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


def _build_name_map(conversation, mensagens=None):
    """Mapa {digitos: nome} dos participantes do grupo, para exibir o remetente e
    resolver mencoes (@numero). Fonte UNICA: Contato CADASTRADO. O pushName do
    WhatsApp NAO entra aqui — sem contato cadastrado o numero fica visivel (e
    clicavel, para cadastrar na hora); so nome cadastrado aparece como nome.

    Recebe as mensagens JA CARREGADAS quando quem chama as tem em maos. Antes a
    funcao fazia uma varredura propria de TODAS as mensagens da conversa — uma
    segunda leitura completa do grupo, no mesmo request que ja havia lido a primeira,
    a cada poll de 6 segundos.
    """
    numbers = set()  # numeros relevantes (remetentes + mencionados)
    if mensagens is None:
        rows = conversation.messages.values_list('sender_id', 'text')
    else:
        rows = [(m.sender_id, m.text) for m in mensagens]
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


# ---------------------------------------------------------------------------
# JANELAS DE CARREGAMENTO (paginacao)
#
# Antes a tela mandava TUDO: `conversations_view` serializava todas as conversas
# visiveis no HTML, `conversation_list_view` repetia a lista completa a cada 12s e
# `conversation_messages_view` fazia `list()` da conversa inteira a cada 6s — por aba
# aberta. Um grupo com anos de historico era lido por completo dez vezes por minuto.
#
# Com janela, o custo passa a ser CONSTANTE: o que a pessoa esta realmente olhando.
# ---------------------------------------------------------------------------
CONVERSATION_PAGE_SIZE = 60   # conversas por pagina da lista
MESSAGE_PAGE_SIZE = 60        # mensagens carregadas de uma vez no chat
MAX_PAGE_SIZE = 500           # teto de seguranca para `?limite=` vindo da URL


def tamanho_da_pagina(valor, padrao):
    """Le `?limite=` da URL com teto, para ninguem pedir a base inteira por URL."""
    limite = id_valido(valor)
    if not limite:
        return padrao
    return min(limite, MAX_PAGE_SIZE)


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


# Cada contador da tela Conversas como um Q — a MESMA condicao usada para filtrar a
# listagem, para contador e lista nunca divergirem.
CONVERSATION_COUNT_Q = {
    'todas': Q(),
    'nao-lidas': Q(unread_count__gt=0),
    'em-atendimento': Q(assigned_attendant__isnull=False) & ~Q(status='closed'),
    # Fila de atendimento: so conversas DIRETAS (grupo nao entra em "aguardando").
    'aguardando': (
        Q(assigned_attendant__isnull=True) & Q(chat_type='private') & ~Q(status='closed')
    ),
    'finalizadas': Q(status='closed'),
}
CONVERSATION_TYPE_COUNT_Q = {
    'todas': Q(),
    'diretas': Q(chat_type='private'),
    'grupos': Q(chat_type='group'),
}


def _count_by_q(base, mapa):
    """Conta varios recortes do mesmo queryset em UMA consulta.

    Antes cada contador era um `.count()` proprio: 5 por status + 3 por tipo = 8
    consultas por carregamento da tela E por poll da lista (a cada 12 segundos, por
    aba aberta). Pior: para nao-admin o queryset de visibilidade traz `.distinct()`
    sobre joins com GroupAccess, entao cada uma dessas 8 refazia o join inteiro.

    `Count('id', filter=Q(...), distinct=True)` resolve tudo num agregado. O
    `distinct=True` e obrigatorio aqui: sem ele o join de visibilidade duplicaria
    linhas e os numeros sairiam inflados.
    """
    agregados = {
        f'c_{i}': Count('id', filter=condicao, distinct=True)
        for i, condicao in enumerate(mapa.values())
    }
    linha = base.aggregate(**agregados)
    return {slug: linha[f'c_{i}'] or 0 for i, slug in enumerate(mapa)}


def _conversation_counts(base=None):
    # Totais reais por status; usa o mesmo filtro da listagem para nunca divergir.
    base = base if base is not None else Conversation.objects.all()
    return _count_by_q(base, CONVERSATION_COUNT_Q)


def _conversation_type_counts(base=None):
    base = base if base is not None else Conversation.objects.all()
    return _count_by_q(base, CONVERSATION_TYPE_COUNT_Q)


@login_required
def conversations_view(request):
    forbidden = require_feature(request, 'conversations')
    if forbidden:
        return forbidden
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
    # Primeira PAGINA apenas. Antes a tela serializava todas as conversas visiveis
    # dentro do HTML — um admin com muitas conversas recebia a base inteira no
    # carregamento. O resto vem pelo botao "Carregar mais" (mesmo endpoint da lista).
    pagina = list(conversations[:CONVERSATION_PAGE_SIZE + 1])
    tem_mais = len(pagina) > CONVERSATION_PAGE_SIZE
    pagina = pagina[:CONVERSATION_PAGE_SIZE]
    return render(
        request,
        'accounts/conversations.html',
        {
            'role': role,
            'nav_items': build_nav_items(request.user, 'Conversas', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'conversations': [_serialize_conversation_item(c, request.user) for c in pagina],
            'has_more_conversations': tem_mais,
            'page_size': CONVERSATION_PAGE_SIZE,
            'filter_chips': filter_chips,
            'type_tabs': type_tabs,
            'waiting_count': counts.get('aguardando', 0),
            'read_only': read_only,
        },
    )

@login_required
def conversation_list_view(request):
    """Lista de conversas para o poll da tela (JSON).

    Passa pelas MESMAS guardas da tela Conversas. Sem o gate de feature aqui, quem
    tivesse o botao Conversas removido levava 403 na tela e continuava recebendo por
    esta URL a lista completa — com a previa da ultima mensagem de cada conversa.
    """
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
    status = (request.GET.get('status') or 'todas').strip()
    tipo = (request.GET.get('tipo') or 'todas').strip()
    term = (request.GET.get('q') or '').strip()
    base = visible_conversations(
        request.user,
        Conversation.objects.select_related('contact', 'assigned_attendant', 'sector'),
    )
    queryset = _filter_conversations_by_type(base, tipo)
    queryset = _filter_conversations_by_status(queryset, status)
    queryset = _search_conversations(queryset, term)
    # Janela: pede uma a mais para saber se ainda ha proximas, sem um count() extra.
    limite = tamanho_da_pagina(request.GET.get('limite'), CONVERSATION_PAGE_SIZE)
    pagina = list(queryset[:limite + 1])
    tem_mais = len(pagina) > limite
    pagina = pagina[:limite]
    return JsonResponse({
        'ok': True,
        'counts': _conversation_counts(base),
        'type_counts': _conversation_type_counts(base),
        'conversations': [_serialize_conversation_item(c, request.user) for c in pagina],
        'has_more': tem_mais,
    })


@login_required
def conversation_messages_view(request, conversation_id):
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
    conversation = get_object_or_404(
        Conversation.objects.select_related('contact', 'assigned_attendant', 'sector'),
        pk=conversation_id,
    )
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

    # ---------------------------------------------------------------- JANELA
    # Antes esta view fazia `list()` da conversa INTEIRA — e o poll repetia isso a
    # cada 6 segundos, por aba aberta. Um grupo com anos de historico era lido dez
    # vezes por minuto. Agora carrega as ultimas `limite` mensagens; o front pede
    # mais quando a pessoa rola para cima ("Carregar mensagens anteriores").
    #
    # A janela e ESTENDIDA PARA TRAS ate o inicio do atendimento mais antigo que ela
    # alcanca. Sem isso, um atendimento cortado no meio seria classificado errado
    # pelas abas "Conversa privada"/"Conversa do setor" — elas dependem de ver o
    # segmento COMPLETO para saber de quem ele e e em que setor terminou.
    limite = tamanho_da_pagina(request.GET.get('limite'), MESSAGE_PAGE_SIZE)
    janela_ids = list(
        messages_qs.order_by('-created_at', '-id').values_list('id', 'created_at')[:limite]
    )
    tem_mais_antigas = False
    if janela_ids:
        inicio = janela_ids[-1][1]
        # Completa o atendimento em que a janela comeca.
        divisoria = (
            messages_qs
            .filter(message_type='system', text=SYSTEM_NEW_SERVICE_TEXT,
                    created_at__lte=inicio)
            .order_by('-created_at').values_list('created_at', flat=True).first()
        )
        if divisoria is not None:
            inicio = divisoria
        tem_mais_antigas = messages_qs.filter(created_at__lt=inicio).exists()
        messages_qs = messages_qs.filter(created_at__gte=inicio)
    # Transferencia so pode oferecer setores/atendentes DA EMPRESA da conversa.
    sectors = Sector.objects.filter(company=conversation.company)
    attendants = Attendant.objects.select_related('user').filter(
        company=conversation.company, user__is_active=True
    )
    # Abas "Conversa privada" (o que EU atendi) x "Conversa do setor" (tudo o que vejo):
    # separa os ATENDIMENTOS (segmentos entre as divisorias "Novo atendimento iniciado")
    # por dono. Um segmento e MEU se eu enviei alguma mensagem nele (comparo o nome do
    # atendente que respondeu) ou se a conversa esta atribuida a mim (segmento atual).
    # So faz sentido em conversa DIRETA. Marca cada mensagem com o segmento, se e minha
    # e o SETOR do atendimento (resolvido por segmento).
    msg_objs = list(messages_qs)
    # O mapa de nomes sai das mensagens JA carregadas (nao de uma segunda varredura).
    name_map = _build_name_map(conversation, msg_objs) if conversation.is_group else None
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
        # O front mostra "Carregar mensagens anteriores" quando ha historico antes
        # da janela atual.
        'has_older': tem_mais_antigas,
        'page_size': MESSAGE_PAGE_SIZE,
    })


@login_required
@require_POST
def conversation_send_view(request, conversation_id):
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
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
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
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
    """Busca os grupos na W-API e atualiza os nomes das conversas de grupo.

    Mesmas guardas da tela Conversas: sem elas, quem tinha o botao removido (e o
    proprio master, no painel do cliente) disparava uma chamada externa a W-API da
    empresa por esta URL.
    """
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
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
    Contato. O nome passa a aparecer no lugar do numero nas mensagens.

    Passa pelas MESMAS guardas da tela Contatos: sem isso, o gestor master gravava
    contato dentro da empresa do cliente por este endpoint (a tela dele da 403), e
    quem tivesse o botao Contatos removido tambem escapava pela URL.
    """
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'contacts')
    if forbidden:
        return forbidden
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
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
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
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
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
    forbidden = deny_master_json(request)
    if forbidden:
        return forbidden
    forbidden = require_feature_json(request, 'conversations')
    if forbidden:
        return forbidden
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    readonly = deny_readonly_json(request)
    if readonly:
        return readonly
    denied = deny_conversation_json(request, conversation)
    if denied:
        return denied
    # CLASSIFICACAO AUTOMATICA da carteira: o setor que atendeu entra na carteira do
    # contato. Tem de ser AQUI, antes do `conversation.sector = None` logo abaixo —
    # depois de encerrar, a informacao de qual setor atendeu ja se perdeu. So
    # acrescenta (nunca remove) e a empresa pode desligar; ver
    # Contact.inherit_sector_from_service.
    if conversation.contact_id and conversation.sector_id:
        conversation.contact.inherit_sector_from_service(conversation.sector)

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
