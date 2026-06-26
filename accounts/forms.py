from django import forms


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


class SettingsForm(forms.Form):
    company_name = forms.CharField(
        label='Nome da empresa',
        max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'Nome exibido no sistema'}),
    )
    workspace_name = forms.CharField(
        label='Nome do painel',
        max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'Nome da central ou operação'}),
    )
    support_email = forms.EmailField(
        label='E-mail de suporte',
        widget=forms.EmailInput(attrs={'placeholder': 'suporte@empresa.com'}),
    )
    support_phone = forms.CharField(
        label='Telefone de suporte',
        max_length=30,
        widget=forms.TextInput(attrs={'placeholder': '(11) 99999-9999'}),
    )
    business_hours = forms.CharField(
        label='Horário de atendimento',
        max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'Seg a Sex, 08:00 às 18:00'}),
    )
    default_sector = forms.CharField(
        label='Setor padrão',
        max_length=80,
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: Suporte'}),
    )
    welcome_message = forms.CharField(
        label='Mensagem de boas-vindas',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Mensagem automática para o cliente...'}),
    )
    auto_assignment = forms.ChoiceField(
        label='Distribuição automática',
        choices=(('sim', 'Sim'), ('nao', 'Não')),
    )
    notification_email = forms.EmailField(
        label='E-mail de notificações',
        required=False,
        widget=forms.EmailInput(attrs={'placeholder': 'alertas@empresa.com'}),
    )
    primary_color = forms.CharField(
        label='Cor principal',
        max_length=20,
        widget=forms.TextInput(attrs={'placeholder': '#21c25e'}),
    )
