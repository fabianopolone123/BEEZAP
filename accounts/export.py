"""Exportacao dos dados de UMA empresa cliente (portabilidade).

Por que existe: quando a empresa deixa de usar o BEEZAP, ela tem direito de levar o
que e dela — contatos, historico de conversas e os arquivos trocados. Este modulo
monta um ZIP com tudo isso em formato aberto (CSV/JSON), que abre no Excel ou em
qualquer sistema.

QUEM EXPORTA: o **Administrador da propria empresa**, nunca o gestor master. Os dados
sao do cliente, e a regra do projeto e que o master administra sem ler o atendimento
(ver docs/CONTEXTO.md secao 16). O master, na tela Clientes, so avisa para exportar
antes de encerrar.

O ZIP e escrito num arquivo TEMPORARIO em disco, nao na memoria: uma empresa com anos
de conversa pode ter milhares de midias, e montar isso em memoria derrubaria o
gunicorn.
"""

import csv
import io
import json
import os
import tempfile
import zipfile

from django.utils import timezone


LEIA_ME = """EXPORTACAO DE DADOS - {empresa}
Gerada em {data} pelo BEEZAP.

O QUE TEM AQUI DENTRO
---------------------
empresa.json    Dados cadastrais da empresa.
setores.csv     Setores (filas de atendimento).
atendentes.csv  Atendentes e a qual setor pertencem.
usuarios.csv    Contas de acesso (SEM senha - senha nao e exportavel).
contatos.csv    Agenda de contatos.
conversas.csv   Uma linha por conversa (pessoa ou grupo).
mensagens.csv   Todo o historico de mensagens, na ordem cronologica.
midias/         Os arquivos trocados (foto, audio, video, documento).

COMO LIGAR AS COISAS
--------------------
Cada linha de mensagens.csv tem a coluna "conversa_id", que aponta para a coluna
"id" de conversas.csv. Quando a mensagem tem arquivo, a coluna "arquivo" traz o
nome dele dentro da pasta midias/.

Os arquivos CSV estao em UTF-8 com BOM e separador ";", entao abrem direto no
Excel em portugues.

Este pacote contem dados pessoais de clientes finais (nomes, telefones e conversas).
Guarde em local seguro e trate conforme a LGPD.
"""

# Separador ";" e UTF-8 com BOM: e o que o Excel em pt-BR abre sem pedir importacao.
CSV_DELIMITER = ';'
CSV_ENCODING = 'utf-8-sig'


