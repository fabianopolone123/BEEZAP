"""Endpoint publico do webhook da W-API — a porta de entrada das mensagens.

E o caminho mais quente do sistema: identifica a EMPRESA antes de qualquer
coisa (URL, `instanceId` ou empresa padrao), valida o token DELA e nunca
expoe traceback para quem chama.
"""

from .common import (
    JsonResponse,
    create_wapi_webhook_event,
    csrf_exempt,
    is_valid_wapi_webhook_token,
    json,
    mask_phone_for_log,
    resolve_webhook_company,
    wapi_webhook_logger,
)


@csrf_exempt
def wapi_webhook_view(request, company_slug=''):
    """Recebe as mensagens da W-API.

    MULTIEMPRESA: cada cliente cadastra na W-API a URL PROPRIA dele
    (`webhook/wapi/<empresa>/`). A URL antiga, sem identificador, continua
    funcionando: nesse caso a empresa e descoberta pelo `instanceId` do payload e,
    se nada casar, cai na empresa padrao (instalacao de um unico cliente).
    """
    if request.method != 'POST':
        # GET/HEAD respondem JSON amigavel (405) para facilitar o diagnostico.
        return JsonResponse({'ok': False, 'error': 'Metodo nao permitido.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        wapi_webhook_logger.warning('Webhook W-API com corpo invalido; salvando payload vazio.')
        payload = {}

    # Log seguro para diagnostico da estrutura real: apenas nomes de chaves,
    # nunca valores, token ou payload completo.
    if isinstance(payload, dict):
        wapi_webhook_logger.info('Webhook W-API keys: %s', list(payload.keys()))
        data_node = payload.get('data')
        if isinstance(data_node, dict):
            wapi_webhook_logger.info('Webhook W-API data keys: %s', list(data_node.keys()))
            message_node = data_node.get('message')
            if isinstance(message_node, dict):
                wapi_webhook_logger.info('Webhook W-API data.message keys: %s', list(message_node.keys()))

    # Identifica a EMPRESA dona da mensagem antes de qualquer coisa: o token de
    # webhook e as credenciais sao dela, nao do sistema.
    company = resolve_webhook_company(company_slug, payload)
    if company is None:
        wapi_webhook_logger.warning(
            'Webhook W-API recusado: empresa nao identificada ou inativa (%s).',
            company_slug or 'sem identificador',
        )
        return JsonResponse({'ok': False, 'error': 'Empresa nao encontrada.'}, status=404)

    if not is_valid_wapi_webhook_token(request, company):
        wapi_webhook_logger.warning('Webhook W-API recusado: token invalido (%s).', company.slug)
        return JsonResponse({'ok': False, 'error': 'Token de webhook invalido.'}, status=403)

    try:
        event = create_wapi_webhook_event(payload, company)
    except Exception:
        # Nunca expor traceback para quem chama o webhook.
        wapi_webhook_logger.exception('Falha ao registrar evento de webhook W-API.')
        return JsonResponse({'ok': False, 'error': 'Nao foi possivel registrar o webhook.'}, status=500)

    # Log seguro: sem token, sem payload bruto e com telefone mascarado.
    wapi_webhook_logger.info(
        'Webhook W-API registrado: id=%s tipo=%s telefone=%s from_me=%s',
        event.id,
        event.event_type,
        mask_phone_for_log(event.phone),
        event.from_me,
    )

    return JsonResponse({'ok': True, 'message': 'Webhook recebido com sucesso.'})
