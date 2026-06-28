from django import forms

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
