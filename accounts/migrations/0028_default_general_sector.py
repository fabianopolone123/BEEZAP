from django.db import migrations


def create_general_sector(apps, schema_editor):
    """Cria o setor padrao 'Geral' (se faltar) e inclui TODOS os atendentes nele."""
    Sector = apps.get_model('accounts', 'Sector')
    Attendant = apps.get_model('accounts', 'Attendant')
    sector, _ = Sector.objects.get_or_create(
        name='Geral',
        defaults={'description': 'Setor padrão de triagem. Todos os atendentes fazem parte dele.'},
    )
    attendants = list(Attendant.objects.all())
    if attendants:
        sector.attendants.add(*attendants)


def noop(apps, schema_editor):
    # Nao remove o setor na reversao (pode ter conversas/atendentes vinculados).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0027_rolemenupermission_full_history_and_more'),
    ]

    operations = [
        migrations.RunPython(create_general_sector, noop),
    ]
