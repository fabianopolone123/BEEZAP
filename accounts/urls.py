from django.urls import path

from .views import dashboard_view, login_view, logout_view, settings_view

urlpatterns = [
    path('', login_view, name='login'),
    path('dashboard/', dashboard_view, name='dashboard'),
    path('configuracoes/', settings_view, name='settings'),
    path('logout/', logout_view, name='logout'),
]
