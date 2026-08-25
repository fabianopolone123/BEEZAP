"""Tela Contatos (agenda de nomes que aparecem no lugar do numero).

A agenda e a CARTEIRA de clientes da empresa, e por isso ela tem visibilidade
propria: o contato pode ser classificado em setores e, em Permissoes -> Contatos, o
ADM libera quem mais ve cada carteira (ver accounts/permissions.py:visible_contacts).

ESCOPO da restricao: SO esta tela. Conversa, transferencia e o nome que aparece no
lugar do numero NAO passam por essa regra — um contato de Vendas que escreve para o
Comercial e atendido normalmente ali; ele so nao entra na agenda de quem nao alcanca
aquela carteira.
"""

from .common import (
    Contact,
    IntegrityError,
    Q,
    Sector,
    User,
    _digits,
    block_readonly,
    build_nav_items,
    id_valido,
    is_read_only,
    login_required,
    messages,
    redirect,
    render,
    request_company,
    require_feature,
    visible_contacts,
)


def _pode_classificar(user):
    """Quem mexe no SETOR do contato e o ADM.

    Classificar nao e editar cadastro: e mexer em QUEM VE aquele contato. Se um
    atendente pudesse trocar o setor, ele poderia tirar um cliente da carteira dos
    colegas (ou puxar para a dele) sem passar por Permissoes. Nome e telefone seguem
    editaveis por quem tem o botao Contatos, como sempre foram.
    """
    return getattr(user, 'role', None) == User.Role.ADM


def _setores_do_post(request, company):
    """IDs de setor validos enviados pelo formulario (dentro da empresa)."""
    brutos = request.POST.getlist('sectors')
    ids = [id_valido(valor) for valor in brutos]
    ids = [i for i in ids if i]
    if not ids:
        return []
    return list(
        Sector.objects.filter(company=company, pk__in=ids).values_list('id', flat=True)
    )


@login_required
def contacts_view(request):
    """Lista/gerencia os contatos (nome + telefone + setores)."""
    forbidden = require_feature(request, 'contacts')
    if forbidden:
        return forbidden
    company = request_company(request)

    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
        # Toda acao desta tela acontece DENTRO da empresa de quem esta logado E dentro
        # do que essa pessoa alcanca: contato fora da carteira dela nao e encontrado,
        # nem para editar nem para excluir.
        alcance = visible_contacts(request.user, Contact.objects.all())
        action = (request.POST.get('action') or '').strip()

        if action == 'delete':
            contact_id = id_valido(request.POST.get('contact_id'))
            removidos = 0
            if contact_id:
                removidos, _ = alcance.filter(pk=contact_id).delete()
            if removidos:
                messages.success(request, 'Contato removido.')
            else:
                messages.error(request, 'Contato não encontrado.')
            return redirect('contacts')

        contact_id = id_valido(request.POST.get('contact_id'))
        name = (request.POST.get('name') or '').strip()
        phone = _digits(request.POST.get('phone'))
        if not name or not phone:
            messages.error(request, 'Informe o nome e o telefone do contato.')
            return redirect('contacts')
        try:
            if contact_id:
                contact = alcance.filter(pk=contact_id).first()
                if contact:
                    contact.name = name
                    contact.phone = phone
                    contact.save(update_fields=['name', 'phone', 'updated_at'])
                    if _pode_classificar(request.user):
                        contact.sectors.set(_setores_do_post(request, company))
                    messages.success(request, 'Contato atualizado.')
                else:
                    messages.error(request, 'Contato não encontrado.')
            else:
                contact = Contact.objects.create(company=company, name=name, phone=phone)
                if _pode_classificar(request.user):
                    contact.sectors.set(_setores_do_post(request, company))
                messages.success(request, 'Contato adicionado.')
        except IntegrityError:
            messages.error(request, 'Ja existe um contato com esse telefone.')
        return redirect('contacts')

    term = (request.GET.get('q') or '').strip()
    # Filtro por setor: `sem` isola os que ainda nao foram classificados — e o modo
    # pratico de organizar a carteira sem varrer a lista inteira.
    filtro_setor = (request.GET.get('setor') or '').strip()

    contacts = visible_contacts(request.user, Contact.objects.all())
    if term:
        contacts = contacts.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    if filtro_setor == 'sem':
        contacts = contacts.filter(sectors__isnull=True)
    elif filtro_setor:
        setor_id = id_valido(filtro_setor)
        if setor_id:
            contacts = contacts.filter(sectors__id=setor_id)
    contacts = contacts.prefetch_related('sectors').distinct()

    return render(
        request,
        'accounts/contacts.html',
        {
            'contacts': contacts,
            'search_term': term,
            'filtro_setor': filtro_setor,
            'setores': Sector.objects.filter(company=company),
            'pode_classificar': _pode_classificar(request.user),
            'total_contacts': visible_contacts(
                request.user, Contact.objects.all()
            ).distinct().count(),
            'nav_items': build_nav_items(request.user, 'Contatos', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'read_only': is_read_only(request.user),
        },
    )
