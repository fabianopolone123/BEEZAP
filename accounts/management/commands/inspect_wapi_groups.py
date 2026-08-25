"""Diagnostico: mostra a resposta crua de get-all-groups da W-API, o nome que o
parser extrai de cada grupo E o cruzamento com as conversas de grupo do banco —
dizendo, para cada conversa sem nome, QUAL das causas se aplica. Rodar no servidor
(onde a W-API esta configurada):

    python manage.py inspect_wapi_groups
    python manage.py inspect_wapi_groups --full     # imprime o JSON cru (truncado)

A pergunta pratica nunca e "o que a W-API devolveu?", e "por que ESTE grupo mostra
`Grupo <id>` em vez do nome?". As causas possiveis sao tres, e a saida separa as tres:

1. a W-API TEM o grupo com nome, mas a conversa esta sem nome -> a busca feita na
   CRIACAO da conversa falhou (instancia fora do ar, timeout, token). O nome so e
   buscado uma vez, ali; depois disso so o `sync_wapi_group_names` (ou o botao
   "Atualizar nomes" em Permissoes -> Grupos) resolve;
2. a W-API devolve o grupo SEM nome em nenhum campo conhecido -> falta ensinar o
   campo novo ao `_group_item_name`;
3. a W-API NAO lista o chat -> provavelmente nao e grupo (canal `@newsletter` ou
   comunidade que chegou com id "pelado", sem sufixo, que o parser classifica como
   grupo pelo tamanho do numero), ou a conta saiu do grupo.
"""

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Mostra a resposta de get-all-groups da W-API e o nome extraido de cada grupo.'

    def add_arguments(self, parser):
        parser.add_argument('--full', action='store_true', help='Imprime a resposta crua (JSON, truncada).')
        parser.add_argument(
            '--empresa', default='',
            help='Identificador (slug) da empresa. Sem isto, usa a empresa padrao.',
        )

    def handle(self, *args, **options):
        from wapi.services import (
            get_all_groups_safe, build_group_name_map, _iter_group_items,
            _group_item_id, _group_item_name, _group_key,
        )

        # MULTIEMPRESA: os grupos vem da instancia da W-API DA EMPRESA escolhida.
        from accounts.models import Company
        slug = (options.get('empresa') or '').strip()
        if slug:
            company = Company.objects.filter(slug=slug).first()
            if company is None:
                self.stdout.write(self.style.ERROR(f'Empresa "{slug}" nao encontrada.'))
                return
        else:
            company = Company.get_default()
        self.stdout.write(f'Empresa: {company.name} ({company.slug})')

        resp = get_all_groups_safe(company)
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

        # ----- Cruzamento com as conversas de grupo do banco -----
        # `mapping` so guarda quem TEM nome; aqui precisamos saber tambem quais
        # chats a W-API devolveu sem nome, para separar a causa 2 da causa 3.
        vistos = {}
        for item in items:
            if isinstance(item, dict):
                chave = _group_key(_group_item_id(item))
                if chave:
                    vistos[chave] = _group_item_name(item)

        from accounts.models import Conversation
        conversas = (
            Conversation.objects
            .filter(company=company, chat_type='group')
            .order_by('external_id')
        )
        self.stdout.write(f'\nConversas de grupo no banco: {conversas.count()}')
        for conversa in conversas:
            chave = _group_key(conversa.external_id)
            if conversa.name:
                situacao = f'ok (nome: {conversa.name})'
            elif vistos.get(chave):
                situacao = (
                    f'SEM NOME no banco, mas a W-API tem "{vistos[chave]}" -> causa 1: '
                    'a busca da criacao falhou; rode sync_wapi_group_names'
                )
            elif chave in vistos:
                situacao = (
                    'SEM NOME -> causa 2: a W-API devolve este grupo, mas sem nome em '
                    'nenhum campo conhecido'
                )
            else:
                situacao = (
                    'SEM NOME -> causa 3: a W-API NAO lista este chat (canal/comunidade '
                    'com id pelado, ou a conta saiu do grupo)'
                )
            self.stdout.write(
                f'- external_id={conversa.external_id} | key={chave or "?"} | {situacao}'
            )

        if options['full']:
            self.stdout.write('\n--- JSON cru (truncado em 8000 chars) ---')
            try:
                self.stdout.write(json.dumps(resp, ensure_ascii=False, indent=2)[:8000])
            except (TypeError, ValueError):
                self.stdout.write(str(resp)[:8000])
