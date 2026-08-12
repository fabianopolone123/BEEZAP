"""GPT passa a ser UMA configuracao da PLATAFORMA (nao mais uma por empresa).

Decisao de produto: a API Key do GPT e do gestor master (e ele quem paga a conta da
OpenAI), entao existe UMA configuracao para todos os clientes. O que cada empresa
decide por conta dela e apenas SE usa IA, chatbot de menu ou nada — e isso vive em
`MenuBotConfiguration.mode`, que continua sendo POR EMPRESA. Quem tem credencial
propria por empresa e a **W-API** (instancia + token de cada cliente).

O que esta migration faz:

1. escolhe UMA linha de `OpenAiConfiguration` para virar a da plataforma — de
   preferencia a que ja tem API Key cadastrada; senao a da empresa padrao; senao a
   primeira — e apaga as demais (evita ficar com uma linha por empresa);
2. remove o vinculo `company`;
3. remove `fallback_sector`: um setor pertence a uma empresa e nao cabe numa
   configuracao da plataforma. O destino do encaminhamento quando a IA nao entende
   passa a ser o MESMO da empresa usado pelo chatbot
   (`MenuBotConfiguration.fallback_sector`) e, na falta dele, o setor Geral.
"""

from django.db import migrations


def merge_into_platform_config(apps, schema_editor):
    """Mantem uma unica OpenAiConfiguration (a da plataforma) e descarta o resto."""
    OpenAiConfiguration = apps.get_model('accounts', 'OpenAiConfiguration')
    Company = apps.get_model('accounts', 'Company')

    rows = list(OpenAiConfiguration.objects.order_by('id'))
    if not rows:
        return

    # 1o critério: a linha que realmente tem API Key cadastrada (é a que importa).
    keep = next((r for r in rows if (r.api_key or '').strip()), None)
    # 2o: a linha da empresa padrao.
    if keep is None:
        default_company = Company.objects.filter(is_default=True).order_by('id').first()
        if default_company is not None:
            keep = next((r for r in rows if r.company_id == default_company.id), None)
    # 3o: a primeira.
    if keep is None:
        keep = rows[0]

    # O fallback_sector some: a IA passa a usar o fallback POR EMPRESA do chatbot.
    keep.fallback_sector = None
    keep.save(update_fields=['fallback_sector'])

    OpenAiConfiguration.objects.exclude(pk=keep.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0031_multiempresa'),
    ]

    operations = [
        migrations.RunPython(merge_into_platform_config, migrations.RunPython.noop),
        migrations.RemoveField(model_name='openaiconfiguration', name='company'),
        migrations.RemoveField(model_name='openaiconfiguration', name='fallback_sector'),
    ]
