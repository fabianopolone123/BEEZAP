from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.auth.hashers import check_password
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from wapi.parser import (
    is_group_jid,
    is_ignorable_jid,
    is_status_or_broadcast,
    normalize_phone,
    normalize_wapi_message_context,
)

from .models import Attendant, PasswordResetCode, User


class WapiJidClassificationTests(SimpleTestCase):
    """Grupo/canal/transmissao nunca podem virar 'telefone' nem conversa direta.

    Regressao do caso em que um canal/grupo (JID numerico interno "120363...",
    18 digitos) chegou como conversa DIRETA, criando um contato com telefone
    invalido.
    """

    GROUP_LIKE_JID = '120363144038483540'  # id interno do WhatsApp (nao e telefone)

    def test_normalize_phone_accepts_real_phone(self):
        self.assertEqual(normalize_phone('5516999999999@s.whatsapp.net'), '5516999999999')
        self.assertEqual(normalize_phone('+55 (16) 99999-9999'), '5516999999999')

    def test_normalize_phone_rejects_non_personal_jids(self):
        self.assertEqual(normalize_phone(self.GROUP_LIKE_JID + '@g.us'), '')
        self.assertEqual(normalize_phone(self.GROUP_LIKE_JID + '@newsletter'), '')
        self.assertEqual(normalize_phone('status@broadcast'), '')
        self.assertEqual(normalize_phone('183545595199545@lid'), '')

    def test_normalize_phone_rejects_internal_id_too_long_for_phone(self):
        # 18 digitos "pelados": id interno de grupo/canal, nunca telefone.
        self.assertEqual(normalize_phone(self.GROUP_LIKE_JID), '')

    def test_is_group_jid(self):
        self.assertTrue(is_group_jid(self.GROUP_LIKE_JID + '@g.us'))
        self.assertTrue(is_group_jid(self.GROUP_LIKE_JID + '@newsletter'))
        self.assertTrue(is_group_jid('status@broadcast'))
        self.assertTrue(is_group_jid(self.GROUP_LIKE_JID))  # bare, longo demais
        self.assertFalse(is_group_jid('5516999999999'))
        self.assertFalse(is_group_jid('5516999999999@s.whatsapp.net'))
        self.assertFalse(is_group_jid('183545595199545@lid'))

    def test_newsletter_message_is_not_a_direct_conversation(self):
        ctx = normalize_wapi_message_context(
            {'data': {'key': {'remoteJid': self.GROUP_LIKE_JID + '@newsletter'}}}
        )
        self.assertTrue(ctx['is_group'])
        self.assertEqual(ctx['chat_type'], 'group')

    def test_bare_internal_id_is_not_a_direct_conversation(self):
        ctx = normalize_wapi_message_context({'sender': {'id': self.GROUP_LIKE_JID}})
        self.assertTrue(ctx['is_group'])
        self.assertEqual(ctx['chat_type'], 'group')

    def test_real_direct_message_still_private(self):
        ctx = normalize_wapi_message_context({'sender': {'id': '5516999999999'}})
        self.assertFalse(ctx['is_group'])
        self.assertEqual(ctx['chat_type'], 'private')
        self.assertEqual(ctx['sender_id'], '5516999999999')

    def test_real_group_still_group(self):
        ctx = normalize_wapi_message_context(
            {'data': {'key': {'remoteJid': self.GROUP_LIKE_JID + '@g.us',
                              'participant': '5516999999999@s.whatsapp.net'}}}
        )
        self.assertTrue(ctx['is_group'])
        self.assertEqual(ctx['sender_id'], '5516999999999')

    def test_is_ignorable_jid(self):
        self.assertTrue(is_ignorable_jid(self.GROUP_LIKE_JID + '@newsletter'))
        self.assertTrue(is_ignorable_jid('status@broadcast'))
        self.assertFalse(is_ignorable_jid(self.GROUP_LIKE_JID + '@g.us'))  # grupo fica
        self.assertFalse(is_ignorable_jid('5516999999999@s.whatsapp.net'))


