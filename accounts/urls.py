from django.urls import path

from .views import (
    attendants_view,
    change_initial_password_view,
    dashboard_view,
    login_view,
    logout_view,
    password_recovery_request_view,
    password_recovery_resend_view,
    password_recovery_set_password_view,
    password_recovery_verify_code_view,
    wapi_settings_view,
)

urlpatterns = [
    path('', login_view, name='login'),
    path('recuperar-senha/solicitar/', password_recovery_request_view, name='password-recovery-request'),
    path('recuperar-senha/reenviar/', password_recovery_resend_view, name='password-recovery-resend'),
    path('recuperar-senha/verificar/', password_recovery_verify_code_view, name='password-recovery-verify'),
    path('recuperar-senha/nova-senha/', password_recovery_set_password_view, name='password-recovery-set-password'),
    path('dashboard/', dashboard_view, name='dashboard'),
    path('atendentes/', attendants_view, name='attendants'),
    path('trocar-senha-inicial/', change_initial_password_view, name='change-initial-password'),
    path('configuracoes/wapi/', wapi_settings_view, name='wapi-settings'),
    path('logout/', logout_view, name='logout'),
]
