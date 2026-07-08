from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import models
from django.utils import timezone
import re


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
    name = models.CharField('Nome', max_length=100, unique=True)
    description = models.TextField('Descrição', blank=True, default='')
    attendants = models.ManyToManyField(
        Attendant,
        blank=True,
        related_name='sectors',
        verbose_name='Atendentes',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Setor'
        verbose_name_plural = 'Setores'

    def __str__(self):
        return self.name


class AutomationRule(models.Model):
    title = models.CharField('Titulo da regra', max_length=120)
    sector = models.ForeignKey(
        Sector,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='automation_rules',
        verbose_name='Setor',
    )
    keywords = models.CharField('Palavras-chave', max_length=255)
    customer_example = models.TextField('Pergunta/exemplo do cliente', blank=True, default='')
    response_text = models.TextField('Resposta orientada')
    internal_instruction = models.TextField('Instrucao interna', blank=True, default='')
    is_active = models.BooleanField('Ativa', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-is_active', 'title')
        verbose_name = 'Regra de atendimento'
        verbose_name_plural = 'Regras de atendimento'

    @staticmethod
    def normalize_keywords(value):
        parts = [part.strip().lower() for part in (value or '').replace(';', ',').split(',')]
        return ', '.join(part for part in parts if part)

    @property
    def sector_label(self):
        return self.sector.name if self.sector else 'Geral'

    @property
    def status_label(self):
        return 'Ativa' if self.is_active else 'Inativa'

    @property
    def keyword_list(self):
        return [part.strip().lower() for part in self.keywords.split(',') if part.strip()]

    def __str__(self):
        return self.title


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
    # Estado do atendente virtual (IA) nesta conversa.
    AI_STATE_CHOICES = [
        ('active', 'IA atendendo'),
        ('handed_off', 'Transferida pela IA'),
        ('off', 'IA desligada'),
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
    # Atendente virtual (IA): estado e quantas vezes a IA ja falou nesta conversa.
    ai_state = models.CharField(max_length=12, choices=AI_STATE_CHOICES, default='active')
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
    # ID real da mensagem na W-API (serve tambem como wapi_message_id).
    external_message_id = models.CharField(max_length=150, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    # Campos de midia (imagem/audio/video/documento/sticker/gif).
    media_file = models.FileField(upload_to='whatsapp/', blank=True, null=True)
    media_url = models.URLField(max_length=500, blank=True, default='')
    media_mimetype = models.CharField(max_length=120, blank=True, default='')
    media_status = models.CharField(max_length=20, choices=MEDIA_STATUS_CHOICES, default='none')
    # True quando a mensagem foi enviada pelo atendente virtual (IA). Serve para
    # detectar quando um humano assume a conversa (mensagem 'out' com is_ai=False).
    is_ai = models.BooleanField(default=False)
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


class AiAttendantConfig(models.Model):
    """Configuracao (singleton) do atendente virtual (IA) de recepcao.

    O bot recebe o cliente numa conversa DIRETA, entende a intencao e transfere
    para o setor certo. Nada roda enquanto `enabled` estiver desligado (padrao).
    """
    enabled = models.BooleanField('Atendente virtual ativo', default=False)
    company_name = models.CharField('Nome da empresa', max_length=120, default='BEEZAP')
    welcome_message = models.TextField(
        'Mensagem de boas-vindas',
        default=(
            'Ola! Bem-vindo(a) a {empresa}. '
            'Sou o atendimento inicial e vou te direcionar para a area certa. '
            'Como posso ajudar voce hoje?'
        ),
        help_text='Use {empresa} para inserir o nome da empresa automaticamente.',
    )
    # Setor usado quando a IA nao consegue identificar a intencao apos max_turns.
    fallback_sector = models.ForeignKey(
        Sector, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='ai_fallback_configs', verbose_name='Setor padrao (fallback)',
    )
    max_turns = models.PositiveSmallIntegerField(
        'Tentativas de esclarecer', default=3,
        help_text='Quantas vezes a IA tenta entender antes de transferir mesmo assim.',
    )
    # Modo de teste: quando ligado, a IA decide o setor SO pelo modelo local, sem a
    # camada de palavras-chave nem a trava anti-ambiguidade (deixa a IA "sozinha").
    llm_only = models.BooleanField(
        'IA decide sozinha (sem palavras-chave)', default=False,
        help_text='Modo de teste: ignora as regras de palavras-chave e deixa o modelo decidir o setor.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao do atendente virtual'
        verbose_name_plural = 'Configuracao do atendente virtual'

    @classmethod
    def get_solo(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    def render_welcome(self):
        return (self.welcome_message or '').replace('{empresa}', self.company_name or '').strip()

    def __str__(self):
        return f'Atendente virtual ({"ativo" if self.enabled else "desligado"})'
