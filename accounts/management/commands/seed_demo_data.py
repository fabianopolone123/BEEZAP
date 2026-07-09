"""Popula o banco com dados de DEMONSTRACAO para o dashboard e a tela de Conversas.

Limpa o CONTEUDO (conversas, mensagens, contatos, setores e atendentes ficticios)
e recria: 5 setores, 5 atendentes (um por setor) e varias conversas/mensagens
distribuidas nos ultimos 7 dias, com status variados (em atendimento, aguardando,
finalizadas).

PRESERVA: o(s) usuario(s) administrador(es), as configuracoes (W-API, OpenAI,
Chatbot) e as permissoes. Ideal para ver o dashboard e as conversas com dados reais
sem depender do WhatsApp conectado.

    python manage.py seed_demo_data
    python manage.py seed_demo_data --no-clear   # so adiciona, sem limpar

Rodar tambem no servidor (VPS) para popular a producao de demonstracao.
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


SECTORS = ['Vendas', 'Suporte', 'Financeiro', 'Comercial', 'Cobranca']
ATTENDANTS = [
    ('Ana Souza', 'ana.souza'),
    ('Bruno Lima', 'bruno.lima'),
    ('Carla Dias', 'carla.dias'),
    ('Diego Alves', 'diego.alves'),
    ('Eduarda Nunes', 'eduarda.nunes'),
]
CLIENT_NAMES = [
    'Joao Silva', 'Maria Santos', 'Pedro Costa', 'Juliana Oliveira', 'Ricardo Souza',
    'Fernanda Lima', 'Marcos Pereira', 'Patricia Rocha', 'Lucas Almeida', 'Camila Ferreira',
    'Rafael Gomes', 'Beatriz Martins', 'Gustavo Ribeiro', 'Larissa Carvalho', 'Thiago Barbosa',
    'Aline Ramos', 'Felipe Araujo', 'Vanessa Cardoso', 'Rodrigo Teixeira', 'Sabrina Melo',
]
CLIENT_MSGS = [
    'Ola, gostaria de mais informacoes.',
    'Bom dia! Preciso de ajuda com meu pedido.',
    'Qual o valor do plano mensal?',
    'Nao consegui finalizar o pagamento.',
    'Quero saber sobre a garantia.',
    'Voces entregam na minha regiao?',
    'Preciso da segunda via do boleto.',
    'Gostaria de falar com um atendente.',
]
AGENT_MSGS = [
    'Claro! Ja te ajudo com isso.',
    'Bom dia! Pode me passar mais detalhes?',
    'Perfeito, vou verificar aqui pra voce.',
    'Consegui localizar o seu cadastro.',
    'Enviei as informacoes no seu e-mail.',
]


class Command(BaseCommand):
    help = 'Popula dados de demonstracao (setores, atendentes e conversas dos ultimos 7 dias).'

    def add_arguments(self, parser):
        parser.add_argument('--no-clear', action='store_true',
                            help='Nao apaga o conteudo atual antes de popular.')

    def handle(self, *args, **options):
        from accounts.models import (
            Attendant, Contact, Conversation, GroupAccess, Message, Sector, User,
        )
        from wapi.services import save_system_message

        rnd = random.Random(42)
        now = timezone.localtime()
        today = timezone.localdate()

        if not options['no_clear']:
            Message.objects.all().delete()
            Conversation.objects.all().delete()  # cascata: mensagens/GroupAccess restantes
            GroupAccess.objects.all().delete()
            Contact.objects.all().delete()
            # Remove atendentes/usuarios ficticios (nunca o administrador).
            User.objects.filter(role__in=['usuario', 'leitor']).delete()
            Attendant.objects.exclude(user__role='adm').delete()
            Sector.objects.all().delete()
            self.stdout.write('Conteudo anterior removido (admin e configuracoes preservados).')

        # Setores + atendentes (um por setor).
        sectors, attendants = [], []
        for i, sector_name in enumerate(SECTORS):
            sector = Sector.objects.create(name=sector_name, description=f'Setor de {sector_name}')
            sectors.append(sector)
            full_name, slug = ATTENDANTS[i]
            first, _, last = full_name.partition(' ')
            user = User.objects.create_user(
                email=f'{slug}@demo.beezap', password='1234',
                role=User.Role.USUARIO, first_name=first, last_name=last,
            )
            attendant = Attendant.objects.create(
                user=user, name=full_name, phone=f'5516{rnd.randint(900000000, 999999999)}',
                must_change_password=False,
            )
            attendant.sectors.add(sector)
            attendants.append(attendant)
        self.stdout.write(f'{len(sectors)} setores e {len(attendants)} atendentes criados.')

        phone_seq = 5516000000000

        def new_contact(name):
            nonlocal phone_seq
            phone_seq += rnd.randint(1000, 9999)
            return Contact.objects.create(name=name, phone=str(phone_seq))

        def make_conversation(status, days_ago, sector, attendant, unread):
            """Cria uma conversa com 1 msg do cliente + (se atendida) 1 resposta, no dia indicado."""
            contact = new_contact(rnd.choice(CLIENT_NAMES))
            base = now - timedelta(days=days_ago, hours=rnd.randint(0, 8), minutes=rnd.randint(0, 59))
            client_text = rnd.choice(CLIENT_MSGS)
            conv = Conversation.objects.create(
                contact=contact, external_id=contact.phone, chat_type='private',
                status=status, sector=sector, assigned_attendant=attendant,
                unread_count=unread, last_message_text=client_text, last_message_at=base,
            )
            msg_in = Message.objects.create(
                conversation=conv, direction='in', message_type='text',
                text=client_text, phone=contact.phone, status='received',
            )
            Message.objects.filter(pk=msg_in.pk).update(created_at=base)
            last_at = base
            if status in ('open', 'closed'):
                reply_at = base + timedelta(minutes=rnd.randint(1, 12))
                reply_text = rnd.choice(AGENT_MSGS)
                msg_out = Message.objects.create(
                    conversation=conv, direction='out', message_type='text',
                    text=reply_text, status='sent',
                )
                Message.objects.filter(pk=msg_out.pk).update(created_at=reply_at)
                last_at = reply_at
                conv.last_message_text = reply_text
            if status == 'closed':
                divider = save_system_message(conv, 'Atendimento encerrado')
                last_at = reply_at + timedelta(minutes=rnd.randint(1, 30))
                Message.objects.filter(pk=divider.pk).update(created_at=last_at)
            conv.last_message_at = last_at
            conv.save(update_fields=['last_message_text', 'last_message_at', 'updated_at'])
            Conversation.objects.filter(pk=conv.pk).update(created_at=base)
            return conv

        created = {'closed': 0, 'open': 0, 'pending': 0}
        # Finalizadas: espalhadas nos 7 dias.
        for _ in range(18):
            make_conversation('closed', rnd.randint(0, 6), rnd.choice(sectors),
                              rnd.choice(attendants), unread=0)
            created['closed'] += 1
        # Em atendimento: dias mais recentes, atribuidas.
        for _ in range(10):
            sector = rnd.choice(sectors)
            attendant = rnd.choice([a for a in attendants if sector in a.sectors.all()] or attendants)
            make_conversation('open', rnd.randint(0, 3), sector, attendant, unread=0)
            created['open'] += 1
        # Aguardando: setor sem atendente, com nao lidas.
        for _ in range(8):
            make_conversation('pending', rnd.randint(0, 2), rnd.choice(sectors),
                              None, unread=rnd.randint(1, 4))
            created['pending'] += 1

        self.stdout.write(self.style.SUCCESS(
            f'Conversas criadas: {created["open"]} em atendimento, '
            f'{created["pending"]} aguardando, {created["closed"]} finalizadas.'
        ))
        self.stdout.write('Pronto. Abra o Dashboard e a tela de Conversas para ver os dados.')