class WapiMediaExtensionTests(SimpleTestCase):
    """A midia deve ser salva/baixada com a extensao correta (nao .bin)."""

    def _msg(self, message_type, text='', mimetype=''):
        from accounts.models import Message
        return Message(message_type=message_type, text=text, media_mimetype=mimetype)

    def test_document_uses_original_filename_extension(self):
        from wapi.services import _ext_for_media
        # Mesmo sem mimetype conhecido, o nome original manda.
        self.assertEqual(_ext_for_media(self._msg('document', 'contrato.docx'), ''), 'docx')
        self.assertEqual(_ext_for_media(self._msg('document', 'planilha.xlsx'), ''), 'xlsx')
        self.assertEqual(_ext_for_media(self._msg('document', 'notas.PDF'), ''), 'pdf')

    def test_document_falls_back_to_mimetype(self):
        from wapi.services import _ext_for_media
        docx = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        self.assertEqual(_ext_for_media(self._msg('document', 'semext', docx), docx), 'docx')
        self.assertEqual(
            _ext_for_media(self._msg('document', '', 'application/pdf'), 'application/pdf'),
            'pdf',
        )

    def test_media_types_use_mimetype(self):
        from wapi.services import _ext_for_media
        self.assertEqual(_ext_for_media(self._msg('image', '', 'image/jpeg'), 'image/jpeg'), 'jpg')
        self.assertEqual(_ext_for_media(self._msg('audio', '', 'audio/ogg'), 'audio/ogg'), 'ogg')
        self.assertEqual(_ext_for_media(self._msg('video', '', 'video/mp4'), 'video/mp4'), 'mp4')

    def test_unknown_falls_back_to_bin(self):
        from wapi.services import _ext_for_media
        self.assertEqual(_ext_for_media(self._msg('document', 'semextensao'), ''), 'bin')

    def test_any_extension_from_filename_even_if_unknown_type(self):
        # Extensao fora de qualquer lista (ex.: CAD .dwg) deve ser preservada.
        from wapi.services import _ext_for_media
        self.assertEqual(_ext_for_media(self._msg('document', 'planta.dwg'), ''), 'dwg')
        self.assertEqual(_ext_for_media(self._msg('document', 'arte.psd'), ''), 'psd')

    def test_document_with_caption_still_uses_real_filename(self):
        # BRECHA: documento com legenda -> message.text guarda a legenda, mas o
        # nome/extensao reais vem do fileName no payload.
        from accounts.models import Message
        from wapi.services import _ext_for_media, document_filename
        payload = {'msgContent': {'documentMessage': {
            'fileName': 'contrato assinado.docx',
            'caption': 'segue o contrato',
            'mimetype': 'application/octet-stream',
        }}}
        msg = Message(message_type='document', text='segue o contrato', raw_payload=payload)
        self.assertEqual(document_filename(msg), 'contrato assinado.docx')
        self.assertEqual(_ext_for_media(msg, 'application/octet-stream'), 'docx')