def _csv_bytes(header, rows):
    """Monta um CSV completo (em memoria — sao textos, nao arquivos) e devolve bytes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    for row in rows:
        writer.writerow(['' if value is None else value for value in row])
    return buffer.getvalue().encode(CSV_ENCODING)


def _local(value):
    """Data/hora local em formato legivel (vazio quando nao ha data)."""
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M:%S') if value else ''


def export_filename(company):
    """Nome do arquivo baixado: beezap-<empresa>-<data>.zip."""
    slug = (company.slug or 'empresa').strip('-') or 'empresa'
    return f'beezap-{slug}-{timezone.localdate().strftime("%Y-%m-%d")}.zip'


def build_company_export(company):
    """Monta o ZIP da empresa e devolve o arquivo temporario ABERTO, no inicio.

    Quem chamou e responsavel por entregar (FileResponse fecha o arquivo, e o
    temporario some do disco junto).
    """
    from .models import Attendant, Contact, Conversation, Message, Sector, User

    tmp = tempfile.TemporaryFile()
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('LEIA-ME.txt', LEIA_ME.format(
            empresa=company.display_name, data=_local(timezone.now()),
        ))

        bundle.writestr('empresa.json', json.dumps({
            'nome': company.name,
            'razao_social': company.legal_name,
            'cnpj': company.formatted_document,
            'email': company.email,
            'telefone': company.formatted_phone,
            'endereco': company.address,
            'cidade': company.city,
            'estado': company.state,
            'ativa': company.is_active,
            'criada_em': _local(company.created_at),
            'exportada_em': _local(timezone.now()),
        }, ensure_ascii=False, indent=2))

        sectors = Sector.objects.filter(company=company).order_by('name')
        bundle.writestr('setores.csv', _csv_bytes(
            ['id', 'nome', 'descricao', 'atendentes'],
            [[s.id, s.name, getattr(s, 'description', ''), s.attendants.count()] for s in sectors],
        ))

        attendants = (
            Attendant.objects.filter(company=company)
            .prefetch_related('sectors').select_related('user').order_by('name')
        )
        bundle.writestr('atendentes.csv', _csv_bytes(
            ['id', 'nome', 'email', 'telefone', 'setores'],
            [[
                a.id, a.name, getattr(a.user, 'email', ''), getattr(a, 'phone', ''),
                ', '.join(s.name for s in a.sectors.all()),
            ] for a in attendants],
        ))

        # Senha NUNCA e exportada (nem o hash): nao e dado do cliente, e credencial.
        users = User.objects.filter(company=company).order_by('email')
        bundle.writestr('usuarios.csv', _csv_bytes(
            ['id', 'email', 'nome', 'perfil', 'ativo', 'ultimo_acesso'],
            [[
                u.id, u.email, u.get_full_name(), u.get_role_display(),
                'sim' if u.is_active else 'nao', _local(u.last_login),
            ] for u in users],
        ))

        contacts = Contact.objects.filter(company=company).order_by('name', 'phone')
        bundle.writestr('contatos.csv', _csv_bytes(
            ['id', 'nome', 'telefone', 'criado_em'],
            [[c.id, c.name, c.phone, _local(c.created_at)] for c in contacts],
        ))

        conversations = (
            Conversation.objects.filter(company=company)
            .select_related('contact', 'sector', 'assigned_attendant').order_by('id')
        )
        bundle.writestr('conversas.csv', _csv_bytes(
            ['id', 'titulo', 'tipo', 'telefone_ou_id', 'situacao', 'setor',
             'atendente', 'criada_em', 'ultima_mensagem_em'],
            [[
                c.id, c.display_title, 'grupo' if c.is_group else 'direta', c.external_id,
                c.get_status_display(),
                c.sector.name if c.sector else '',
                c.assigned_attendant.name if c.assigned_attendant else '',
                _local(c.created_at), _local(c.last_message_at),
            ] for c in conversations],
        ))

        _write_messages_and_media(bundle, company)

    tmp.seek(0)
    return tmp


def _write_messages_and_media(bundle, company):
    """Escreve mensagens.csv e copia os arquivos para midias/ na mesma passagem.

    Percorre em blocos (`iterator`) para nao carregar anos de historico na memoria.
    """
    from .models import Message

    rows = []
    seen_names = set()
    messages = (
        Message.objects.filter(conversation__company=company)
        .select_related('conversation', 'sector').order_by('created_at', 'id')
    )
    for message in messages.iterator(chunk_size=500):
        stored_name = ''
        if message.media_file:
            stored_name = _copy_media(bundle, message, seen_names)
        rows.append([
            message.id,
            message.conversation_id,
            _local(message.created_at),
            'recebida' if message.direction == 'in' else 'enviada',
            message.message_type,
            message.text,
            message.sender_name,
            message.sender_id,
            message.sector.name if message.sector else '',
            'sim' if message.is_ai else 'nao',
            stored_name,
        ])

    bundle.writestr('mensagens.csv', _csv_bytes(
        ['id', 'conversa_id', 'data_hora', 'direcao', 'tipo', 'texto',
         'remetente', 'remetente_id', 'setor', 'automatica', 'arquivo'],
        rows,
    ))


def _copy_media(bundle, message, seen_names):
    """Copia o arquivo da mensagem para midias/ e devolve o nome usado no ZIP.

    O nome do arquivo em disco e um uuid (ver wapi/services.py), que nao diz nada a
    quem abre o ZIP. Aqui ele vira `<id_da_mensagem>-<nome_original>` — assim da para
    achar o arquivo pela linha do CSV e o nome real do documento se preserva.
    Arquivo que sumiu do disco e simplesmente pulado: a exportacao nao pode falhar
    inteira por causa de uma midia perdida.
    """
    from wapi.services import document_filename

    original = ''
    if message.message_type == 'document':
        original = document_filename(message) or ''
    original = original or os.path.basename(message.media_file.name)
    name = f'midias/{message.id}-{original}'
    if name in seen_names:
        return name
    try:
        with message.media_file.open('rb') as handle:
            bundle.writestr(name, handle.read())
    except (FileNotFoundError, OSError, ValueError):
        return ''
    seen_names.add(name)
    return name
