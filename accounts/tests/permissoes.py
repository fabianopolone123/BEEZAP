"""Permissoes de menu, perfil somente-leitura e alcance de
visualizacao das conversas.
"""

from .base import (
    Attendant,
    TestCase,
    User,
    default_company,
    reverse,
)


class MenuPermissionsTests(TestCase):
    """Permissoes de menu: padrao por perfil, telas gateadas, concessao por perfil
    e personalizacao por usuario."""

    def setUp(self):
        self.admin = User.objects.create_user(company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.user = User.objects.create_user(company=default_company(), email='joao@x.com', password='x', role=User.Role.USUARIO)

    def test_usuario_default_menu(self):
        self.client.force_login(self.user)
        # Sem Dashboard (redireciona) e sem Setores (403); Conversas ok.
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        self.assertEqual(self.client.get(reverse('sectors')).status_code, 403)
        self.assertEqual(self.client.get(reverse('conversations')).status_code, 200)
        nav = self.client.get(reverse('conversations')).content.decode()
        self.assertNotIn('>Setores<', nav)
        self.assertNotIn('>Dashboard<', nav)

    def test_admin_sees_permissions_page(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('permissions')).status_code, 200)
        self.assertEqual(self.client.get(reverse('sectors')).status_code, 200)

    def test_usuario_cannot_open_permissions(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('permissions')).status_code, 403)

    def test_grant_sector_to_role(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('permissions'), {
            'form_type': 'roles',
            'role__usuario__conversations': 'on',
            'role__usuario__sectors': 'on',
        })
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('sectors')).status_code, 200)

    def test_user_override(self):
        from accounts.models import UserMenuPermission
        self.client.force_login(self.admin)
        # Concede tudo ao perfil, mas restringe o usuario so a Conversas.
        self.client.post(reverse('permissions'), {
            'form_type': 'roles',
            'role__usuario__conversations': 'on',
            'role__usuario__contacts': 'on',
            'role__usuario__sectors': 'on',
        })
        self.client.post(reverse('permissions'), {
            'form_type': 'user', 'user_id': str(self.user.id),
            'userkey__conversations': 'on',
        })
        self.assertTrue(UserMenuPermission.objects.filter(user=self.user).exists())
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('sectors')).status_code, 403)
        self.assertEqual(self.client.get(reverse('contacts')).status_code, 403)
        self.assertEqual(self.client.get(reverse('conversations')).status_code, 200)

    def test_user_override_reset(self):
        from accounts.models import UserMenuPermission
        UserMenuPermission.objects.create(user=self.user, allowed_keys=['conversations'])
        self.client.force_login(self.admin)
        self.client.post(reverse('permissions'), {
            'form_type': 'user-reset', 'user_id': str(self.user.id),
        })
        self.assertFalse(UserMenuPermission.objects.filter(user=self.user).exists())

    def test_view_tab_renders(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('permissions') + '?tab=visualizacao').content.decode()
        self.assertIn('data-panel="visualizacao"', html)
        self.assertIn('Visualização por setor', html)

    def test_save_sector_view(self):
        from accounts.models import Sector
        sec = Sector.objects.create(company=default_company(), name='Suporte')
        self.client.force_login(self.admin)
        self.client.post(reverse('permissions'), {
            'form_type': 'view-sectors',
            f'sector__{sec.id}__scope': 'all',
            f'sector__{sec.id}__full_history': 'on',
        })
        sec.refresh_from_db()
        self.assertEqual(sec.view_scope, 'all')
        self.assertTrue(sec.view_full_history)

    def test_save_and_reset_user_view(self):
        from accounts.models import UserConversationView
        self.client.force_login(self.admin)
        self.client.post(reverse('permissions'), {
            'form_type': 'view-user', 'user_id': str(self.user.id),
            'user_scope': 'sector_all', 'user_full_history': 'yes',
        })
        ov = UserConversationView.objects.get(user=self.user)
        self.assertEqual(ov.view_scope, 'sector_all')
        self.assertTrue(ov.view_full_history)
        # Herdar em tudo remove a linha (sem personalizacao).
        self.client.post(reverse('permissions'), {
            'form_type': 'view-user', 'user_id': str(self.user.id),
            'user_scope': '', 'user_full_history': 'inherit',
        })
        self.assertFalse(UserConversationView.objects.filter(user=self.user).exists())
