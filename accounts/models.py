from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import models
from django.utils import timezone
import re


class ConversationViewScope(models.TextChoices):
    """Alcance de visualizacao de conversas (quais chats diretos a pessoa enxerga).
    Ordem crescente de permissividade (ver VIEW_SCOPE_RANK em accounts/permissions.py)."""
    OWN = 'own', 'Somente as próprias conversas'
    SECTOR_OPEN = 'sector_open', 'Conversas em aberto do setor'
    SECTOR_ALL = 'sector_all', 'Todas do setor (inclui finalizadas)'
    ALL = 'all', 'Conversas de todos os setores'


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('O e-mail é obrigatório.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', User.Role.ADM)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser precisa de is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser precisa de is_superuser=True.')
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADM = 'adm', 'Administrador'
        USUARIO = 'usuario', 'Usuário'
        LEITOR = 'leitor', 'Leitor'

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USUARIO)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class WapiConfiguration(models.Model):
    instance_id = models.CharField(max_length=120, blank=True)
    token = models.CharField(max_length=255, blank=True)
    webhook_token = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao W-API'
        verbose_name_plural = 'Configuracoes W-API'

    @classmethod
    def get_solo(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    @property
    def has_token(self):
        return bool(self.token or settings.WAPI_TOKEN)

    @property
    def has_webhook_token(self):
        return bool(self.webhook_token or settings.WAPI_WEBHOOK_TOKEN)

    def resolved_instance_id(self):
        return self.instance_id or settings.WAPI_INSTANCE_ID

    def resolved_token(self):
        return self.token or settings.WAPI_TOKEN

    def resolved_webhook_token(self):
        return self.webhook_token or settings.WAPI_WEBHOOK_TOKEN

    def __str__(self):
        return 'Configuracao W-API'


class OpenAiConfiguration(models.Model):
    """Configuracao da integracao com a API do OpenAI (GPT). Singleton (pk=1).

    A API Key fica salva AQUI (no banco), editada na tela Inteligencia (IA) — nunca
    fica no codigo e nunca e exibida de novo depois de salva (mesmo padrao do token
    da W-API). `resolved_api_key()` cai para a variavel de ambiente OPENAI_API_KEY
    quando o campo esta vazio. `enabled` e um interruptor mestre: enquanto False,
    nada usa a IA.
    """
    api_key = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=80, blank=True, default='gpt-4.1-nano')
    enabled = models.BooleanField(default=False)
    # Prompt/persona do atendente virtual (editavel na tela). Os setores, os
    # atendentes e as ultimas mensagens sao anexados automaticamente pelo codigo.
    instructions = models.TextField(blank=True, default='')
    # Numero maximo de respostas da IA no mesmo atendimento antes de encaminhar
    # para o setor de fallback (evita loop/gasto e nao prende o cliente).
    max_turns = models.PositiveSmallIntegerField(default=3)
    # Setor para onde a IA encaminha quando nao identifica o setor certo (ou ao
    # atingir max_turns). Se vazio, a conversa fica em aberto sem setor.
    fallback_sector = models.ForeignKey(
        'Sector', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='ai_fallback_configs',
    )
    # Contador de consumo (acumulado). O OpenAI devolve `usage` em cada resposta;
    # o cliente soma aqui de forma atomica. Serve para controle de gasto.
    total_requests = models.PositiveBigIntegerField(default=0)
    total_prompt_tokens = models.PositiveBigIntegerField(default=0)
    total_completion_tokens = models.PositiveBigIntegerField(default=0)
    total_tokens = models.PositiveBigIntegerField(default=0)
    usage_since = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Diagnostico: conteudo COMPLETO da ultima chamada ao GPT (o que foi enviado e
    # o que voltou), para o ADM inspecionar exatamente o contexto. Nunca contem a
    # API Key (ela vai so no header, nao no corpo).
    last_request = models.TextField(blank=True, default='')
    last_response = models.TextField(blank=True, default='')
    last_exchange_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao OpenAI (GPT)'
        verbose_name_plural = 'Configuracoes OpenAI (GPT)'

    @classmethod
    def get_solo(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    @property
    def has_api_key(self):
        return bool(self.api_key or settings.OPENAI_API_KEY)

    def resolved_api_key(self):
        return (self.api_key or settings.OPENAI_API_KEY or '').strip()

    def resolved_model(self):
        return (self.model or settings.OPENAI_MODEL or 'gpt-4.1-nano').strip()

    @classmethod
    def record_usage(cls, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        """Soma o consumo de uma chamada ao GPT de forma atomica (F()), segura
        para chamadas concorrentes (ex.: threads em background)."""
        from django.db.models import F
        now = timezone.now()
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        total_tokens = int(total_tokens or prompt_tokens + completion_tokens)
        # Marca o inicio da contagem apenas na 1a chamada apos um reset (usage_since nulo).
        cls.objects.filter(pk=1, usage_since__isnull=True).update(usage_since=now)
        cls.objects.filter(pk=1).update(
            total_requests=F('total_requests') + 1,
            total_prompt_tokens=F('total_prompt_tokens') + prompt_tokens,
            total_completion_tokens=F('total_completion_tokens') + completion_tokens,
            total_tokens=F('total_tokens') + total_tokens,
            last_used_at=now,
        )

    @classmethod
    def record_last_exchange(cls, request_text, response_text):
        """Guarda o conteudo completo da ultima chamada ao GPT (diagnostico)."""
        cls.objects.filter(pk=1).update(
            last_request=(request_text or '')[:20000],
            last_response=(response_text or '')[:20000],
            last_exchange_at=timezone.now(),
        )

    def reset_usage(self):
        self.total_requests = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.usage_since = None
        self.last_used_at = None
        self.save(update_fields=[
            'total_requests', 'total_prompt_tokens', 'total_completion_tokens',
            'total_tokens', 'usage_since', 'last_used_at',
        ])

    def __str__(self):
        return 'Configuracao OpenAI (GPT)'


class MenuBotConfiguration(models.Model):
    """Chatbot de menu (atendimento automatico SEM IA) + o MODO mestre de primeiro
    atendimento. Singleton (pk=1).

    O campo `mode` e a FONTE UNICA da verdade de qual atendimento automatico atua no
    primeiro contato de uma conversa direta: `off` (nenhum), `menu` (este chatbot de
    menu) ou `ai` (o atendente virtual GPT). O webhook dispara apenas o motor
    correspondente ao modo escolhido — os dois nunca rodam juntos.

    Os textos do menu sao editaveis na tela Atendimento. O placeholder `{saudacao}`
    e trocado por "Bom dia/Boa tarde/Boa noite" conforme o horario; `{setor}` (na
    mensagem de confirmacao) pelo nome do setor escolhido.
    """
    MODE_OFF = 'off'
    MODE_MENU = 'menu'
    MODE_AI = 'ai'
    MODE_CHOICES = [
        (MODE_OFF, 'Desligado'),
        (MODE_MENU, 'Chatbot de menu'),
        (MODE_AI, 'Inteligencia (IA)'),
    ]

    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_OFF)
    greeting = models.TextField(blank=True, default='')
    menu_intro = models.TextField(blank=True, default='')
    invalid_message = models.TextField(blank=True, default='')
    confirmation_message = models.TextField(blank=True, default='')
    handoff_message = models.TextField(blank=True, default='')
    # Tentativas invalidas seguidas antes de encaminhar para um atendente humano.
    max_attempts = models.PositiveSmallIntegerField(default=3)
    # Setor para onde encaminhar quando o cliente nao acerta o menu. Vazio = deixa
    # a conversa aguardando (pending) sem setor.
    fallback_sector = models.ForeignKey(
        'Sector', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='menubot_fallback_configs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao do chatbot (menu)'
        verbose_name_plural = 'Configuracoes do chatbot (menu)'

    @classmethod
    def get_solo(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    def ordered_options(self):
        return list(self.options.select_related('sector').order_by('order', 'id'))

    def __str__(self):
        return 'Configuracao do chatbot (menu)'


class MenuOption(models.Model):
    """Uma opcao do menu do chatbot. O numero que o cliente digita e a `order`
    (1, 2, 3...); cada opcao encaminha para um Setor."""
    config = models.ForeignKey(
        MenuBotConfiguration, on_delete=models.CASCADE, related_name='options'
    )
    order = models.PositiveSmallIntegerField(default=1)
    label = models.CharField(max_length=100)
    sector = models.ForeignKey(
        'Sector', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='menu_options',
    )

    class Meta:
        ordering = ('order', 'id')
        verbose_name = 'Opcao do menu'
        verbose_name_plural = 'Opcoes do menu'

    @property
    def key(self):
        """Numero que o cliente digita para escolher esta opcao."""
        return str(self.order)

    def __str__(self):
        return f'{self.order} - {self.label}'


class RoleMenuPermission(models.Model):
    """Botoes do menu liberados para um PERFIL (role). Uma linha por perfil editavel
    (`usuario`/`leitor`). O admin nao e armazenado aqui (tem sempre acesso total).
    Sem linha, vale o padrao definido em `accounts/permissions.py`."""
    role = models.CharField(max_length=20, unique=True)
    allowed_keys = models.JSONField(default=list)
    # Ao abrir uma conversa, ve a conversa inteira (True) ou so o atendimento atual
    # (False, padrao) — o trecho apos a ultima divisoria.
    full_history = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Permissao de menu (perfil)'
        verbose_name_plural = 'Permissoes de menu (perfis)'

    def __str__(self):
        return f'Permissoes do perfil {self.role}'


class UserMenuPermission(models.Model):
    """Personalizacao de menu de um USUARIO especifico (sobrepoe o padrao do perfil).
    A existencia da linha significa que o usuario tem um conjunto proprio de botoes."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='menu_permission')
    allowed_keys = models.JSONField(default=list)
    full_history = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Permissao de menu (usuario)'
        verbose_name_plural = 'Permissoes de menu (usuarios)'

    def __str__(self):
        return f'Permissoes de {self.user.email}'


class GroupAccess(models.Model):
    """Quem pode ver um GRUPO do WhatsApp. Sem regra cadastrada, o grupo fica
    visivel apenas para o administrador (que ve tudo). Liberacao por setor e/ou por
    usuario especifico."""
    conversation = models.OneToOneField(
        'Conversation', on_delete=models.CASCADE, related_name='access'
    )
    sectors = models.ManyToManyField('Sector', blank=True, related_name='group_accesses')
    users = models.ManyToManyField(User, blank=True, related_name='group_accesses')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acesso a grupo'
        verbose_name_plural = 'Acessos a grupos'

    def __str__(self):
        return f'Acesso ao grupo {self.conversation_id}'


class WapiWebhookEvent(models.Model):
    event_type = models.CharField(max_length=80, default='unknown')
    instance_id = models.CharField(max_length=120, blank=True, default='')
    phone = models.CharField(max_length=40, blank=True, default='')
    contact_name = models.CharField(max_length=150, blank=True, default='')
    message_id = models.CharField(max_length=160, blank=True, default='')
    message_type = models.CharField(max_length=60, default='unknown')
    message_text = models.TextField(blank=True, default='')
    from_me = models.BooleanField(default=False)
    raw_payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ('-received_at',)
        verbose_name = 'Evento webhook W-API'
        verbose_name_plural = 'Eventos webhook W-API'

    @property
    def status_label(self):
        return 'Processado' if self.processed else 'Recebido'

    @property
    def short_text(self):
        text = ' '.join((self.message_text or '').split())
        return text[:90] + '...' if len(text) > 90 else text

    def __str__(self):
        return f'{self.event_type} - {self.phone or "sem telefone"}'


class Attendant(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='attendant_profile')
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Atendente'
        verbose_name_plural = 'Atendentes'
        ordering = ('name', 'user__email')

    @staticmethod
    def normalize_phone(value):
        return re.sub(r'\D', '', value or '')

    @property
    def formatted_phone(self):
        digits = self.phone or ''
        if len(digits) == 11:
            return f'({digits[:2]}) {digits[2:7]}-{digits[7:]}'
        if len(digits) == 10:
            return f'({digits[:2]}) {digits[2:6]}-{digits[6:]}'
        return digits or '-'

    @property
    def status_label(self):
        return 'Ativo' if self.user.is_active else 'Inativo'

    def __str__(self):
        return self.name


class Sector(models.Model):
    # Setor PADRAO de triagem: sempre existe, nao pode ser excluido nem renomeado, e
    # todos os atendentes fazem parte dele por padrao. E o destino garantido do handoff
    # da IA/chatbot (ver gpt/attendant.py e chatbot/handler.py).
    GENERAL_SECTOR_NAME = 'Geral'

    name = models.CharField('Nome', max_length=100, unique=True)
    description = models.TextField('Descrição', blank=True, default='')
    attendants = models.ManyToManyField(
        Attendant,
        blank=True,
        related_name='sectors',
        verbose_name='Atendentes',
    )
    # Visualizacao de conversas (padrao do setor; usuario pode ter excecao propria em
    # UserConversationView). Ver aba "Visualização de conversas" em Permissoes e
    # accounts/permissions.py (effective_view_scope / history_full_for).
    view_scope = models.CharField(
        'Alcance de visualização',
        max_length=20,
        choices=ConversationViewScope.choices,
        default=ConversationViewScope.SECTOR_OPEN,
    )
    view_full_history = models.BooleanField('Ver conversa inteira', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Setor'
        verbose_name_plural = 'Setores'

    @property
    def is_general(self):
        """E o setor Geral padrao? (protegido contra exclusao/renomeacao)."""
        return (self.name or '').strip().lower() == self.GENERAL_SECTOR_NAME.lower()

    @classmethod
    def ensure_general(cls):
        """Garante o setor 'Geral' padrao (cria se faltar). Ao CRIAR, ja inclui TODOS
        os atendentes — depois disso a adesao de novos atendentes e mantida por sinal
        (ver accounts/signals.py)."""
        sector, created = cls.objects.get_or_create(
            name=cls.GENERAL_SECTOR_NAME,
            defaults={'description': 'Setor padrão de triagem. Todos os atendentes fazem parte dele.'},
        )
        if created:
            attendants = list(Attendant.objects.all())
            if attendants:
                sector.attendants.add(*attendants)
        return sector

    def __str__(self):
        return self.name


class UserConversationView(models.Model):
    """Excecao POR USUARIO da visualizacao de conversas — sobrepoe o padrao do(s)
    setor(es). Campos NULOS = herdar do setor. A existencia da linha (com algum campo
    preenchido) significa que o usuario tem uma personalizacao propria."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='conversation_view')
    # null = herdar do setor.
    view_scope = models.CharField(
        max_length=20, choices=ConversationViewScope.choices, null=True, blank=True
    )
    # null = herdar do setor; True/False = forcar.
    view_full_history = models.BooleanField(null=True, blank=True, default=None)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Visualização de conversas (usuário)'
        verbose_name_plural = 'Visualizações de conversas (usuários)'

    def __str__(self):
        return f'Visualização de {self.user.email}'

    @property
    def is_customized(self):
        return self.view_scope is not None or self.view_full_history is not None


class PasswordResetCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_codes')
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Codigo de recuperacao de senha'
        verbose_name_plural = 'Codigos de recuperacao de senha'
        ordering = ('-created_at',)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_available(self):
        return self.used_at is None and not self.is_expired and self.attempts < 5

    def matches(self, code):
        return self.is_available and check_password(code, self.code_hash)

    def invalidate(self):
        self.used_at = timezone.now()
        self.save(update_fields=['used_at'])


class Contact(models.Model):
    name = models.CharField(max_length=150, blank=True, default='')
    phone = models.CharField(max_length=30, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contato'
        verbose_name_plural = 'Contatos'
        ordering = ('name', 'phone')

    @property
    def display_name(self):
        return self.name or self.phone

    @property
    def initials(self):
        base = (self.name or '').strip()
        if not base:
            return (self.phone or '?')[-2:]
        parts = [p for p in base.split() if p]
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[-1][:1]).upper()

    def __str__(self):
        return self.display_name


class Conversation(models.Model):
    STATUS_CHOICES = [
        ('open', 'Aberta'),
        ('pending', 'Pendente'),
        ('closed', 'Encerrada'),
    ]
    CHAT_TYPE_CHOICES = [
        ('private', 'Direta'),
        ('group', 'Grupo'),
    ]

    # Conversa direta tem contato (telefone); conversa de grupo nao tem contato
    # individual, por isso o vinculo e opcional.
    contact = models.ForeignKey(
        Contact, null=True, blank=True, on_delete=models.CASCADE, related_name='conversations'
    )
    # ID real da conversa na W-API: telefone/LID (direta) ou JID do grupo (@g.us).
    external_id = models.CharField(max_length=150, blank=True, default='', db_index=True)
    chat_type = models.CharField(max_length=10, choices=CHAT_TYPE_CHOICES, default='private')
    # Titulo da conversa (usado principalmente para o nome do grupo).
    name = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    assigned_attendant = models.ForeignKey(
        Attendant, null=True, blank=True, on_delete=models.SET_NULL, related_name='conversations'
    )
    sector = models.ForeignKey(
        Sector, null=True, blank=True, on_delete=models.SET_NULL, related_name='conversations'
    )
    last_message_text = models.TextField(blank=True, default='')
    last_message_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.PositiveIntegerField(default=0)
    # Quantas respostas a IA ja deu no atendimento atual (recepcao). Zera ao
    # transferir/encerrar/reabrir. Usado para o limite max_turns.
    ai_turns = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conversa'
        verbose_name_plural = 'Conversas'
        ordering = ('-last_message_at', '-created_at')

    @property
    def status_label(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status)

    @property
    def is_group(self):
        return self.chat_type == 'group'

    @property
    def display_title(self):
        """Nome exibido na lista/cabecalho (grupo, contato ou fallback)."""
        if self.is_group:
            if self.name:
                return self.name
            return f'Grupo {self.external_id}' if self.external_id else 'Grupo'
        if self.contact_id:
            return self.contact.display_name
        return self.name or self.external_id or 'Conversa'

    @property
    def display_initials(self):
        if self.is_group:
            base = (self.name or '').strip()
            if base:
                parts = [p for p in base.split() if p]
                if len(parts) == 1:
                    return parts[0][:2].upper()
                return (parts[0][:1] + parts[-1][:1]).upper()
            return 'GR'
        if self.contact_id:
            return self.contact.initials
        base = (self.name or self.external_id or '?').strip()
        return base[:2].upper()

    @property
    def recipient(self):
        """Destino de envio: o JID do grupo, o LID/numero da conversa direta."""
        if self.external_id:
            return self.external_id
        if self.contact_id:
            return self.contact.phone
        return ''

    def __str__(self):
        return f'Conversa: {self.display_title}'


class Message(models.Model):
    DIRECTION_CHOICES = [
        ('in', 'Recebida'),
        ('out', 'Enviada'),
    ]
    STATUS_CHOICES = [
        ('received', 'Recebida'),
        ('sent', 'Enviada'),
        ('failed', 'Falhou'),
    ]
    TYPE_CHOICES = [
        ('text', 'Texto'),
        ('image', 'Imagem'),
        ('audio', 'Audio'),
        ('video', 'Video'),
        ('document', 'Documento'),
        ('sticker', 'Figurinha'),
        ('gif', 'GIF'),
        ('reaction', 'Reacao'),
        ('location', 'Localizacao'),
        ('contact', 'Contato'),
        ('unknown', 'Nao suportado'),
        # Mensagem de sistema (divisoria no meio do chat: encerramento / novo
        # atendimento). Nao e enviada/recebida pelo WhatsApp; so exibida no chat.
        ('system', 'Sistema'),
    ]
    # Estado do download da midia recebida.
    MEDIA_STATUS_CHOICES = [
        ('none', 'Sem midia'),
        ('pending', 'Baixando'),
        ('ok', 'Disponivel'),
        ('unavailable', 'Indisponivel'),
    ]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    message_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='text')
    text = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=30, blank=True, default='')
    sender_name = models.CharField(max_length=150, blank=True, default='')
    # Quem enviou: em grupo e o participante; em conversa direta e o proprio chat.
    sender_id = models.CharField(max_length=80, blank=True, default='')
    participant_id = models.CharField(max_length=80, blank=True, default='')
    # Contexto de grupo/direta e origem (mensagem enviada pela conta conectada).
    is_group = models.BooleanField(default=False)
    from_me = models.BooleanField(default=False)
    # Marca falas do atendente virtual (IA), para distinguir de respostas humanas
    # (ex.: detectar quando um atendente assume no meio e a IA deve parar).
    is_ai = models.BooleanField(default=False)
    # ID real da mensagem na W-API (serve tambem como wapi_message_id).
    external_message_id = models.CharField(max_length=150, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    # Campos de midia (imagem/audio/video/documento/sticker/gif).
    media_file = models.FileField(upload_to='whatsapp/', blank=True, null=True)
    media_url = models.URLField(max_length=500, blank=True, default='')
    media_mimetype = models.CharField(max_length=120, blank=True, default='')
    media_status = models.CharField(max_length=20, choices=MEDIA_STATUS_CHOICES, default='none')
    raw_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'
        ordering = ('created_at',)

    @property
    def is_media(self):
        return self.message_type in ('image', 'audio', 'video', 'document', 'sticker', 'gif')

    @property
    def resolved_media_url(self):
        """Preferir o arquivo salvo localmente; senao o link remoto (pode expirar)."""
        if self.media_file:
            try:
                return self.media_file.url
            except ValueError:
                return ''
        return self.media_url or ''

    def __str__(self):
        return f'{self.get_direction_display()} ({self.message_type}): {self.text[:30]}'
