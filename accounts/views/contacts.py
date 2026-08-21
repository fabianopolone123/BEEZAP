"""Tela Contatos (agenda de nomes que aparecem no lugar do numero).
"""

from .common import (
    Contact,
    IntegrityError,
    Q,
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
)


@login_required
def contacts_view(request):
    """Lista/gerencia os contatos (nome + telefone). Os nomes salvos aqui aparecem
    no lugar do numero nas mensagens de grupo (remetente e mencoes)."""
    forbidden = require_feature(request, 'contacts')
    if forbidden:
        return forbidden
    if request.method == 'POST':
        blocked = block_readonly(request)
        if blocked:
            return blocked
        # Toda acao desta tela acontece DENTRO da empresa de quem esta logado: um id
        # de contato de outro cliente simplesmente nao e encontrado.
        company = request_company(request)
        action = (request.POST.get('action') or '').strip()
        if action == 'delete':
            contact_id = id_valido(request.POST.get('contact_id'))
            removidos = 0
            if contact_id:
                removidos, _ = Contact.objects.filter(
                    company=company, pk=contact_id
                ).delete()
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
                contact = Contact.objects.filter(company=company, pk=contact_id).first()
                if contact:
                    contact.name = name
                    contact.phone = phone
                    contact.save(update_fields=['name', 'phone', 'updated_at'])
                    messages.success(request, 'Contato atualizado.')
            else:
                Contact.objects.create(company=company, name=name, phone=phone)
                messages.success(request, 'Contato adicionado.')
        except IntegrityError:
            messages.error(request, 'Ja existe um contato com esse telefone.')
        return redirect('contacts')

    term = (request.GET.get('q') or '').strip()
    company = request_company(request)
    contacts = Contact.objects.filter(company=company)
    if term:
        contacts = contacts.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    return render(
        request,
        'accounts/contacts.html',
        {
            'contacts': contacts,
            'search_term': term,
            'total_contacts': Contact.objects.filter(company=company).count(),
            'nav_items': build_nav_items(request.user, 'Contatos', request),
            'role_label': request.user.get_role_display(),
            'user_initial': (request.user.first_name[:1] or request.user.email[:1]).upper(),
            'read_only': is_read_only(request.user),
        },
    )
