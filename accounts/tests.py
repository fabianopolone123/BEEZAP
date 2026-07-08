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


class AiIntentClassificationTests(TestCase):
    """Classificacao de intencao: palavra-chave (deterministica) + IA local."""

    def setUp(self):
        from .models import AutomationRule, Sector
        self.vendas = Sector.objects.create(name='Vendas', description='Compras e orcamentos')
        self.suporte = Sector.objects.create(name='Suporte', description='Problemas e ajuda tecnica')
        self.financeiro = Sector.objects.create(name='Financeiro', description='Boletos e pagamentos')
        AutomationRule.objects.create(
            title='Boletos', sector=self.financeiro, keywords='boleto, fatura, segunda via',
            response_text='ok',
        )

    def _classify(self, message):
        from ai_engine.services import classify_intent
        from .models import Sector
        return classify_intent(message, list(Sector.objects.all()))

    def test_keyword_layer_decides_without_llm(self):
        # Palavra-chave casa direto -> nem chama a IA.
        with patch('ai_engine.services.chat_with_ollama') as mock_llm:
            result = self._classify('preciso da segunda via do boleto')
        self.assertTrue(result.decided)
        self.assertEqual(result.sector, self.financeiro)
        self.assertEqual(result.source, 'keyword')
        mock_llm.assert_not_called()

    def test_llm_layer_decides_when_no_keyword(self):
        with patch('ai_engine.services.chat_with_ollama') as mock_llm:
            mock_llm.return_value = SimpleNamespace(success=True, content='Vendas')
            result = self._classify('gostaria de fazer uma compra')
        self.assertTrue(result.decided)
        self.assertEqual(result.sector, self.vendas)
        self.assertEqual(result.source, 'llm')

    def test_invalid_llm_output_is_undefined(self):
        with patch('ai_engine.services.chat_with_ollama') as mock_llm:
            mock_llm.return_value = SimpleNamespace(success=True, content='XPTO nao existe')
            result = self._classify('mensagem sem intencao clara')
        self.assertFalse(result.decided)
        self.assertEqual(result.source, 'undefined')

    def test_ambiguous_message_does_not_let_llm_guess_sector(self):
        with patch('ai_engine.services.chat_with_ollama') as mock_llm:
            mock_llm.return_value = SimpleNamespace(success=True, content='Financeiro')
            result = self._classify('na tenho certeza ta dando tudo errado')
        self.assertFalse(result.decided)
        self.assertEqual(result.source, 'undefined')
        mock_llm.assert_not_called()

    def test_llm_failure_is_undefined(self):
        with patch('ai_engine.services.chat_with_ollama') as mock_llm:
            mock_llm.return_value = SimpleNamespace(success=False, content='')
            result = self._classify('qualquer coisa')
        self.assertFalse(result.decided)
        self.assertEqual(result.source, 'undefined')


