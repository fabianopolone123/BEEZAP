"""Provisionamento automatico do ADMINISTRADOR como atendente.

O admin precisa conseguir ASSUMIR atendimentos (o botao "Assumir" exige um perfil
de atendente) sem ter que criar/logar uma conta separada. Por isso, todo usuario
com papel `adm` ganha automaticamente um `Attendant` vinculado e passa a fazer
parte de TODOS os setores — assim aparece em Atendentes e em cada setor, podendo
assumir conversas de qualquer fila.

Mantido em sincronia por sinais: ao salvar um usuario adm (garante o atendente) e
ao criar/salvar um setor (adiciona os admins). O backfill dos admins/setores ja
existentes fica na migration 0025. A organizacao por arrastar-e-soltar dos setores
tambem re-inclui os admins (ver `sectors_save_organization_view`).
"""

from django.db.models.signals import post_save
from django.dispatch import receiver


def ensure_admin_attendant(user):
    """Garante o Attendant do admin e o inclui em todos os setores. Retorna o
    Attendant (ou None se o usuario nao for adm)."""
    from .models import Attendant, Sector

    if getattr(user, 'role', None) != 'adm':
        return None
    name = (user.get_full_name() or (user.email or '').split('@')[0] or 'Administrador').strip()
    attendant, _ = Attendant.objects.get_or_create(
        user=user,
        defaults={'name': name or 'Administrador', 'must_change_password': False},
    )
    sectors = list(Sector.objects.all())
    if sectors:
        attendant.sectors.add(*sectors)
    return attendant


def add_admins_to_sector(sector):
    """Inclui todos os atendentes-admin no setor informado."""
    from .models import Attendant

    admins = list(Attendant.objects.filter(user__role='adm'))
    if admins:
        sector.attendants.add(*admins)


@receiver(post_save, sender='accounts.User')
def _ensure_admin_attendant_on_user_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    ensure_admin_attendant(instance)


@receiver(post_save, sender='accounts.Sector')
def _add_admins_on_sector_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    add_admins_to_sector(instance)
