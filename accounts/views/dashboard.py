"""Tela Dashboard — indicadores reais da EMPRESA de quem esta logado.

Nenhum numero mistura clientes: tudo passa pela empresa recebida.
"""

from .common import (
    Conversation,
    Count,
    JsonResponse,
    Message,
    Min,
    Q,
    Sector,
    _fmt_int,
    _format_conv_time,
    build_nav_items,
    first_landing_url_name,
    id_valido,
    login_required,
    parse_date,
    redirect,
    render,
    request_company,
    require_feature_json,
    timedelta,
    timezone,
    user_can_access,
    visible_conversations,
)


def _format_hms(seconds):
    seconds = int(max(0, seconds or 0))
    return f'{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'


# Paleta para o gráfico de setores (donut) e legenda.
_DASHBOARD_PALETTE = ['#21c25e', '#2d6cdf', '#f4b740', '#e5484d', '#7c3aed', '#0d8d43', '#14b8a6', '#ef7d1a']


# ---------------------------------------------------------------------------
# DETALHE das métricas (a janela que abre ao clicar num card/no gráfico)
# ---------------------------------------------------------------------------
# Regra que não pode ser quebrada: a lista sai da MESMA consulta que produziu o
# número do card. Por isso as consultas vivem em `_metric_querysets` e são usadas
# pelos DOIS lados — card e lista. Foi o mesmo cuidado que a tela Conversas já toma
# com `CONVERSATION_COUNT_Q` (contador e listagem nunca divergem).

# Título de cada métrica na janela. Também é a lista de chaves aceitas: `metrica`
# fora daqui é recusada, então a URL não vira um filtro livre sobre o banco.
METRIC_TITLES = {
    'ativas': 'Conversas ativas',
    'novas': 'Novas conversas (7 dias)',
    'finalizadas': 'Atendimentos finalizados',
    'tempo-medio': 'Tempo de resposta por conversa',
    'setor': 'Atendimentos do setor',
    'dia': 'Atendimentos do dia',
}


def _metric_querysets(company):
    """As consultas que alimentam os cards — a fonte única dos números E das listas."""
    today = timezone.localdate()
    start_7 = today - timedelta(days=6)
    convs = Conversation.objects.filter(company=company)
    return {
        'ativas': convs.exclude(status='closed'),
        'novas': convs.filter(created_at__date__gte=start_7),
        'finalizadas': convs.filter(status='closed'),
        'todas': convs,
    }


def _response_times(company, limite_dias=30):
    """Tempo de resposta POR CONVERSA: da 1a mensagem do cliente à 1a resposta.

    Devolve lista de dicts ordenada do mais demorado para o mais rápido. O card
    "Tempo médio de resposta" é a média destes mesmos números — cálculo num lugar só,
    então o detalhe nunca contradiz o card.

    Duas agregações no banco, sem carregar mensagem nenhuma para a memória: antes de
    existir esta função o cálculo do card já era assim, justamente porque um
    `prefetch_related('messages')` trazia 30 dias de mensagens do cliente inteiro para
    produzir um único número.
    """
    today = timezone.localdate()
    convs = Conversation.objects.filter(company=company)
    janela = convs.filter(last_message_at__date__gte=today - timedelta(days=limite_dias))
    primeiras = (
        Message.objects
        .filter(conversation__in=janela).exclude(message_type='system')
        .values('conversation')
        .annotate(primeira_entrada=Min('created_at', filter=Q(direction='in')))
        .filter(primeira_entrada__isnull=False)
    )
    entrada_por_conversa = {
        linha['conversation']: linha['primeira_entrada'] for linha in primeiras
    }
    if not entrada_por_conversa:
        return []

    respostas = (
        Message.objects
        .filter(conversation__in=entrada_por_conversa, direction='out')
        .exclude(message_type='system')
        .values('conversation')
        .annotate(primeira_saida=Min('created_at'))
    )
    linhas = []
    for linha in respostas:
        conv_id = linha['conversation']
        entrada = entrada_por_conversa.get(conv_id)
        saida = linha['primeira_saida']
        # A resposta tem que vir DEPOIS da mensagem do cliente: conversa que começou
        # com o atendente falando não é tempo de resposta.
        if entrada and saida and saida >= entrada:
            linhas.append({
                'conversation_id': conv_id,
                'entrada': entrada,
                'saida': saida,
                'segundos': (saida - entrada).total_seconds(),
            })
    linhas.sort(key=lambda d: d['segundos'], reverse=True)
    return linhas