class ProfileRoleTests(TestCase):
    """Aba Perfis: o admin define o papel (adm/usuario/leitor) de cada pessoa."""

    def setUp(self):
        self.admin = User.objects.create_user(company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.user = User.objects.create_user(company=default_company(), email='joao@x.com', password='x', role=User.Role.USUARIO)

    def test_admin_can_change_role(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': str(self.user.id), 'role': 'leitor',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'leitor')

    def test_promote_to_admin_provisions_attendant(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': str(self.user.id), 'role': 'adm',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'adm')
        # O sinal provisiona o perfil de atendente do admin.
        self.assertTrue(Attendant.objects.filter(user=self.user).exists())

    def test_cannot_change_own_role(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': str(self.admin.id), 'role': 'usuario',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, 'adm')

    def test_can_demote_other_admin(self):
        # Havendo outro admin, o logado pode rebaixar o segundo admin normalmente.
        other_admin = User.objects.create_user(company=default_company(), email='adm2@x.com', password='x', role=User.Role.ADM)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': str(other_admin.id), 'role': 'usuario',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        other_admin.refresh_from_db()
        self.assertEqual(other_admin.role, 'usuario')

    def test_invalid_role_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': str(self.user.id), 'role': 'chefe',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'usuario')

    def test_non_admin_cannot_access(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': str(self.user.id), 'role': 'adm',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 403)
class ReadOnlyLeitorTests(TestCase):
    """Perfil `leitor` = somente leitura: enxerga as conversas, mas nao executa
    NENHUMA acao (enviar/assumir/encerrar/transferir/cadastrar contato)."""

    def setUp(self):
        from accounts.models import Contact, Conversation, Sector
        self.Contact = Contact
        self.Conversation = Conversation
        self.leitor = User.objects.create_user(company=default_company(), email='leo@x.com', password='x', role=User.Role.LEITOR)
        self.att = Attendant.objects.create(company=default_company(), user=self.leitor, name='Leo', must_change_password=False)
        self.vendas = Sector.objects.create(company=default_company(), name='Vendas')
        self.att.sectors.add(self.vendas)
        ct = Contact.objects.create(company=default_company(), name='Cliente', phone='5516988887777')
        self.conv = Conversation.objects.create(company=default_company(),
            contact=ct, external_id='5516988887777', chat_type='private',
            status='pending', sector=self.vendas)
        self.client.force_login(self.leitor)

    def test_can_view(self):
        # VER e permitido: a tela e as mensagens abrem normalmente (GET).
        self.assertEqual(self.client.get(reverse('conversations')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('conversation-messages', args=[self.conv.id])).status_code, 200
        )

    def test_conversations_page_marks_readonly(self):
        html = self.client.get(reverse('conversations')).content.decode()
        self.assertIn('is-readonly', html)

    def test_cannot_send(self):
        r = self.client.post(reverse('conversation-send', args=[self.conv.id]), {'text': 'oi'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()['ok'])

    def test_cannot_take(self):
        r = self.client.post(reverse('conversation-take', args=[self.conv.id]))
        self.assertEqual(r.status_code, 403)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_attendant)

    def test_cannot_close(self):
        r = self.client.post(reverse('conversation-close', args=[self.conv.id]))
        self.assertEqual(r.status_code, 403)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, 'pending')

    def test_cannot_transfer(self):
        r = self.client.post(
            reverse('conversation-transfer', args=[self.conv.id]),
            {'sector_id': str(self.vendas.id)},
        )
        self.assertEqual(r.status_code, 403)

    def test_cannot_create_contact(self):
        before = self.Contact.objects.count()
        r = self.client.post(reverse('contacts'), {'name': 'Novo', 'phone': '5511900000000'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.Contact.objects.count(), before)
class ConversationVisibilityTests(TestCase):
    """Separacao das conversas: quem ve quais chats + escopo do historico."""

    def setUp(self):
        from accounts.models import Contact, Conversation, Sector
        self.Conversation = Conversation
        self.admin = User.objects.create_user(company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.uuser = User.objects.create_user(company=default_company(), email='joao@x.com', password='x', role=User.Role.USUARIO)
        self.att = Attendant.objects.create(company=default_company(), user=self.uuser, name='Joao', must_change_password=False)
        self.vendas = Sector.objects.create(company=default_company(), name='Vendas')
        self.compras = Sector.objects.create(company=default_company(), name='Compras')
        self.att.sectors.add(self.vendas)

        c1 = Contact.objects.create(company=default_company(), name='A', phone='5511111111111')
        self.direct_vendas = Conversation.objects.create(company=default_company(),
            contact=c1, external_id='5511111111111', chat_type='private',
            status='pending', sector=self.vendas)
        c2 = Contact.objects.create(company=default_company(), name='B', phone='5522222222222')
        self.direct_compras = Conversation.objects.create(company=default_company(),
            contact=c2, external_id='5522222222222', chat_type='private',
            status='pending', sector=self.compras)
        self.group = Conversation.objects.create(company=default_company(),
            external_id='123@g.us', chat_type='group', name='Grupo X', status='open')

    def _visible_ids(self, user):
        from accounts.permissions import visible_conversations
        return set(visible_conversations(user, self.Conversation.objects.all())
                   .values_list('id', flat=True))

    def test_admin_sees_all(self):
        self.assertEqual(len(self._visible_ids(self.admin)), 3)

    def test_user_sees_only_own_sector_direct(self):
        ids = self._visible_ids(self.uuser)
        self.assertIn(self.direct_vendas.id, ids)
        self.assertNotIn(self.direct_compras.id, ids)
        self.assertNotIn(self.group.id, ids)  # grupo sem liberacao

    def test_group_access_by_sector(self):
        from accounts.models import GroupAccess
        acc = GroupAccess.objects.create(conversation=self.group)
        acc.sectors.add(self.vendas)
        self.assertIn(self.group.id, self._visible_ids(self.uuser))

    def test_group_access_by_user(self):
        from accounts.models import GroupAccess
        acc = GroupAccess.objects.create(conversation=self.group)
        acc.users.add(self.uuser)
        self.assertIn(self.group.id, self._visible_ids(self.uuser))

    def test_messages_gate_403_for_other_sector(self):
        self.client.force_login(self.uuser)
        self.assertEqual(
            self.client.get(reverse('conversation-messages', args=[self.direct_compras.id])).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('conversation-messages', args=[self.direct_vendas.id])).status_code, 200)

    def test_history_scope(self):
        from datetime import timedelta
        from django.utils import timezone
        from accounts.models import Message
        base = timezone.now()
        old = Message.objects.create(conversation=self.direct_vendas, direction='in',
                                     message_type='text', text='mensagem antiga')
        div = Message.objects.create(conversation=self.direct_vendas, direction='out',
                                     message_type='system', text='Novo atendimento iniciado')
        new = Message.objects.create(conversation=self.direct_vendas, direction='in',
                                     message_type='text', text='mensagem nova')
        Message.objects.filter(pk=old.pk).update(created_at=base - timedelta(hours=2))
        Message.objects.filter(pk=div.pk).update(created_at=base - timedelta(hours=1))
        Message.objects.filter(pk=new.pk).update(created_at=base)

        self.client.force_login(self.uuser)
        # Padrao: so o atendimento atual (apos a divisoria) — nao ve a "mensagem antiga".
        data = self.client.get(reverse('conversation-messages', args=[self.direct_vendas.id])).json()
        texts = [m['text'] for m in data['messages']]
        self.assertIn('mensagem nova', texts)
        self.assertNotIn('mensagem antiga', texts)
        # Com "ver conversa inteira" ligado no SETOR do atendente, ve tudo.
        self.vendas.view_full_history = True
        self.vendas.save(update_fields=['view_full_history'])
        data = self.client.get(reverse('conversation-messages', args=[self.direct_vendas.id])).json()
        texts = [m['text'] for m in data['messages']]
        self.assertIn('mensagem antiga', texts)

    def test_history_full_user_override_beats_sector(self):
        """Excecao do usuario (view_full_history) sobrepoe o setor."""
        from accounts.models import UserConversationView
        from accounts.permissions import history_full_for
        self.vendas.view_full_history = True
        self.vendas.save(update_fields=['view_full_history'])
        self.assertTrue(history_full_for(self.uuser))
        UserConversationView.objects.create(user=self.uuser, view_full_history=False)
        self.assertFalse(history_full_for(self.uuser))

    def test_scope_own_sees_only_assigned(self):
        from accounts.models import UserConversationView
        UserConversationView.objects.create(user=self.uuser, view_scope='own')
        # direct_vendas eh do setor dele, mas NAO atribuida -> com 'own' some.
        self.assertNotIn(self.direct_vendas.id, self._visible_ids(self.uuser))
        self.direct_vendas.assigned_attendant = self.att
        self.direct_vendas.save(update_fields=['assigned_attendant'])
        ids = self._visible_ids(self.uuser)
        self.assertIn(self.direct_vendas.id, ids)
        self.assertNotIn(self.direct_compras.id, ids)

    def test_scope_all_sees_other_sectors(self):
        from accounts.models import UserConversationView
        UserConversationView.objects.create(user=self.uuser, view_scope='all')
        ids = self._visible_ids(self.uuser)
        self.assertIn(self.direct_vendas.id, ids)
        self.assertIn(self.direct_compras.id, ids)  # de outro setor

    def test_scope_sector_all_sees_closed_of_sector(self):
        from accounts.models import Contact, Conversation, UserConversationView
        ct = Contact.objects.create(company=default_company(), name='C', phone='5533333333333')
        closed_vendas = Conversation.objects.create(company=default_company(),
            contact=ct, external_id='5533333333333', chat_type='private',
            status='closed', sector=self.vendas)
        # sector_open (padrao): fechada do setor que nao eh dele -> nao ve.
        self.assertNotIn(closed_vendas.id, self._visible_ids(self.uuser))
        UserConversationView.objects.create(user=self.uuser, view_scope='sector_all')
        self.assertIn(closed_vendas.id, self._visible_ids(self.uuser))

    def test_sector_scope_field_drives_visibility(self):
        # O padrao do SETOR (sem override de usuario) ja muda o alcance.
        self.vendas.view_scope = 'all'
        self.vendas.save(update_fields=['view_scope'])
        self.assertIn(self.direct_compras.id, self._visible_ids(self.uuser))

    def test_effective_scope_most_permissive_multi_sector(self):
        from accounts.permissions import effective_view_scope
        self.att.sectors.add(self.compras)  # agora em vendas + compras
        self.vendas.view_scope = 'own'
        self.vendas.save(update_fields=['view_scope'])
        self.compras.view_scope = 'sector_all'
        self.compras.save(update_fields=['view_scope'])
        self.assertEqual(effective_view_scope(self.uuser), 'sector_all')

    def test_owner_tabs_split_private_vs_sector(self):
        """Abas 'Conversa privada' x 'Conversa do setor': separa os atendimentos por
        dono (o meu segmento x o de outro atendente)."""
        from datetime import timedelta
        from django.utils import timezone
        from accounts.models import Message, UserConversationView
        from wapi.services import SYSTEM_NEW_SERVICE_TEXT
        # Ana ve tudo e a conversa inteira.
        UserConversationView.objects.create(
            user=self.uuser, view_scope='all', view_full_history=True)
        base = timezone.now()

        def mk(txt, direction='in', mtype='text', sender='', off=0):
            m = Message.objects.create(conversation=self.direct_vendas, direction=direction,
                                       message_type=mtype, text=txt, sender_name=sender)
            Message.objects.filter(pk=m.pk).update(created_at=base + timedelta(minutes=off))
            return m

        mk('oi 1', off=1)
        mk('resp bruno', 'out', sender='Bruno', off=2)          # outro atendente
        mk(SYSTEM_NEW_SERVICE_TEXT, 'out', 'system', off=3)     # divisoria
        mk('oi 2', off=4)
        mk('resp ana', 'out', sender='Joao', off=5)             # eu (Attendant.name='Joao')

        self.client.force_login(self.uuser)
        data = self.client.get(
            reverse('conversation-messages', args=[self.direct_vendas.id])).json()
        self.assertTrue(data['owner_tabs'])
        by_text = {m['text']: m for m in data['messages']}
        self.assertFalse(by_text['resp bruno']['seg_mine'])   # segmento do outro
        self.assertTrue(by_text['resp ana']['seg_mine'])      # meu segmento
        self.assertTrue(by_text['oi 2']['seg_mine'])          # cliente no meu segmento

    def test_owner_tabs_hidden_when_single_owner(self):
        """Sem atendimento de outro atendente, nao mostra as abas."""
        from accounts.models import Message, UserConversationView
        UserConversationView.objects.create(
            user=self.uuser, view_scope='all', view_full_history=True)
        Message.objects.create(conversation=self.direct_vendas, direction='in',
                               message_type='text', text='oi')
        Message.objects.create(conversation=self.direct_vendas, direction='out',
                               message_type='text', text='resp', sender_name='Joao')
        self.client.force_login(self.uuser)
        data = self.client.get(
            reverse('conversation-messages', args=[self.direct_vendas.id])).json()
        self.assertFalse(data['owner_tabs'])

    def test_segment_sector_resolution(self):
        """Cada atendimento (segmento) recebe o SETOR resolvido: o ultimo setor
        nao-nulo do segmento vale para o segmento INTEIRO (inclui a triagem sem setor)."""
        from datetime import timedelta
        from django.utils import timezone
        from accounts.models import Message, UserConversationView
        from wapi.services import SYSTEM_NEW_SERVICE_TEXT
        self.admin_view = UserConversationView.objects.create(
            user=self.uuser, view_scope='all', view_full_history=True)
        base = timezone.now()

        def mk(txt, mtype='text', sec=None, off=0, direction='in'):
            m = Message.objects.create(conversation=self.direct_vendas, sector=sec,
                                       direction=direction, message_type=mtype, text=txt)
            Message.objects.filter(pk=m.pk).update(created_at=base + timedelta(minutes=off))
            return m

        mk('oi vendas', sec=self.vendas, off=1)
        mk(SYSTEM_NEW_SERVICE_TEXT, 'system', None, 2, 'out')  # divisoria sem setor
        mk('triagem', sec=None, off=3)                          # ainda sem setor
        mk('agora compras', sec=self.compras, off=4)            # roteou p/ Compras
        self.client.force_login(self.uuser)
        data = self.client.get(
            reverse('conversation-messages', args=[self.direct_vendas.id])).json()
        by_text = {m['text']: m for m in data['messages']}
        self.assertEqual(by_text['oi vendas']['seg_sector'], self.vendas.id)
        # A divisoria e a triagem (sem setor) herdam o setor do atendimento: Compras.
        self.assertEqual(by_text['triagem']['seg_sector'], self.compras.id)
        self.assertEqual(by_text['agora compras']['seg_sector'], self.compras.id)
        present = {s['id'] for s in data['conv_sectors']}
        self.assertEqual(present, {self.vendas.id, self.compras.id})

    def test_new_message_stamps_sector(self):
        """Mensagem nova nasce carimbada com o setor atual da conversa."""
        from wapi.services import save_outgoing_text_message
        self.direct_vendas.sector = self.compras
        self.direct_vendas.save(update_fields=['sector'])
        m = save_outgoing_text_message(self.direct_vendas, 'ola', sender_name='Joao')
        self.assertEqual(m.sector_id, self.compras.id)
class MasterRoleTests(TestCase):
    """GESTOR MASTER: administra as empresas clientes e NAO acessa o atendimento
    delas (decisao de privacidade — ver accounts/tenancy.py)."""

    def setUp(self):
        from accounts.models import Company
        self.company = Company.get_default()
        self.master = User.objects.create_user(
            email='master@beezap.com', password='x', role=User.Role.MASTER
        )
        self.admin = User.objects.create_user(
            email='adm@cliente.com', password='x', role=User.Role.ADM, company=self.company
        )

    def test_master_has_no_company(self):
        self.assertIsNone(self.master.company)
        self.assertTrue(self.master.is_master)

    def test_master_menu_has_only_platform_screens(self):
        """Fora do painel de um cliente, o master ve so as telas da PLATAFORMA."""
        from accounts.permissions import nav_items_for
        labels = [item['label'] for item in nav_items_for(self.master, '')]
        self.assertEqual(labels, ['Clientes', 'Métricas', 'Inteligência (IA)', 'Gestores'])

    def test_master_cannot_access_operational_screens(self):
        self.client.force_login(self.master)
        for route in ('conversations', 'contacts', 'sectors', 'attendants', 'permissions'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 403, route)

    def test_master_lands_on_clients_after_login(self):
        self.client.force_login(self.master)
        r = self.client.get(reverse('dashboard'))
        self.assertRedirects(r, reverse('clients'))

    def test_master_sees_no_conversation(self):
        """Mesmo com conversas cadastradas, o master nao enxerga nenhuma."""
        from accounts.models import Contact, Conversation
        from accounts.permissions import can_see_conversation, visible_conversations
        contact = Contact.objects.create(company=self.company, phone='5516999990001')
        conv = Conversation.objects.create(
            company=self.company, contact=contact, external_id='5516999990001'
        )
        self.assertFalse(visible_conversations(self.master, Conversation.objects.all()).exists())
        self.assertFalse(can_see_conversation(self.master, conv))
        # O administrador do cliente continua vendo normalmente.
        self.assertTrue(can_see_conversation(self.admin, conv))

    def test_master_is_not_provisioned_as_attendant(self):
        from accounts.models import Attendant
        self.assertFalse(Attendant.objects.filter(user=self.master).exists())

    def test_client_admin_cannot_open_clients_screen(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('clients')).status_code, 403)
class AjaxEndpointsRespectMenuPermissionsTests(TestCase):
    """Esconder o botao tem que bloquear a URL — inclusive a URL de DADOS.

    Regressao: o gate de feature estava so nas telas. Um usuario com o botao
    Conversas removido pela tela Permissoes levava 403 na tela e continuava
    recebendo, por `conversation-list`, a lista completa com a PREVIA da ultima
    mensagem de cada conversa. O mesmo valia para sincronizar grupos e nomear
    contato.
    """

    PREVIA = 'conteudo sensivel da ultima mensagem'

    def setUp(self):
        from ..models import (Attendant as Att, Contact, Conversation, Sector,
                             UserMenuPermission)
        self.company = default_company()
        self.user = User.objects.create_user(
            email='sem-botao@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=self.company,
        )
        self.attendant = Att.objects.create(
            company=self.company, user=self.user, name='Ana',
            must_change_password=False,
        )
        setor = Sector.objects.create(company=self.company, name='Financeiro AJAX')
        setor.attendants.add(self.attendant)
        contato = Contact.objects.create(
            company=self.company, name='Cliente', phone='5519777776666',
        )
        self.conversation = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519777776666',
            chat_type='private', sector=setor, last_message_text=self.PREVIA,
        )
        # O ADM retira o botao Conversas desta pessoa.
        UserMenuPermission.objects.create(user=self.user, allowed_keys=['contacts'])
        self.client.force_login(self.user)

    def test_tela_e_endpoint_de_lista_respondem_igual(self):
        self.assertEqual(self.client.get(reverse('conversations')).status_code, 403)
        response = self.client.get(reverse('conversation-list'))
        self.assertEqual(response.status_code, 403)

    def test_previa_da_mensagem_nao_vaza_pela_lista(self):
        response = self.client.get(reverse('conversation-list'))
        self.assertNotIn(self.PREVIA, response.content.decode())

    def test_mensagens_da_conversa_bloqueadas(self):
        response = self.client.get(
            reverse('conversation-messages', args=[self.conversation.id])
        )
        self.assertEqual(response.status_code, 403)

    def test_sincronizar_grupos_bloqueado(self):
        """Sem o gate, esta URL disparava uma chamada externa a W-API da empresa."""
        response = self.client.post(reverse('conversation-sync-groups'))
        self.assertEqual(response.status_code, 403)

    def test_acoes_de_atendimento_bloqueadas(self):
        for nome in ('conversation-send', 'conversation-transfer',
                     'conversation-take', 'conversation-close'):
            with self.subTest(endpoint=nome):
                response = self.client.post(
                    reverse(nome, args=[self.conversation.id]), {'text': 'oi'}
                )
                self.assertEqual(response.status_code, 403)

    def test_nomear_contato_bloqueado_sem_o_botao_contatos(self):
        from ..models import UserMenuPermission
        UserMenuPermission.objects.filter(user=self.user).update(allowed_keys=[])
        response = self.client.post(
            reverse('conversation-name-contact'),
            {'number': '5519333334444', 'name': 'Nome Novo'},
        )
        self.assertEqual(response.status_code, 403)


class PermissionsCsrfTests(TestCase):
    """A tela Permissoes salva sozinha por AJAX: o POST tem que passar no CSRF.

    Regressao real: o JS pegava o token com `document.cookie['csrftoken']`, mas o
    cookie tem nome proprio (`CSRF_COOKIE_NAME = beeonboard_csrftoken`, para nao
    colidir com os outros sistemas do mesmo dominio). O header saia VAZIO, o Django
    respondia 403 em HTML, o `r.json()` do JS estourava e o usuario via so
    "Nao foi possivel alterar o perfil." — nenhum perfil mudava, em nenhuma aba.

    Os outros testes desta tela usam o Client sem CSRF, entao passavam com a tela
    quebrada; aqui o CSRF e conferido de verdade e o token vem SO do que a pagina
    entrega ao navegador.
    """

    def setUp(self):
        from django.test import Client
        self.admin = User.objects.create_user(
            company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.user = User.objects.create_user(
            company=default_company(), email='joao@x.com', password='x', role=User.Role.USUARIO)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)

    def _token_da_pagina(self):
        """Le o token como o navegador leria: do que o template renderizou."""
        import re
        corpo = self.client.get(reverse('permissions')).content.decode()
        achado = re.search(r"CSRF_TOKEN\s*=\s*'([^']+)'", corpo)
        self.assertIsNotNone(achado, 'a tela nao entrega o token CSRF para o JS')
        return achado.group(1)

    def test_mudar_perfil_passa_no_csrf(self):
        resposta = self.client.post(
            reverse('permissions'),
            {'form_type': 'profile-role', 'user_id': str(self.user.id), 'role': 'leitor'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_X_CSRFTOKEN=self._token_da_pagina(),
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('application/json', resposta.headers.get('Content-Type', ''))
        self.assertTrue(resposta.json()['ok'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'leitor')

    def test_token_do_cookie_padrao_nao_serve(self):
        """Prova o motivo do bug: nao existe cookie `csrftoken` para o JS achar."""
        from django.conf import settings
        self.client.get(reverse('permissions'))
        self.assertNotIn('csrftoken', self.client.cookies)
        self.assertIn(settings.CSRF_COOKIE_NAME, self.client.cookies)


class ContactSectorVisibilityTests(TestCase):
    """Carteira de contatos por setor: quem ve quais contatos na tela Contatos.

    Regras (decididas com o dono do produto):
      - contato SEM setor aparece para todos (e o estado de todo contato antigo);
      - contato classificado aparece para quem ATUA naquele setor — quem e de Compras
        e Comercial ve as duas carteiras — mais o extra liberado em Permissoes;
      - administrador ve tudo; gestor master nao ve nada.

    E a regra que NAO vale: isto e so a AGENDA. A conversa nao muda.
    """

    def setUp(self):
        from accounts.models import Company, Contact, Sector
        self.Contact = Contact
        self.empresa = default_company()
        self.vizinha = Company.objects.create(name='Vizinha', slug='vizinha')
        self.compras = Sector.objects.create(company=self.empresa, name='Compras')
        self.comercial = Sector.objects.create(company=self.empresa, name='Comercial')
        self.suporte = Sector.objects.create(company=self.empresa, name='Suporte')

        self.sem_setor = Contact.objects.create(
            company=self.empresa, name='Sem Setor', phone='5516900000000')
        self.de_compras = Contact.objects.create(
            company=self.empresa, name='Fornecedor', phone='5516900000001')
        self.de_compras.sectors.add(self.compras)
        self.de_comercial = Contact.objects.create(
            company=self.empresa, name='Cliente Novo', phone='5516900000002')
        self.de_comercial.sectors.add(self.comercial)
        self.dos_dois = Contact.objects.create(
            company=self.empresa, name='Compra e Vende', phone='5516900000003')
        self.dos_dois.sectors.add(self.compras, self.comercial)
        self.da_vizinha = Contact.objects.create(
            company=self.vizinha, name='Alheio', phone='5516900000009')

        self.adm = User.objects.create_user(
            company=self.empresa, email='adm@x.com', password='x', role=User.Role.ADM)

    def _atendente(self, email, *setores):
        user = User.objects.create_user(
            company=self.empresa, email=email, password='x', role=User.Role.USUARIO)
        att = Attendant.objects.create(
            company=self.empresa, user=user, name=email.split('@')[0],
            must_change_password=False)
        if setores:
            att.sectors.add(*setores)
        return user

    def _visiveis(self, user):
        from accounts.permissions import visible_contacts
        return sorted(
            c.name for c in visible_contacts(user, self.Contact.objects.all())
        )

    # ----- o basico -----

    def test_adm_ve_todos_da_empresa(self):
        self.assertEqual(
            self._visiveis(self.adm),
            ['Cliente Novo', 'Compra e Vende', 'Fornecedor', 'Sem Setor'])

    def test_contato_sem_setor_aparece_para_todos(self):
        de_fora = self._atendente('fora@x.com', self.suporte)
        self.assertIn('Sem Setor', self._visiveis(de_fora))

    def test_atendente_ve_a_carteira_do_proprio_setor(self):
        comprador = self._atendente('compras@x.com', self.compras)
        visiveis = self._visiveis(comprador)
        self.assertIn('Fornecedor', visiveis)
        self.assertNotIn('Cliente Novo', visiveis)

    def test_atendente_de_dois_setores_ve_as_duas_carteiras(self):
        """O caso pedido: quem e de Compras E Comercial ve as duas."""
        os_dois = self._atendente('ambos@x.com', self.compras, self.comercial)
        visiveis = self._visiveis(os_dois)
        self.assertIn('Fornecedor', visiveis)      # Compras
        self.assertIn('Cliente Novo', visiveis)    # Comercial
        self.assertIn('Compra e Vende', visiveis)  # nos dois

    def test_contato_em_dois_setores_nao_duplica_na_lista(self):
        os_dois = self._atendente('ambos@x.com', self.compras, self.comercial)
        nomes = self._visiveis(os_dois)
        self.assertEqual(nomes.count('Compra e Vende'), 1)

    def test_sem_setor_nenhum_ve_apenas_os_nao_classificados(self):
        solto = self._atendente('solto@x.com')
        self.assertEqual(self._visiveis(solto), ['Sem Setor'])

    def test_contato_de_outra_empresa_nunca_aparece(self):
        for quem in (self.adm, self._atendente('a@x.com', self.compras)):
            with self.subTest(quem=quem.email):
                self.assertNotIn('Alheio', self._visiveis(quem))

    def test_gestor_master_nao_ve_contato_nenhum(self):
        master = User.objects.create_user(
            email='master@x.com', password='x', role=User.Role.MASTER)
        self.assertEqual(self._visiveis(master), [])

    # ----- liberacoes de Permissoes -----

    def test_liberacao_por_setor(self):
        from accounts.models import ContactSectorAccess
        acesso = ContactSectorAccess.objects.create(sector=self.compras)
        acesso.sectors.add(self.suporte)
        do_suporte = self._atendente('sup@x.com', self.suporte)
        self.assertIn('Fornecedor', self._visiveis(do_suporte))
        # E nao ganha a carteira do Comercial de brinde.
        self.assertNotIn('Cliente Novo', self._visiveis(do_suporte))

    def test_liberacao_por_pessoa(self):
        from accounts.models import ContactSectorAccess
        pessoa = self._atendente('pessoa@x.com', self.suporte)
        acesso = ContactSectorAccess.objects.create(sector=self.comercial)
        acesso.users.add(pessoa)
        self.assertIn('Cliente Novo', self._visiveis(pessoa))

    def test_liberacao_nao_vaza_para_quem_nao_foi_liberado(self):
        from accounts.models import ContactSectorAccess
        pessoa = self._atendente('pessoa@x.com', self.suporte)
        outra = self._atendente('outra@x.com', self.suporte)
        acesso = ContactSectorAccess.objects.create(sector=self.comercial)
        acesso.users.add(pessoa)
        self.assertNotIn('Cliente Novo', self._visiveis(outra))

    # ----- o limite do escopo: a CONVERSA nao muda -----

    def test_a_conversa_nao_e_afetada_pela_carteira(self):
        """Contato de Vendas que escreve para o Comercial e atendido normalmente.

        A restricao e da AGENDA. Se algum dia alguem ligar `visible_contacts` na
        visibilidade de conversa, este teste reprova.
        """
        from accounts.models import Conversation
        from accounts.permissions import can_see_conversation, visible_contacts
        conversa = Conversation.objects.create(
            company=self.empresa, contact=self.de_compras,
            external_id=self.de_compras.phone, chat_type='private',
            status='pending', sector=self.comercial)
        do_comercial = self._atendente('com@x.com', self.comercial)

        # A conversa e do setor dele: ele atende.
        self.assertTrue(can_see_conversation(do_comercial, conversa))
        # Mas o contato e da carteira de Compras: nao entra na agenda dele.
        self.assertNotIn(
            'Fornecedor',
            [c.name for c in visible_contacts(do_comercial, self.Contact.objects.all())])

    def test_o_nome_do_contato_continua_resolvendo_nas_conversas(self):
        """A agenda restrita nao pode fazer a conversa voltar a mostrar numero."""
        from accounts.models import Conversation
        conversa = Conversation.objects.create(
            company=self.empresa, contact=self.de_compras,
            external_id=self.de_compras.phone, chat_type='private', status='pending')
        self.assertEqual(conversa.display_title, 'Fornecedor')


class ContactsScreenSectorTests(TestCase):
    """Tela Contatos: classificar (so o ADM), filtrar e o alcance no POST."""

    def setUp(self):
        from accounts.models import Contact, Sector, UserMenuPermission
        self.Contact = Contact
        self.empresa = default_company()
        self.compras = Sector.objects.create(company=self.empresa, name='Compras')
        self.comercial = Sector.objects.create(company=self.empresa, name='Comercial')
        self.adm = User.objects.create_user(
            company=self.empresa, email='adm@x.com', password='x', role=User.Role.ADM)
        self.atendente = User.objects.create_user(
            company=self.empresa, email='at@x.com', password='x', role=User.Role.USUARIO)
        att = Attendant.objects.create(
            company=self.empresa, user=self.atendente, name='At',
            must_change_password=False)
        att.sectors.add(self.compras)
        UserMenuPermission.objects.update_or_create(
            user=self.atendente, defaults={'allowed_keys': ['contacts']})

    def test_adm_classifica_ao_cadastrar(self):
        self.client.force_login(self.adm)
        self.client.post(reverse('contacts'), {
            'name': 'Novo Fornecedor', 'phone': '(11) 98888-7777',
            'sectors': [str(self.compras.pk)],
        })
        contato = self.Contact.objects.get(name='Novo Fornecedor')
        self.assertEqual([s.name for s in contato.sectors.all()], ['Compras'])

    def test_adm_classifica_em_dois_setores(self):
        self.client.force_login(self.adm)
        self.client.post(reverse('contacts'), {
            'name': 'Compra e Vende', 'phone': '11988887777',
            'sectors': [str(self.compras.pk), str(self.comercial.pk)],
        })
        contato = self.Contact.objects.get(name='Compra e Vende')
        self.assertEqual(
            sorted(s.name for s in contato.sectors.all()), ['Comercial', 'Compras'])

    def test_atendente_nao_classifica_nem_forjando_o_post(self):
        """Classificar mexe em QUEM VE: se o atendente pudesse, esconderia contato
        dos colegas sem passar por Permissoes."""
        contato = self.Contact.objects.create(
            company=self.empresa, name='Alvo', phone='5516900000001')
        self.client.force_login(self.atendente)
        self.client.post(reverse('contacts'), {
            'contact_id': str(contato.pk), 'name': 'Alvo', 'phone': '5516900000001',
            'sectors': [str(self.comercial.pk)],
        })
        contato.refresh_from_db()
        self.assertEqual(list(contato.sectors.all()), [])

    def test_atendente_ainda_edita_nome_e_telefone(self):
        contato = self.Contact.objects.create(
            company=self.empresa, name='Antigo', phone='5516900000001')
        self.client.force_login(self.atendente)
        self.client.post(reverse('contacts'), {
            'contact_id': str(contato.pk), 'name': 'Nome Novo', 'phone': '5516900000001',
        })
        contato.refresh_from_db()
        self.assertEqual(contato.name, 'Nome Novo')

    def test_setor_de_outra_empresa_e_ignorado(self):
        from accounts.models import Company, Sector
        outra = Company.objects.create(name='Outra', slug='outra')
        alheio = Sector.objects.create(company=outra, name='Alheio')
        self.client.force_login(self.adm)
        self.client.post(reverse('contacts'), {
            'name': 'Teste', 'phone': '11977776666', 'sectors': [str(alheio.pk)],
        })
        self.assertEqual(list(self.Contact.objects.get(name='Teste').sectors.all()), [])

    def test_atendente_nao_edita_contato_fora_da_carteira(self):
        de_outro = self.Contact.objects.create(
            company=self.empresa, name='Do Comercial', phone='5516900000002')
        de_outro.sectors.add(self.comercial)
        self.client.force_login(self.atendente)
        self.client.post(reverse('contacts'), {
            'contact_id': str(de_outro.pk), 'name': 'Invadido', 'phone': '5516900000002',
        })
        de_outro.refresh_from_db()
        self.assertEqual(de_outro.name, 'Do Comercial')

    def test_atendente_nao_exclui_contato_fora_da_carteira(self):
        de_outro = self.Contact.objects.create(
            company=self.empresa, name='Do Comercial', phone='5516900000002')
        de_outro.sectors.add(self.comercial)
        self.client.force_login(self.atendente)
        self.client.post(reverse('contacts'), {
            'action': 'delete', 'contact_id': str(de_outro.pk)})
        self.assertTrue(self.Contact.objects.filter(pk=de_outro.pk).exists())

    def test_filtro_sem_setor(self):
        classificado = self.Contact.objects.create(
            company=self.empresa, name='Classificado', phone='5516900000003')
        classificado.sectors.add(self.compras)
        self.Contact.objects.create(
            company=self.empresa, name='Solto', phone='5516900000004')
        self.client.force_login(self.adm)
        corpo = self.client.get(reverse('contacts'), {'setor': 'sem'}).content.decode()
        self.assertIn('Solto', corpo)
        self.assertNotIn('Classificado', corpo)

    def test_filtro_por_setor(self):
        classificado = self.Contact.objects.create(
            company=self.empresa, name='Classificado', phone='5516900000003')
        classificado.sectors.add(self.compras)
        self.Contact.objects.create(
            company=self.empresa, name='Solto', phone='5516900000004')
        self.client.force_login(self.adm)
        corpo = self.client.get(
            reverse('contacts'), {'setor': str(self.compras.pk)}).content.decode()
        self.assertIn('Classificado', corpo)
        self.assertNotIn('Solto', corpo)

    def test_campo_de_setores_so_aparece_para_o_adm(self):
        # Confere a MARCA do bloco HTML, nao `data-field-sectors`: esse nome tambem
        # aparece no seletor do JS e daria falso positivo.
        marca = 'class="contacts-sector-picker"'
        self.client.force_login(self.adm)
        self.assertIn(marca, self.client.get(reverse('contacts')).content.decode())
        self.client.force_login(self.atendente)
        self.assertNotIn(marca, self.client.get(reverse('contacts')).content.decode())

    def test_a_lista_mostra_so_a_carteira_de_quem_esta_logado(self):
        do_comercial = self.Contact.objects.create(
            company=self.empresa, name='Do Comercial', phone='5516900000002')
        do_comercial.sectors.add(self.comercial)
        de_compras = self.Contact.objects.create(
            company=self.empresa, name='De Compras', phone='5516900000005')
        de_compras.sectors.add(self.compras)
        self.client.force_login(self.atendente)
        corpo = self.client.get(reverse('contacts')).content.decode()
        self.assertIn('De Compras', corpo)
        self.assertNotIn('Do Comercial', corpo)


class ContactAccessTabTests(TestCase):
    """Aba Contatos em Permissoes: salvar as liberacoes da carteira."""

    def setUp(self):
        from accounts.models import Sector
        self.empresa = default_company()
        self.compras = Sector.objects.create(company=self.empresa, name='Compras')
        self.comercial = Sector.objects.create(company=self.empresa, name='Comercial')
        self.adm = User.objects.create_user(
            company=self.empresa, email='adm@x.com', password='x', role=User.Role.ADM)
        self.atendente = User.objects.create_user(
            company=self.empresa, email='at@x.com', password='x', role=User.Role.USUARIO)
        Attendant.objects.create(
            company=self.empresa, user=self.atendente, name='At',
            must_change_password=False)
        self.client.force_login(self.adm)

    def test_a_aba_aparece(self):
        corpo = self.client.get(reverse('permissions')).content.decode()
        self.assertIn('data-tab="contatos"', corpo)
        self.assertIn('carteira__%s__sector' % self.compras.pk, corpo)
        self.assertIn('carteira__%s__user' % self.compras.pk, corpo)

    def test_o_proprio_setor_nao_aparece_como_opcao(self):
        """Quem atua no setor ja ve a carteira dele: oferecer a opcao confundiria."""
        corpo = self.client.get(reverse('permissions')).content.decode()
        bloco = corpo.split('carteira__%s__sector' % self.compras.pk)[1].split('perm-group-block')[0]
        self.assertNotIn('value="%s"' % self.compras.pk, bloco)

    def test_salva_liberacao_por_setor_e_por_pessoa(self):
        from accounts.models import ContactSectorAccess
        resposta = self.client.post(reverse('permissions'), {
            'form_type': 'contact-access',
            'carteira__%s__sector' % self.compras.pk: [str(self.comercial.pk)],
            'carteira__%s__user' % self.compras.pk: [str(self.atendente.pk)],
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resposta.status_code, 200)
        acesso = ContactSectorAccess.objects.get(sector=self.compras)
        self.assertEqual([s.name for s in acesso.sectors.all()], ['Comercial'])
        self.assertEqual([u.email for u in acesso.users.all()], ['at@x.com'])

    def test_o_proprio_setor_e_ignorado_se_vier_no_post(self):
        from accounts.models import ContactSectorAccess
        self.client.post(reverse('permissions'), {
            'form_type': 'contact-access',
            'carteira__%s__sector' % self.compras.pk: [str(self.compras.pk)],
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        acesso = ContactSectorAccess.objects.get(sector=self.compras)
        self.assertEqual(list(acesso.sectors.all()), [])

    def test_setor_de_outra_empresa_e_ignorado(self):
        from accounts.models import Company, ContactSectorAccess, Sector
        outra = Company.objects.create(name='Outra', slug='outra')
        alheio = Sector.objects.create(company=outra, name='Alheio')
        self.client.post(reverse('permissions'), {
            'form_type': 'contact-access',
            'carteira__%s__sector' % self.compras.pk: [str(alheio.pk)],
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        acesso = ContactSectorAccess.objects.get(sector=self.compras)
        self.assertEqual(list(acesso.sectors.all()), [])

    def test_desmarcar_remove_a_liberacao(self):
        from accounts.models import ContactSectorAccess
        acesso = ContactSectorAccess.objects.create(sector=self.compras)
        acesso.sectors.add(self.comercial)
        self.client.post(reverse('permissions'), {
            'form_type': 'contact-access',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        acesso.refresh_from_db()
        self.assertEqual(list(acesso.sectors.all()), [])

    def test_atendente_nao_alcanca_a_tela(self):
        self.client.force_login(self.atendente)
        self.assertEqual(self.client.get(reverse('permissions')).status_code, 403)


class AutoClassifyContactTests(TestCase):
    """Classificacao automatica: ao ENCERRAR, o contato sem setor herda o setor.

    Existe porque classificar mil contatos a mao nao acontece — sem isto a carteira
    nasce vazia e fica vazia. As duas travas que fazem isto ser seguro sao o alvo
    principal dos testes: so age em contato SEM NENHUM setor (nunca sobrescreve nem
    acrescenta) e a empresa pode desligar.
    """

    def setUp(self):
        from accounts.models import Company, Contact, Conversation, Sector
        self.Contact = Contact
        self.Conversation = Conversation
        self.empresa = default_company()
        self.vendas = Sector.objects.create(company=self.empresa, name='Vendas')
        self.suporte = Sector.objects.create(company=self.empresa, name='Suporte')
        self.adm = User.objects.create_user(
            company=self.empresa, email='adm@x.com', password='x', role=User.Role.ADM)
        self.client.force_login(self.adm)

    def _conversa(self, setor, telefone='5516900000001', nome='Cliente'):
        contato = self.Contact.objects.create(
            company=self.empresa, name=nome, phone=telefone)
        conversa = self.Conversation.objects.create(
            company=self.empresa, contact=contato, external_id=telefone,
            chat_type='private', status='open', sector=setor)
        return contato, conversa

    def _encerrar(self, conversa):
        return self.client.post(
            reverse('conversation-close', args=[conversa.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_encerrar_classifica_o_contato_sem_setor(self):
        contato, conversa = self._conversa(self.vendas)
        self.assertEqual(self._encerrar(conversa).status_code, 200)
        self.assertEqual([s.name for s in contato.sectors.all()], ['Vendas'])

    def test_nao_mexe_em_contato_ja_classificado(self):
        """A escolha do ADM nunca e sobrescrita — nem acrescentada.

        Se acrescentasse, um contato que passou por cinco setores acabaria nas cinco
        carteiras, visivel para todo mundo: o oposto do que a carteira serve.
        """
        contato, conversa = self._conversa(self.suporte)
        contato.sectors.add(self.vendas)
        self._encerrar(conversa)
        self.assertEqual([s.name for s in contato.sectors.all()], ['Vendas'])

    def test_empresa_pode_desligar(self):
        from accounts.models import Company
        Company.objects.filter(pk=self.empresa.pk).update(auto_classify_contacts=False)
        contato, conversa = self._conversa(self.vendas)
        self._encerrar(conversa)
        self.assertEqual(list(contato.sectors.all()), [])

    def test_conversa_sem_setor_nao_classifica_nada(self):
        contato, conversa = self._conversa(None)
        self._encerrar(conversa)
        self.assertEqual(list(contato.sectors.all()), [])

    def test_grupo_nao_tem_contato_para_classificar(self):
        """Conversa de grupo nao tem contato: encerrar nao pode estourar."""
        grupo = self.Conversation.objects.create(
            company=self.empresa, external_id='120363000000000001@g.us',
            chat_type='group', name='Equipe', status='open', sector=self.vendas)
        self.assertEqual(self._encerrar(grupo).status_code, 200)

    def test_o_setor_e_o_que_ENCERROU_nao_o_que_abriu(self):
        """Transferido de Vendas para Suporte e encerrado la: a carteira e do Suporte."""
        contato, conversa = self._conversa(self.vendas)
        conversa.sector = self.suporte
        conversa.save(update_fields=['sector'])
        self._encerrar(conversa)
        self.assertEqual([s.name for s in contato.sectors.all()], ['Suporte'])

    def test_o_encerramento_continua_limpando_o_setor_da_conversa(self):
        """A classificacao nao pode ter mudado o comportamento de encerrar."""
        contato, conversa = self._conversa(self.vendas)
        self._encerrar(conversa)
        conversa.refresh_from_db()
        self.assertEqual(conversa.status, 'closed')
        self.assertIsNone(conversa.sector_id)

    def test_setor_de_outra_empresa_nunca_classifica(self):
        from accounts.models import Company, Sector
        outra = Company.objects.create(name='Outra', slug='outra')
        alheio = Sector.objects.create(company=outra, name='Alheio')
        contato = self.Contact.objects.create(
            company=self.empresa, name='Cliente', phone='5516900000002')
        self.assertFalse(contato.inherit_sector_if_unclassified(alheio))
        self.assertEqual(list(contato.sectors.all()), [])

    def test_o_interruptor_aparece_e_salva(self):
        from accounts.models import Company
        corpo = self.client.get(reverse('permissions')).content.decode()
        self.assertIn('name="auto_classify"', corpo)
        # Vem marcado por padrao.
        self.assertIn('name="auto_classify" checked', corpo)

        # Salvar a aba sem a caixa marcada desliga.
        self.client.post(reverse('permissions'), {'form_type': 'contact-access'},
                         HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertFalse(Company.objects.get(pk=self.empresa.pk).auto_classify_contacts)

        # E marcar de novo religa.
        self.client.post(reverse('permissions'),
                         {'form_type': 'contact-access', 'auto_classify': 'on'},
                         HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(Company.objects.get(pk=self.empresa.pk).auto_classify_contacts)

    def test_classificado_automaticamente_ja_entra_na_carteira_do_setor(self):
        """Ponta a ponta: encerrou em Vendas -> quem e de Vendas passa a ver o contato."""
        from accounts.permissions import visible_contacts
        contato, conversa = self._conversa(self.vendas)
        de_vendas = User.objects.create_user(
            company=self.empresa, email='v@x.com', password='x', role=User.Role.USUARIO)
        att = Attendant.objects.create(
            company=self.empresa, user=de_vendas, name='V', must_change_password=False)
        att.sectors.add(self.vendas)
        do_suporte = User.objects.create_user(
            company=self.empresa, email='s@x.com', password='x', role=User.Role.USUARIO)
        att2 = Attendant.objects.create(
            company=self.empresa, user=do_suporte, name='S', must_change_password=False)
        att2.sectors.add(self.suporte)

        # Antes de encerrar, sem setor: os dois veem.
        self.assertIn('Cliente', [c.name for c in visible_contacts(
            de_vendas, self.Contact.objects.all())])
        self.assertIn('Cliente', [c.name for c in visible_contacts(
            do_suporte, self.Contact.objects.all())])

        self._encerrar(conversa)

        # Depois: virou carteira de Vendas.
        self.assertIn('Cliente', [c.name for c in visible_contacts(
            de_vendas, self.Contact.objects.all())])
        self.assertNotIn('Cliente', [c.name for c in visible_contacts(
            do_suporte, self.Contact.objects.all())])
