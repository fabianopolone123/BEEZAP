"""Multiempresa (SaaS): cria a EMPRESA CLIENTE e liga tudo o que ja existe a ela.

Estrategia (para nao perder nada do que esta em producao):

1. cria o model `Company`;
2. adiciona o campo `company` NULO em todos os models operacionais;
3. cria a **Empresa padrao** e aponta TODOS os registros existentes para ela
   (usuarios, atendentes, setores, contatos, conversas, eventos de webhook,
   permissoes de perfil e as tres configuracoes que antes eram unicas/globais);
4. torna o campo obrigatorio e troca as unicidades GLOBAIS (`Sector.name`,
   `Contact.phone`, `RoleMenuPermission.role`) por unicidades POR EMPRESA.

Depois desta migration o sistema segue funcionando exatamente como antes: existe
uma unica empresa e todo mundo esta nela. `User.company` continua podendo ser nulo
de proposito — nulo identifica o GESTOR MASTER, que fica acima das empresas.
"""

import django.db.models.deletion
from django.db import migrations, models


# Models que ganham `company` obrigatorio e recebem o backfill da empresa padrao.
COMPANY_MODELS = [
    'Attendant',
    'Contact',
    'Conversation',
    'MenuBotConfiguration',
    'OpenAiConfiguration',
    'RoleMenuPermission',
    'Sector',
    'WapiConfiguration',
    'WapiWebhookEvent',
]


def create_default_company(apps, schema_editor):
    """Cria a Empresa padrao e vincula a ela todos os dados que ja existiam."""
    Company = apps.get_model('accounts', 'Company')
    company = Company.objects.filter(is_default=True).order_by('id').first()
    if company is None:
        company = Company.objects.create(
            name='Empresa padrão',
            slug='empresa-padrao',
            legal_name='',
            is_default=True,
            is_active=True,
            notes='Empresa criada automaticamente com os dados que já existiam '
                  'antes do sistema virar multiempresa.',
        )

    for model_name in COMPANY_MODELS:
        model = apps.get_model('accounts', model_name)
        model.objects.filter(company__isnull=True).update(company=company)

    # Usuarios existentes sao todos operacionais (adm/usuario/leitor) — entram na
    # empresa padrao. Master (company nulo) so passa a existir depois, cadastrado
    # na tela Clientes.
    User = apps.get_model('accounts', 'User')
    User.objects.filter(company__isnull=True).exclude(role='master').update(company=company)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0030_message_sector'),
    ]

    operations = [
        # ── 1. A empresa cliente ────────────────────────────────────────────────
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, verbose_name='Nome fantasia')),
                ('legal_name', models.CharField(blank=True, default='', max_length=180, verbose_name='Razão social')),
                ('document', models.CharField(blank=True, default='', max_length=20, verbose_name='CNPJ')),
                ('slug', models.SlugField(max_length=60, unique=True, verbose_name='Identificador')),
                ('email', models.EmailField(blank=True, default='', max_length=254, verbose_name='E-mail')),
                ('phone', models.CharField(blank=True, default='', max_length=20, verbose_name='Telefone')),
                ('address', models.CharField(blank=True, default='', max_length=200, verbose_name='Endereço')),
                ('city', models.CharField(blank=True, default='', max_length=100, verbose_name='Cidade')),
                ('state', models.CharField(blank=True, default='', max_length=2, verbose_name='UF')),
                ('logo', models.FileField(blank=True, null=True, upload_to='empresas/logos/', verbose_name='Logo')),
                ('accent_color', models.CharField(blank=True, default='', max_length=7, verbose_name='Cor de destaque')),
                ('notes', models.TextField(blank=True, default='', verbose_name='Observações')),
                ('is_active', models.BooleanField(default=True, verbose_name='Ativa')),
                ('is_default', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Empresa cliente',
                'verbose_name_plural': 'Empresas clientes',
                'ordering': ('name',),
            },
        ),

        # ── 2. Vinculo de empresa (nulo por enquanto, para o backfill rodar) ────
        migrations.AddField(
            model_name='user',
            name='company',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='users', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='attendant',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='attendants', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='sector',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='sectors', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='contact',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='contacts', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='conversation',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='conversations', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='wapiwebhookevent',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='webhook_events', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='rolemenupermission',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='role_permissions', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='wapiconfiguration',
            name='company',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name='wapi_config', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='openaiconfiguration',
            name='company',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name='openai_config', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AddField(
            model_name='menubotconfiguration',
            name='company',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name='menubot_config', to='accounts.company', verbose_name='Empresa'),
        ),

        # ── 3. Backfill: tudo o que existe passa a ser da Empresa padrao ────────
        migrations.RunPython(create_default_company, migrations.RunPython.noop),

        # ── 4. Campo obrigatorio + unicidades agora POR EMPRESA ────────────────
        migrations.AlterField(
            model_name='attendant',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='attendants', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='sector',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='sectors', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='contact',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='contacts', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='conversation',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='conversations', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='wapiwebhookevent',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='webhook_events', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='rolemenupermission',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='role_permissions', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='wapiconfiguration',
            name='company',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                       related_name='wapi_config', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='openaiconfiguration',
            name='company',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                       related_name='openai_config', to='accounts.company', verbose_name='Empresa'),
        ),
        migrations.AlterField(
            model_name='menubotconfiguration',
            name='company',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                       related_name='menubot_config', to='accounts.company', verbose_name='Empresa'),
        ),

        # Perfil MASTER passa a ser uma opcao de papel.
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('master', 'Gestor master'), ('adm', 'Administrador'),
                         ('usuario', 'Usuário'), ('leitor', 'Leitor')],
                default='usuario', max_length=20,
            ),
        ),

        # As unicidades GLOBAIS saem; entram as unicidades POR EMPRESA.
        migrations.AlterField(
            model_name='sector',
            name='name',
            field=models.CharField(max_length=100, verbose_name='Nome'),
        ),
        migrations.AlterField(
            model_name='contact',
            name='phone',
            field=models.CharField(max_length=30),
        ),
        migrations.AlterField(
            model_name='rolemenupermission',
            name='role',
            field=models.CharField(max_length=20),
        ),
        migrations.AddConstraint(
            model_name='sector',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_sector_name_per_company'),
        ),
        migrations.AddConstraint(
            model_name='contact',
            constraint=models.UniqueConstraint(fields=('company', 'phone'), name='unique_contact_phone_per_company'),
        ),
        migrations.AddConstraint(
            model_name='rolemenupermission',
            constraint=models.UniqueConstraint(fields=('company', 'role'), name='unique_role_permission_per_company'),
        ),
    ]
