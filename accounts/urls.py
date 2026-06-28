from django.urls import path

from .views import (
    attendants_view,
    change_initial_password_view,
    dashboard_view,
    login_view,
    logout_view,
    wapi_settings_view,
)

urlpatterns = [
    path('', login_view, name='login'),
    path('dashboard/', dashboard_view, name='dashboard'),
    path('atendentes/', attendants_view, name='attendants'),
    path('trocar-senha-inicial/', change_initial_password_view, name='change-initial-password'),
    path('configuracoes/wapi/', wapi_settings_view, name='wapi-settings'),
    path('logout/', logout_view, name='logout'),
]
