from django import forms
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import AiAttendantConfig, Attendant, AutomationRule, Sector, User


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


class AutomationAiTestForm(forms.Form):
    MODEL_CHOICES = (
        ('qwen2.5:1.5b', 'qwen2.5:1.5b'),
        ('qwen2.5:0.5b', 'qwen2.5:0.5b'),
        ('llama3.2:1b', 'llama3.2:1b'),
    )

    model = forms.ChoiceField(
        label='Modelo local',
        choices=MODEL_CHOICES,
        initial=settings.OLLAMA_MODEL,
        widget=forms.Select(attrs={'autocomplete': 'off'}),
    )
    ollama_url = forms.URLField(
        label='URL do Ollama',
        initial=settings.OLLAMA_BASE_URL,
        widget=forms.URLInput(attrs={
            'placeholder': 'http://localhost:11434',
            'autocomplete': 'off',
        }),
    )
    timeout = forms.IntegerField(
        label='Tempo maximo de resposta',
        min_value=5,
        max_value=60,
        initial=settings.OLLAMA_TIMEOUT,
        widget=forms.NumberInput(attrs={
            'placeholder': '20',
            'autocomplete': 'off',
        }),
    )
    sector = forms.ModelChoiceField(
        label='Setor',
        queryset=Sector.objects.none(),
        required=False,
        empty_label='Geral / sem setor',
        widget=forms.Select(attrs={'autocomplete': 'off'}),
    )
    use_rules = forms.BooleanField(
        label='Usar regras cadastradas',
        required=False,
        help_text='Quando ativado, a IA usara as regras cadastradas em Regras de atendimento para responder.',
    )
    message = forms.CharField(
        label='Mensagem de teste',
        max_length=1200,
        widget=forms.Textarea(attrs={
            'rows': 5,
            'placeholder': 'Digite uma mensagem curta para testar a IA',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sector'].queryset = Sector.objects.all()

    def clean_message(self):
        message = self.cleaned_data['message'].strip()
        if not message:
            raise forms.ValidationError('Digite uma mensagem para testar a IA.')
        return message


class AutomationRuleForm(forms.ModelForm):
    class Meta:
        model = AutomationRule
        fields = [
            'title',
            'sector',
            'keywords',
            'customer_example',
            'response_text',
            'internal_instruction',
            'is_active',
        ]
        labels = {
            'title': 'Titulo da regra',
            'sector': 'Setor',
            'keywords': 'Palavras-chave',
            'customer_example': 'Pergunta/exemplo do cliente',
            'response_text': 'Resposta orientada',
            'internal_instruction': 'Instrucao interna',
            'is_active': 'Regra ativa',
        }
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'Ex: Horario de atendimento',
                'autocomplete': 'off',
            }),
            'keywords': forms.TextInput(attrs={
                'placeholder': 'horario, atendimento, aberto',
                'autocomplete': 'off',
            }),
            'customer_example': forms.Textarea(attrs={
                'placeholder': 'Ex: Qual o horario de atendimento?',
                'rows': 3,
            }),
            'response_text': forms.Textarea(attrs={
                'placeholder': 'Resposta curta e adequada para WhatsApp',
                'rows': 4,
            }),
            'internal_instruction': forms.Textarea(attrs={
                'placeholder': 'Ex: Se pedir desconto, encaminhar para atendente.',
                'rows': 3,
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sector'].required = False
        self.fields['sector'].empty_label = 'Geral'

    def clean_title(self):
        title = ' '.join(self.cleaned_data.get('title', '').split())
        if not title:
            raise forms.ValidationError('Informe o titulo da regra.')
        return title

    def clean_keywords(self):
        keywords = AutomationRule.normalize_keywords(self.cleaned_data.get('keywords', ''))
        if not keywords:
            raise forms.ValidationError('Informe ao menos uma palavra-chave.')
        return keywords

    def clean_response_text(self):
        response_text = self.cleaned_data.get('response_text', '').strip()
        if not response_text:
            raise forms.ValidationError('Informe a resposta orientada.')
        return response_text[:1200]

    def clean_customer_example(self):
        return self.cleaned_data.get('customer_example', '').strip()[:600]

    def clean_internal_instruction(self):
        return self.cleaned_data.get('internal_instruction', '').strip()[:600]


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


class AiAttendantConfigForm(forms.ModelForm):
    """Configuracao do atendente virtual (IA) editada pelo ADM."""

    class Meta:
        model = AiAttendantConfig
        fields = ['enabled', 'company_name', 'welcome_message', 'fallback_sector', 'max_turns']
        labels = {
            'enabled': 'Ativar atendente virtual',
            'company_name': 'Nome da empresa',
            'welcome_message': 'Mensagem de boas-vindas',
            'fallback_sector': 'Setor padrao (quando nao entender)',
            'max_turns': 'Tentativas de entender antes de transferir',
        }
        widgets = {
            'company_name': forms.TextInput(attrs={'placeholder': 'Ex.: BEEZAP', 'autocomplete': 'off'}),
            'welcome_message': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Use {empresa} para inserir o nome da empresa.',
            }),
            'max_turns': forms.NumberInput(attrs={'min': 1, 'max': 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fallback_sector'].required = False
        self.fields['fallback_sector'].empty_label = 'Nenhum (encaminhar sem setor)'
        self.fields['fallback_sector'].queryset = Sector.objects.all()

    def clean_max_turns(self):
        value = self.cleaned_data.get('max_turns') or 1
        return max(1, min(int(value), 10))