def _serialize_conversation_row(conversation):
    """Uma linha da janela de detalhe. Responde "quem falou por último e o quê"."""
    ultima_direcao = ''
    ultima_de = ''
    ultima = (
        conversation.messages
        .exclude(message_type='system')
        .order_by('-created_at', '-id')
        .only('direction', 'sender_name', 'from_me')
        .first()
    )
    if ultima is not None:
        ultima_direcao = 'out' if (ultima.direction == 'out' or ultima.from_me) else 'in'
        if ultima_direcao == 'in':
            # Em grupo, quem falou é o participante; na direta, o próprio contato.
            ultima_de = (ultima.sender_name or '').strip() or conversation.display_title
        else:
            ultima_de = (
                conversation.assigned_attendant.name
                if conversation.assigned_attendant_id else 'Você'
            )
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
        'ultima_mensagem': conversation.last_message_text or '',
        'ultima_direcao': ultima_direcao,
        'ultima_de': ultima_de,
        'quando': _format_conv_time(conversation.last_message_at),
        'nao_lidas': conversation.unread_count or 0,
    }




def build_dashboard_context(company):
    """Métricas reais do dashboard a partir do banco (conversas/mensagens/setores).

    MULTIEMPRESA: todos os numeros sao SOMENTE da empresa informada — nenhum
    indicador mistura clientes."""
    today = timezone.localdate()
    start_7 = today - timedelta(days=6)
    convs = Conversation.objects.filter(company=company)

    # Card e janela de detalhe leem a MESMA consulta (ver `_metric_querysets`): se cada
    # lado montasse a sua, o numero do card e a lista divergiriam na primeira mudanca
    # de regra — foi o que a tela Conversas ja evita com `CONVERSATION_COUNT_Q`.
    consultas = _metric_querysets(company)
    ativas = consultas['ativas'].count()
    novas = consultas['novas'].count()
    finalizadas = consultas['finalizadas'].count()

    # Tempo médio de resposta: média dos MESMOS tempos que a janela de detalhe lista
    # (`_response_times`), para o card e o detalhe nunca se contradizerem.
    tempos = _response_times(company)
    tempo_medio = (
        _format_hms(sum(t['segundos'] for t in tempos) / len(tempos))
        if tempos else '--:--:--'
    )

    # `key` liga o card a janela de detalhe (ver METRIC_TITLES e
    # dashboard_metric_detail_view). Card sem `key` nao abre nada.
    stats = [
        {'key': 'ativas', 'label': 'Conversas ativas', 'value': _fmt_int(ativas),
         'bruto': ativas},
        {'key': 'novas', 'label': 'Novas conversas', 'value': _fmt_int(novas),
         'bruto': novas},
        {'key': 'finalizadas', 'label': 'Atendimentos finalizados',
         'value': _fmt_int(finalizadas), 'bruto': finalizadas},
        {'key': 'tempo-medio', 'label': 'Tempo médio de resposta', 'value': tempo_medio,
         'bruto': len(tempos)},
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
            'iso': d.isoformat(),  # o clique no ponto pede o detalhe deste dia
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
        .values('sector', 'sector__name').annotate(n=Count('id')).order_by('-n')
    )
    total_sector = sum(r['n'] for r in sector_rows)
    segments, sector_legend, acc = [], [], 0.0
    for i, r in enumerate(sector_rows):
        pct = (r['n'] / total_sector * 100) if total_sector else 0
        start, acc = acc, acc + pct
        color = _DASHBOARD_PALETTE[i % len(_DASHBOARD_PALETTE)]
        segments.append(f'{color} {start:.2f}% {acc:.2f}%')
        # `inicio`/`fim` em % viajam para o JS: e como o clique no donut descobre em
        # qual setor caiu (o conic-gradient nao tem elemento por fatia para clicar).
        sector_legend.append({
            'id': r['sector'],
            'name': r['sector__name'],
            'pct': round(pct),
            'color': color,
            'total': r['n'],
            'inicio': round(start, 2),
            'fim': round(acc, 2),
        })
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

# Quantas linhas a janela mostra. Um cliente com 20 mil finalizados não pode virar um
# JSON de 20 mil itens: o número total continua no rodapé da janela.
DETAIL_LIMIT = 60


