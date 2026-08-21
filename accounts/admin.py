"""Admin do Django — SUPORTE TECNICO, nao operacao.

O sistema e multiempresa e a regra do produto e que ninguem le o atendimento de um
cliente sem passar pelo escopo de empresa (ver accounts/tenancy.py e
docs/CONTEXTO.md secao 16). O admin do Django NAO conhece essa regra: ele mostra a
tabela crua.

Por isso:

- **`Conversation` e `Message` NAO ficam registradas aqui.** Estavam, sem nenhum
  filtro de empresa e com `search_fields = ('text', ...)` — ou seja, qualquer conta
  `is_staff` com as permissoes do modelo lia e PESQUISAVA o texto das conversas de
  todos os clientes por `/beeonboard/admin/`. Era um caminho que a secao 16 nem
  mencionava ao enumerar o que o master nao alcanca. A operacao normal nunca precisou
  delas ali: quem le conversa e o atendente, na tela Conversas, com o alcance dele.
- Os models que sobram sao de CADASTRO (empresa, acesso, contato, atendente) e
  mostram a coluna `company`, para o suporte enxergar de quem e cada linha.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import (
    Attendant,
    Company,
    Contact,
    User,
    WapiConfiguration,
)


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'role', 'company')


class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = ('email', 'role', 'company', 'is_active', 'is_staff', 'is_superuser')


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User
    list_display = ('email', 'role', 'company', 'is_staff', 'is_active')
    list_filter = ('role', 'company', 'is_staff', 'is_superuser', 'is_active')
    ordering = ('email',)
    search_fields = ('email',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Empresa', {'fields': ('company',)}),
        ('Permissões', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Datas importantes', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'role', 'company', 'is_staff', 'is_superuser'),
        }),
    )


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """A gestao normal das empresas e feita na tela Clientes (perfil master). Este
    registro no admin do Django serve apenas para suporte tecnico."""
    list_display = ('name', 'slug', 'document', 'is_active', 'is_default', 'created_at')
    list_filter = ('is_active', 'is_default')
    search_fields = ('name', 'legal_name', 'document', 'slug')


@admin.register(WapiConfiguration)
class WapiConfigurationAdmin(admin.ModelAdmin):
    # Uma configuracao por EMPRESA (nao e mais unica no sistema).
    list_display = ('company', 'instance_id', 'updated_at')
    list_filter = ('company',)


@admin.register(Attendant)
class AttendantAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'user', 'phone', 'created_at')
    list_filter = ('company',)
    search_fields = ('name', 'user__email', 'phone')
    list_select_related = ('company', 'user')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'phone', 'created_at')
    list_filter = ('company',)
    search_fields = ('name', 'phone')
    list_select_related = ('company',)


# Conversation e Message ficam DE FORA de proposito — ver o cabecalho do modulo.
# Se algum dia for preciso inspecionar conversa por linha de comando, use os comandos
# `inspect_wapi_messages` / `inspect_wapi_events` (accounts/management/commands/),
# que rodam no servidor e nao abrem uma tela de busca por texto na web.
