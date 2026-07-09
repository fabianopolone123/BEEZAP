from django.db import migrations


def backfill_admin_attendants(apps, schema_editor):
    """Cria o Attendant de cada admin ja existente e o inclui em todos os setores."""
    User = apps.get_model('accounts', 'User')
    Attendant = apps.get_model('accounts', 'Attendant')
    Sector = apps.get_model('accounts', 'Sector')

    sectors = list(Sector.objects.all())
    for user in User.objects.filter(role='adm'):
        full = f'{user.first_name} {user.last_name}'.strip()
        name = full or (user.email or '').split('@')[0] or 'Administrador'
        attendant, _ = Attendant.objects.get_or_create(
            user=user,
            defaults={'name': name, 'must_change_password': False},
        )
        if sectors:
            attendant.sectors.add(*sectors)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0024_menubotconfiguration_menuoption'),
    ]

    operations = [
        migrations.RunPython(backfill_admin_attendants, noop),
    ]
