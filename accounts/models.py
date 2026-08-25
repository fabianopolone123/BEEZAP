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


# Cor de destaque usada quando a empresa nao cadastrou nenhuma.
DEFAULT_ACCENT_COLOR = '#1f7a53'


def readable_text_color(background, dark='#0b1f3d', light='#ffffff'):
    """Cor de TEXTO legivel sobre `background` (hex `#rgb` ou `#rrggbb`).

    Existe porque o gestor master escolhe livremente a cor de destaque da empresa e
    ela vai como fundo das INICIAIS (barra lateral, cartao do cliente, metricas).
    Com cor de texto fixa no CSS, uma empresa com destaque preto ficava com as
    letras invisiveis — foi o que aconteceu de verdade com um cliente cadastrado
    com `#000000`.

    Usa a luminancia relativa (WCAG, canais linearizados): fundo claro recebe texto
    escuro, fundo escuro recebe texto claro. Cor invalida ou vazia cai no texto
    escuro, que e o padrao visual do sistema.
    """
    valor = (background or '').strip().lstrip('#')
    if len(valor) == 3:
        valor = ''.join(c * 2 for c in valor)
    if len(valor) != 6:
        return dark
    try:
        canais = [int(valor[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return dark
    fundo = _relative_luminance(canais)
    # Escolhe pelo CONTRASTE REAL (razao WCAG) entre as duas opcoes, em vez de um
    # limiar magico: com limiar fixo, verde claro acabava recebendo texto branco,
    # que le pior do que o texto escuro.
    def contraste(cor_texto):
        outro = (cor_texto or '').strip().lstrip('#')
        canais_texto = [int(outro[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        texto = _relative_luminance(canais_texto)
        claro, escuro = max(texto, fundo), min(texto, fundo)
        return (claro + 0.05) / (escuro + 0.05)

    return dark if contraste(dark) >= contraste(light) else light


def _relative_luminance(canais):
    """Luminancia relativa (WCAG) de canais RGB já normalizados em 0..1."""
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


class Company(models.Model):
    """EMPRESA CLIENTE (uma "instancia" do sistema).

    O BEEonBOARD e multiempresa: cada cliente tem os SEUS setores, atendentes,
    contatos, conversas, mensagens e as SUAS proprias configuracoes de W-API e
    GPT. Todo dado operacional aponta para a empresa dona (campo `company`), e o
    sistema so mostra a cada pessoa os dados da empresa dela.

    Quem cadastra/edita empresas e o GESTOR MASTER (`User.Role.MASTER`), na tela
    "Clientes". Os dados cadastrais e o logo daqui aparecem na barra lateral do
    cliente (ver accounts/context_processors.py).

    Existe sempre UMA empresa padrao (`is_default`), criada na migration 0031 com
    tudo o que ja existia antes do multiempresa. Ela nao pode ser excluida.
    """

    name = models.CharField('Nome fantasia', max_length=120)
    legal_name = models.CharField('Razão social', max_length=180, blank=True, default='')
    document = models.CharField('CNPJ', max_length=20, blank=True, default='')
    # Identificador curto e estavel da empresa (so letras/numeros/hifen). Sera a
    # base da URL propria de webhook de cada cliente na Parte 2.
    slug = models.SlugField('Identificador', max_length=60, unique=True)
    email = models.EmailField('E-mail', blank=True, default='')
    phone = models.CharField('Telefone', max_length=20, blank=True, default='')
    address = models.CharField('Endereço', max_length=200, blank=True, default='')
    city = models.CharField('Cidade', max_length=100, blank=True, default='')
    state = models.CharField('UF', max_length=2, blank=True, default='')
    # Logo da empresa (FileField, nao ImageField: ImageField exigiria o pacote
    # Pillow. A validacao da extensao/tamanho e feita no CompanyForm).
    logo = models.FileField('Logo', upload_to='empresas/logos/', blank=True, null=True)
    accent_color = models.CharField('Cor de destaque', max_length=7, blank=True, default='')
    notes = models.TextField('Observações', blank=True, default='')
    is_active = models.BooleanField('Ativa', default=True)
    # A empresa padrao (dona de tudo o que existia antes do multiempresa). Nao
    # pode ser excluida nem desativada.
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Empresa cliente'
        verbose_name_plural = 'Empresas clientes'
        ordering = ('name',)

    DEFAULT_NAME = 'Empresa padrão'
    DEFAULT_SLUG = 'empresa-padrao'

    @classmethod
    def get_default(cls):
        """Empresa padrao (a que recebeu os dados anteriores ao multiempresa).
        Usada como destino de qualquer dado sem empresa definida."""
        company = cls.objects.filter(is_default=True).order_by('id').first()
        if company is not None:
            return company
        company = cls.objects.order_by('id').first()
        if company is not None:
            return company
        return cls.objects.create(
            name=cls.DEFAULT_NAME, slug=cls.DEFAULT_SLUG, is_default=True
        )

    @property
    def display_name(self):
        return self.name or self.legal_name or self.slug

    @property
    def initials(self):
        base = (self.name or self.slug or '?').strip()
        parts = [p for p in base.split() if p]
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[-1][:1]).upper()

    @property
    def accent_text_color(self):
        """Cor de texto legivel sobre a cor de destaque da empresa (ver
        `readable_text_color`). Usada nas INICIAIS, quando nao ha logo cadastrado."""
        return readable_text_color(self.accent_color or DEFAULT_ACCENT_COLOR)

    @property
    def status_label(self):
        return 'Ativa' if self.is_active else 'Inativa'

    @property
    def formatted_document(self):
        """CNPJ formatado (00.000.000/0000-00) quando tem 14 digitos."""
        digits = re.sub(r'\D', '', self.document or '')
        if len(digits) == 14:
            return f'{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}'
        return self.document or ''

    @property
    def formatted_phone(self):
        digits = re.sub(r'\D', '', self.phone or '')
        if len(digits) == 11:
            return f'({digits[:2]}) {digits[2:7]}-{digits[7:]}'
        if len(digits) == 10:
            return f'({digits[:2]}) {digits[2:6]}-{digits[6:]}'
        return self.phone or ''

    @property
    def location(self):
        parts = [p for p in (self.city, self.state) if p]
        return '/'.join(parts)

    @property
    def logo_url(self):
        if self.logo:
            try:
                return self.logo.url
            except ValueError:
                return ''
        return ''

    def __str__(self):
        return self.display_name


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
        # GESTOR MASTER (dono da plataforma): cadastra/administra as EMPRESAS
        # CLIENTES. Nao pertence a nenhuma empresa (`company` nulo) e, por decisao
        # de privacidade, NAO le as conversas dos clientes — so administra.
        MASTER = 'master', 'Gestor master'
        ADM = 'adm', 'Administrador'
        USUARIO = 'usuario', 'Usuário'
        LEITOR = 'leitor', 'Leitor'

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USUARIO)
    # Empresa cliente a que a pessoa pertence. NULO = gestor master (fica acima
    # das empresas). Todo usuario operacional (adm/usuario/leitor) tem empresa.
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.CASCADE,
        related_name='users', verbose_name='Empresa',
    )
    # RECUPERACAO DE SENHA de quem NAO tem perfil de atendente — na pratica o gestor
    # master, que nao pertence a nenhuma empresa e por isso nunca teve `Attendant`
    # (era dai que saia o telefone). Sem este campo ele so recuperaria a senha pelo
    # shell do servidor. Para os demais perfis o telefone continua vindo do Attendant.
    recovery_phone = models.CharField(
        'WhatsApp para recuperar a senha', max_length=20, blank=True,
    )
    # Troca obrigatoria no primeiro acesso para quem nao tem Attendant (o master).
    # O `InitialPasswordChangeMiddleware` olha os dois lugares.
    must_change_password = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    @property
    def is_master(self):
        return self.role == self.Role.MASTER

    def save(self, *args, **kwargs):
        """Normaliza o e-mail para MINUSCULO antes de gravar.

        `email` e unico no banco de forma SENSIVEL a caixa, mas o login busca com
        `email__iexact` (ver accounts/backends.EmailBackend). Sem normalizar, era
        possivel existirem `Joao@x.com` e `joao@x.com` ao mesmo tempo — e aí o login
        estourava `MultipleObjectsReturned`, ou seja, 500 na tela de entrada. Os
        formularios do sistema ja normalizavam; a porta aberta era criar conta pelo
        shell ou pelo admin do Django.
        """
        if self.email:
            self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class WapiConfiguration(models.Model):
    """Credenciais da W-API de UMA empresa cliente (uma configuracao por empresa)."""

    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name='wapi_config', verbose_name='Empresa',
    )
    instance_id = models.CharField(max_length=120, blank=True)
    token = models.CharField(max_length=255, blank=True)
    webhook_token = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao W-API'
        verbose_name_plural = 'Configuracoes W-API'

    @classmethod
    def for_company(cls, company):
        """Configuracao da empresa informada (cria vazia na primeira vez).

        Sem empresa devolve uma instancia VAZIA e NAO SALVA, em vez de estourar. O
        campo `company` e obrigatorio, entao `get_or_create(company=None)` levantava
        `IntegrityError` — um erro de banco cru, no meio de um fluxo que so queria
        saber "tem credencial configurada?". Devolvendo config vazia, quem pergunta
        recebe "nao" e a tela mostra o aviso normal de canal nao configurado.
        """
        if company is None:
            return cls()
        config, _ = cls.objects.get_or_create(company=company)
        return config

    @property
    def usa_credencial_do_ambiente(self):
        """Esta configuracao pode cair para as variaveis de ambiente?

        SO A EMPRESA PADRAO pode. As variaveis `WAPI_INSTANCE_ID`/`WAPI_TOKEN` do
        `.env` sao heranca da epoca de UM cliente unico, e a empresa padrao e a dona
        de tudo o que existia antes do multiempresa — para ela o fallback e o
        comportamento certo, e e o que mantem uma instalacao antiga funcionando sem
        reconfigurar nada.

        Para QUALQUER OUTRA empresa o fallback e perigoso: um cliente novo, ainda sem
        credencial cadastrada, mandaria mensagem pela instancia do `.env` — ou seja,
        **pelo WhatsApp de outro cliente**. Isso anularia justamente a garantia que a
        Parte 2 do multiempresa construiu ao tornar `company` obrigatorio em todas as
        funcoes de `wapi/client.py`. Sem credencial propria, o certo e nao enviar
        nada e a tela avisar "WhatsApp ainda nao configurado".
        """
        return bool(getattr(self.company, 'is_default', False))

    def _do_ambiente(self, valor_do_env):
        return valor_do_env if self.usa_credencial_do_ambiente else ''

    @property
    def has_token(self):
        return bool(self.token or self._do_ambiente(settings.WAPI_TOKEN))

    @property
    def has_webhook_token(self):
        return bool(self.webhook_token or self._do_ambiente(settings.WAPI_WEBHOOK_TOKEN))

    def resolved_instance_id(self):
        return self.instance_id or self._do_ambiente(settings.WAPI_INSTANCE_ID)

    def resolved_token(self):
        return self.token or self._do_ambiente(settings.WAPI_TOKEN)

    def resolved_webhook_token(self):
        return self.webhook_token or self._do_ambiente(settings.WAPI_WEBHOOK_TOKEN)

    def __str__(self):
        return 'Configuracao W-API'


class OpenAiConfiguration(models.Model):
    """Configuracao da integracao com a API do OpenAI (GPT) — UMA para toda a
    PLATAFORMA (nao e por empresa).

    Decisao de produto: a API Key do GPT e da plataforma (o gestor master paga a
    conta), entao existe UMA configuracao e todos os clientes usam a mesma chave. O
    que cada empresa decide por conta dela e apenas SE usa IA, chatbot de menu ou
    nada — isso vive em `MenuBotConfiguration.mode`, que e por empresa. Quem tem
    instancia/token proprios por empresa e a **W-API** (ver WapiConfiguration).

    So o GESTOR MASTER edita esta tela; o cliente nem a enxerga.

    A API Key fica salva AQUI (no banco), editada na tela Inteligencia (IA) — nunca
    fica no codigo e nunca e exibida de novo depois de salva (mesmo padrao do token
    da W-API). `resolved_api_key()` cai para a variavel de ambiente OPENAI_API_KEY
    quando o campo esta vazio.

    NAO existe mais um campo `enabled` aqui: a ativacao da IA e do MODO de primeiro
    atendimento de cada empresa (`MenuBotConfiguration.mode == 'ai'`), que e a fonte
    unica da verdade. O `enabled` sobrevivia so sendo ESCRITO — nenhum codigo o lia —
    e uma flag de plataforma nao poderia mesmo decidir por cada cliente.
    """
    api_key = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=80, blank=True, default='gpt-4.1-nano')
    # Prompt/persona do atendente virtual (editavel na tela). Os setores, os
    # atendentes e as ultimas mensagens sao anexados automaticamente pelo codigo.
    instructions = models.TextField(blank=True, default='')
    # Numero maximo de respostas da IA no mesmo atendimento antes de encaminhar
    # para o setor de fallback (evita loop/gasto e nao prende o cliente).
    max_turns = models.PositiveSmallIntegerField(default=3)
    #
    # NAO existe `fallback_sector` aqui: como esta configuracao e da PLATAFORMA, um
    # setor (que pertence a uma empresa) nao caberia. O destino do encaminhamento
    # quando a IA nao entende e o MESMO da empresa usado pelo chatbot
    # (`MenuBotConfiguration.fallback_sector`, por empresa) — um conceito so: "para
    # onde mandar quando nao entender". Na falta dele, vale o setor Geral da empresa.
    #
    # Contador de consumo (acumulado) DA PLATAFORMA. O OpenAI devolve `usage` em cada
    # resposta; o cliente soma aqui de forma atomica. Serve para controle de gasto.
    # (Quebrar o consumo por empresa cliente e item da Parte 4 — ver docs/CONTEXTO.md.)
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
        """A configuracao UNICA da plataforma (cria vazia na primeira vez).

        Nao usa `pk=1` fixo de proposito: a migration 0032 (que juntou as
        configuracoes por empresa numa so) pode ter mantido uma linha com outro id.
        """
        config = cls.objects.order_by('id').first()
        if config is None:
            config = cls.objects.create()
        return config

    @property
    def has_api_key(self):
        return bool(self.api_key or settings.OPENAI_API_KEY)

    def resolved_api_key(self):
        return (self.api_key or settings.OPENAI_API_KEY or '').strip()

    def resolved_model(self):
        return (self.model or settings.OPENAI_MODEL or 'gpt-4.1-nano').strip()

    def record_usage(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        """Soma o consumo de uma chamada ao GPT de forma atomica (F()), segura
        para chamadas concorrentes (ex.: threads em background). O consumo e
        contabilizado na configuracao DESTA empresa."""
        from django.db.models import F
        now = timezone.now()
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        total_tokens = int(total_tokens or prompt_tokens + completion_tokens)
        rows = type(self).objects.filter(pk=self.pk)
        # Marca o inicio da contagem apenas na 1a chamada apos um reset (usage_since nulo).
        rows.filter(usage_since__isnull=True).update(usage_since=now)
        rows.update(
            total_requests=F('total_requests') + 1,
            total_prompt_tokens=F('total_prompt_tokens') + prompt_tokens,
            total_completion_tokens=F('total_completion_tokens') + completion_tokens,
            total_tokens=F('total_tokens') + total_tokens,
            last_used_at=now,
        )

    def record_last_exchange(self, request_text, response_text):
        """Guarda o conteudo completo da ultima chamada ao GPT (diagnostico)."""
        type(self).objects.filter(pk=self.pk).update(
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


class CompanyAiUsage(models.Model):
    """Consumo de IA (GPT) de UMA empresa cliente em UM mes — so contagem.

    Por que existe: a API Key do GPT e UMA da plataforma (o gestor master paga a
    conta em `OpenAiConfiguration`), entao o contador da plataforma nao diz QUEM
    gastou. Esta tabela quebra o mesmo consumo por empresa e por mes, que e o que
    responde "qual cliente esta usando IA e quanto".

    Uma linha por empresa por mes (nao uma por chamada): o historico fica mes a mes
    sem a tabela crescer com o volume de mensagens, e o "mes atual" reinicia sozinho
    na virada, sem ninguem zerar nada a mao.

    NAO existe limite nem bloqueio aqui — e medicao. Se um dia a plataforma tiver
    plano/teto por cliente, e nesta tabela que o consumo do ciclo sera lido.

    Privacidade: so numeros e datas. Nada de texto de mensagem, contato ou conversa
    (a mesma regra da tela de Metricas — ver docs/CONTEXTO.md secao 16).
    """

    company = models.ForeignKey(
        'Company', on_delete=models.CASCADE, related_name='ai_usage',
        verbose_name='Empresa cliente',
    )
    # Mes de referencia (ano + mes de 1 a 12), no fuso local do projeto.
    year = models.PositiveSmallIntegerField('Ano')
    month = models.PositiveSmallIntegerField('Mês')
    total_requests = models.PositiveBigIntegerField(default=0)
    total_prompt_tokens = models.PositiveBigIntegerField(default=0)
    total_completion_tokens = models.PositiveBigIntegerField(default=0)
    total_tokens = models.PositiveBigIntegerField(default=0)
    first_used_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Consumo de IA da empresa'
        verbose_name_plural = 'Consumo de IA das empresas'
        ordering = ('-year', '-month', 'company__name')
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'year', 'month'], name='unique_company_ai_usage_month'
            ),
        ]

    MONTH_NAMES = [
        'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
        'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
    ]

    @staticmethod
    def reference(moment=None):
        """(ano, mes) do momento informado (ou de agora), no fuso local."""
        moment = timezone.localtime(moment or timezone.now())
        return moment.year, moment.month

    @classmethod
    def record(cls, company, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        """Soma UMA chamada ao GPT no mes atual daquela empresa.

        Atomico via F() (as chamadas da IA rodam em thread de background) e
        tolerante a corrida na criacao da linha do mes (get_or_create). Sem empresa
        nao grava nada — o consumo da plataforma continua em OpenAiConfiguration.
        """
        from django.db.models import F

        if company is None:
            return None
        now = timezone.now()
        year, month = cls.reference(now)
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        total_tokens = int(total_tokens or prompt_tokens + completion_tokens)
        row, _created = cls.objects.get_or_create(
            company=company, year=year, month=month,
            defaults={'first_used_at': now},
        )
        rows = cls.objects.filter(pk=row.pk)
        # first_used_at so na primeira chamada do mes (linha recem-criada tem nulo).
        rows.filter(first_used_at__isnull=True).update(first_used_at=now)
        rows.update(
            total_requests=F('total_requests') + 1,
            total_prompt_tokens=F('total_prompt_tokens') + prompt_tokens,
            total_completion_tokens=F('total_completion_tokens') + completion_tokens,
            total_tokens=F('total_tokens') + total_tokens,
            last_used_at=now,
        )
        return row

    @classmethod
    def month_totals(cls, company, year=None, month=None):
        """Consumo de um mes (o atual, por padrao). Sempre devolve numeros — mes sem
        uso vem zerado, para a tela nao precisar tratar ausencia."""
        if year is None or month is None:
            year, month = cls.reference()
        row = cls.objects.filter(company=company, year=year, month=month).first()
        return {
            'ano': year,
            'mes': month,
            'rotulo': cls.month_label(year, month),
            'chamadas': row.total_requests if row else 0,
            'tokens': row.total_tokens if row else 0,
            'tokens_entrada': row.total_prompt_tokens if row else 0,
            'tokens_saida': row.total_completion_tokens if row else 0,
            'ultimo_uso': timezone.localtime(row.last_used_at) if row and row.last_used_at else None,
        }

    @classmethod
    def previous_reference(cls, year=None, month=None):
        """(ano, mes) do mes anterior ao informado (ou ao atual)."""
        if year is None or month is None:
            year, month = cls.reference()
        if month == 1:
            return year - 1, 12
        return year, month - 1

    @classmethod
    def all_time_totals(cls, company):
        """Consumo acumulado da empresa (soma de todos os meses)."""
        from django.db.models import Sum

        agg = cls.objects.filter(company=company).aggregate(
            chamadas=Sum('total_requests'),
            tokens=Sum('total_tokens'),
            tokens_entrada=Sum('total_prompt_tokens'),
            tokens_saida=Sum('total_completion_tokens'),
        )
        return {chave: (valor or 0) for chave, valor in agg.items()}

    @classmethod
    def month_label(cls, year, month):
        try:
            nome = cls.MONTH_NAMES[int(month) - 1]
        except (IndexError, ValueError, TypeError):
            return f'{month}/{year}'
        return f'{nome} de {year}'

    @property
    def label(self):
        return self.month_label(self.year, self.month)

    def __str__(self):
        return f'{self.company_id} — {self.label}: {self.total_tokens} tokens'


class MenuBotConfiguration(models.Model):
    """Chatbot de menu (atendimento automatico SEM IA) + o MODO mestre de primeiro
    atendimento. Uma configuracao por EMPRESA cliente.

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

    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name='menubot_config', verbose_name='Empresa',
    )
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
    def for_company(cls, company):
        """Configuracao da empresa informada (cria vazia na primeira vez).

        Sem empresa devolve instancia VAZIA e NAO SALVA (mesmo motivo de
        `WapiConfiguration.for_company`): `company` e obrigatorio, entao
        `get_or_create(company=None)` levantava `IntegrityError`. Config vazia
        significa modo `off` — nenhum atendimento automatico — que e a resposta
        segura quando nao se sabe de qual empresa se trata.
        """
        if company is None:
            return cls()
        config, _ = cls.objects.get_or_create(company=company)
        return config

    def ordered_options(self):
        # Instancia nao salva (ver `for_company` sem empresa) nao tem opcoes.
        if self.pk is None:
            return []
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
    """Botoes do menu liberados para um PERFIL (role) DENTRO DE UMA EMPRESA. Uma
    linha por perfil editavel (`usuario`/`leitor`) de cada empresa — assim cada
    cliente define os proprios botoes. O admin nao e armazenado aqui (tem sempre
    acesso total). Sem linha, vale o padrao definido em `accounts/permissions.py`."""
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='role_permissions', verbose_name='Empresa',
    )
    role = models.CharField(max_length=20)
    allowed_keys = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Permissao de menu (perfil)'
        verbose_name_plural = 'Permissoes de menu (perfis)'
        # O perfil e unico POR EMPRESA (antes era unico global).
        constraints = [
            models.UniqueConstraint(
                fields=('company', 'role'), name='unique_role_permission_per_company'
            ),
        ]

    def __str__(self):
        return f'Permissoes do perfil {self.role}'


class UserMenuPermission(models.Model):
    """Personalizacao de menu de um USUARIO especifico (sobrepoe o padrao do perfil).
    A existencia da linha significa que o usuario tem um conjunto proprio de botoes."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='menu_permission')
    allowed_keys = models.JSONField(default=list)
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
    # Empresa dona do evento (resolvida pela URL/instancia do webhook).
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='webhook_events', verbose_name='Empresa',
    )
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

    class Meta:
        ordering = ('-received_at',)
        verbose_name = 'Evento webhook W-API'
        verbose_name_plural = 'Eventos webhook W-API'
        # A tabela cresce com TODO evento recebido (e e a maior do sistema em linhas).
        # As consultas sao "os ultimos N desta empresa" e `Max(received_at)` por
        # empresa, nas telas de Metricas — as duas cobertas pelo indice abaixo.
        indexes = [
            models.Index(fields=['company', '-received_at'], name='evento_empresa_data_idx'),
            models.Index(fields=['instance_id'], name='evento_instancia_idx'),
        ]

    @property
    def short_text(self):
        text = ' '.join((self.message_text or '').split())
        return text[:90] + '...' if len(text) > 90 else text

    def __str__(self):
        return f'{self.event_type} - {self.phone or "sem telefone"}'


