from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Attendant, Sector, User

# Import tardio evita ciclo; usado so no default do prompt da IA.


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
    webhook_token = forms.CharField(
        label='Token do webhook',
        max_length=255,
        required=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite um token separado para proteger o webhook',
            'autocomplete': 'new-password',
        }, render_value=False),
    )


# Modelos do GPT oferecidos na tela (do mais barato ao mais caro). O campo aceita
# tambem um modelo digitado, mas as opcoes cobrem o uso comum e evitam erro de digitacao.
GPT_MODEL_CHOICES = [
    ('gpt-4.1-nano', 'gpt-4.1-nano (mais barato)'),
    ('gpt-4o-mini', 'gpt-4o-mini (barato)'),
    ('gpt-4.1-mini', 'gpt-4.1-mini (intermediario)'),
    ('gpt-4o', 'gpt-4o (mais caro)'),
]


class OpenAiConfigurationForm(forms.Form):
    api_key = forms.CharField(
        label='API Key do GPT',
        max_length=255,
        required=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Cole aqui a API Key do OpenAI (sk-...)',
            'autocomplete': 'new-password',
        }, render_value=False),
    )
    model = forms.ChoiceField(
        label='Modelo do GPT',
        choices=GPT_MODEL_CHOICES,
        required=False,
    )
    instructions = forms.CharField(
        label='Prompt do atendente virtual',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 8,
            'placeholder': 'Ex.: Voce e o atendente virtual da BEEZAP. Cumprimente conforme o horario, '
                           'pergunte como pode ajudar e encaminhe para o setor certo...',
            'autocomplete': 'off',
        }),
    )
    max_turns = forms.IntegerField(
        label='Limite de respostas da IA',
        required=False,
        min_value=1,
        max_value=10,
        widget=forms.NumberInput(attrs={'autocomplete': 'off'}),
    )
    fallback_sector = forms.ModelChoiceField(
        label='Setor de fallback (quando nao identificar)',
        queryset=Sector.objects.all().order_by('name'),
        required=False,
        empty_label='(deixar em aberto, sem setor)',
    )


class ReceptionModeForm(forms.Form):
    """Seletor do MODO mestre de primeiro atendimento (desligado / chatbot / IA)."""
    from .models import MenuBotConfiguration

    mode = forms.ChoiceField(
        label='Modo de primeiro atendimento',
        choices=MenuBotConfiguration.MODE_CHOICES,
        widget=forms.RadioSelect,
    )


class MenuBotConfigurationForm(forms.Form):
    """Textos e regras do chatbot de menu (sem IA). As opcoes do menu (rotulo +
    setor) sao tratadas a parte na view, a partir de arrays do formulario."""
    greeting = forms.CharField(
        label='Saudacao',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Ex.: Ola, {saudacao}! Seja bem-vindo(a) a BEEZAP.',
            'autocomplete': 'off',
        }),
    )
    menu_intro = forms.CharField(
        label='Introducao do menu',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Ex.: Digite o numero da opcao desejada:',
            'autocomplete': 'off',
        }),
    )
    confirmation_message = forms.CharField(
        label='Mensagem de confirmacao (ao escolher uma opcao)',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Ex.: Certo! Vou te encaminhar para o setor {setor}.',
            'autocomplete': 'off',
        }),
    )
    invalid_message = forms.CharField(
        label='Mensagem de opcao invalida',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Ex.: Nao entendi. Digite o numero de uma das opcoes.',
            'autocomplete': 'off',
        }),
    )
    handoff_message = forms.CharField(
        label='Mensagem ao encaminhar para um atendente',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Ex.: Nao consegui entender. Vou chamar um atendente.',
            'autocomplete': 'off',
        }),
    )
    max_attempts = forms.IntegerField(
        label='Tentativas antes de chamar um atendente',
        required=False,
        min_value=1,
        max_value=10,
        widget=forms.NumberInput(attrs={'autocomplete': 'off'}),
    )
    fallback_sector = forms.ModelChoiceField(
        label='Setor de fallback (quando o cliente nao acerta o menu)',
        queryset=Sector.objects.all().order_by('name'),
        required=False,
        empty_label='(deixar aguardando, sem setor)',
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


class SectorForm(forms.ModelForm):
    class Meta:
        model = Sector
        fields = ['name', 'description']
        labels = {
            'name': 'Nome do setor',
            'description': 'Descrição',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'Nome do setor',
                'autocomplete': 'off',
            }),
            'description': forms.Textarea(attrs={
                'placeholder': 'Descrição (opcional)',
                'rows': 3,
                'autocomplete': 'off',
            }),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise forms.ValidationError('O nome do setor é obrigatório.')
        qs = Sector.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe um setor com este nome.')
        return name


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
