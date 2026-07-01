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

    def resolved_instance_id(self):
        return self.instance_id or settings.WAPI_INSTANCE_ID

    def resolved_token(self):
        return self.token or settings.WAPI_TOKEN

    def __str__(self):
        return 'Configuracao W-API'


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
