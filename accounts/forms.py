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