class AiAttendantFlowTests(TestCase):
    """Maquina de estados do atendente virtual (falas mockadas, sem rede)."""

    def setUp(self):
        from .models import AiAttendantConfig, Contact, Conversation, Sector
        self.vendas = Sector.objects.create(name='Vendas', description='Compras')
        self.geral = Sector.objects.create(name='Geral', description='Outros assuntos')
        self.config = AiAttendantConfig.get_solo()
        self.config.enabled = True
        self.config.max_turns = 2
        self.config.fallback_sector = self.geral
        self.config.save()
        self.contact = Contact.objects.create(name='Cliente', phone='5516999990000')
        self.conversation = Conversation.objects.create(
            contact=self.contact, external_id='5516999990000', chat_type='private',
            status='open', ai_state='active', ai_turns=0,
        )

    def _incoming(self, text='oi', message_type='text'):
        from .models import Message
        return Message.objects.create(
            conversation=self.conversation, direction='in', message_type=message_type,
            text=text, status='received',
        )

    def _handle(self, message):
        from ai_engine import attendant
        with patch.object(attendant, '_send_bot_message') as mock_send:
            attendant.handle_incoming_for_ai(self.conversation, message)
        return mock_send

    def test_first_message_sends_welcome(self):
        mock_send = self._handle(self._incoming('ola'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_turns, 1)
        self.assertEqual(self.conversation.ai_state, 'active')
        mock_send.assert_called_once()

    def test_clear_intent_routes_to_sector(self):
        self.conversation.ai_turns = 1
        self.conversation.save(update_fields=['ai_turns'])
        with patch('ai_engine.attendant.classify_intent') as mock_intent:
            from ai_engine.services import IntentResult
            mock_intent.return_value = IntentResult(sector=self.vendas, source='llm')
            self._handle(self._incoming('quero comprar'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sector, self.vendas)
        self.assertEqual(self.conversation.status, 'pending')
        self.assertEqual(self.conversation.ai_state, 'handed_off')

    def test_undefined_then_fallback_after_max_turns(self):
        from ai_engine.services import IntentResult
        # turno 1 -> ainda pergunta (indefinido, turns 1 < max 2)
        self.conversation.ai_turns = 1
        self.conversation.save(update_fields=['ai_turns'])
        with patch('ai_engine.attendant.classify_intent') as mock_intent:
            mock_intent.return_value = IntentResult(sector=None, source='undefined')
            self._handle(self._incoming('hmm'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_turns, 2)
        self.assertEqual(self.conversation.ai_state, 'active')
        # turno 2 -> atingiu max -> transfere para fallback
        with patch('ai_engine.attendant.classify_intent') as mock_intent:
            mock_intent.return_value = IntentResult(sector=None, source='undefined')
            self._handle(self._incoming('nao sei'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sector, self.geral)
        self.assertEqual(self.conversation.status, 'pending')
        self.assertEqual(self.conversation.ai_state, 'handed_off')

    def test_reception_uses_recent_customer_context_to_classify(self):
        self.conversation.ai_turns = 1
        self.conversation.save(update_fields=['ai_turns'])
        self._incoming('nao tenho certeza')
        message = self._incoming('preciso de boleto')

        with patch('ai_engine.attendant.classify_intent') as mock_intent:
            from ai_engine.services import IntentResult
            mock_intent.return_value = IntentResult(sector=self.vendas, source='llm')
            self._handle(message)

        context_used = mock_intent.call_args.args[0]
        self.assertIn('nao tenho certeza', context_used)
        self.assertIn('preciso de boleto', context_used)

    def test_greeting_after_clarification_gets_contextual_prompt(self):
        from ai_engine import attendant
        from ai_engine.services import IntentResult

        self.config.max_turns = 4
        self.config.save(update_fields=['max_turns'])
        self.conversation.ai_turns = 2
        self.conversation.save(update_fields=['ai_turns'])
        with patch('ai_engine.attendant.classify_intent') as mock_intent:
            mock_intent.return_value = IntentResult(sector=None, source='undefined')
            mock_send = self._handle(self._incoming('oi'))

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_turns, 3)
        mock_send.assert_called_once_with(
            self.conversation, attendant.CLARIFY_AFTER_GREETING_TEMPLATE,
        )

    def test_stops_when_human_took_over(self):
        from .models import Message
        self.conversation.ai_turns = 1
        self.conversation.save(update_fields=['ai_turns'])
        Message.objects.create(conversation=self.conversation, direction='out',
                               message_type='text', text='boas-vindas da IA', is_ai=True)
        # atendente respondeu depois da IA (mensagem out nao-IA)
        Message.objects.create(conversation=self.conversation, direction='out',
                               message_type='text', text='oi, eu assumo', is_ai=False)
        mock_send = self._handle(self._incoming('ainda ai?'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_state, 'off')
        mock_send.assert_not_called()

    def test_old_human_history_does_not_block_new_reception(self):
        from .models import Message
        Message.objects.create(conversation=self.conversation, direction='out',
                               message_type='text', text='mensagem antiga humana', is_ai=False)
        self.conversation.ai_state = 'off'
        self.conversation.ai_turns = 0
        self.conversation.save(update_fields=['ai_state', 'ai_turns'])

        mock_send = self._handle(self._incoming('oi'))

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_state, 'active')
        self.assertEqual(self.conversation.ai_turns, 1)
        mock_send.assert_called_once()

    def test_off_without_current_human_takeover_reactivates_even_with_previous_turns(self):
        self.conversation.ai_state = 'off'
        self.conversation.ai_turns = 1
        self.conversation.save(update_fields=['ai_state', 'ai_turns'])

        with patch('ai_engine.attendant.classify_intent') as mock_intent:
            from ai_engine.services import IntentResult
            mock_intent.return_value = IntentResult(sector=None, source='undefined')
            mock_send = self._handle(self._incoming('bom dia'))

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_state, 'active')
        self.assertEqual(self.conversation.ai_turns, 2)
        mock_send.assert_called_once()

    def test_skips_group_conversations(self):
        self.conversation.chat_type = 'group'
        self.conversation.save(update_fields=['chat_type'])
        mock_send = self._handle(self._incoming('oi grupo'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_turns, 0)
        mock_send.assert_not_called()

    def test_skips_when_disabled(self):
        self.config.enabled = False
        self.config.save(update_fields=['enabled'])
        mock_send = self._handle(self._incoming('ola'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_turns, 0)
        mock_send.assert_not_called()

    def test_skips_when_conversation_already_has_sector(self):
        self.conversation.sector = self.vendas
        self.conversation.save(update_fields=['sector'])
        mock_send = self._handle(self._incoming('ola'))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.ai_turns, 0)
        mock_send.assert_not_called()

    def test_async_trigger_guards_do_not_spawn(self):
        # Guardas baratas: nao dispara (retorna False) quando desligado, em grupo
        # ou para mensagem enviada — sem criar thread nem tocar a rede.
        from ai_engine.attendant import handle_incoming_for_ai_async
        self.config.enabled = False
        self.config.save(update_fields=['enabled'])
        self.assertFalse(handle_incoming_for_ai_async(self.conversation, self._incoming('oi')))
        self.config.enabled = True
        self.config.save(update_fields=['enabled'])
        self.conversation.chat_type = 'group'
        self.conversation.save(update_fields=['chat_type'])
        self.assertFalse(handle_incoming_for_ai_async(self.conversation, self._incoming('oi')))

    def test_ingest_triggers_ai_for_incoming_message(self):
        from wapi.services import save_incoming_message
        ctx = {
            'chat_id': '5516999990000', 'chat_type': 'private', 'is_group': False,
            'from_me': False, 'sender_name': 'Cliente', 'sender_id': '', 'participant_id': '',
        }
        with patch('ai_engine.attendant.handle_incoming_for_ai_async') as mock_async:
            save_incoming_message(self.conversation, ctx, message_type='text', text='oi',
                                  external_message_id='X1')
        mock_async.assert_called_once()


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
            ai_state='active',
        )

    def test_admin_transfer_to_sector_marks_pending_and_stops_ai(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('conversation-transfer', args=[self.conversation.id]),
            {'sector_id': str(self.sector.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sector, self.sector)
        self.assertEqual(self.conversation.status, 'pending')
        self.assertEqual(self.conversation.ai_state, 'off')
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
        self.assertEqual(self.conversation.ai_state, 'off')
        self.assertEqual(response.json()['contact']['attendant'], 'Atendente Vendas')

    def test_close_conversation_ends_current_service(self):
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
        self.assertEqual(self.conversation.ai_state, 'off')
        self.assertEqual(response.json()['contact']['status_label'], 'Encerrada')

    def test_incoming_after_closed_conversation_starts_new_ai_cycle(self):
        from wapi.services import resolve_conversation_for_context
        self.conversation.status = 'closed'
        self.conversation.sector = self.sector
        self.conversation.assigned_attendant = self.attendant
        self.conversation.ai_state = 'off'
        self.conversation.save(update_fields=['status', 'sector', 'assigned_attendant', 'ai_state'])

        new_conversation = resolve_conversation_for_context({
            'chat_id': self.contact.phone,
            'is_group': False,
            'sender_name': self.contact.name,
        })

        self.assertNotEqual(new_conversation.id, self.conversation.id)
        self.assertEqual(new_conversation.status, 'open')
        self.assertIsNone(new_conversation.sector)
        self.assertIsNone(new_conversation.assigned_attendant)
        self.assertEqual(new_conversation.ai_state, 'active')
        self.assertEqual(new_conversation.ai_turns, 0)


class AiAttendantSettingsViewTests(TestCase):
    """Tela de administracao do atendente virtual."""

    def setUp(self):
        self.admin = User.objects.create_user(email='adm@beezap.com', password='1234', role=User.Role.ADM)
        self.common = User.objects.create_user(email='user@beezap.com', password='1234', role=User.Role.USUARIO)

    def test_requires_admin(self):
        self.client.force_login(self.common)
        response = self.client.get(reverse('ai-attendant-settings'))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_open_and_save(self):
        from .models import AiAttendantConfig, Sector
        sector = Sector.objects.create(name='Vendas')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('ai-attendant-settings')).status_code, 200)
        response = self.client.post(reverse('ai-attendant-settings'), {
            'enabled': 'on',
            'company_name': 'Minha Empresa',
            'welcome_message': 'Ola, bem-vindo a {empresa}!',
            'fallback_sector': sector.id,
            'max_turns': 4,
        })
        self.assertEqual(response.status_code, 302)
        config = AiAttendantConfig.get_solo()
        self.assertTrue(config.enabled)
        self.assertEqual(config.company_name, 'Minha Empresa')
        self.assertEqual(config.fallback_sector, sector)
        self.assertEqual(config.max_turns, 4)
        self.assertIn('Minha Empresa', config.render_welcome())