class WapiIngestIgnoreTests(TestCase):
    """Mensagens de canal (@newsletter) e transmissao (@broadcast) devem ser
    ignoradas: nenhuma conversa/contato criado."""

    def _payload(self, remote_jid):
        return {
            'data': {
                'key': {'remoteJid': remote_jid, 'id': 'MSGID123'},
                'message': {'conversation': 'oi'},
            }
        }

    def test_newsletter_message_is_ignored(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation, Contact

        result = ingest_wapi_payload(self._payload('120363144038483540@newsletter'))

        self.assertIsNone(result)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_broadcast_message_is_ignored(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation

        result = ingest_wapi_payload(self._payload('status@broadcast'))

        self.assertIsNone(result)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_group_message_is_not_ignored(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation

        with patch('wapi.services.resolve_group_name', return_value=''):
            result = ingest_wapi_payload(self._payload('120363144038483540@g.us'))

        self.assertIsNotNone(result)
        self.assertEqual(Conversation.objects.filter(chat_type='group').count(), 1)

    def test_status_broadcast_is_ignored(self):
        # Status do WhatsApp: remoteJid = status@broadcast (autor no remetente).
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation, Contact

        result = ingest_wapi_payload(self._payload('status@broadcast'))

        self.assertIsNone(result)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_wapi_lite_status_is_ignored(self):
        # Formato real capturado no VPS: chat.id="status", autor no sender.
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation, Contact

        payload = {
            'event': 'webhookReceived',
            'isGroup': False,
            'messageId': 'ACSTATUS1',
            'chat': {'id': 'status'},
            'sender': {'id': '143241756299511', 'pushName': 'Alessandro'},
            'msgContent': {'imageMessage': {'mimetype': 'image/jpeg',
                                            'contextInfo': {'statusSourceType': 'IMAGE'}}},
        }
        result = ingest_wapi_payload(payload)

        self.assertIsNone(result)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_status_with_author_as_sender_is_ignored(self):
        # Caso real: o autor (telefone) vem como remetente e o status@broadcast
        # aparece em outro campo do payload — nao pode virar conversa direta.
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation, Contact

        payload = {
            'sender': {'id': '5516999998888', 'pushName': 'Marcia Nunes'},
            'chat': {'id': 'status@broadcast'},
            'msgContent': {'conversation': 'Boa tarde'},
            'messageId': 'STATUSMSG1',
        }
        result = ingest_wapi_payload(payload)

        self.assertIsNone(result)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)


class WapiUnknownMessageTests(TestCase):
    """Mensagens de sistema/tipo desconhecido nao devem virar 'Tipo nao suportado'."""

    def test_system_message_is_ignored(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Message, Conversation

        # senderKeyDistributionMessage: mensagem de sistema de grupo, sem conteudo.
        payload = {
            'data': {'key': {'remoteJid': '120363144038483540@g.us',
                             'participant': '5516999998888@s.whatsapp.net',
                             'id': 'SYS1'},
                     'message': {'senderKeyDistributionMessage': {'groupId': 'x'}}},
        }
        with patch('wapi.services.resolve_group_name', return_value=''):
            result = ingest_wapi_payload(payload)

        self.assertIsNone(result)
        self.assertEqual(Message.objects.filter(message_type='unknown').count(), 0)

    def test_real_group_text_has_sender(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Message

        payload = {
            'data': {'key': {'remoteJid': '120363144038483540@g.us',
                             'participant': '5516999998888@s.whatsapp.net',
                             'id': 'TXT1'},
                     'message': {'conversation': 'ok'}},
            'sender': {'pushName': 'Fulano'},
        }
        with patch('wapi.services.resolve_group_name', return_value=''):
            msg = ingest_wapi_payload(payload)

        self.assertIsNotNone(msg)
        self.assertEqual(msg.message_type, 'text')
        self.assertEqual(msg.sender_name, 'Fulano')


class WapiSenderNameTests(SimpleTestCase):
    def test_punctuation_only_name_is_invalid(self):
        from wapi.parser import normalize_wapi_message_context
        ctx = normalize_wapi_message_context({
            'data': {'key': {'remoteJid': '120363144038483540@g.us',
                             'participant': '5516999998888@s.whatsapp.net'}},
            'sender': {'pushName': '.'},
        })
        self.assertTrue(ctx['is_group'])
        self.assertEqual(ctx['sender_name'], '')          # "." rejeitado
        self.assertEqual(ctx['sender_id'], '5516999998888')  # front usa como fallback

    def test_real_name_is_kept(self):
        from wapi.parser import normalize_wapi_message_context
        ctx = normalize_wapi_message_context({
            'data': {'key': {'remoteJid': '120363144038483540@g.us',
                             'participant': '5516999998888@s.whatsapp.net'}},
            'sender': {'pushName': 'Marcelo'},
        })
        self.assertEqual(ctx['sender_name'], 'Marcelo')


class WapiStatusDetectionTests(SimpleTestCase):
    def test_detects_status_broadcast_anywhere(self):
        self.assertTrue(is_status_or_broadcast({'data': {'key': {'remoteJid': 'status@broadcast'}}}))
        self.assertTrue(is_status_or_broadcast({'chat': {'id': 'status@broadcast'}}))
        self.assertTrue(is_status_or_broadcast({'foo': {'bar': ['x', 'STATUS@BROADCAST']}}))

    def test_detects_broadcast_flag(self):
        self.assertTrue(is_status_or_broadcast({'broadcast': True}))
        self.assertTrue(is_status_or_broadcast({'data': {'isStatus': 'true'}}))

    def test_normal_message_is_not_status(self):
        self.assertFalse(is_status_or_broadcast({'sender': {'id': '5516999998888'},
                                                 'msgContent': {'conversation': 'oi'}}))
        self.assertFalse(is_status_or_broadcast({'data': {'key': {'remoteJid': '5516999998888@s.whatsapp.net'}}}))

    def test_detects_wapi_lite_status_chat_id(self):
        # Formato real do W-API Lite: chat.id == "status" + statusSourceType.
        payload = {
            'chat': {'id': 'status'},
            'sender': {'id': '143241756299511', 'pushName': 'Alguem'},
            'msgContent': {'imageMessage': {'mimetype': 'image/jpeg',
                                            'contextInfo': {'statusSourceType': 'IMAGE'}}},
        }
        self.assertTrue(is_status_or_broadcast(payload))
        self.assertTrue(is_ignorable_jid('status'))

    def test_detects_status_marker_key(self):
        self.assertTrue(is_status_or_broadcast(
            {'chat': {'id': 'x'}, 'msgContent': {'imageMessage': {'posterStatusID': 'abc'}}}
        ))


class AttendantsViewTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            email='admin@beezap.com',
            password='1234',
            role=User.Role.ADM,
        )
        self.common_user = User.objects.create_user(
            email='usuario@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )

    def test_adm_can_access_attendants_page(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse('attendants'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Atendentes')
        self.assertContains(response, 'Novo atendente')

    def test_common_user_cannot_access_attendants_page(self):
        self.client.force_login(self.common_user)

        response = self.client.get(reverse('attendants'))

        self.assertEqual(response.status_code, 403)

    def test_create_attendant_creates_user_and_profile(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse('attendants'),
            {
                'name': 'Maria Souza',
                'email': 'maria@beezap.com',
                'phone': '(11) 99999-9999',
            },
            follow=True,
        )

        self.assertRedirects(response, reverse('attendants'))
        attendant = Attendant.objects.get(user__email='maria@beezap.com')
        self.assertEqual(attendant.name, 'Maria Souza')
        self.assertEqual(attendant.phone, '11999999999')
        self.assertTrue(attendant.must_change_password)
        self.assertTrue(attendant.user.check_password('1234'))
        self.assertEqual(attendant.user.role, User.Role.USUARIO)
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn('Atendente cadastrado com sucesso.', messages)

    def test_edit_attendant_updates_user_and_profile(self):
        attendant_user = User.objects.create_user(
            email='joao@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        attendant = Attendant.objects.create(
            user=attendant_user,
            name='Joao Silva',
            phone='11988887777',
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse('attendants'),
            {
                'attendant_id': attendant.id,
                'name': 'Joao Pedro Silva',
                'email': 'joaopedro@beezap.com',
                'phone': '(11) 97777-6666',
            },
            follow=True,
        )

        self.assertRedirects(response, reverse('attendants'))
        attendant.refresh_from_db()
        self.assertEqual(attendant.name, 'Joao Pedro Silva')
        self.assertEqual(attendant.phone, '11977776666')
        self.assertEqual(attendant.user.email, 'joaopedro@beezap.com')
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn('Atendente atualizado com sucesso.', messages)

    def test_duplicate_email_is_rejected(self):
        existing_user = User.objects.create_user(
            email='ana@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(
            user=existing_user,
            name='Ana',
            phone='11999999999',
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse('attendants'),
            {
                'name': 'Ana Paula',
                'email': 'ana@beezap.com',
                'phone': '11911112222',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attendant.objects.count(), 1)
        self.assertContains(response, 'Ja existe um atendente com este e-mail.')

    def test_attendant_with_initial_password_is_redirected_to_change_password(self):
        attendant_user = User.objects.create_user(
            email='primeiroacesso@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(
            user=attendant_user,
            name='Primeiro Acesso',
            phone='11999999999',
            must_change_password=True,
        )

        login_ok = self.client.login(email='primeiroacesso@beezap.com', password='1234')
        response = self.client.get(reverse('dashboard'))

        self.assertTrue(login_ok)
        self.assertRedirects(response, reverse('change-initial-password'))

    def test_initial_password_change_rejects_mismatched_passwords(self):
        attendant_user = User.objects.create_user(
            email='senhasdiferentes@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(
            user=attendant_user,
            name='Senhas Diferentes',
            phone='11999999999',
            must_change_password=True,
        )
        self.client.force_login(attendant_user)

        response = self.client.post(
            reverse('change-initial-password'),
            {
                'new_password': 'SenhaNova123',
                'confirm_password': 'SenhaOutra123',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'As senhas digitadas nao conferem.')

    def test_initial_password_change_rejects_1234(self):
        attendant_user = User.objects.create_user(
            email='senha1234@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(
            user=attendant_user,
            name='Senha Inicial',
            phone='11999999999',
            must_change_password=True,
        )
        self.client.force_login(attendant_user)

        response = self.client.post(
            reverse('change-initial-password'),
            {
                'new_password': '1234',
                'confirm_password': '1234',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escolha uma senha diferente da senha inicial.')

    def test_valid_initial_password_change_unlocks_user(self):
        attendant_user = User.objects.create_user(
            email='trocasenha@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        attendant = Attendant.objects.create(
            user=attendant_user,
            name='Troca Senha',
            phone='11999999999',
            must_change_password=True,
        )
        self.client.force_login(attendant_user)

        response = self.client.post(
            reverse('change-initial-password'),
            {
                'new_password': 'SenhaNova123',
                'confirm_password': 'SenhaNova123',
            },
            follow=True,
        )

        self.assertRedirects(response, reverse('dashboard'))
        attendant.refresh_from_db()
        attendant_user.refresh_from_db()
        self.assertFalse(attendant.must_change_password)
        self.assertTrue(attendant_user.check_password('SenhaNova123'))
        self.client.logout()
        self.assertTrue(self.client.login(email='trocasenha@beezap.com', password='SenhaNova123'))
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_admin_without_attendant_profile_is_not_forced_to_change_password(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)


class PasswordRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='atendente@beezap.com',
            password='SenhaAntiga123',
            role=User.Role.USUARIO,
        )
        self.attendant = Attendant.objects.create(
            user=self.user,
            name='Atendente',
            phone='(11) 99999-9999',
            must_change_password=False,
        )

    @patch('accounts.views.secrets.randbelow', return_value=123456)
    @patch('accounts.views.send_text_message')
    def test_request_password_recovery_sends_code_without_exposing_it(self, mock_send, mock_randbelow):
        mock_send.return_value = SimpleNamespace(success=True)

        response = self.client.post(
            reverse('password-recovery-request'),
            {'email': 'atendente@beezap.com'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Se os dados estiverem corretos')
        self.assertNotContains(response, '123456')
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs['phone'], '11999999999')
        self.assertIn('123456', mock_send.call_args.kwargs['message'])
        reset_code = PasswordResetCode.objects.get(user=self.user)
        self.assertTrue(check_password('123456', reset_code.code_hash))
        self.assertNotEqual(reset_code.code_hash, '123456')
        self.assertEqual(self.client.session['password_recovery_code_id'], reset_code.id)

    @patch('accounts.views.send_text_message')
    def test_request_password_recovery_keeps_generic_message_for_unknown_email(self, mock_send):
        response = self.client.post(
            reverse('password-recovery-request'),
            {'email': 'naoexiste@beezap.com'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Se os dados estiverem corretos')
        self.assertNotContains(response, 'nao encontrado')
        mock_send.assert_not_called()
        self.assertFalse(PasswordResetCode.objects.exists())

    @patch('accounts.views.send_text_message')
    def test_request_password_recovery_keeps_generic_message_without_phone(self, mock_send):
        self.attendant.phone = ''
        self.attendant.save()

        response = self.client.post(
            reverse('password-recovery-request'),
            {'email': 'atendente@beezap.com'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Se os dados estiverem corretos')
        mock_send.assert_not_called()
        self.assertFalse(PasswordResetCode.objects.exists())

    @patch('accounts.views.secrets.randbelow', return_value=123456)
    @patch('accounts.views.send_text_message')
    def test_wrong_code_counts_attempts_and_blocks_after_limit(self, mock_send, mock_randbelow):
        mock_send.return_value = SimpleNamespace(success=True)
        self.client.post(reverse('password-recovery-request'), {'email': 'atendente@beezap.com'})
        reset_code = PasswordResetCode.objects.get(user=self.user)

        for _ in range(4):
            response = self.client.post(reverse('password-recovery-verify'), {'code': '000000'})
            self.assertContains(response, 'Codigo invalido ou expirado')

        response = self.client.post(reverse('password-recovery-verify'), {'code': '000000'})

        reset_code.refresh_from_db()
        self.assertContains(response, 'Muitas tentativas')
        self.assertEqual(reset_code.attempts, 5)
        self.assertIsNotNone(reset_code.used_at)

    @patch('accounts.views.secrets.randbelow', return_value=123456)
    @patch('accounts.views.send_text_message')
    def test_recovery_changes_password_after_valid_code(self, mock_send, mock_randbelow):
        mock_send.return_value = SimpleNamespace(success=True)
        self.client.post(reverse('password-recovery-request'), {'email': 'atendente@beezap.com'})
        verify_response = self.client.post(reverse('password-recovery-verify'), {'code': '123456'})

        self.assertEqual(verify_response.status_code, 200)
        self.assertContains(verify_response, 'Criar nova senha')

        response = self.client.post(
            reverse('password-recovery-set-password'),
            {
                'new_password': 'SenhaNova123',
                'confirm_password': 'SenhaNova123',
            },
            follow=True,
        )

        self.assertRedirects(response, reverse('login'))
        self.user.refresh_from_db()
        reset_code = PasswordResetCode.objects.get(user=self.user)
        self.assertTrue(self.user.check_password('SenhaNova123'))
        self.assertFalse(self.client.login(email='atendente@beezap.com', password='SenhaAntiga123'))
        self.assertTrue(self.client.login(email='atendente@beezap.com', password='SenhaNova123'))
        self.assertIsNotNone(reset_code.used_at)

    @patch('accounts.views.secrets.randbelow', return_value=123456)
    @patch('accounts.views.send_text_message')
    def test_recovery_rejects_mismatched_passwords(self, mock_send, mock_randbelow):
        mock_send.return_value = SimpleNamespace(success=True)
        self.client.post(reverse('password-recovery-request'), {'email': 'atendente@beezap.com'})
        self.client.post(reverse('password-recovery-verify'), {'code': '123456'})

        response = self.client.post(
            reverse('password-recovery-set-password'),
            {
                'new_password': 'SenhaNova123',
                'confirm_password': 'SenhaOutra123',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'As senhas digitadas nao conferem.')
