from django.urls import path

from .views import (
    atendimento_view,
    atendimento_set_mode_view,
    attendants_view,
    change_initial_password_view,
    client_connection_check_view,
    client_metrics_view,
    clients_view,
    masters_view,
    company_data_view,
    company_export_view,
    conversation_list_view,
    conversation_close_view,
    conversation_messages_view,
    conversation_name_contact_view,
    conversation_take_view,
    conversation_send_media_view,
    conversation_send_view,
    conversation_sync_groups_view,
    conversation_transfer_view,
    conversations_view,
    contacts_view,
    dashboard_view,
    login_view,
    logout_view,
    media_public_view,
    message_media_view,
    openai_settings_view,
    permissions_view,
    platform_metrics_view,
    password_recovery_request_view,
    password_recovery_resend_view,
    password_recovery_set_password_view,
    password_recovery_verify_code_view,
    sectors_save_organization_view,
    sectors_view,
    wapi_webhook_events_view,
    wapi_webhook_view,
    wapi_settings_view,
)

urlpatterns = [
    path('', login_view, name='login'),
    path('recuperar-senha/solicitar/', password_recovery_request_view, name='password-recovery-request'),
    path('recuperar-senha/reenviar/', password_recovery_resend_view, name='password-recovery-resend'),
    path('recuperar-senha/verificar/', password_recovery_verify_code_view, name='password-recovery-verify'),
    path('recuperar-senha/nova-senha/', password_recovery_set_password_view, name='password-recovery-set-password'),
    path('dashboard/', dashboard_view, name='dashboard'),
    # Gestao das empresas clientes (exclusiva do gestor master).
    path('clientes/', clients_view, name='clients'),
    # Gestores da PLATAFORMA (os proprios masters) — um master cadastra outro.
    path('gestores/', masters_view, name='masters'),
    # Metricas de TODOS os clientes num lugar so (exclusiva do gestor master).
    path('metricas/', platform_metrics_view, name='platform-metrics'),
    # Metricas de um cliente (so o gestor master): numeros e saude do canal, nunca
    # conteudo de conversa. Ver docs/CONTEXTO.md secao 16.
    path('clientes/<int:company_id>/metricas/', client_metrics_view, name='client-metrics'),
    path('clientes/<int:company_id>/metricas/conexao/', client_connection_check_view,
         name='client-connection-check'),
    path('conversas/', conversations_view, name='conversations'),
    path('contatos/', contacts_view, name='contacts'),
    path('conversas/lista/', conversation_list_view, name='conversation-list'),
    path('conversas/sincronizar-grupos/', conversation_sync_groups_view, name='conversation-sync-groups'),
    path('conversas/nomear-contato/', conversation_name_contact_view, name='conversation-name-contact'),
    path('conversas/<int:conversation_id>/mensagens/', conversation_messages_view, name='conversation-messages'),
    path('conversas/<int:conversation_id>/enviar/', conversation_send_view, name='conversation-send'),
    path('conversas/<int:conversation_id>/enviar-midia/', conversation_send_media_view, name='conversation-send-media'),
    path('conversas/<int:conversation_id>/transferir/', conversation_transfer_view, name='conversation-transfer'),
    path('conversas/<int:conversation_id>/assumir/', conversation_take_view, name='conversation-take'),
    path('conversas/<int:conversation_id>/encerrar/', conversation_close_view, name='conversation-close'),
    # Midia das conversas. O arquivo NAO e mais servido direto pelo /media/ do Nginx:
    # e conteudo do cliente, entao sai por uma view que aplica as regras da conversa
    # (empresa + alcance). O link publico e assinado, temporario e existe so porque a
    # W-API (nuvem) baixa a midia que enviamos. Ver accounts/views.py.
    path('midia/<int:message_id>/', message_media_view, name='message-media'),
    path('midia-publica/<str:token>/', media_public_view, name='media-public'),
    path('setores/', sectors_view, name='sectors'),
    path('setores/salvar/', sectors_save_organization_view, name='sectors-save'),
    path('atendentes/', attendants_view, name='attendants'),
    path('permissoes/', permissions_view, name='permissions'),
    path('trocar-senha-inicial/', change_initial_password_view, name='change-initial-password'),
    path('configuracoes/ia/', openai_settings_view, name='openai-settings'),
    # Portabilidade: o CLIENTE leva os dados dele (o master nao exporta).
    path('configuracoes/dados/', company_data_view, name='company-data'),
    path('configuracoes/dados/exportar/', company_export_view, name='company-export'),
    path('configuracoes/atendimento/', atendimento_view, name='atendimento'),
    path('configuracoes/atendimento/modo/', atendimento_set_mode_view, name='atendimento-mode'),
    path('configuracoes/wapi/', wapi_settings_view, name='wapi-settings'),
    path('configuracoes/wapi/eventos/', wapi_webhook_events_view, name='wapi-webhook-events'),
    # Endpoint publico do webhook da W-API. Registrado tambem sob /beeonboard/
    # para funcionar quando o app e servido atras do prefixo /beeonboard/ no Nginx
    # sem que ele seja removido no proxy. As rotas com /beezap/ continuam valendo
    # porque a URL antiga pode estar cadastrada no painel da W-API de algum cliente
    # (POST nao segue o redirect do Nginx) — mantidas ate todos re-cadastrarem.
    #
    # MULTIEMPRESA: a rota COM identificador da empresa e a recomendada (cada cliente
    # cadastra na W-API a URL propria dele). A rota SEM identificador continua
    # valida: a empresa e descoberta pelo `instanceId` do payload e, se nada casar,
    # cai na empresa padrao — assim quem ja usa nao precisa reconfigurar nada.
    path('webhook/wapi/<slug:company_slug>/', wapi_webhook_view, name='wapi-webhook-company'),
    path('beeonboard/webhook/wapi/<slug:company_slug>/', wapi_webhook_view, name='wapi-webhook-company-beeonboard'),
    path('beezap/webhook/wapi/<slug:company_slug>/', wapi_webhook_view, name='wapi-webhook-company-beezap'),
    path('webhook/wapi/', wapi_webhook_view, name='wapi-webhook'),
    path('beeonboard/webhook/wapi/', wapi_webhook_view, name='wapi-webhook-beeonboard'),
    path('beezap/webhook/wapi/', wapi_webhook_view, name='wapi-webhook-beezap'),
    path('logout/', logout_view, name='logout'),
]
