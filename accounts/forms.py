from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Attendant, User


class LoginForm(forms.Form):
    email = forms.EmailField(
        label='E-mail',
        widget=forms.EmailInput(attrs={
            'placeholder': 'voce@empresa.com',
            'autocomplete': 'email',
        }),
    )
    password = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite sua senha',
            'autocomplete': 'current-password',
        }),
    )


class WapiConfigurationForm(forms.Form):
    instance_id = forms.CharField(
        label='Instance ID',
        max_length=120,
        widget=forms.TextInput(attrs={
            'placeholder': 'Informe o Instance ID da W-API',
            'autocomplete': 'off',
        }),
    )
    token = forms.CharField(
        label='Token',
        max_length=255,
        required=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite um novo token para salvar ou trocar',
            'autocomplete': 'new-password',
        }, render_value=False),
    )


class WapiSendTextForm(forms.Form):
    phone = forms.CharField(
        label='Telefone de destino',
        max_length=40,
        widget=forms.TextInput(attrs={
            'placeholder': '5511999999999',
            'autocomplete': 'off',
        }),
    )
    message = forms.CharField(
        label='Mensagem',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Digite a mensagem de teste',
        }),
    )


class AttendantForm(forms.Form):
    attendant_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    name = forms.CharField(
        label='Nome',
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'Digite o nome do atendente',
            'autocomplete': 'off',
        }),
    )
    email = forms.EmailField(
        label='E-mail',
        widget=forms.EmailInput(attrs={
            'placeholder': 'atendente@empresa.com',
            'autocomplete': 'off',
        }),
    )
    phone = forms.CharField(
        label='Telefone/WhatsApp',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': '5511999999999',
            'autocomplete': 'off',
        }),
    )

    def __init__(self, *args, attendant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.attendant = attendant

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        user_qs = User.objects.filter(email=email)
        if self.attendant:
            user_qs = user_qs.exclude(pk=self.attendant.user_id)
        if user_qs.exists():
            raise forms.ValidationError('Ja existe um atendente com este e-mail.')
        return email

    def clean_phone(self):
        return Attendant.normalize_phone(self.cleaned_data['phone'])


class InitialPasswordChangeForm(forms.Form):
    new_password = forms.CharField(
        label='Nova senha',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite a nova senha',
            'autocomplete': 'new-password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirmar nova senha',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite a nova senha novamente',
            'autocomplete': 'new-password',
        }),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_new_password(self):
        password = self.cleaned_data['new_password']
        if not password:
            raise forms.ValidationError('Informe uma nova senha.')
        if password == '1234':
            raise forms.ValidationError('Escolha uma senha diferente da senha inicial.')
        try:
            validate_password(password, self.user)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)
        return password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        if new_password and confirm_password and new_password != confirm_password:
            self.add_error('confirm_password', 'As senhas digitadas nao conferem.')
        return cleaned_data


class PasswordRecoveryRequestForm(forms.Form):
    email = forms.EmailField(
        label='E-mail',
        widget=forms.EmailInput(attrs={
            'placeholder': 'voce@empresa.com',
            'autocomplete': 'email',
        }),
    )


class PasswordRecoveryCodeForm(forms.Form):
    code = forms.CharField(
        label='Codigo',
        widget=forms.TextInput(attrs={
            'placeholder': '000000',
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'maxlength': '6',
        }),
    )

    def clean_code(self):
        code = ''.join(char for char in self.cleaned_data['code'] if char.isdigit())
        if len(code) != 6:
            raise forms.ValidationError('Codigo invalido ou expirado. Verifique e tente novamente.')
        return code


class PasswordRecoveryNewPasswordForm(forms.Form):
    new_password = forms.CharField(
        label='Nova senha',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite a nova senha',
            'autocomplete': 'new-password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirmar nova senha',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite a nova senha novamente',
            'autocomplete': 'new-password',
        }),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_new_password(self):
        password = self.cleaned_data['new_password']
        if not password:
            raise forms.ValidationError('Informe uma nova senha.')
        try:
            validate_password(password, self.user)
        except ValidationError:
            raise forms.ValidationError('Escolha uma senha mais segura.')
        return password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        if new_password and confirm_password and new_password != confirm_password:
            self.add_error('confirm_password', 'As senhas digitadas nao conferem.')
        return cleaned_data
