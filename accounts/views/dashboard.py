"""Tela Dashboard — indicadores reais da EMPRESA de quem esta logado.

Nenhum numero mistura clientes: tudo passa pela empresa recebida.
"""

from .common import (
    Conversation,
    Count,
    Message,
    Min,
    Q,
    _fmt_int,
    _format_conv_time,
    build_nav_items,
    first_landing_url_name,
    login_required,
    redirect,
    render,
    request_company,
    timedelta,
    timezone,
    user_can_access,
)


def _format_hms(seconds):
    seconds = int(max(0, seconds or 0))
    return f'{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'


# Paleta para o gráfico de setores (donut) e legenda.
_DASHBOARD_PALETTE = ['#21c25e', '#2d6cdf', '#f4b740', '#e5484d', '#7c3aed', '#0d8d43', '#14b8a6', '#ef7d1a']


def build_dashboard_context(company):
    """Métricas reais do dashboard a partir do banco (conversas/mensagens/setores).

    MULTIEMPRESA: todos os numeros sao SOMENTE da empresa informada — nenhum
    indicador mistura clientes."""
    today = timezone.localdate()
    start_7 = today - timedelta(days=6)
    convs = Conversation.objects.filter(company=company)

    ativas = convs.exclude(status='closed').count()
    novas = convs.filter(created_at__date__gte=start_7).count()
    finalizadas = convs.filter(status='closed').count()

    # Tempo médio de resposta: 1a resposta do atendente após a 1a mensagem do cliente
    # (considera atendimentos com atividade nos últimos 30 dias).
    #
    # Duas agregações no banco em vez de carregar as mensagens. Antes isto fazia
    # `prefetch_related('messages')` sobre TODAS as conversas com atividade em 30
    # dias e ordenava em memória — ou seja, trazia 30 dias de mensagens do cliente
    # inteiro para calcular um único número.
    janela = convs.filter(last_message_at__date__gte=today - timedelta(days=30))
    primeiras = (
        Message.objects
        .filter(conversation__in=janela).exclude(message_type='system')
        .values('conversation')
        .annotate(
            primeira_entrada=Min('created_at', filter=Q(direction='in')),
        )
        .filter(primeira_entrada__isnull=False)
    )
    entrada_por_conversa = {
        linha['conversation']: linha['primeira_entrada'] for linha in primeiras
    }
    deltas = []
    if entrada_por_conversa:
        respostas = (
            Message.objects
            .filter(conversation__in=entrada_por_conversa, direction='out')
            .exclude(message_type='system')
            .values('conversation')
            .annotate(primeira_saida=Min('created_at'))
        )
        for linha in respostas:
            entrada = entrada_por_conversa.get(linha['conversation'])
            saida = linha['primeira_saida']
            # A resposta tem que vir DEPOIS da 1a mensagem do cliente (uma conversa
            # que começou com o atendente falando não conta como tempo de resposta).
            if entrada and saida and saida >= entrada:
                deltas.append((saida - entrada).total_seconds())
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
