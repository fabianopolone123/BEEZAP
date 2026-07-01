from django.urls import path

from .views import (
    automation_ai_view,
    automation_rules_view,
    attendants_view,
    change_initial_password_view,
    conversations_view,
    dashboard_view,
    login_view,
    logout_view,
    password_recovery_request_view,
    password_recovery_resend_view,
    password_recovery_set_password_view,
    password_recovery_verify_code_view,
    sectors_save_organization_view,
    sectors_view,
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
    path('conversas/', conversations_view, name='conversations'),
    path('automacao/ia/', automation_ai_view, name='automation-ai'),
    path('automacao/regras/', automation_rules_view, name='automation-rules'),
    path('setores/', sectors_view, name='sectors'),
    path('setores/salvar/', sectors_save_organization_view, name='sectors-save'),
    path('atendentes/', attendants_view, name='attendants'),
    path('trocar-senha-inicial/', change_initial_password_view, name='change-initial-password'),
    path('configuracoes/wapi/', wapi_settings_view, name='wapi-settings'),
    path('webhook/wapi/', wapi_webhook_view, name='wapi-webhook'),
    path('logout/', logout_view, name='logout'),
]