@login_required
def dashboard_metric_detail_view(request):
    """Lista por trás de um número do Dashboard (a janela que abre ao clicar).

    Guardas, todas no servidor: só quem tem o botão Dashboard (o mesmo gate da tela),
    a empresa de quem está logado e — o ponto mais importante — o ALCANCE DE
    VISUALIZAÇÃO da pessoa.

    Por que o alcance importa aqui e não importava no card: o card é um NÚMERO, esta
    janela mostra nome de cliente e trecho de mensagem, ou seja, conteúdo de
    atendimento. Um usuário com o botão Dashboard liberado mas alcance restrito a um
    setor veria, por esta URL, a conversa de todos os outros. Por isso a lista passa por
    `visible_conversations` e a janela informa quantos itens ficaram de fora
    (`ocultas`), para o número do card não parecer errado.
    """
    forbidden = require_feature_json(request, 'dashboard')
    if forbidden:
        return forbidden

    metrica = (request.GET.get('metrica') or '').strip()
    if metrica not in METRIC_TITLES:
        return JsonResponse({'ok': False, 'error': 'Métrica desconhecida.'}, status=400)

    company = request_company(request)
    consultas = _metric_querysets(company)
    titulo = METRIC_TITLES[metrica]
    subtitulo = ''

    if metrica == 'tempo-medio':
        return _detail_response_times(request, company, titulo)

    if metrica == 'setor':
        setor_id = id_valido(request.GET.get('setor'))
        setor = Sector.objects.filter(company=company, pk=setor_id).first() if setor_id else None
        if setor is None:
            return JsonResponse({'ok': False, 'error': 'Setor não encontrado.'}, status=404)
        base = consultas['todas'].filter(sector=setor)
        titulo = setor.name
        subtitulo = 'Atendimentos deste setor'
    elif metrica == 'dia':
        dia = parse_date((request.GET.get('data') or '').strip())
        if dia is None:
            return JsonResponse({'ok': False, 'error': 'Data inválida.'}, status=400)
        base = consultas['todas'].filter(last_message_at__date=dia)
        subtitulo = dia.strftime('%d/%m/%Y')
    else:
        base = consultas[metrica]

    total_empresa = base.count()
    permitidas = visible_conversations(request.user, base)
    total = permitidas.count()
    itens = [
        _serialize_conversation_row(conv)
        for conv in (
            permitidas
            .select_related('contact', 'assigned_attendant', 'sector')
            .order_by('-last_message_at', '-created_at')[:DETAIL_LIMIT]
        )
    ]
    return JsonResponse({
        'ok': True,
        'metrica': metrica,
        'titulo': titulo,
        'subtitulo': subtitulo,
        'tipo': 'conversas',
        'total': total,
        'ocultas': max(0, total_empresa - total),
        'limite': DETAIL_LIMIT,
        'itens': itens,
    })


def _detail_response_times(request, company, titulo):
    """Detalhe do card "Tempo médio de resposta": uma linha por conversa."""
    linhas = _response_times(company)
    por_id = {linha['conversation_id']: linha for linha in linhas}
    if not por_id:
        return JsonResponse({
            'ok': True, 'metrica': 'tempo-medio', 'titulo': titulo,
            'subtitulo': 'Últimos 30 dias', 'tipo': 'tempos',
            'total': 0, 'ocultas': 0, 'limite': DETAIL_LIMIT, 'itens': [],
        })

    base = Conversation.objects.filter(company=company, pk__in=por_id)
    permitidas = {
        conv.pk: conv
        for conv in visible_conversations(request.user, base)
        .select_related('contact', 'assigned_attendant', 'sector')
    }
    itens = []
    for linha in linhas:  # já ordenado: mais demorado primeiro
        conv = permitidas.get(linha['conversation_id'])
        if conv is None:
            continue
        itens.append({
            'id': conv.pk,
            'cliente': conv.display_title,
            'iniciais': conv.display_initials,
            'is_group': conv.is_group,
            'setor': conv.sector.name if conv.sector_id else '',
            'atendente': (
                conv.assigned_attendant.name if conv.assigned_attendant_id else ''
            ),
            'cliente_em': timezone.localtime(linha['entrada']).strftime('%d/%m %H:%M'),
            'resposta_em': timezone.localtime(linha['saida']).strftime('%d/%m %H:%M'),
            'tempo': _format_hms(linha['segundos']),
            'segundos': int(linha['segundos']),
        })
        if len(itens) >= DETAIL_LIMIT:
            break
    return JsonResponse({
        'ok': True,
        'metrica': 'tempo-medio',
        'titulo': titulo,
        'subtitulo': 'Últimos 30 dias — da 1ª mensagem do cliente à 1ª resposta',
        'tipo': 'tempos',
        'total': len(permitidas),
        'ocultas': max(0, len(por_id) - len(permitidas)),
        'limite': DETAIL_LIMIT,
        'itens': itens,
    })