class Attendant(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='attendants', verbose_name='Empresa',
    )
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

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='sectors', verbose_name='Empresa',
    )
    # O nome e unico POR EMPRESA (antes era unico global) — duas empresas podem ter
    # um setor "Financeiro" cada uma. Ver a constraint no Meta.
    name = models.CharField('Nome', max_length=100)
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
        constraints = [
            models.UniqueConstraint(fields=('company', 'name'), name='unique_sector_name_per_company'),
        ]

    @property
    def is_general(self):
        """E o setor Geral padrao? (protegido contra exclusao/renomeacao)."""
        return (self.name or '').strip().lower() == self.GENERAL_SECTOR_NAME.lower()

    @classmethod
    def ensure_general(cls, company=None):
        """Garante o setor 'Geral' padrao DA EMPRESA (cria se faltar). Ao CRIAR, ja
        inclui TODOS os atendentes dessa empresa — depois disso a adesao de novos
        atendentes e mantida por sinal (ver accounts/signals.py).

        Sem `company`, usa a empresa padrao (compatibilidade enquanto as chamadas
        nao recebem a empresa do contexto — Parte 2 do multiempresa)."""
        if company is None:
            company = Company.get_default()
        sector, created = cls.objects.get_or_create(
            company=company,
            name=cls.GENERAL_SECTOR_NAME,
            defaults={'description': 'Setor padrão de triagem. Todos os atendentes fazem parte dele.'},
        )
        if created:
            attendants = list(Attendant.objects.filter(company=company))
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
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='contacts', verbose_name='Empresa',
    )
    name = models.CharField(max_length=150, blank=True, default='')
    # O telefone e unico POR EMPRESA (antes era unico global): o mesmo cliente final
    # pode falar com duas empresas diferentes, e cada uma tem o seu proprio cadastro.
    phone = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contato'
        verbose_name_plural = 'Contatos'
        ordering = ('name', 'phone')
        constraints = [
            models.UniqueConstraint(fields=('company', 'phone'), name='unique_contact_phone_per_company'),
        ]
        # A unicidade (company, phone) ja cobre a busca por telefone DENTRO da
        # empresa, que e como o sistema sempre consulta (`_build_name_map`,
        # `get_or_create_contact`). Este indice cobre a listagem/busca por nome.
        indexes = [
            models.Index(fields=['company', 'name'], name='contato_empresa_nome_idx'),
        ]

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

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='conversations', verbose_name='Empresa',
    )
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
    # TRAVA do atendimento automatico (IA/chatbot): "estou processando desde".
    # Precisa ficar no BANCO porque a trava tem que valer ENTRE OS WORKERS do
    # gunicorn — um `set()` em memoria e por processo, e com 2 workers uma rajada de
    # mensagens fazia o cliente receber o menu duas vezes. Ver wapi/autoreply_lock.py.
    auto_reply_lock_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conversa'
        verbose_name_plural = 'Conversas'
        ordering = ('-last_message_at', '-created_at')
        # As consultas quentes desta tabela sao SEMPRE por empresa + algo. Sem indice
        # composto, cada contador da tela Conversas (5 por status + 3 por tipo, a
        # cada poll de 12s) e cada indicador do Dashboard varria a tabela inteira.
        indexes = [
            models.Index(fields=['company', 'status'], name='conv_company_status_idx'),
            models.Index(fields=['company', '-last_message_at'],
                         name='conv_company_ultima_idx'),
            models.Index(fields=['company', 'chat_type'], name='conv_company_tipo_idx'),
            # `created_at` alimenta "novas em 7 dias" (Dashboard e Metricas).
            models.Index(fields=['company', 'created_at'], name='conv_company_criada_idx'),
        ]
        constraints = [
            # UMA conversa por grupo, por empresa. `resolve_conversation_for_context`
            # consulta e depois cria: sem trava no banco, duas mensagens de um grupo
            # NOVO chegando quase juntas faziam dois webhooks criarem duas conversas
            # com o mesmo JID, e o historico do grupo rachava entre as duas (aconteceu
            # de verdade com 120363257947973768@g.us). A criacao trata o
            # IntegrityError e reaproveita a conversa que ganhou a corrida.
            #
            # So GRUPO: e o caso chaveado unicamente pelo JID. Conversa direta com
            # telefone e chaveada pelo CONTATO (que ja e por empresa) e historicamente
            # tinha varias conversas por pessoa, unificadas pelo
            # `merge_contact_conversations`. `external_id` vazio (linhas antigas) fica
            # fora da trava, senao a migracao quebraria por causa delas.
            models.UniqueConstraint(
                fields=('company', 'external_id'),
                condition=models.Q(chat_type='group') & ~models.Q(external_id=''),
                name='unique_group_conversation_per_company',
            ),
        ]

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
    # Setor da conversa NO MOMENTO em que a mensagem foi criada (para separar os
    # atendimentos por setor na aba "Conversa do setor"). Nulo enquanto sem setor
    # (ex.: triagem da IA antes de rotear). Ver conversation_messages_view.
    sector = models.ForeignKey(
        Sector, null=True, blank=True, on_delete=models.SET_NULL, related_name='messages'
    )
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    message_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='text')
    text = models.TextField(blank=True, default='')
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
        # A chave estrangeira `conversation` ja ganha indice sozinha, mas as consultas
        # reais sao COMPOSTAS e nenhuma tinha cobertura:
        #  - abrir/poll de conversa le as mensagens daquela conversa em ordem de data;
        #  - o escopo por divisoria filtra `message_type='system'` + data;
        #  - as Metricas contam por data (7d/30d) sobre a tabela toda;
        #  - a deduplicacao do webhook busca por `external_message_id`.
        indexes = [
            models.Index(fields=['conversation', 'created_at'], name='msg_conv_data_idx'),
            models.Index(fields=['conversation', 'message_type', '-created_at'],
                         name='msg_conv_tipo_idx'),
            models.Index(fields=['created_at'], name='msg_data_idx'),
            models.Index(fields=['external_message_id'], name='msg_id_externo_idx'),
        ]

    @property
    def is_media(self):
        return self.message_type in ('image', 'audio', 'video', 'document', 'sticker', 'gif')

    @property
    def resolved_media_url(self):
        """URL para EXIBIR a midia no chat.

        O arquivo local NAO e mais entregue pela URL direta do /media/: foto, audio,
        video e documento sao conteudo do cliente, entao passam pela view autenticada
        `message-media`, que aplica as MESMAS regras da conversa (empresa + alcance) —
        e por isso o gestor master tambem nao alcanca. Sem arquivo local, sobra o link
        remoto da W-API (que expira).
        """
        if self.media_file:
            from django.urls import reverse
            return reverse('message-media', args=[self.pk])
        return self.media_url or ''

    def __str__(self):
        return f'{self.get_direction_display()} ({self.message_type}): {self.text[:30]}'
