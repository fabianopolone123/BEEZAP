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


class MentionResolutionTests(SimpleTestCase):
    """@<numero> no texto do grupo deve virar @<nome> quando conhecemos a pessoa."""

    def test_resolves_known_mention(self):
        from accounts.views import _resolve_mentions
        text = '@140437377568773 coloca as fotos !!'
        out = _resolve_mentions(text, {'140437377568773': 'Juliane'})
        self.assertEqual(out, '@Juliane coloca as fotos !!')

    def test_keeps_unknown_mention(self):
        from accounts.views import _resolve_mentions
        text = '@140437377568773 oi'
        self.assertEqual(_resolve_mentions(text, {'999': 'X'}), '@140437377568773 oi')

    def test_no_mention_and_empty(self):
        from accounts.views import _resolve_mentions
        self.assertEqual(_resolve_mentions('sem mencao', {'1': 'a'}), 'sem mencao')
        self.assertEqual(_resolve_mentions('', {'1': 'a'}), '')
        self.assertEqual(_resolve_mentions('texto', None), 'texto')

    def test_multiple_mentions(self):
        from accounts.views import _resolve_mentions
        out = _resolve_mentions('@111111111 e @222222222 vejam',
                                {'111111111': 'Ana', '222222222': 'Bia'})
        self.assertEqual(out, '@Ana e @Bia vejam')


class ContactNamingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='adm@beezap.com', password='1234', role=User.Role.ADM)

    def _group_with_message(self, sender_id, sender_name='', text='oi'):
        from accounts.models import Conversation, Message
        conv = Conversation.objects.create(external_id='120363@g.us', chat_type='group', name='Grupo')
        Message.objects.create(conversation=conv, direction='in', message_type='text',
                               text=text, is_group=True, sender_id=sender_id, sender_name=sender_name)
        return conv

    def test_name_endpoint_creates_contact_and_resolves_mention(self):
        from accounts.views import _build_name_map, _resolve_mentions
        from accounts.models import Contact
        conv = self._group_with_message('5516993364676', '', '@140437377568773 vem')

        nm = _build_name_map(conv)
        self.assertEqual(_resolve_mentions('@140437377568773 vem', nm), '@140437377568773 vem')

        self.client.force_login(self.user)
        r = self.client.post(reverse('conversation-name-contact'),
                             {'number': '140437377568773', 'name': 'Juliane'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Contact.objects.filter(phone='140437377568773', name='Juliane').exists())

        nm2 = _build_name_map(conv)
        self.assertEqual(_resolve_mentions('@140437377568773 vem', nm2), '@Juliane vem')

    def test_contact_overrides_pushname(self):
        from accounts.views import _build_name_map
        from accounts.models import Contact
        conv = self._group_with_message('5516993364676', 'Ze')
        Contact.objects.create(phone='5516993364676', name='Jose Silva')
        self.assertEqual(_build_name_map(conv)['5516993364676'], 'Jose Silva')

    def test_name_endpoint_rejects_empty(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse('conversation-name-contact'), {'number': '', 'name': ''})
        self.assertEqual(r.status_code, 400)


class ContactsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='adm@beezap.com', password='1234', role=User.Role.ADM)
        self.client.force_login(self.user)

    def test_page_loads(self):
        r = self.client.get(reverse('contacts'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Contatos')

    def test_create_normalizes_phone(self):
        from accounts.models import Contact
        r = self.client.post(reverse('contacts'), {'name': 'Maria', 'phone': '+55 (16) 99999-8888'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Contact.objects.filter(name='Maria', phone='5516999998888').exists())

    def test_edit_contact(self):
        from accounts.models import Contact
        c = Contact.objects.create(name='Ana', phone='5516000000000')
        self.client.post(reverse('contacts'), {'contact_id': c.id, 'name': 'Ana Paula', 'phone': '5516111111111'})
        c.refresh_from_db()
        self.assertEqual(c.name, 'Ana Paula')
        self.assertEqual(c.phone, '5516111111111')

    def test_delete_contact(self):
        from accounts.models import Contact
        c = Contact.objects.create(name='X', phone='5516222222222')
        self.client.post(reverse('contacts'), {'action': 'delete', 'contact_id': c.id})
        self.assertFalse(Contact.objects.filter(pk=c.id).exists())

    def test_create_requires_name_and_phone(self):
        from accounts.models import Contact
        self.client.post(reverse('contacts'), {'name': '', 'phone': ''})
        self.assertEqual(Contact.objects.count(), 0)

    def test_search_filters(self):
        from accounts.models import Contact
        Contact.objects.create(name='Joao', phone='5516333333333')
        Contact.objects.create(name='Pedro', phone='5516444444444')
        r = self.client.get(reverse('contacts'), {'q': 'Joao'})
        self.assertContains(r, 'Joao')
        self.assertNotContains(r, 'Pedro')


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

    def test_normal_media_with_status_source_type_is_not_status(self):
        # Regressao (payload real): foto/video/GIF NORMAIS trazem statusSourceType
        # no contextInfo so para indicar que podem ser repostados como status.
        # Nao pode ser tratado como status, senao lotes de fotos somem do chat.
        photo = {
            'chat': {'id': '55525538541752@lid'},
            'sender': {'id': '393519098476', 'pushName': 'Lucas P'},
            'msgContent': {'imageMessage': {
                'mimetype': 'image/jpeg', 'mediaKey': 'K', 'directPath': '/d',
                'contextInfo': {'pairedMediaType': 'NOT_PAIRED_MEDIA',
                                'statusSourceType': 'IMAGE'},
            }},
        }
        self.assertFalse(is_status_or_broadcast(photo))
        gif = {'chat': {'id': '120363039427798532@g.us'},
               'msgContent': {'videoMessage': {'gifPlayback': True,
                                               'contextInfo': {'statusSourceType': 'GIF'}}}}
        self.assertFalse(is_status_or_broadcast(gif))

    def test_real_status_still_detected_without_source_type(self):
        # Status de verdade continua pego por chat.id == "status" e posterStatusID.
        status = {
            'chat': {'id': 'status'},
            'msgContent': {'videoMessage': {
                'contextInfo': {'posterStatusID': 'Xb+0mG5wAuzlU0nW8V2WhFc=',
                                'statusSourceType': 'VIDEO'}}},
        }
        self.assertTrue(is_status_or_broadcast(status))


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


class ConversationTransferViewTests(TestCase):
    """Transferencia manual pelo painel de Conversas."""

    def setUp(self):
        from .models import Contact, Conversation, Sector
        self.admin = User.objects.create_user(email='adm-transfer@beezap.com', password='1234', role=User.Role.ADM)
        self.attendant_user = User.objects.create_user(
            email='atendente-transfer@beezap.com', password='1234', role=User.Role.USUARIO,
        )
        self.attendant = Attendant.objects.create(
            user=self.attendant_user, name='Atendente Vendas', must_change_password=False,
        )
        self.sector = Sector.objects.create(name='Vendas')
        self.contact = Contact.objects.create(name='Cliente', phone='5516999990000')
        self.conversation = Conversation.objects.create(
            contact=self.contact,
            external_id='5516999990000',
            chat_type='private',
            status='open',
        )

    def test_admin_transfer_to_sector_marks_pending(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('conversation-transfer', args=[self.conversation.id]),
            {'sector_id': str(self.sector.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sector, self.sector)
        self.assertEqual(self.conversation.status, 'pending')
        data = response.json()
        self.assertEqual(data['contact']['sector'], 'Vendas')
        self.assertEqual(data['contact']['status_label'], 'Pendente')

    def test_admin_assign_attendant_marks_open_service(self):
        self.client.force_login(self.admin)
        self.conversation.sector = self.sector
        self.conversation.status = 'pending'
        self.conversation.save(update_fields=['sector', 'status'])

        response = self.client.post(
            reverse('conversation-transfer', args=[self.conversation.id]),
            {'attendant_id': str(self.attendant.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_attendant, self.attendant)
        self.assertEqual(self.conversation.status, 'open')
        data = response.json()
        self.assertEqual(data['contact']['attendant'], 'Atendente Vendas')
        self.assertEqual(data['contact']['status_label'], 'Aberta')

    def test_pending_sector_conversation_has_queue_label(self):
        from accounts.views import _serialize_conversation_item
        self.conversation.sector = self.sector
        self.conversation.status = 'pending'
        self.conversation.save(update_fields=['sector', 'status'])

        data = _serialize_conversation_item(self.conversation)

        self.assertEqual(data['queue_label'], 'Aguardando Vendas')
        self.assertEqual(data['sector'], 'Vendas')

    def test_attendant_can_take_conversation(self):
        self.client.force_login(self.attendant_user)

        response = self.client.post(reverse('conversation-take', args=[self.conversation.id]))

        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_attendant, self.attendant)
        self.assertEqual(self.conversation.status, 'open')
        self.assertEqual(response.json()['contact']['attendant'], 'Atendente Vendas')

    def test_close_conversation_inserts_divider_and_clears_service(self):
        from .models import Message
        self.client.force_login(self.admin)
        self.conversation.sector = self.sector
        self.conversation.assigned_attendant = self.attendant
        self.conversation.status = 'open'
        self.conversation.save(update_fields=['sector', 'assigned_attendant', 'status'])

        response = self.client.post(reverse('conversation-close', args=[self.conversation.id]))

        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, 'closed')
        self.assertIsNone(self.conversation.assigned_attendant)
        self.assertIsNone(self.conversation.sector)  # novo atendimento comeca do zero
        # Divisoria de "encerrado" inserida no chat (mensagem de sistema).
        divider = Message.objects.filter(conversation=self.conversation, message_type='system').last()
        self.assertIsNotNone(divider)
        self.assertIn('encerrado', divider.text.lower())

    def test_incoming_after_closed_reuses_same_conversation_with_divider(self):
        # Padrao WhatsApp: um unico chat por pessoa. Mensagem apos encerrar NAO cria
        # conversa nova — reusa a mesma, reabre e insere a divisoria de novo atendimento.
        from wapi.services import resolve_conversation_for_context
        from .models import Conversation, Message
        self.conversation.status = 'closed'
        self.conversation.save(update_fields=['status'])

        resolved = resolve_conversation_for_context({
            'chat_id': self.contact.phone,
            'is_group': False,
            'sender_name': self.contact.name,
        })

        self.assertEqual(resolved.id, self.conversation.id)   # MESMA conversa
        self.assertEqual(resolved.status, 'open')             # reaberta
        self.assertEqual(Conversation.objects.filter(contact=self.contact).count(), 1)
        divider = Message.objects.filter(conversation=resolved, message_type='system').last()
        self.assertIsNotNone(divider)
        self.assertIn('novo atendimento', divider.text.lower())

    def test_system_message_serializes_as_system_kind(self):
        from accounts.views import _serialize_message
        from wapi.services import save_system_message
        msg = save_system_message(self.conversation, 'Atendimento encerrado')
        data = _serialize_message(msg)
        self.assertEqual(data['kind'], 'system')
        self.assertEqual(data['text'], 'Atendimento encerrado')




class MergeContactConversationsTests(TestCase):
    """Comando que unifica conversas picotadas em um unico chat por contato."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import Contact, Conversation, Message
        now = timezone.now()
        self.contact = Contact.objects.create(name='Cliente', phone='5516999990000')
        # Conversa 1 (atendimento antigo, encerrado) com uma mensagem de "ontem".
        self.conv1 = Conversation.objects.create(
            contact=self.contact, external_id='5516999990000', chat_type='private', status='closed',
        )
        m1 = Message.objects.create(conversation=self.conv1, direction='in', message_type='text',
                                    text='primeira mensagem', status='received')
        # Conversa 2 (atendimento novo) com outra mensagem de "hoje".
        self.conv2 = Conversation.objects.create(
            contact=self.contact, external_id='5516999990000', chat_type='private', status='open',
        )
        m2 = Message.objects.create(conversation=self.conv2, direction='in', message_type='text',
                                    text='segunda mensagem', status='received')
        # Timestamps realistas (conversas separadas no tempo, como em producao).
        Conversation.objects.filter(pk=self.conv1.pk).update(created_at=now - timedelta(days=1))
        Message.objects.filter(pk=m1.pk).update(created_at=now - timedelta(days=1))
        Conversation.objects.filter(pk=self.conv2.pk).update(created_at=now)
        Message.objects.filter(pk=m2.pk).update(created_at=now)

    def test_dry_run_does_not_change(self):
        from django.core.management import call_command
        from .models import Conversation
        call_command('merge_contact_conversations')  # sem --apply
        self.assertEqual(Conversation.objects.filter(contact=self.contact).count(), 2)

    def test_apply_merges_into_single_chat_with_divider(self):
        from django.core.management import call_command
        from .models import Conversation, Message
        call_command('merge_contact_conversations', '--apply')

        # Sobra 1 conversa (a mais antiga, canonica) com todo o historico.
        convs = Conversation.objects.filter(contact=self.contact)
        self.assertEqual(convs.count(), 1)
        canonical = convs.first()
        self.assertEqual(canonical.id, self.conv1.id)
        self.assertEqual(canonical.status, 'open')  # estado do atendimento mais recente

        texts = list(
            Message.objects.filter(conversation=canonical).order_by('created_at', 'id')
            .values_list('message_type', 'text')
        )
        # Ordem: 1a msg, divisoria (system), 2a msg.
        self.assertEqual(texts[0], ('text', 'primeira mensagem'))
        self.assertEqual(texts[1][0], 'system')
        self.assertEqual(texts[2], ('text', 'segunda mensagem'))


class AiAttendantFlowTests(TestCase):
    """Atendente virtual (IA/GPT): recepcao, roteamento e fallback.

    O GPT e o envio pela W-API sao mockados — nenhum teste faz chamada externa."""

    def setUp(self):
        from accounts.models import Contact, Conversation, Message, OpenAiConfiguration, Sector

        self.Conversation = Conversation
        self.Message = Message
        self.Sector = Sector

        from accounts.models import MenuBotConfiguration
        self.MenuBotConfiguration = MenuBotConfiguration
        # A ativacao da IA vem do MODO mestre (mode == 'ai'), nao mais de enabled.
        menubot = MenuBotConfiguration.get_solo()
        menubot.mode = MenuBotConfiguration.MODE_AI
        menubot.save()

        self.config = OpenAiConfiguration.get_solo()
        self.config.api_key = 'sk-test'
        self.config.model = 'gpt-4.1-nano'
        self.config.max_turns = 3
        self.config.save()

        self.financeiro = Sector.objects.create(name='Financeiro')
        self.suporte = Sector.objects.create(name='Suporte')
        self.geral = Sector.objects.create(name='Geral')

        fab_user = User.objects.create_user(email='fab@beezap.local', password='x', role='usuario')
        self.fabiano = Attendant.objects.create(user=fab_user, name='Fabiano')
        self.fabiano.sectors.add(self.suporte)

        self.contact = Contact.objects.create(name='Cliente', phone='5516999990000')
        self.conv = Conversation.objects.create(
            contact=self.contact, external_id='5516999990000', chat_type='private', status='open',
        )
        Message.objects.create(conversation=self.conv, direction='in', message_type='text',
                               text='oi, preciso de ajuda')

    def _gpt(self, mensagem='', setor='', atendente=''):
        import json
        from gpt.client import GptResult
        payload = json.dumps({'mensagem': mensagem, 'setor': setor, 'atendente': atendente})
        return GptResult(success=True, text=payload, model='gpt-4.1-nano', total_tokens=10)

    def _run(self, gpt_result):
        from gpt.attendant import handle_incoming_for_ai
        send_ok = SimpleNamespace(success=True, message_id='wamid-1', error=None)
        with patch('gpt.client.chat_completion', return_value=gpt_result) as mock_gpt, \
             patch('wapi.client.send_text_message', return_value=send_ok) as mock_send:
            handle_incoming_for_ai(self.conv.id)
        return mock_gpt, mock_send

    def test_routes_to_sector(self):
        self._run(self._gpt(mensagem='Vou te transferir para o Financeiro.', setor='Financeiro'))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sector_id, self.financeiro.id)
        self.assertEqual(self.conv.status, 'pending')
        self.assertIsNone(self.conv.assigned_attendant_id)
        # Resposta da IA enviada e divisoria de encaminhamento criadas.
        self.assertTrue(self.Message.objects.filter(conversation=self.conv, direction='out', is_ai=True).exists())
        self.assertTrue(self.Message.objects.filter(conversation=self.conv, message_type='system',
                                                    text__icontains='Financeiro').exists())

    def test_routes_to_attendant(self):
        self._run(self._gpt(mensagem='Ja te encaminho pro Fabiano.', atendente='Fabiano'))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_attendant_id, self.fabiano.id)
        self.assertEqual(self.conv.sector_id, self.suporte.id)  # setor do atendente
        self.assertEqual(self.conv.status, 'open')

    def test_clarify_keeps_triage(self):
        self._run(self._gpt(mensagem='Pode me dar mais detalhes do que precisa?'))
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.sector_id)
        self.assertEqual(self.conv.ai_turns, 1)
        self.assertEqual(self.conv.status, 'open')
        self.assertTrue(self.Message.objects.filter(conversation=self.conv, direction='out', is_ai=True).exists())

    def test_fallback_after_max_turns(self):
        from gpt.attendant import HANDOFF_NOTICE
        self.config.fallback_sector = self.geral
        self.config.save()
        self.conv.ai_turns = 2  # com max_turns=3, o proximo turno sem decisao estoura
        self.conv.save(update_fields=['ai_turns'])
        _, mock_send = self._run(self._gpt(mensagem='Ainda nao entendi, pode explicar?'))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sector_id, self.geral.id)
        self.assertEqual(self.conv.status, 'pending')
        # SEMPRE avisa o cliente antes de transferir (nunca em silencio), e a mensagem
        # e o aviso de handoff (nao a pergunta de esclarecimento do GPT).
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], HANDOFF_NOTICE)
        last_out = self.Message.objects.filter(
            conversation=self.conv, direction='out', is_ai=True
        ).order_by('-created_at').first()
        self.assertEqual(last_out.text, HANDOFF_NOTICE)

    def test_fallback_without_sector_announces_and_waits(self):
        from gpt.attendant import HANDOFF_NOTICE
        self.config.fallback_sector = None  # sem fallback configurado
        self.config.save()
        self.geral.delete()  # e sem setor "Geral" (fallback automatico)
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        _, mock_send = self._run(self._gpt(mensagem='segue confuso'))
        self.conv.refresh_from_db()
        # Avisou o cliente, deixou aguardando sem setor e NAO fica re-tentando.
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], HANDOFF_NOTICE)
        self.assertIsNone(self.conv.sector_id)
        self.assertEqual(self.conv.status, 'pending')
        self.assertEqual(self.conv.ai_turns, self.config.max_turns)
        # Proxima mensagem: como nao ha fallback e ja avisou, a IA fica quieta.
        mock_gpt2, mock_send2 = self._run(self._gpt(mensagem='oi?'))
        mock_gpt2.assert_not_called()
        mock_send2.assert_not_called()

    def test_skips_group(self):
        self.conv.chat_type = 'group'
        self.conv.contact = None
        self.conv.save(update_fields=['chat_type', 'contact'])
        mock_gpt, _ = self._run(self._gpt(mensagem='x'))
        mock_gpt.assert_not_called()

    def test_skips_when_disabled(self):
        # Modo mestre desligado: a IA nao atua.
        menubot = self.MenuBotConfiguration.get_solo()
        menubot.mode = self.MenuBotConfiguration.MODE_OFF
        menubot.save()
        mock_gpt, _ = self._run(self._gpt(mensagem='x'))
        mock_gpt.assert_not_called()

    def test_skips_when_already_routed(self):
        self.conv.sector = self.financeiro
        self.conv.save(update_fields=['sector'])
        mock_gpt, _ = self._run(self._gpt(mensagem='x'))
        mock_gpt.assert_not_called()

    def test_skips_when_human_replied(self):
        self.Message.objects.create(conversation=self.conv, direction='out', message_type='text',
                                    text='oi, sou o atendente', is_ai=False)
        mock_gpt, _ = self._run(self._gpt(mensagem='x'))
        mock_gpt.assert_not_called()

    def test_skips_when_attendant_assigned(self):
        # Conversa em atendimento humano (atendente assumiu): a IA nao interfere.
        self.conv.assigned_attendant = self.fabiano
        self.conv.status = 'open'
        self.conv.save(update_fields=['assigned_attendant', 'status'])
        mock_gpt, mock_send = self._run(self._gpt(mensagem='deveria ficar quieta'))
        mock_gpt.assert_not_called()
        mock_send.assert_not_called()

    def test_skips_when_closed(self):
        # Atendimento encerrado: enquanto fechado, a IA nao responde.
        self.conv.status = 'closed'
        self.conv.save(update_fields=['status'])
        mock_gpt, _ = self._run(self._gpt(mensagem='x'))
        mock_gpt.assert_not_called()

    def test_time_since_previous_text(self):
        from datetime import timedelta
        from django.utils import timezone
        from gpt.attendant import _time_since_previous_text
        # So a mensagem atual -> primeira mensagem (apresente-se).
        self.assertIn('primeira mensagem', _time_since_previous_text(self.conv))
        # Mensagem anterior ha 2 dias -> a IA e avisada que vale reapresentar.
        old = self.conv.messages.first()
        self.Message.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        self.Message.objects.create(conversation=self.conv, direction='in',
                                    message_type='text', text='oi de novo')
        self.assertIn('dia(s)', _time_since_previous_text(self.conv))

    def test_records_last_exchange(self):
        import json as _json
        from accounts.models import OpenAiConfiguration
        from gpt import client as gpt_client

        class _FakeResp:
            status = 200
            headers = {}

            def __init__(self, body):
                self._body = body.encode('utf-8')

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        body = _json.dumps({
            'choices': [{'message': {'content': '{"mensagem":"oi","setor":"","atendente":""}'}}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8},
        })
        with patch.object(gpt_client.request, 'urlopen', return_value=_FakeResp(body)):
            result = gpt_client.chat_completion([{'role': 'user', 'content': 'ola tudo bem'}])
        self.assertTrue(result.success)
        cfg = OpenAiConfiguration.get_solo()
        # O request guardado contem a mensagem enviada; o response guardado, o corpo cru.
        self.assertIn('ola tudo bem', cfg.last_request)
        self.assertIn('mensagem', cfg.last_response)
        self.assertIsNotNone(cfg.last_exchange_at)

    def test_history_scoped_to_current_segment(self):
        # Mensagens antes da ultima divisoria (atendimento anterior) nao entram no contexto.
        from datetime import timedelta
        from django.utils import timezone
        from gpt.attendant import build_history
        from wapi.services import save_system_message
        old = self.conv.messages.first()  # 'oi, preciso de ajuda' (do setUp)
        divider = save_system_message(self.conv, 'Novo atendimento iniciado')  # divisoria
        nova = self.Message.objects.create(conversation=self.conv, direction='in',
                                           message_type='text', text='mensagem nova')
        # O relogio do Windows tem baixa resolucao (chamadas seguidas retornam o
        # mesmo instante), o que empataria created_at com a divisoria; garante que a
        # mensagem nova e posterior (em producao ela chega segundos depois).
        self.Message.objects.filter(pk=nova.pk).update(
            created_at=divider.created_at + timedelta(seconds=1))
        history = build_history(self.conv)
        texts = [h['content'] for h in history]
        self.assertIn('mensagem nova', texts)
        self.assertNotIn(old.text, texts)  # mensagem do atendimento anterior fica de fora

    def test_default_prompt_has_behavior_rules(self):
        # Com o prompt padrao (instructions vazio), as REGRAS DE COMPORTAMENTO ficam
        # no texto editavel (nao mais auto-injetadas).
        from gpt.attendant import build_system_prompt
        self.config.instructions = ''
        self.config.save()
        prompt = build_system_prompt(self.config).lower()
        self.assertIn('breve', prompt)
        self.assertIn('nao use apenas "ola"', prompt)
        self.assertIn('nunca invente', prompt)
        self.assertIn('setor geral', prompt)

    def test_system_prompt_auto_parts(self):
        # O sistema anexa sempre os DADOS DINAMICOS + formato JSON, mesmo com prompt custom.
        from gpt.attendant import build_system_prompt
        self.config.instructions = 'Prompt custom curtinho.'
        self.config.fallback_sector = self.geral
        self.config.save()
        prompt = build_system_prompt(self.config)
        self.assertIn('Prompt custom curtinho.', prompt)
        self.assertTrue(any(g in prompt for g in ('Bom dia', 'Boa tarde', 'Boa noite')))
        self.assertIn('Setores disponiveis', prompt)
        self.assertIn('Atendentes cadastrados', prompt)
        self.assertIn('Geral', prompt)  # setor geral/curinga (dado dinamico)
        self.assertIn('JSON', prompt)   # regra de formato (obrigatoria, automatica)


class MenuBotFlowTests(TestCase):
    """Chatbot de menu (sem IA): saudacao, escolha valida, opcao invalida, handoff.

    O envio pela W-API e mockado — nenhum teste faz chamada externa."""

    def setUp(self):
        from accounts.models import (
            Contact, Conversation, MenuBotConfiguration, MenuOption, Message, Sector,
        )

        self.Conversation = Conversation
        self.Message = Message
        self.MenuBotConfiguration = MenuBotConfiguration

        self.financeiro = Sector.objects.create(name='Financeiro')
        self.vendas = Sector.objects.create(name='Vendas')
        self.geral = Sector.objects.create(name='Geral')

        self.config = MenuBotConfiguration.get_solo()
        self.config.mode = MenuBotConfiguration.MODE_MENU
        self.config.max_attempts = 3
        self.config.fallback_sector = self.geral
        self.config.save()
        MenuOption.objects.create(config=self.config, order=1, label='Financeiro', sector=self.financeiro)
        MenuOption.objects.create(config=self.config, order=2, label='Vendas', sector=self.vendas)

        self.contact = Contact.objects.create(name='Cliente', phone='5516999990000')
        self.conv = Conversation.objects.create(
            contact=self.contact, external_id='5516999990000', chat_type='private', status='open',
        )

    def _incoming(self, text):
        return self.Message.objects.create(
            conversation=self.conv, direction='in', message_type='text', text=text,
        )

    def _run(self):
        from chatbot.handler import handle_incoming_for_menu
        send_ok = SimpleNamespace(success=True, message_id='wamid-1', error=None)
        with patch('wapi.client.send_text_message', return_value=send_ok) as mock_send:
            handle_incoming_for_menu(self.conv.id)
        return mock_send

    def test_first_contact_sends_menu(self):
        self._incoming('oi')
        self._run()
        # Uma mensagem automatica (menu) foi salva, sem encaminhar ainda.
        out = self.Message.objects.filter(conversation=self.conv, direction='out', is_ai=True)
        self.assertEqual(out.count(), 1)
        self.assertIn('1 - Financeiro', out.first().text)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.sector_id)

    def test_valid_option_routes_to_sector(self):
        # Menu ja apresentado, agora o cliente escolhe "1".
        self.Message.objects.create(conversation=self.conv, direction='out',
                                    message_type='text', text='menu...', is_ai=True)
        self._incoming('1')
        self._run()
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sector_id, self.financeiro.id)
        self.assertEqual(self.conv.status, 'pending')
        self.assertTrue(self.Message.objects.filter(
            conversation=self.conv, message_type='system', text__icontains='Financeiro').exists())

    def test_invalid_option_repeats_menu(self):
        self.Message.objects.create(conversation=self.conv, direction='out',
                                    message_type='text', text='menu...', is_ai=True)
        self._incoming('abc')
        self._run()
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.sector_id)
        self.assertEqual(self.conv.ai_turns, 1)

    def test_handoff_after_max_attempts(self):
        # Ja houve o menu + 2 tentativas invalidas (ai_turns=2); a 3a estoura o limite.
        self.Message.objects.create(conversation=self.conv, direction='out',
                                    message_type='text', text='menu...', is_ai=True)
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        self._incoming('xyz')
        self._run()
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sector_id, self.geral.id)  # fallback
        self.assertEqual(self.conv.status, 'pending')

    def test_skips_when_mode_not_menu(self):
        self.config.mode = self.MenuBotConfiguration.MODE_OFF
        self.config.save(update_fields=['mode'])
        self._incoming('oi')
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_skips_group(self):
        self.conv.chat_type = 'group'
        self.conv.contact = None
        self.conv.save(update_fields=['chat_type', 'contact'])
        self._incoming('oi')
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_skips_when_already_routed(self):
        self.conv.sector = self.financeiro
        self.conv.save(update_fields=['sector'])
        self._incoming('oi')
        mock_send = self._run()
        mock_send.assert_not_called()

    def test_skips_when_human_replied(self):
        self.Message.objects.create(conversation=self.conv, direction='out',
                                    message_type='text', text='sou o atendente', is_ai=False)
        self._incoming('1')
        mock_send = self._run()
        mock_send.assert_not_called()
