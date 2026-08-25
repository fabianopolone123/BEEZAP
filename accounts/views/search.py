"""Tela PESQUISAR — achar qualquer coisa no atendimento, rápido.

O sistema tinha o histórico todo e nenhum jeito de garimpar nele: para saber "em que
conversa falaram de nota fiscal em julho?" só restava abrir conversa por conversa. Aqui
o ADM combina texto livre com atendente, setor, contato, estado e período, e o resultado
mostra **o trecho que casou** — não só o nome do contato.

Decisões que sustentam o "rápido e sem explicação":

- **Um resultado só: CONVERSAS.** Não há aba "mensagens" separada. A pergunta real é
  sempre "qual conversa?", e cada linha traz até três trechos que casaram, então a
  resposta e a prova aparecem juntas.
- **Busca ao digitar** (com espera de 300 ms no JS) e **teto de resultados**: a tela
  responde em vez de "carregar".
- **Escopo é regra, não filtro.** Tudo passa por `visible_conversations` — quem tem o
  botão mas alcance restrito não garimpa a conversa dos outros. O gestor master não
  alcança nada (feature de empresa).
"""

from .common import (
    Conversation,
    JsonResponse,
    Message,
    Q,
    Sector,
    User,
    build_nav_items,
    id_valido,
    login_required,
    parse_date,
    render,
    request_company,
    require_feature,
    require_feature_json,
    timezone,
    visible_conversations,
)

# Teto de conversas devolvidas. Quem precisa de mais que isto precisa de um filtro
# melhor, não de uma lista maior — e uma lista sem teto acabaria varrendo o histórico
# inteiro do cliente a cada tecla digitada.
SEARCH_LIMIT = 40
# Quantas mensagens são varridas para agrupar por conversa. Limita o custo do
# `icontains` (que no SQLite é varredura) sem cortar resultado na prática: 400
# mensagens recentes que casam já dão muito mais que as 40 conversas do teto.
MESSAGE_SCAN = 400
# Trechos mostrados por conversa: o suficiente para reconhecer o contexto sem virar
# uma leitura da conversa inteira dentro do resultado.
EXCERPTS_PER_CONVERSATION = 3
# Texto curto casa com quase tudo e só custa varredura.
MIN_QUERY = 2


def _excerpt(text, term, span=70):
    """Trecho em volta da primeira ocorrência do termo (com reticências)."""
    texto = (text or '').replace('\n', ' ').strip()
    if not term:
        return texto[:span * 2] + ('…' if len(texto) > span * 2 else '')
    pos = texto.lower().find(term.lower())
    if pos < 0:
        return texto[:span * 2] + ('…' if len(texto) > span * 2 else '')
    inicio = max(0, pos - span)
    fim = min(len(texto), pos + len(term) + span)
    return (
        ('…' if inicio > 0 else '')
        + texto[inicio:fim]
        + ('…' if fim < len(texto) else '')
    )


def _apply_filters(queryset, request, company):
    """Aplica os filtros que NÃO são texto livre. Devolve (queryset, rotulos)."""
    rotulos = []

    atendente_id = id_valido(request.GET.get('atendente'))
    if atendente_id:
        queryset = queryset.filter(assigned_attendant_id=atendente_id)
        nome = (
            User.objects.filter(attendant_profile__id=atendente_id, company=company)
            .values_list('attendant_profile__name', flat=True).first()
        )
        rotulos.append('atendente: %s' % (nome or atendente_id))

    setor_id = id_valido(request.GET.get('setor'))
    if setor_id:
        queryset = queryset.filter(sector_id=setor_id)
        nome = Sector.objects.filter(pk=setor_id, company=company).values_list(
            'name', flat=True).first()
        rotulos.append('setor: %s' % (nome or setor_id))

    status = (request.GET.get('status') or '').strip()
    if status in dict(Conversation.STATUS_CHOICES):
        queryset = queryset.filter(status=status)
        rotulos.append('estado: %s' % dict(Conversation.STATUS_CHOICES)[status])

    contato = (request.GET.get('contato') or '').strip()
    if contato:
        # Texto, não select: um cliente com milhares de contatos não cabe num select,
        # e quem pesquisa lembra do nome ou do número, não do id.
        queryset = queryset.filter(
            Q(contact__name__icontains=contato) | Q(contact__phone__icontains=contato)
        )
        rotulos.append('contato: %s' % contato)

    tipo = (request.GET.get('tipo') or '').strip()
    if tipo in ('diretas', 'grupos'):
        queryset = queryset.filter(
            chat_type='private' if tipo == 'diretas' else 'group')
        rotulos.append('somente %s' % tipo)

    de = parse_date((request.GET.get('de') or '').strip())
    ate = parse_date((request.GET.get('ate') or '').strip())
    # A data filtra pela ATIVIDADE da conversa (última mensagem), que é o que a pessoa
    # tem em mente ao dizer "aquela conversa de julho".
    if de:
        queryset = queryset.filter(last_message_at__date__gte=de)
        rotulos.append('de %s' % de.strftime('%d/%m/%Y'))
    if ate:
        queryset = queryset.filter(last_message_at__date__lte=ate)
        rotulos.append('até %s' % ate.strftime('%d/%m/%Y'))

    return queryset, rotulos


