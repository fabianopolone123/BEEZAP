"""Diagnostico: mostra a resposta crua de get-all-groups da W-API e o nome que o
parser consegue extrair de cada grupo. Serve para descobrir por que o nome de um
grupo nao aparece (ex.: a W-API nao devolve o grupo, ou devolve o nome num campo
que ainda nao lemos). Rodar no servidor (onde a W-API esta configurada):

    python manage.py inspect_wapi_groups
    python manage.py inspect_wapi_groups --full     # imprime o JSON cru (truncado)
"""

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Mostra a resposta de get-all-groups da W-API e o nome extraido de cada grupo.'

    def add_arguments(self, parser):
        parser.add_argument('--full', action='store_true', help='Imprime a resposta crua (JSON, truncada).')

    def handle(self, *args, **options):
        from wapi.services import (
            get_all_groups_safe, build_group_name_map, _iter_group_items,
            _group_item_id, _group_item_name, _group_key,
        )

        resp = get_all_groups_safe()
        if resp is None:
            self.stdout.write(self.style.ERROR(
                'Falha ao chamar get-all-groups. Verifique Instance ID/Token e a conexao do WhatsApp.'))
            return

        if isinstance(resp, dict):
            self.stdout.write(f'Resposta: dict; chaves do topo: {list(resp.keys())}')
        else:
            self.stdout.write(f'Resposta: {type(resp).__name__}')

        items = _iter_group_items(resp)
        self.stdout.write(f'Grupos encontrados na lista: {len(items)}')
        for item in items[:80]:
            if not isinstance(item, dict):
                self.stdout.write(f'- (item nao-dict: {type(item).__name__})')
                continue
            gid = _group_item_id(item)
            name = _group_item_name(item)
            self.stdout.write(
                f'- id={gid or "?"} | key={_group_key(gid) or "?"} | '
                f'nome={name or "(vazio)"} | chaves={list(item.keys())}'
            )

        mapping = build_group_name_map(resp)
        self.stdout.write(f'\nMapa {{digitos: nome}} ({len(mapping)}): {mapping}')

        if options['full']:
            self.stdout.write('\n--- JSON cru (truncado em 8000 chars) ---')
            try:
                self.stdout.write(json.dumps(resp, ensure_ascii=False, indent=2)[:8000])
            except (TypeError, ValueError):
                self.stdout.write(str(resp)[:8000])
