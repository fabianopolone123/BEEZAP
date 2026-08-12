from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import (
    Attendant,
    Company,
    Contact,
    Conversation,
    Message,
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
    search_fields = ('name', 'user__email', 'phone')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'phone', 'created_at')
    search_fields = ('name', 'phone')


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('contact', 'company', 'status', 'assigned_attendant', 'sector', 'unread_count', 'last_message_at')
    list_filter = ('company', 'status')
    search_fields = ('contact__name', 'contact__phone')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'direction', 'status', 'phone', 'created_at')
    list_filter = ('direction', 'status')
    search_fields = ('text', 'phone', 'sender_name')
