import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import Attendant, Company, Sector, User

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
        queryset=Sector.objects.none(),
        required=False,
        empty_label='(deixar em aberto, sem setor)',
    )

    def __init__(self, *args, company=None, **kwargs):
        """O select de setor lista SOMENTE os setores da empresa (multiempresa).
        Sem empresa a lista fica vazia — nunca mostra setor de outro cliente."""
        super().__init__(*args, **kwargs)
        self.fields['fallback_sector'].queryset = (
            Sector.objects.filter(company=company).order_by('name')
            if company is not None else Sector.objects.none()
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
        queryset=Sector.objects.none(),
        required=False,
        empty_label='(deixar aguardando, sem setor)',
    )

    def __init__(self, *args, company=None, **kwargs):
        """O select de setor lista SOMENTE os setores da empresa (multiempresa)."""
        super().__init__(*args, **kwargs)
        self.fields['fallback_sector'].queryset = (
            Sector.objects.filter(company=company).order_by('name')
            if company is not None else Sector.objects.none()
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

    def __init__(self, *args, company=None, **kwargs):
        """`company` = empresa dona do setor, usada na checagem de nome repetido.
        Ao editar, vale a empresa do proprio setor."""
        super().__init__(*args, **kwargs)
        self.company = company or getattr(self.instance, 'company', None)

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise forms.ValidationError('O nome do setor é obrigatório.')
        # O nome e unico POR EMPRESA: outra empresa pode ter um setor com o mesmo
        # nome, então a checagem precisa ser feita dentro da empresa.
        qs = Sector.objects.filter(name__iexact=name)
        qs = qs.filter(company=self.company) if self.company is not None else qs.none()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe um setor com este nome.')
        return name


class CompanyForm(forms.ModelForm):
    """Cadastro da EMPRESA CLIENTE (tela Clientes, perfil master).

    Guarda os dados da empresa e a identidade visual (logo e cor de destaque) que
    aparecem na barra lateral do cliente. O `slug` (identificador curto) e gerado
    automaticamente a partir do nome quando nao e informado.
    """

    # Extensoes e tamanho aceitos no logo. FileField (nao ImageField) para nao
    # exigir o pacote Pillow — a validacao fica aqui.
    LOGO_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.svg')
    LOGO_MAX_MB = 2

    class Meta:
        model = Company
        fields = [
            'name', 'legal_name', 'document', 'slug', 'email', 'phone',
            'address', 'city', 'state', 'logo', 'accent_color', 'notes', 'is_active',
        ]
        labels = {
            'name': 'Nome da empresa',
            'legal_name': 'Razão social',
            'document': 'CNPJ',
            'slug': 'Identificador',
            'email': 'E-mail',
            'phone': 'Telefone',
            'address': 'Endereço',
            'city': 'Cidade',
            'state': 'UF',
            'logo': 'Logo da empresa',
            'accent_color': 'Cor de destaque',
            'notes': 'Observações',
            'is_active': 'Empresa ativa',
        }
        help_texts = {
            'slug': 'Nome curto usado pelo sistema. Deixe em branco para gerar pelo nome.',
            'logo': 'PNG, JPG, WEBP ou SVG, até 2 MB. Aparece na barra lateral do cliente.',
            'accent_color': 'Usada quando a empresa não tem logo cadastrado.',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Padaria do Bairro', 'autocomplete': 'off'}),
            'legal_name': forms.TextInput(attrs={'placeholder': 'Razão social (opcional)', 'autocomplete': 'off'}),
            'document': forms.TextInput(attrs={'placeholder': '00.000.000/0000-00', 'autocomplete': 'off', 'inputmode': 'numeric'}),
            'slug': forms.TextInput(attrs={'placeholder': 'padaria-do-bairro', 'autocomplete': 'off'}),
            'email': forms.EmailInput(attrs={'placeholder': 'contato@empresa.com', 'autocomplete': 'off'}),
            'phone': forms.TextInput(attrs={'placeholder': '(00) 00000-0000', 'autocomplete': 'off', 'inputmode': 'numeric'}),
            'address': forms.TextInput(attrs={'placeholder': 'Rua, número, bairro (opcional)', 'autocomplete': 'off'}),
            'city': forms.TextInput(attrs={'placeholder': 'Cidade (opcional)', 'autocomplete': 'off'}),
            'state': forms.TextInput(attrs={'placeholder': 'UF', 'maxlength': 2, 'autocomplete': 'off'}),
            'accent_color': forms.TextInput(attrs={'type': 'color'}),
            'notes': forms.Textarea(attrs={'placeholder': 'Anotações internas (opcional)', 'rows': 3, 'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        # A empresa padrao nao pode ser desativada (e a dona dos dados que ja
        # existiam e o destino de qualquer registro sem empresa).
        if self.instance and self.instance.pk and self.instance.is_default:
            self.fields['is_active'].disabled = True
            self.fields['is_active'].help_text = 'A empresa padrão não pode ser desativada.'

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('Informe o nome da empresa.')
        return name

    def clean_document(self):
        """CNPJ e guardado so em digitos (a exibicao formata). Vazio e permitido."""
        digits = re.sub(r'\D', '', self.cleaned_data.get('document') or '')
        if digits and len(digits) != 14:
            raise forms.ValidationError('O CNPJ deve ter 14 números.')
        return digits

    def clean_phone(self):
        digits = re.sub(r'\D', '', self.cleaned_data.get('phone') or '')
        if digits and len(digits) not in (10, 11):
            raise forms.ValidationError('Informe o telefone com DDD.')
        return digits

    def clean_state(self):
        return (self.cleaned_data.get('state') or '').strip().upper()

    def clean_accent_color(self):
        color = (self.cleaned_data.get('accent_color') or '').strip()
        if not color:
            return ''
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise forms.ValidationError('Escolha uma cor válida.')
        return color.lower()

    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        # Sem arquivo novo (ou mantendo o atual) nao ha o que validar.
        if not logo or not hasattr(logo, 'name') or not hasattr(logo, 'size'):
            return logo
        name = (logo.name or '').lower()
        if not name.endswith(self.LOGO_EXTENSIONS):
            raise forms.ValidationError('O logo deve ser PNG, JPG, WEBP ou SVG.')
        if logo.size > self.LOGO_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f'O logo deve ter no máximo {self.LOGO_MAX_MB} MB.')
        return logo

    def clean_slug(self):
        """Identificador curto: gerado pelo nome quando vazio e unico no sistema."""
        slug = slugify(self.cleaned_data.get('slug') or '')
        if not slug:
            # `clean_name` roda antes (ordem dos campos), então o nome já validado
            # está disponível; o `self.data` cobre o caso de o nome ter dado erro.
            slug = slugify(self.cleaned_data.get('name') or self.data.get('name') or '')
        if not slug:
            raise forms.ValidationError('Não foi possível gerar o identificador. Informe um nome válido.')
        qs = Company.objects.filter(slug=slug)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe uma empresa com este identificador.')
        return slug

    def clean_is_active(self):
        # Rede de seguranca: campo desabilitado nao vem no POST, mas a empresa
        # padrao precisa continuar ativa de qualquer forma.
        if self.instance and self.instance.pk and self.instance.is_default:
            return True
        return self.cleaned_data.get('is_active', True)


class CompanyAdminForm(forms.Form):
    """Primeiro ACESSO de uma empresa cliente (criado pelo gestor master).

    Cria o **Administrador** da empresa: e ele quem, depois, cadastra os atendentes,
    os setores e as configuracoes do cliente. A senha informada aqui e inicial — a
    pessoa e obrigada a troca-la no primeiro login (ver
    `InitialPasswordChangeMiddleware`).
    """

    name = forms.CharField(
        label='Nome do responsável',
        max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'Nome de quem vai administrar', 'autocomplete': 'off'}),
    )
    email = forms.EmailField(
        label='E-mail de acesso',
        widget=forms.EmailInput(attrs={'placeholder': 'responsavel@empresa.com', 'autocomplete': 'off'}),
    )
    password = forms.CharField(
        label='Senha inicial',
        min_length=4,
        widget=forms.PasswordInput(attrs={'placeholder': 'Senha para o primeiro acesso', 'autocomplete': 'new-password'}),
        help_text='A pessoa será obrigada a trocar esta senha no primeiro acesso.',
    )
    phone = forms.CharField(
        label='WhatsApp (para recuperar a senha)',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': '5511999999999', 'autocomplete': 'off'}),
    )

    def clean_email(self):
        """O e-mail e a chave de login, portanto unico em TODO o sistema (não por
        empresa) — duas empresas nao podem usar o mesmo e-mail."""
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('Este e-mail já está em uso no sistema.')
        return email

    def clean_phone(self):
        return Attendant.normalize_phone(self.cleaned_data.get('phone'))


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