def _conversations_for_text(base, termo):
    """IDs de conversa que casam com o texto, em ordem de atividade mais recente.

    Duas fontes: o CONTEÚDO das mensagens e a identificação da conversa (nome do
    contato, telefone, nome do grupo). Quem digita "nota fiscal" quer a primeira;
    quem digita "Joana" quer a segunda — e não deveria precisar saber a diferença.
    """
    mensagens = (
        Message.objects
        .filter(conversation__in=base, text__icontains=termo)
        .exclude(message_type='system')
        .order_by('-created_at')
        .values_list('conversation_id', flat=True)[:MESSAGE_SCAN]
    )
    ids, vistos = [], set()
    for conv_id in mensagens:
        if conv_id not in vistos:
            vistos.add(conv_id)
            ids.append(conv_id)

    por_identificacao = (
        base.filter(
            Q(contact__name__icontains=termo)
            | Q(contact__phone__icontains=termo)
            | Q(name__icontains=termo)
        )
        .order_by('-last_message_at')
        .values_list('id', flat=True)[:SEARCH_LIMIT]
    )
    for conv_id in por_identificacao:
        if conv_id not in vistos:
            vistos.add(conv_id)
            ids.append(conv_id)
    return ids


def _serialize(conversation, termo):
    """Uma linha do resultado: a conversa + a prova de por que ela apareceu."""
    trechos = []
    total_casadas = 0
    if termo:
        casadas = (
            conversation.messages
            .filter(text__icontains=termo)
            .exclude(message_type='system')
            .order_by('-created_at')
        )
        total_casadas = casadas.count()
        for msg in casadas[:EXCERPTS_PER_CONVERSATION]:
            trechos.append({
                'texto': _excerpt(msg.text, termo),
                'quem': (
                    (msg.sender_name or '').strip() or conversation.display_title
                    if msg.direction == 'in' and not msg.from_me
                    else (
                        conversation.assigned_attendant.name
                        if conversation.assigned_attendant_id else 'Atendimento'
                    )
                ),
                'direcao': 'in' if (msg.direction == 'in' and not msg.from_me) else 'out',
                'quando': timezone.localtime(msg.created_at).strftime('%d/%m/%Y %H:%M'),
            })
    return {
        'id': conversation.pk,
        'cliente': conversation.display_title,
        'iniciais': conversation.display_initials,
        'is_group': conversation.is_group,
        'setor': conversation.sector.name if conversation.sector_id else '',
        'atendente': (
            conversation.assigned_attendant.name
            if conversation.assigned_attendant_id else ''
        ),
        'status': conversation.status,
        'status_label': conversation.status_label,
        'ultima': (conversation.last_message_text or '')[:120],
        'quando': (
            timezone.localtime(conversation.last_message_at).strftime('%d/%m/%Y %H:%M')
            if conversation.last_message_at else ''
        ),
        'trechos': trechos,
        'total_trechos': total_casadas,
    }


@login_required
def search_view(request):
    """A tela (só a casca; os resultados vêm por AJAX)."""
    forbidden = require_feature(request, 'search')
    if forbidden:
        return forbidden
    company = request_company(request)
    return render(
        request,
        'accounts/search.html',
        {
            'atendentes': (
                User.objects.filter(company=company, attendant_profile__isnull=False)
                .select_related('attendant_profile').order_by('attendant_profile__name')
            ),
            'setores': Sector.objects.filter(company=company),
            'status_options': Conversation.STATUS_CHOICES,
            'nav_items': build_nav_items(request.user, 'Pesquisar', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
        },
    )


@login_required
def search_results_view(request):
    """Resultado da pesquisa, em JSON.

    Escopo antes de tudo: `visible_conversations` (empresa + alcance de quem está
    logado). O filtro é conveniência; o escopo é regra.
    """
    forbidden = require_feature_json(request, 'search')
    if forbidden:
        return forbidden

    company = request_company(request)
    termo = (request.GET.get('q') or '').strip()
    base = visible_conversations(request.user, Conversation.objects.all())
    base, rotulos = _apply_filters(base, request, company)

    if termo and len(termo) < MIN_QUERY:
        return JsonResponse({
            'ok': True, 'itens': [], 'total': 0, 'limite': SEARCH_LIMIT,
            'filtros': rotulos, 'aviso': 'Digite ao menos %d letras.' % MIN_QUERY,
        })

    if termo:
        ids = _conversations_for_text(base, termo)
        total = len(ids)
        escolhidos = ids[:SEARCH_LIMIT]
        # `order_by` do banco não preserva a ordem da lista; reordena em memória
        # (são no máximo SEARCH_LIMIT itens).
        por_id = {
            c.pk: c for c in
            Conversation.objects.filter(pk__in=escolhidos)
            .select_related('contact', 'assigned_attendant', 'sector')
        }
        conversas = [por_id[i] for i in escolhidos if i in por_id]
    else:
        # Sem texto: é uma navegação por filtros, do mais recente para o mais antigo.
        base = base.select_related('contact', 'assigned_attendant', 'sector')
        total = base.count()
        conversas = list(base.order_by('-last_message_at', '-created_at')[:SEARCH_LIMIT])

    return JsonResponse({
        'ok': True,
        'termo': termo,
        'filtros': rotulos,
        'total': total,
        'limite': SEARCH_LIMIT,
        'itens': [_serialize(c, termo) for c in conversas],
    })
