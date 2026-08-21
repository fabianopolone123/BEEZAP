import json as _json
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


def default_company():
    """Empresa cliente usada pelos testes.

    Com o multiempresa, setor/atendente/contato/conversa pertencem OBRIGATORIAMENTE
    a uma empresa, e os usuarios operacionais tambem. Os testes usam a empresa
    padrao (a mesma que a migration 0031 cria).
    """
    from .models import Company
    return Company.get_default()


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
        self.user = User.objects.create_user(company=default_company(), email='adm@beezap.com', password='1234', role=User.Role.ADM)

    def _group_with_message(self, sender_id, sender_name='', text='oi'):
        from accounts.models import Conversation, Message
        conv = Conversation.objects.create(company=default_company(), external_id='120363@g.us', chat_type='group', name='Grupo')
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
        Contact.objects.create(company=default_company(), phone='5516993364676', name='Jose Silva')
        self.assertEqual(_build_name_map(conv)['5516993364676'], 'Jose Silva')

    def test_name_endpoint_rejects_empty(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse('conversation-name-contact'), {'number': '', 'name': ''})
        self.assertEqual(r.status_code, 400)


class ContactDefaultsToNumberTests(TestCase):
    """A conversa nasce mostrando o NUMERO: nome nunca vem do WhatsApp (pushName).
    So aparece nome depois que alguem CADASTRA o contato (clicando no numero), e o
    cadastro cai na tela Contatos."""

    PHONE = '5516999997777'

    def setUp(self):
        self.user = User.objects.create_user(company=default_company(), email='adm@beezap.com', password='1234', role=User.Role.ADM)

    def _incoming_direct(self, push_name='Marcia Nunes', message_id='DIR1'):
        from wapi.services import ingest_wapi_payload
        return ingest_wapi_payload({
            'sender': {'id': self.PHONE, 'pushName': push_name},
            'chat': {'id': self.PHONE},
            'msgContent': {'conversation': 'ola'},
            'messageId': message_id,
        }, trigger_ai=False)

    def test_incoming_direct_message_creates_contact_without_name(self):
        from accounts.models import Contact

        msg = self._incoming_direct()

        self.assertIsNotNone(msg)
        contact = Contact.objects.get(phone=self.PHONE)
        self.assertEqual(contact.name, '')                        # nao herda o pushName
        self.assertEqual(contact.display_name, self.PHONE)        # exibe o numero
        self.assertEqual(msg.conversation.display_title, self.PHONE)
        self.assertEqual(msg.sender_name, 'Marcia Nunes')         # pushName segue guardado

    def test_pushname_never_overwrites_registered_name(self):
        from accounts.models import Contact
        Contact.objects.create(company=default_company(), phone=self.PHONE, name='Cliente Antigo')

        self._incoming_direct(push_name='Apelido do WhatsApp')

        self.assertEqual(Contact.objects.get(phone=self.PHONE).name, 'Cliente Antigo')

    def test_messages_endpoint_shows_number_and_marks_as_unnamed(self):
        msg = self._incoming_direct()
        self.client.force_login(self.user)

        r = self.client.get(reverse('conversation-messages', args=[msg.conversation_id]))

        contact = r.json()['contact']
        self.assertEqual(contact['name'], self.PHONE)   # cabecalho/lista mostram o numero
        self.assertEqual(contact['contact_name'], '')   # front sabe que falta cadastrar
        self.assertEqual(contact['phone'], self.PHONE)

    def test_naming_the_number_registers_contact_and_shows_name(self):
        from accounts.models import Contact
        msg = self._incoming_direct()
        self.client.force_login(self.user)

        r = self.client.post(reverse('conversation-name-contact'),
                             {'number': self.PHONE, 'name': 'Marcia'})
        self.assertEqual(r.status_code, 200)

        self.assertTrue(Contact.objects.filter(phone=self.PHONE, name='Marcia').exists())
        msg.conversation.refresh_from_db()
        self.assertEqual(msg.conversation.display_title, 'Marcia')
        # E o cadastro aparece na tela Contatos.
        self.assertContains(self.client.get(reverse('contacts')), 'Marcia')

    def test_group_sender_shows_number_until_registered(self):
        from accounts.views import _build_name_map
        from accounts.models import Contact, Conversation, Message
        conv = Conversation.objects.create(company=default_company(), external_id='120363@g.us', chat_type='group', name='Grupo')
        Message.objects.create(conversation=conv, direction='in', message_type='text',
                               text='oi', is_group=True, sender_id='5516993364676',
                               sender_name='Ze do WhatsApp')

        # Sem contato cadastrado: nenhum nome resolvido (o front mostra o numero).
        self.assertEqual(_build_name_map(conv), {})

        Contact.objects.create(company=default_company(), phone='5516993364676', name='Jose Silva')
        self.assertEqual(_build_name_map(conv)['5516993364676'], 'Jose Silva')


class DiscardedPayloadLeavesNoConversationTests(TestCase):
    """Payload descartado nao pode deixar CONVERSA VAZIA para tras.

    Regressao real (producao): a conversa era resolvida/criada ANTES dos descartes por
    tipo nao suportado / texto vazio, entao mensagem de sistema de grupo
    (`protocolMessage`), evento de participantes e `templateMessage` de empresa criavam
    conversas sem nenhuma mensagem (apareciam na lista com o JID/@lid cru no titulo)."""

    def _direct(self, content, message_id='DISC1'):
        return {
            'connectedPhone': '5514988208134',
            'isGroup': False,
            'messageId': message_id,
            'chat': {'id': '103445042315337@lid'},
            'sender': {'id': '5519971548270', 'pushName': 'Alguem'},
            'msgContent': content,
        }

    def _group(self, content, message_id='DISCG1'):
        return {
            'connectedPhone': '5514988208134',
            'isGroup': True,
            'messageId': message_id,
            'chat': {'id': '120363408906113722@g.us'},
            'sender': {'id': '5519971548270', 'pushName': 'Alguem'},
            'msgContent': content,
        }

    def test_template_message_creates_no_conversation(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Contact, Conversation

        payload = self._direct({
            'messageContextInfo': {'deviceListMetadataVersion': 2},
            'templateMessage': {'hydratedTemplate': {'hydratedContentText': 'Oferta!'}},
        })
        self.assertIsNone(ingest_wapi_payload(payload, trigger_ai=False))
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_group_system_message_creates_no_conversation(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation

        payload = self._group({
            'messageContextInfo': {'deviceListMetadataVersion': 2},
            'protocolMessage': {'type': 'REVOKE'},
        })
        # resolve_group_name NAO deve nem ser chamado (gastava requisicao na W-API).
        with patch('wapi.services.resolve_group_name') as resolve_name:
            self.assertIsNone(ingest_wapi_payload(payload, trigger_ai=False))
        resolve_name.assert_not_called()
        self.assertEqual(Conversation.objects.count(), 0)

    def test_empty_text_creates_no_conversation(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation

        self.assertIsNone(
            ingest_wapi_payload(self._direct({'conversation': ''}), trigger_ai=False)
        )
        self.assertEqual(Conversation.objects.count(), 0)

    def test_real_message_still_creates_conversation_and_message(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation, Message

        msg = ingest_wapi_payload(self._direct({'conversation': 'ola'}), trigger_ai=False)

        self.assertIsNotNone(msg)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(msg.text, 'ola')

    def test_media_still_arrives_with_metadata(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Message

        payload = self._direct({'imageMessage': {
            'mimetype': 'image/jpeg', 'mediaKey': 'K', 'directPath': '/d', 'caption': 'foto',
        }}, message_id='DISCIMG')
        with patch('wapi.services._try_download_media'):
            msg = ingest_wapi_payload(payload, trigger_ai=False)

        self.assertIsNotNone(msg)
        self.assertEqual(msg.message_type, 'image')
        self.assertEqual(msg.text, 'foto')
        self.assertEqual(msg.media_mimetype, 'image/jpeg')
        self.assertEqual(Message.objects.count(), 1)

    def test_system_event_does_not_reopen_closed_conversation(self):
        """Conversa FINALIZADA nao pode reabrir por causa de um evento de sistema."""
        from wapi.services import ingest_wapi_payload
        from accounts.models import Contact, Conversation

        contact = Contact.objects.create(company=default_company(), phone='5519971548270', name='')
        conv = Conversation.objects.create(company=default_company(), contact=contact, external_id='103445042315337@lid',
                                           chat_type='private', status='closed')

        payload = self._direct({'protocolMessage': {'type': 'REVOKE'}}, message_id='DISCSYS')
        self.assertIsNone(ingest_wapi_payload(payload, trigger_ai=False))

        conv.refresh_from_db()
        self.assertEqual(conv.status, 'closed')


class LidRealPhoneTests(TestCase):
    """W-API Lite entrega a conversa DIRETA chaveada por `@lid` (id interno), mas manda
    o TELEFONE real no remetente (`sender.id`). O contato tem de ser resolvido por esse
    telefone: a conversa aparece pelo NUMERO (nao pelo pushName) e a pessoa fica
    unificada com os grupos e a tela Contatos."""

    LID = '53094503153686@lid'
    PHONE = '5519971548270'
    OURS = '5514988208134'

    def _payload(self, message_id='LID1', from_me=False, sender_id=None, chat_id=None):
        return {
            'event': 'webhookReceived',
            'connectedPhone': self.OURS,
            'connectedLid': '253222916751588@lid',
            'isGroup': False,
            'messageId': message_id,
            'fromMe': from_me,
            'chat': {'id': chat_id or self.LID},
            'sender': {'id': sender_id or self.PHONE,
                       'senderLid': self.LID,
                       'pushName': 'elvisgoncalves123'},
            'msgContent': {'conversation': 'oi'},
        }

    def test_parser_exposes_real_phone_and_our_number(self):
        ctx = normalize_wapi_message_context(self._payload())
        self.assertFalse(ctx['is_group'])
        self.assertEqual(ctx['chat_id'], self.LID)        # conversa segue chaveada pelo @lid
        self.assertEqual(ctx['sender_phone'], self.PHONE)  # telefone REAL de quem enviou
        self.assertEqual(ctx['connected_phone'], self.OURS)

    def test_parser_has_no_phone_when_sender_is_only_a_lid(self):
        ctx = normalize_wapi_message_context({
            'chat': {'id': self.LID},
            'sender': {'id': self.LID, 'pushName': 'X'},
            'msgContent': {'conversation': 'oi'},
        })
        self.assertEqual(ctx['sender_phone'], '')

    def test_incoming_lid_message_links_contact_of_real_phone(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Contact

        msg = ingest_wapi_payload(self._payload(), trigger_ai=False)

        conv = msg.conversation
        self.assertEqual(conv.external_id, self.LID)   # chave de envio preservada
        self.assertEqual(conv.chat_type, 'private')
        self.assertIsNotNone(conv.contact_id)
        self.assertEqual(conv.contact.phone, self.PHONE)
        self.assertEqual(conv.contact.name, '')        # nasce sem nome
        self.assertEqual(conv.display_title, self.PHONE)  # aparece pelo NUMERO
        self.assertFalse(Contact.objects.filter(phone=self.OURS).exists())

    def test_naming_the_number_names_the_lid_conversation(self):
        from wapi.services import ingest_wapi_payload
        user = User.objects.create_user(company=default_company(), email='adm@beezap.com', password='1234', role=User.Role.ADM)
        msg = ingest_wapi_payload(self._payload(), trigger_ai=False)
        self.client.force_login(user)

        r = self.client.post(reverse('conversation-name-contact'),
                             {'number': self.PHONE, 'name': 'Elvis'})
        self.assertEqual(r.status_code, 200)

        msg.conversation.refresh_from_db()
        self.assertEqual(msg.conversation.display_title, 'Elvis')

    def test_contact_is_shared_with_group_messages(self):
        """Mesmo telefone = mesmo Contato: nomear pela direta nomeia no grupo tambem."""
        from wapi.services import ingest_wapi_payload
        from accounts.views import _build_name_map
        from accounts.models import Contact, Conversation, Message

        ingest_wapi_payload(self._payload(), trigger_ai=False)
        group = Conversation.objects.create(company=default_company(), external_id='120363@g.us', chat_type='group', name='Grupo')
        Message.objects.create(conversation=group, direction='in', message_type='text',
                               text='oi', is_group=True, sender_id=self.PHONE,
                               sender_name='elvisgoncalves123')

        self.assertEqual(_build_name_map(group), {})  # sem cadastro: numero no grupo

        Contact.objects.filter(phone=self.PHONE).update(name='Elvis')
        self.assertEqual(_build_name_map(group)[self.PHONE], 'Elvis')

    def test_our_own_outgoing_message_does_not_become_the_contact(self):
        """Em mensagem NOSSA (`fromMe`), `sender.id` e o numero da instancia — nunca
        pode virar o contato da conversa."""
        from wapi.services import ingest_wapi_payload
        from accounts.models import Contact

        msg = ingest_wapi_payload(
            self._payload(message_id='LIDME', from_me=True, sender_id=self.OURS),
            trigger_ai=False,
        )

        self.assertIsNone(msg.conversation.contact_id)
        self.assertFalse(Contact.objects.filter(phone=self.OURS).exists())

    def test_connected_phone_is_never_the_contact(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Contact

        # Remetente igual ao numero conectado (eco/anomalia): nao vira contato.
        msg = ingest_wapi_payload(
            self._payload(message_id='LIDECHO', sender_id=self.OURS), trigger_ai=False
        )

        self.assertIsNone(msg.conversation.contact_id)
        self.assertFalse(Contact.objects.filter(phone=self.OURS).exists())

    def test_group_participant_never_gets_private_contact(self):
        from wapi.services import ingest_wapi_payload
        from accounts.models import Contact, Conversation

        payload = self._payload(message_id='GRP1', chat_id='120363144038483540@g.us')
        payload['isGroup'] = True
        with patch('wapi.services.resolve_group_name', return_value='Grupo'):
            msg = ingest_wapi_payload(payload, trigger_ai=False)

        self.assertTrue(msg.conversation.is_group)
        self.assertIsNone(msg.conversation.contact_id)
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(Conversation.objects.filter(chat_type='private').count(), 0)

    def test_existing_lid_conversation_gets_contact_on_next_message(self):
        """Conversa antiga (sem contato) se resolve sozinha quando chega mensagem."""
        from wapi.services import ingest_wapi_payload
        from accounts.models import Conversation

        old = Conversation.objects.create(company=default_company(), external_id=self.LID, chat_type='private',
                                          name='elvisgoncalves123', contact=None)

        ingest_wapi_payload(self._payload(message_id='LID2'), trigger_ai=False)

        old.refresh_from_db()
        self.assertEqual(Conversation.objects.filter(external_id=self.LID).count(), 1)
        self.assertEqual(old.contact.phone, self.PHONE)
        self.assertEqual(old.display_title, self.PHONE)  # `name` (pushName) deixa de valer


class LinkLidContactsCommandTests(TestCase):
    """`link_lid_contacts`: resolve pelo historico o telefone das conversas `@lid` que
    ficaram sem contato (criadas antes do tratamento) e anexa o Contato."""

    LID = '53094503153686@lid'
    PHONE = '5519971548270'
    OURS = '5514988208134'

    def _old_conversation(self, with_messages=True):
        from accounts.models import Conversation, Message
        conv = Conversation.objects.create(company=default_company(), external_id=self.LID, chat_type='private',
                                           name='elvisgoncalves123', contact=None)
        if with_messages:
            Message.objects.create(conversation=conv, direction='in', message_type='text',
                                   text='oi', sender_id=self.PHONE, sender_name='elvis',
                                   raw_payload={'connectedPhone': self.OURS})
        return conv

    def _run(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('link_lid_contacts', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_lists_without_changing(self):
        conv = self._old_conversation()

        out = self._run()

        self.assertIn(self.PHONE, out)
        self.assertIn('Dry-run', out)
        conv.refresh_from_db()
        self.assertIsNone(conv.contact_id)

    def test_apply_links_contact_and_shows_number(self):
        conv = self._old_conversation()

        self._run('--apply')

        conv.refresh_from_db()
        self.assertEqual(conv.contact.phone, self.PHONE)
        self.assertEqual(conv.contact.name, '')
        self.assertEqual(conv.display_title, self.PHONE)

    def test_apply_reuses_contact_already_named_elsewhere(self):
        from accounts.models import Contact
        Contact.objects.create(company=default_company(), phone=self.PHONE, name='Elvis')  # nomeado num grupo
        conv = self._old_conversation()

        out = self._run('--apply')

        conv.refresh_from_db()
        self.assertEqual(conv.display_title, 'Elvis')   # nome cadastrado vale na direta
        self.assertEqual(Contact.objects.filter(phone=self.PHONE).count(), 1)
        self.assertIn('nome cadastrado', out)

    def test_conversation_without_history_is_left_alone(self):
        conv = self._old_conversation(with_messages=False)

        out = self._run('--apply')

        conv.refresh_from_db()
        self.assertIsNone(conv.contact_id)
        self.assertIn('sem telefone no historico', out)

    def test_nothing_to_do(self):
        out = self._run('--apply')
        self.assertIn('Nenhuma conversa direta sem contato', out)


class CleanupPushnameContactsCommandTests(TestCase):
    """`cleanup_pushname_contacts`: limpa o nome herdado do pushName (o contato volta
    a aparecer pelo numero) e PRESERVA nome cadastrado a mao."""

    def _incoming(self, phone, push_name, message_id):
        from accounts.models import Contact, Conversation, Message
        company = default_company()
        contact, _ = Contact.objects.get_or_create(
            company=company, phone=phone, defaults={'name': ''}
        )
        conv, _ = Conversation.objects.get_or_create(
            company=company, contact=contact,
            defaults={'external_id': phone, 'chat_type': 'private'},
        )
        Message.objects.create(conversation=conv, direction='in', message_type='text',
                               text='oi', sender_id=phone, sender_name=push_name,
                               external_message_id=message_id)
        return contact

    def _run(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('cleanup_pushname_contacts', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_lists_without_changing(self):
        contact = self._incoming('5516999990001', 'Marcia Nunes', 'M1')
        contact.name = 'Marcia Nunes'
        contact.save(update_fields=['name'])

        out = self._run()

        self.assertIn('5516999990001', out)
        self.assertIn('Dry-run', out)
        contact.refresh_from_db()
        self.assertEqual(contact.name, 'Marcia Nunes')

    def test_apply_clears_pushname_and_keeps_manual_name(self):
        from_push = self._incoming('5516999990001', 'Marcia Nunes', 'M1')
        from_push.name = 'Marcia Nunes'
        from_push.save(update_fields=['name'])
        # Mesmo numero com pushName no historico, mas nome DIGITADO diferente.
        manual = self._incoming('5516999990002', 'Ze do Zap', 'M2')
        manual.name = 'Jose da Silva (financeiro)'
        manual.save(update_fields=['name'])

        self._run('--apply')

        from_push.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(from_push.name, '')                            # volta ao numero
        self.assertEqual(manual.name, 'Jose da Silva (financeiro)')     # preservado

    def test_ignores_case_and_extra_spaces(self):
        contact = self._incoming('5516999990003', 'Marcia  Nunes', 'M3')
        contact.name = 'marcia nunes'
        contact.save(update_fields=['name'])

        self._run('--apply')

        contact.refresh_from_db()
        self.assertEqual(contact.name, '')

    def test_nothing_to_do(self):
        from accounts.models import Contact
        Contact.objects.create(company=default_company(), phone='5516999990004', name='So Cadastro')

        out = self._run('--apply')

        self.assertIn('Nenhum contato com nome vindo do WhatsApp', out)
        self.assertEqual(Contact.objects.get(phone='5516999990004').name, 'So Cadastro')


class ContactsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(company=default_company(), email='adm@beezap.com', password='1234', role=User.Role.ADM)
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
        c = Contact.objects.create(company=default_company(), name='Ana', phone='5516000000000')
        self.client.post(reverse('contacts'), {'contact_id': c.id, 'name': 'Ana Paula', 'phone': '5516111111111'})
        c.refresh_from_db()
        self.assertEqual(c.name, 'Ana Paula')
        self.assertEqual(c.phone, '5516111111111')

    def test_delete_contact(self):
        from accounts.models import Contact
        c = Contact.objects.create(company=default_company(), name='X', phone='5516222222222')
        self.client.post(reverse('contacts'), {'action': 'delete', 'contact_id': c.id})
        self.assertFalse(Contact.objects.filter(pk=c.id).exists())

    def test_create_requires_name_and_phone(self):
        from accounts.models import Contact
        self.client.post(reverse('contacts'), {'name': '', 'phone': ''})
        self.assertEqual(Contact.objects.count(), 0)

    def test_search_filters(self):
        from accounts.models import Contact
        Contact.objects.create(company=default_company(), name='Joao', phone='5516333333333')
        Contact.objects.create(company=default_company(), name='Pedro', phone='5516444444444')
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
        self.admin_user = User.objects.create_user(company=default_company(),
            email='admin@beezap.com',
            password='1234',
            role=User.Role.ADM,
        )
        self.common_user = User.objects.create_user(company=default_company(),
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
        attendant_user = User.objects.create_user(company=default_company(),
            email='joao@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        attendant = Attendant.objects.create(company=default_company(),
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
        existing_user = User.objects.create_user(company=default_company(),
            email='ana@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(company=default_company(),
            user=existing_user,
            name='Ana',
            phone='11999999999',
        )
        self.client.force_login(self.admin_user)
        count_before = Attendant.objects.count()

        response = self.client.post(
            reverse('attendants'),
            {
                'name': 'Ana Paula',
                'email': 'ana@beezap.com',
                'phone': '11911112222',
            },
        )

        self.assertEqual(response.status_code, 200)
        # O e-mail duplicado nao pode criar um novo atendente (o admin ja e atendente
        # automaticamente, entao comparamos com a contagem anterior).
        self.assertEqual(Attendant.objects.count(), count_before)
        self.assertEqual(Attendant.objects.filter(user__email='ana@beezap.com').count(), 1)
        self.assertContains(response, 'Ja existe um atendente com este e-mail.')

    def test_attendant_with_initial_password_is_redirected_to_change_password(self):
        attendant_user = User.objects.create_user(company=default_company(),
            email='primeiroacesso@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(company=default_company(),
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
        attendant_user = User.objects.create_user(company=default_company(),
            email='senhasdiferentes@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(company=default_company(),
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
        attendant_user = User.objects.create_user(company=default_company(),
            email='senha1234@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        Attendant.objects.create(company=default_company(),
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
        attendant_user = User.objects.create_user(company=default_company(),
            email='trocasenha@beezap.com',
            password='1234',
            role=User.Role.USUARIO,
        )
        attendant = Attendant.objects.create(company=default_company(),
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

        # O usuario comum nao tem Dashboard por padrao: apos trocar a senha ele cai
        # na primeira tela disponivel (Conversas).
        self.assertRedirects(response, reverse('conversations'))
        attendant.refresh_from_db()
        attendant_user.refresh_from_db()
        self.assertFalse(attendant.must_change_password)
        self.assertTrue(attendant_user.check_password('SenhaNova123'))
        self.client.logout()
        self.assertTrue(self.client.login(email='trocasenha@beezap.com', password='SenhaNova123'))
        response = self.client.get(reverse('conversations'))
        self.assertEqual(response.status_code, 200)

    def test_admin_without_attendant_profile_is_not_forced_to_change_password(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)


class PasswordRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(company=default_company(),
            email='atendente@beezap.com',
            password='SenhaAntiga123',
            role=User.Role.USUARIO,
        )
        self.attendant = Attendant.objects.create(company=default_company(),
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
        self.admin = User.objects.create_user(company=default_company(), email='adm-transfer@beezap.com', password='1234', role=User.Role.ADM)
        self.attendant_user = User.objects.create_user(company=default_company(),
            email='atendente-transfer@beezap.com', password='1234', role=User.Role.USUARIO,
        )
        self.attendant = Attendant.objects.create(company=default_company(),
            user=self.attendant_user, name='Atendente Vendas', must_change_password=False,
        )
        self.sector = Sector.objects.create(company=default_company(), name='Vendas')
        self.contact = Contact.objects.create(company=default_company(), name='Cliente', phone='5516999990000')
        self.conversation = Conversation.objects.create(company=default_company(),
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
        # O atendente so ve/assume conversas do setor dele (ou atribuidas a ele).
        self.attendant.sectors.add(self.sector)
        self.conversation.sector = self.sector
        self.conversation.status = 'pending'
        self.conversation.save(update_fields=['sector', 'status'])
        self.client.force_login(self.attendant_user)

        response = self.client.post(reverse('conversation-take', args=[self.conversation.id]))

        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_attendant, self.attendant)
        self.assertEqual(self.conversation.status, 'open')
        self.assertEqual(response.json()['contact']['attendant'], 'Atendente Vendas')

    def test_close_conversation_inserts_divider_and_keeps_attendant(self):
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
        # MANTEM o atendente (para ele ver nos Finalizados); so limpa o setor.
        self.assertEqual(self.conversation.assigned_attendant, self.attendant)
        self.assertIsNone(self.conversation.sector)
        # Divisoria de "encerrado" inserida no chat (mensagem de sistema).
        divider = Message.objects.filter(conversation=self.conversation, message_type='system').last()
        self.assertIsNotNone(divider)
        self.assertIn('encerrado', divider.text.lower())

    def test_incoming_after_closed_reuses_same_conversation_with_divider(self):
        # Padrao WhatsApp: um unico chat por pessoa. Mensagem apos encerrar NAO cria
        # conversa nova — reusa a mesma, reabre e insere a divisoria de novo atendimento.
        from wapi.services import resolve_conversation_for_context
        from .models import Conversation, Message
        # Fechada com atendente/setor do atendimento anterior.
        self.conversation.status = 'closed'
        self.conversation.assigned_attendant = self.attendant
        self.conversation.sector = self.sector
        self.conversation.save(update_fields=['status', 'assigned_attendant', 'sector'])

        resolved = resolve_conversation_for_context({
            'chat_id': self.contact.phone,
            'is_group': False,
            'sender_name': self.contact.name,
        }, default_company())

        self.assertEqual(resolved.id, self.conversation.id)   # MESMA conversa
        self.assertEqual(resolved.status, 'open')             # reaberta
        # A nova conversa volta para a recepcao/fila: sem dono e sem setor.
        self.assertIsNone(resolved.assigned_attendant_id)
        self.assertIsNone(resolved.sector_id)
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
        self.contact = Contact.objects.create(company=default_company(), name='Cliente', phone='5516999990000')
        # Conversa 1 (atendimento antigo, encerrado) com uma mensagem de "ontem".
        self.conv1 = Conversation.objects.create(company=default_company(),
            contact=self.contact, external_id='5516999990000', chat_type='private', status='closed',
        )
        m1 = Message.objects.create(conversation=self.conv1, direction='in', message_type='text',
                                    text='primeira mensagem', status='received')
        # Conversa 2 (atendimento novo) com outra mensagem de "hoje".
        self.conv2 = Conversation.objects.create(company=default_company(),
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

        self.financeiro = Sector.objects.create(company=default_company(), name='Financeiro')
        self.suporte = Sector.objects.create(company=default_company(), name='Suporte')
        # O setor 'Geral' ja existe (criado pela migracao 0028); reaproveita.
        self.geral, _ = Sector.objects.get_or_create(company=default_company(), name='Geral')

        fab_user = User.objects.create_user(company=default_company(), email='fab@beezap.local', password='x', role='usuario')
        self.fabiano = Attendant.objects.create(company=default_company(), user=fab_user, name='Fabiano')
        self.fabiano.sectors.add(self.suporte)

        self.contact = Contact.objects.create(company=default_company(), name='Cliente', phone='5516999990000')
        self.conv = Conversation.objects.create(company=default_company(),
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
        self.assertEqual(self.conv.status, 'pending')          # AGUARDANDO na fila do setor
        self.assertIsNone(self.conv.assigned_attendant_id)     # sem atribuir a ninguem
        # A IA responde ao cliente, mas NAO cria divisoria (o encaminhar e o mesmo
        # atendimento, para o atendente ver o historico com a IA ao assumir).
        self.assertTrue(self.Message.objects.filter(conversation=self.conv, direction='out', is_ai=True).exists())
        self.assertFalse(self.Message.objects.filter(conversation=self.conv, message_type='system').exists())

    def test_routes_to_attendant_goes_to_sector_queue(self):
        # Cliente citou um atendente: vai para o SETOR dele, AGUARDANDO (sem atribuir
        # a pessoa); a atribuicao acontece quando alguem clica em Assumir.
        self._run(self._gpt(mensagem='Ja te encaminho pro Fabiano.', atendente='Fabiano'))
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_attendant_id)
        self.assertEqual(self.conv.sector_id, self.suporte.id)  # setor do atendente citado
        self.assertEqual(self.conv.status, 'pending')
        self.assertFalse(self.Message.objects.filter(conversation=self.conv, message_type='system').exists())

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

    def test_handoff_creates_general_sector_when_no_fallback(self):
        # Sem fallback configurado E sem setor "Geral": o handoff deve CRIAR o "Geral"
        # e encaminhar a conversa para la (nunca deixar orfa/invisivel).
        from gpt.attendant import HANDOFF_NOTICE
        from accounts.models import Sector
        self.config.fallback_sector = None
        self.config.save()
        self.geral.delete()  # remove o "Geral" pre-existente
        self.assertFalse(Sector.objects.filter(name__iexact='Geral').exists())
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        _, mock_send = self._run(self._gpt(mensagem='segue confuso'))
        self.conv.refresh_from_db()
        # Avisou o cliente com o handoff e encaminhou para o "Geral" recem-criado.
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], HANDOFF_NOTICE)
        geral = Sector.objects.filter(name__iexact='Geral').first()
        self.assertIsNotNone(geral)                     # foi criado
        self.assertEqual(self.conv.sector_id, geral.id)  # conversa encaminhada
        self.assertEqual(self.conv.status, 'pending')
        self.assertIsNone(self.conv.assigned_attendant_id)

    def test_handoff_routes_to_existing_general_when_no_fallback(self):
        # Sem fallback configurado, mas com um "Geral" existente: encaminha para ele
        # (nao cria duplicado).
        from accounts.models import Sector
        self.config.fallback_sector = None
        self.config.save()
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        self._run(self._gpt(mensagem='segue confuso'))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sector_id, self.geral.id)
        self.assertEqual(Sector.objects.filter(name__iexact='Geral').count(), 1)

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
        prompt = build_system_prompt(self.config, default_company()).lower()
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
        prompt = build_system_prompt(self.config, default_company())
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

        self.financeiro = Sector.objects.create(company=default_company(), name='Financeiro')
        self.vendas = Sector.objects.create(company=default_company(), name='Vendas')
        # O setor 'Geral' ja existe (criado pela migracao 0028); reaproveita.
        self.geral, _ = Sector.objects.get_or_create(company=default_company(), name='Geral')

        self.config = MenuBotConfiguration.get_solo()
        self.config.mode = MenuBotConfiguration.MODE_MENU
        self.config.max_attempts = 3
        self.config.fallback_sector = self.geral
        self.config.save()
        MenuOption.objects.create(config=self.config, order=1, label='Financeiro', sector=self.financeiro)
        MenuOption.objects.create(config=self.config, order=2, label='Vendas', sector=self.vendas)

        self.contact = Contact.objects.create(company=default_company(), name='Cliente', phone='5516999990000')
        self.conv = Conversation.objects.create(company=default_company(),
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
        self.assertEqual(self.conv.status, 'pending')          # AGUARDANDO na fila do setor
        self.assertIsNone(self.conv.assigned_attendant_id)
        # Nao cria divisoria (o atendente que assumir ve o historico do menu).
        self.assertFalse(self.Message.objects.filter(
            conversation=self.conv, message_type='system').exists())

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

    def test_handoff_creates_general_when_no_fallback(self):
        # Sem fallback e sem "Geral": o handoff CRIA o "Geral" e encaminha (nao deixa orfa).
        from accounts.models import Sector
        self.config.fallback_sector = None
        self.config.save(update_fields=['fallback_sector'])
        self.geral.delete()
        self.Message.objects.create(conversation=self.conv, direction='out',
                                    message_type='text', text='menu...', is_ai=True)
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        self._incoming('xyz')
        self._run()
        self.conv.refresh_from_db()
        geral = Sector.objects.filter(name__iexact='Geral').first()
        self.assertIsNotNone(geral)
        self.assertEqual(self.conv.sector_id, geral.id)
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


class AdminAttendantTests(TestCase):
    """O administrador vira atendente automaticamente, em todos os setores, e
    consegue assumir atendimentos."""

    def setUp(self):
        from accounts.models import Sector
        self.Sector = Sector
        self.compras = Sector.objects.create(company=default_company(), name='Compras')  # setor antes do admin
        self.admin = User.objects.create_user(company=default_company(),
            email='adm@beezap.local', password='x', role=User.Role.ADM,
            first_name='Ze', last_name='Admin',
        )

    def test_admin_gets_attendant_in_all_sectors(self):
        att = getattr(self.admin, 'attendant_profile', None)
        self.assertIsNotNone(att)
        self.assertFalse(att.must_change_password)
        self.assertIn(self.compras, att.sectors.all())

    def test_new_sector_includes_admin(self):
        novo = self.Sector.objects.create(company=default_company(), name='Vendas')  # criado DEPOIS do admin
        self.assertIn(self.admin.attendant_profile, novo.attendants.all())

    def test_admin_can_take_conversation(self):
        from accounts.models import Contact, Conversation
        contact = Contact.objects.create(company=default_company(), name='Cliente', phone='5516999990000')
        conv = Conversation.objects.create(company=default_company(),
            contact=contact, external_id='5516999990000', chat_type='private',
            status='pending', sector=self.compras,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('conversation-take', args=[conv.id]))
        self.assertEqual(resp.status_code, 200)
        conv.refresh_from_db()
        self.assertEqual(conv.assigned_attendant_id, self.admin.attendant_profile.id)
        self.assertEqual(conv.status, 'open')


class GeneralSectorTests(TestCase):
    """Setor 'Geral' padrao: sempre existe, todos os atendentes fazem parte dele por
    padrao, e ele nao pode ser excluido nem renomeado."""

    def setUp(self):
        from accounts.models import Sector
        self.Sector = Sector
        self.admin = User.objects.create_user(company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.client.force_login(self.admin)

    def test_ensure_general_creates_and_adds_all_attendants(self):
        self.Sector.objects.filter(name__iexact='Geral').delete()
        user = User.objects.create_user(company=default_company(), email='joao@x.com', password='x', role=User.Role.USUARIO)
        att = Attendant.objects.create(company=default_company(), user=user, name='Joao', must_change_password=False)
        geral = self.Sector.ensure_general()
        self.assertTrue(geral.is_general)
        self.assertIn(att, geral.attendants.all())            # atendente ja existente entrou
        self.assertIn(self.admin.attendant_profile, geral.attendants.all())  # admin tambem

    def test_new_attendant_auto_joins_general(self):
        geral = self.Sector.ensure_general()
        user = User.objects.create_user(company=default_company(), email='ana@x.com', password='x', role=User.Role.USUARIO)
        att = Attendant.objects.create(company=default_company(), user=user, name='Ana', must_change_password=False)
        self.assertIn(att, geral.attendants.all())

    def test_general_cannot_be_deleted(self):
        geral = self.Sector.ensure_general()
        r = self.client.post(reverse('sectors'), {'action': 'delete', 'sector_id': str(geral.id)}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.Sector.objects.filter(pk=geral.id).exists())  # continua existindo
        msgs = [m.message for m in get_messages(r.wsgi_request)]
        self.assertTrue(any('não pode ser excluído' in m for m in msgs))

    def test_general_cannot_be_renamed(self):
        geral = self.Sector.ensure_general()
        self.client.post(reverse('sectors'), {
            'sector_id': str(geral.id), 'name': 'Outro Nome', 'description': 'nova desc',
        }, follow=True)
        geral.refresh_from_db()
        self.assertEqual(geral.name, 'Geral')                 # nome mantido
        self.assertEqual(geral.description, 'nova desc')      # descricao pode mudar

    def test_regular_sector_can_be_deleted(self):
        outro = self.Sector.objects.create(company=default_company(), name='Financeiro')
        self.client.post(reverse('sectors'), {'action': 'delete', 'sector_id': str(outro.id)}, follow=True)
        self.assertFalse(self.Sector.objects.filter(pk=outro.id).exists())


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


class DashboardTests(TestCase):
    """Dashboard com dados reais + comando de dados de demonstracao."""

    def test_seed_and_dashboard(self):
        from django.core.management import call_command
        from accounts.models import Conversation, Sector
        admin = User.objects.create_user(company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        call_command('seed_demo_data', verbosity=0)

        # 5 setores de demo + o 'Geral' padrao (sempre presente).
        self.assertEqual(Sector.objects.exclude(name__iexact='Geral').count(), 5)
        self.assertTrue(Sector.objects.filter(name__iexact='Geral').exists())
        self.assertEqual(Conversation.objects.count(), 36)
        self.assertEqual(Conversation.objects.filter(status='closed').count(), 18)
        self.assertEqual(Conversation.objects.exclude(status='closed').count(), 18)

        self.client.force_login(admin)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Atendimentos por setor')
        self.assertContains(resp, 'Atendimentos em andamento')
        # Os atalhos foram removidos do dashboard.
        self.assertNotContains(resp, 'Fila de atendimento')

    def test_dashboard_empty_ok(self):
        # Sem dados, o dashboard ainda renderiza (tempo medio placeholder, listas vazias).
        admin = User.objects.create_user(company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.client.force_login(admin)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Conversas ativas')


class ClosedConversationTests(TestCase):
    """Encerrar: vai para Finalizados, continua visivel ao atendente que fechou, e o
    historico do atendimento continua visivel (nao some com a divisoria de encerrado)."""

    def setUp(self):
        from accounts.models import Contact, Conversation, Message, Sector
        self.Conversation = Conversation
        self.u = User.objects.create_user(company=default_company(), email='ana@x.com', password='x', role=User.Role.USUARIO)
        self.att = Attendant.objects.create(company=default_company(), user=self.u, name='Ana', must_change_password=False)
        self.vendas = Sector.objects.create(company=default_company(), name='Vendas')
        self.att.sectors.add(self.vendas)
        ct = Contact.objects.create(company=default_company(), name='Cliente', phone='5516999990000')
        self.conv = Conversation.objects.create(company=default_company(),
            contact=ct, external_id='5516999990000', chat_type='private',
            status='open', sector=self.vendas, assigned_attendant=self.att)
        Message.objects.create(conversation=self.conv, direction='in', message_type='text',
                               text='oi preciso de ajuda')
        Message.objects.create(conversation=self.conv, direction='out', message_type='text',
                               text='claro, como posso ajudar', is_ai=False)

    def test_close_keeps_attendant_and_shows_in_finalizadas(self):
        self.client.force_login(self.u)
        r = self.client.post(reverse('conversation-close', args=[self.conv.id]))
        self.assertEqual(r.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, 'closed')
        self.assertEqual(self.conv.assigned_attendant, self.att)  # mantido
        lst = self.client.get(reverse('conversation-list') + '?status=finalizadas&tipo=todas').json()
        item = next((c for c in lst['conversations'] if c['id'] == self.conv.id), None)
        self.assertIsNotNone(item)          # a atendente ve seu finalizado
        self.assertFalse(item['mine'])      # finalizado NAO fica azul ("comigo")
        self.assertEqual(item['queue_label'], 'Finalizado')

    def test_attendant_sees_full_history_of_closed(self):
        self.client.force_login(self.u)
        self.client.post(reverse('conversation-close', args=[self.conv.id]))
        data = self.client.get(reverse('conversation-messages', args=[self.conv.id])).json()
        texts = [m['text'] for m in data['messages']]
        # Ve toda a conversa do atendimento, nao so a divisoria de "encerrado".
        self.assertIn('oi preciso de ajuda', texts)
        self.assertIn('claro, como posso ajudar', texts)
        self.assertEqual(data['contact']['status'], 'closed')


class GroupConversationTests(TestCase):
    """Grupos: nao entram em "aguardando", contact info marca is_group, e a mensagem
    enviada registra o atendente que mandou (para exibir no grupo)."""

    def setUp(self):
        from accounts.models import Contact, Conversation, GroupAccess, Sector
        self.Conversation = Conversation
        self.v = Sector.objects.create(company=default_company(), name='Vendas')
        self.u = User.objects.create_user(company=default_company(), email='ana@x.com', password='x', role=User.Role.USUARIO)
        self.ana = Attendant.objects.create(company=default_company(), user=self.u, name='Ana Souza', must_change_password=False)
        self.ana.sectors.add(self.v)
        self.group = Conversation.objects.create(company=default_company(),
            external_id='55@g.us', chat_type='group', name='Contas', status='open')
        acc = GroupAccess.objects.create(conversation=self.group)
        acc.sectors.add(self.v)
        ct = Contact.objects.create(company=default_company(), name='C', phone='5511111111111')
        self.direct = Conversation.objects.create(company=default_company(),
            contact=ct, external_id='5511111111111', chat_type='private',
            status='pending', sector=self.v)

    def test_group_not_in_aguardando(self):
        self.client.force_login(self.u)
        lst = self.client.get(reverse('conversation-list') + '?status=todas&tipo=todas').json()
        # So a direta conta como aguardando; o grupo nao.
        self.assertEqual(lst['counts']['aguardando'], 1)
        ag = self.client.get(reverse('conversation-list') + '?status=aguardando&tipo=todas').json()
        ids = [c['id'] for c in ag['conversations']]
        self.assertIn(self.direct.id, ids)
        self.assertNotIn(self.group.id, ids)

    def test_group_contact_info_is_group(self):
        self.client.force_login(self.u)
        data = self.client.get(reverse('conversation-messages', args=[self.group.id])).json()
        self.assertTrue(data['contact']['is_group'])

    def test_outgoing_group_message_records_sender_name(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from accounts.models import WapiConfiguration, Message
        cfg = WapiConfiguration.get_solo()
        cfg.instance_id = 'i'; cfg.token = 't'; cfg.save()
        self.client.force_login(self.u)
        send_ok = SimpleNamespace(success=True, message_id='w1', error=None)
        with patch('accounts.views.send_text_message', return_value=send_ok) as mock_send:
            r = self.client.post(reverse('conversation-send', args=[self.group.id]),
                                 {'text': 'ola pessoal'})
        self.assertEqual(r.status_code, 200)
        # O que foi ENVIADO ao WhatsApp leva o nome do atendente no corpo (grupo).
        sent = mock_send.call_args.kwargs.get('message')
        self.assertIn('Ana Souza', sent)
        self.assertIn('ola pessoal', sent)
        # No nosso chat, guardamos o texto SEM o prefixo (nome aparece acima do balao).
        msg = Message.objects.filter(conversation=self.group, direction='out').last()
        self.assertEqual(msg.text, 'ola pessoal')
        self.assertEqual(msg.sender_name, 'Ana Souza')          # atendente que enviou
        self.assertEqual(r.json()['message']['sender_name'], 'Ana Souza')


class MultiCompanyModelTests(TestCase):
    """MULTIEMPRESA: cada empresa cliente tem os SEUS dados e as SUAS configuracoes.

    O que estes testes garantem: duas empresas podem ter setor com o mesmo nome e
    contato com o mesmo telefone (as unicidades passaram a ser POR EMPRESA), e as
    configuracoes de W-API/GPT/chatbot sao independentes entre elas.
    """

    def setUp(self):
        from accounts.models import Company
        self.a = Company.get_default()
        self.b = Company.objects.create(name='Padaria do Bairro', slug='padaria-do-bairro')

    def test_same_sector_name_in_two_companies(self):
        from accounts.models import Sector
        Sector.objects.create(company=self.a, name='Financeiro')
        Sector.objects.create(company=self.b, name='Financeiro')
        self.assertEqual(Sector.objects.filter(name='Financeiro').count(), 2)

    def test_duplicated_sector_name_in_same_company_is_blocked(self):
        from django.db import IntegrityError
        from accounts.models import Sector
        Sector.objects.create(company=self.a, name='Suporte')
        with self.assertRaises(IntegrityError):
            Sector.objects.create(company=self.a, name='Suporte')

    def test_same_phone_in_two_companies(self):
        """O mesmo cliente final pode falar com duas empresas; cada uma tem o seu
        proprio cadastro (e o nome de uma nao aparece na outra)."""
        from accounts.models import Contact
        Contact.objects.create(company=self.a, phone='5516999990001', name='Marcia (A)')
        Contact.objects.create(company=self.b, phone='5516999990001', name='Marcia (B)')
        self.assertEqual(Contact.objects.filter(phone='5516999990001').count(), 2)

    def test_duplicated_phone_in_same_company_is_blocked(self):
        from django.db import IntegrityError
        from accounts.models import Contact
        Contact.objects.create(company=self.a, phone='5516999990002')
        with self.assertRaises(IntegrityError):
            Contact.objects.create(company=self.a, phone='5516999990002')

    def test_wapi_and_chatbot_are_per_company(self):
        """W-API (instancia/token) e chatbot de menu sao POR EMPRESA."""
        from accounts.models import MenuBotConfiguration, WapiConfiguration
        wapi_a = WapiConfiguration.for_company(self.a)
        wapi_b = WapiConfiguration.for_company(self.b)
        wapi_a.instance_id = 'INSTANCIA-A'
        wapi_a.save(update_fields=['instance_id'])
        wapi_b.instance_id = 'INSTANCIA-B'
        wapi_b.save(update_fields=['instance_id'])

        self.assertNotEqual(wapi_a.pk, wapi_b.pk)
        self.assertEqual(WapiConfiguration.for_company(self.a).instance_id, 'INSTANCIA-A')
        self.assertEqual(WapiConfiguration.for_company(self.b).instance_id, 'INSTANCIA-B')
        # get_solo() continua apontando para a empresa padrao (compatibilidade).
        self.assertEqual(WapiConfiguration.get_solo().pk, wapi_a.pk)

        MenuBotConfiguration.for_company(self.b).save()
        self.assertNotEqual(
            MenuBotConfiguration.for_company(self.a).pk,
            MenuBotConfiguration.for_company(self.b).pk,
        )

    def test_gpt_is_a_single_platform_configuration(self):
        """O GPT NAO e por empresa: existe UMA configuracao (a API Key e do gestor
        master, que paga a conta da OpenAI). Cada empresa so decide SE usa IA."""
        from accounts.models import OpenAiConfiguration
        cfg1 = OpenAiConfiguration.get_solo()
        cfg2 = OpenAiConfiguration.get_solo()
        self.assertEqual(cfg1.pk, cfg2.pk)
        self.assertEqual(OpenAiConfiguration.objects.count(), 1)
        # O model nao tem mais vinculo com empresa nem setor de fallback.
        field_names = {f.name for f in OpenAiConfiguration._meta.get_fields()}
        self.assertNotIn('company', field_names)
        self.assertNotIn('fallback_sector', field_names)

    def test_token_usage_is_counted_for_the_platform(self):
        """O consumo e acumulado da plataforma (quebrar por cliente e Parte 4)."""
        from accounts.models import OpenAiConfiguration
        cfg = OpenAiConfiguration.get_solo()
        cfg.record_usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        cfg.refresh_from_db()
        self.assertEqual(cfg.total_tokens, 15)
        self.assertEqual(cfg.total_requests, 1)

    def test_general_sector_exists_per_company(self):
        from accounts.models import Sector
        geral_a = Sector.ensure_general(self.a)
        geral_b = Sector.ensure_general(self.b)
        self.assertNotEqual(geral_a.pk, geral_b.pk)
        self.assertEqual(geral_a.company, self.a)
        self.assertEqual(geral_b.company, self.b)
        # Chamar de novo nao duplica.
        self.assertEqual(Sector.ensure_general(self.b).pk, geral_b.pk)

    def test_new_attendant_joins_general_of_own_company(self):
        """O sinal coloca o atendente novo no Geral DA EMPRESA dele, nao no do outro."""
        from accounts.models import Attendant, Sector
        Sector.ensure_general(self.a)
        geral_b = Sector.ensure_general(self.b)
        user = User.objects.create_user(
            email='ana@padaria.com', password='x', role=User.Role.USUARIO, company=self.b
        )
        attendant = Attendant.objects.create(company=self.b, user=user, name='Ana')
        self.assertIn(geral_b, list(attendant.sectors.all()))
        self.assertEqual(attendant.sectors.count(), 1)

    def test_default_company_helpers(self):
        from accounts.models import Company
        self.assertTrue(self.a.is_default)
        self.assertEqual(Company.get_default().pk, self.a.pk)
        self.assertFalse(self.b.is_default)


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


class ClientsScreenTests(TestCase):
    """Tela CLIENTES (cadastro das empresas pelo gestor master)."""

    def setUp(self):
        from accounts.models import Company
        self.default_company = Company.get_default()
        self.master = User.objects.create_user(
            email='master@beezap.com', password='x', role=User.Role.MASTER
        )
        self.client.force_login(self.master)

    def test_page_loads(self):
        r = self.client.get(reverse('clients'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Clientes')

    def test_create_company_generates_slug_and_keeps_only_digits(self):
        from accounts.models import Company
        r = self.client.post(reverse('clients'), {
            'name': 'Padaria do Bairro',
            'document': '12.345.678/0001-95',
            'phone': '(16) 99999-0001',
            'state': 'sp',
            'slug': '',
            'is_active': 'on',
        })
        self.assertRedirects(r, reverse('clients'))
        company = Company.objects.get(name='Padaria do Bairro')
        self.assertEqual(company.slug, 'padaria-do-bairro')
        self.assertEqual(company.document, '12345678000195')     # so digitos
        self.assertEqual(company.formatted_document, '12.345.678/0001-95')
        self.assertEqual(company.phone, '16999990001')
        self.assertEqual(company.state, 'SP')                    # UF em maiuscula

    def test_invalid_document_is_rejected(self):
        from accounts.models import Company
        r = self.client.post(reverse('clients'), {
            'name': 'Empresa Teste', 'document': '123', 'is_active': 'on',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'O CNPJ deve ter 14 números.')
        self.assertFalse(Company.objects.filter(name='Empresa Teste').exists())

    def test_duplicated_slug_is_rejected(self):
        from accounts.models import Company
        Company.objects.create(name='Ja Existe', slug='padaria')
        r = self.client.post(reverse('clients'), {
            'name': 'Outra', 'slug': 'padaria', 'is_active': 'on',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Já existe uma empresa com este identificador.')

    def test_edit_company(self):
        from accounts.models import Company
        company = Company.objects.create(name='Antigo Nome', slug='antigo-nome')
        r = self.client.post(reverse('clients'), {
            'company_id': company.pk,
            'name': 'Nome Novo',
            'slug': 'antigo-nome',
            'is_active': 'on',
        })
        self.assertRedirects(r, reverse('clients'))
        company.refresh_from_db()
        self.assertEqual(company.name, 'Nome Novo')

    def test_default_company_cannot_be_deleted(self):
        from accounts.models import Company
        r = self.client.post(reverse('clients'), {
            'action': 'delete', 'company_id': self.default_company.pk,
        })
        self.assertRedirects(r, reverse('clients'))
        self.assertTrue(Company.objects.filter(pk=self.default_company.pk).exists())
        self.assertIn(
            'A empresa padrão não pode ser excluída.',
            [str(m) for m in get_messages(r.wsgi_request)],
        )

    def test_default_company_cannot_be_deactivated(self):
        r = self.client.post(reverse('clients'), {
            'action': 'toggle-active', 'company_id': self.default_company.pk,
        })
        self.assertRedirects(r, reverse('clients'))
        self.default_company.refresh_from_db()
        self.assertTrue(self.default_company.is_active)

    def test_other_company_can_be_deactivated_and_reactivated(self):
        from accounts.models import Company
        company = Company.objects.create(name='Cliente X', slug='cliente-x')
        self.client.post(reverse('clients'), {
            'action': 'toggle-active', 'company_id': company.pk,
        })
        company.refresh_from_db()
        self.assertFalse(company.is_active)
        self.client.post(reverse('clients'), {
            'action': 'toggle-active', 'company_id': company.pk,
        })
        company.refresh_from_db()
        self.assertTrue(company.is_active)

    def test_other_company_can_be_deleted(self):
        """Excluir exige as travas do encerramento seguro: desativada + nome digitado
        (ver SafeCompanyDeletionTests)."""
        from accounts.models import Company
        company = Company.objects.create(name='Cliente Y', slug='cliente-y', is_active=False)
        self.client.post(reverse('clients'), {
            'action': 'delete', 'company_id': company.pk, 'confirm_name': 'Cliente Y',
        })
        self.assertFalse(Company.objects.filter(pk=company.pk).exists())

    def test_search_filters_by_name(self):
        from accounts.models import Company
        Company.objects.create(name='Padaria Central', slug='padaria-central')
        Company.objects.create(name='Oficina Rapida', slug='oficina-rapida')
        r = self.client.get(reverse('clients'), {'q': 'Padaria'})
        self.assertContains(r, 'Padaria Central')
        self.assertNotContains(r, 'Oficina Rapida')


class CompanyBrandingTests(TestCase):
    """A barra lateral mostra a marca DA EMPRESA de quem esta logado."""

    def setUp(self):
        from accounts.models import Company
        self.company = Company.objects.create(
            name='Padaria do Bairro', slug='padaria-do-bairro', accent_color='#ff8800'
        )
        self.user = User.objects.create_user(
            email='adm@padaria.com', password='x', role=User.Role.ADM, company=self.company
        )

    def test_sidebar_shows_company_name_and_initials(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('contacts'))
        self.assertContains(r, 'Padaria do Bairro')
        self.assertContains(r, 'PB')            # iniciais (sem logo cadastrado)
        self.assertContains(r, '#ff8800')       # cor de destaque da empresa

    def test_master_sidebar_shows_platform_brand(self):
        master = User.objects.create_user(
            email='master@beezap.com', password='x', role=User.Role.MASTER
        )
        self.client.force_login(master)
        r = self.client.get(reverse('clients'))
        self.assertContains(r, 'BEEonBOARD')
        self.assertContains(r, 'Gestão de clientes')

    def test_company_initials_and_location(self):
        from accounts.models import Company
        self.company.city = 'Ribeirão Preto'
        self.company.state = 'SP'
        self.company.save()
        self.assertEqual(self.company.initials, 'PB')
        self.assertEqual(self.company.location, 'Ribeirão Preto/SP')
        self.assertEqual(Company(name='Sidertec', slug='s').initials, 'SI')


class WebhookCompanyRoutingTests(TestCase):
    """Webhook por cliente: de quem e a mensagem que acabou de chegar?

    Tres degraus (ver wapi.services.resolve_webhook_company): identificador na URL,
    `instanceId` do payload e, por ultimo, a empresa padrao.
    """

    def setUp(self):
        from accounts.models import Company, WapiConfiguration
        self.default_company = Company.get_default()
        self.acme = Company.objects.create(name='Acme', slug='acme')
        cfg = WapiConfiguration.for_company(self.acme)
        cfg.instance_id = 'INSTANCIA-ACME'
        cfg.save(update_fields=['instance_id'])

    def _payload(self, text='ola', instance_id=None, phone='5516999990001'):
        payload = {
            'messageId': f'MSG-{text}-{phone}',
            'sender': {'id': phone, 'pushName': 'Cliente'},
            'chat': {'id': phone},
            'msgContent': {'conversation': text},
        }
        if instance_id:
            payload['instanceId'] = instance_id
        return payload

    def test_url_with_slug_routes_to_that_company(self):
        from wapi.services import resolve_webhook_company
        self.assertEqual(resolve_webhook_company('acme', {}).pk, self.acme.pk)

    def test_unknown_slug_is_refused(self):
        from wapi.services import resolve_webhook_company
        self.assertIsNone(resolve_webhook_company('nao-existe', {}))

    def test_inactive_company_is_refused(self):
        from wapi.services import resolve_webhook_company
        self.acme.is_active = False
        self.acme.save(update_fields=['is_active'])
        self.assertIsNone(resolve_webhook_company('acme', {}))

    def test_instance_id_resolves_company_without_slug(self):
        """URL antiga (sem identificador): a empresa vem do instanceId do payload."""
        from wapi.services import resolve_webhook_company
        company = resolve_webhook_company('', self._payload(instance_id='INSTANCIA-ACME'))
        self.assertEqual(company.pk, self.acme.pk)

    def test_unknown_instance_falls_back_to_default(self):
        from wapi.services import resolve_webhook_company
        company = resolve_webhook_company('', self._payload(instance_id='NAO-CADASTRADA'))
        self.assertEqual(company.pk, self.default_company.pk)

    def test_no_slug_no_instance_falls_back_to_default(self):
        from wapi.services import resolve_webhook_company
        self.assertEqual(resolve_webhook_company('', {}).pk, self.default_company.pk)

    def test_webhook_url_creates_conversation_in_that_company(self):
        from accounts.models import Conversation
        with patch('wapi.services._try_download_media'):
            r = self.client.post(
                reverse('wapi-webhook-company', args=['acme']),
                data=_json.dumps(self._payload(text='ola acme')),
                content_type='application/json',
            )
        self.assertEqual(r.status_code, 200)
        conv = Conversation.objects.get(company=self.acme)
        self.assertEqual(conv.messages.first().text, 'ola acme')
        # Nada foi criado na outra empresa.
        self.assertFalse(Conversation.objects.filter(company=self.default_company).exists())

    def test_webhook_of_unknown_company_creates_nothing(self):
        from accounts.models import Conversation, WapiWebhookEvent
        r = self.client.post(
            reverse('wapi-webhook-company', args=['nao-existe']),
            data=_json.dumps(self._payload()),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 404)
        self.assertFalse(WapiWebhookEvent.objects.exists())
        self.assertFalse(Conversation.objects.exists())

    def test_same_number_talking_to_two_companies_stays_separate(self):
        """O MESMO cliente final falando com duas empresas gera dois chats."""
        from accounts.models import Conversation
        with patch('wapi.services._try_download_media'):
            self.client.post(
                reverse('wapi-webhook-company', args=['acme']),
                data=_json.dumps(self._payload(text='oi acme')),
                content_type='application/json',
            )
            self.client.post(
                reverse('wapi-webhook-company', args=[self.default_company.slug]),
                data=_json.dumps(self._payload(text='oi padrao')),
                content_type='application/json',
            )
        self.assertEqual(Conversation.objects.filter(company=self.acme).count(), 1)
        self.assertEqual(Conversation.objects.filter(company=self.default_company).count(), 1)
        self.assertEqual(
            Conversation.objects.filter(company=self.acme).first().messages.first().text,
            'oi acme',
        )

    def test_webhook_token_is_validated_per_company(self):
        """Token cadastrado numa empresa nao libera o webhook da outra."""
        from accounts.models import WapiConfiguration
        cfg = WapiConfiguration.for_company(self.acme)
        cfg.webhook_token = 'SEGREDO-ACME'
        cfg.save(update_fields=['webhook_token'])

        url = reverse('wapi-webhook-company', args=['acme'])
        r = self.client.post(url, data=_json.dumps(self._payload()), content_type='application/json')
        self.assertEqual(r.status_code, 403)

        r = self.client.post(
            f'{url}?token=SEGREDO-ACME',
            data=_json.dumps(self._payload()), content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)

        # A empresa padrao nao tem token: continua aberta (comportamento atual).
        r = self.client.post(
            reverse('wapi-webhook-company', args=[self.default_company.slug]),
            data=_json.dumps(self._payload()), content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)


class SendUsesCompanyInstanceTests(TestCase):
    """O envio sai SEMPRE pela instancia da W-API da empresa da conversa."""

    def setUp(self):
        from accounts.models import Company, Contact, Conversation, WapiConfiguration
        self.acme = Company.objects.create(name='Acme', slug='acme')
        for company, instance in ((Company.get_default(), 'INSTANCIA-PADRAO'),
                                  (self.acme, 'INSTANCIA-ACME')):
            cfg = WapiConfiguration.for_company(company)
            cfg.instance_id = instance
            cfg.token = f'TOKEN-{instance}'
            cfg.save(update_fields=['instance_id', 'token'])

        self.user = User.objects.create_user(
            email='adm@acme.com', password='x', role=User.Role.ADM, company=self.acme
        )
        contact = Contact.objects.create(company=self.acme, phone='5516999990001', name='Cliente')
        self.conversation = Conversation.objects.create(
            company=self.acme, contact=contact, external_id='5516999990001',
            assigned_attendant=self.user.attendant_profile,
        )
        self.client.force_login(self.user)

    def test_send_passes_conversation_company(self):
        from wapi.client import WapiSendResult
        send_ok = WapiSendResult(success=True, message_id='WAPI-1')
        with patch('accounts.views.send_text_message', return_value=send_ok) as mock_send:
            r = self.client.post(
                reverse('conversation-send', args=[self.conversation.id]),
                {'text': 'ola'},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mock_send.call_args.kwargs['company'], self.acme)

    def test_wapi_client_uses_company_credentials(self):
        """O `_wapi_post` monta a URL com o instanceId DA EMPRESA informada."""
        from accounts.models import Company
        from wapi import client as wapi_client

        captured = {}

        class _Resp:
            status = 200

            def read(self):
                return b'{"messageId": "X"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _fake_urlopen(req, timeout=None):
            captured['url'] = req.full_url
            captured['auth'] = req.headers.get('Authorization')
            return _Resp()

        with patch.object(wapi_client.request, 'urlopen', _fake_urlopen):
            wapi_client.send_text_message('5516999990001', 'ola', company=self.acme)
        self.assertIn('INSTANCIA-ACME', captured['url'])
        self.assertIn('TOKEN-INSTANCIA-ACME', captured['auth'])

        with patch.object(wapi_client.request, 'urlopen', _fake_urlopen):
            wapi_client.send_text_message('5516999990001', 'ola', company=Company.get_default())
        self.assertIn('INSTANCIA-PADRAO', captured['url'])

    def test_company_is_required(self):
        """Sem empresa a chamada falha de proposito — nunca "escolhe" uma instancia."""
        from wapi import client as wapi_client
        with self.assertRaises(ValueError):
            wapi_client._company_config(None)


class CompanyDataIsolationTests(TestCase):
    """Duas empresas usando o sistema ao mesmo tempo: nenhuma ve a outra."""

    def setUp(self):
        from accounts.models import Company, Contact, Conversation, Sector
        self.a = Company.objects.create(name='Empresa A', slug='empresa-a')
        self.b = Company.objects.create(name='Empresa B', slug='empresa-b')

        self.adm_a = User.objects.create_user(
            email='adm@a.com', password='x', role=User.Role.ADM, company=self.a
        )
        self.adm_b = User.objects.create_user(
            email='adm@b.com', password='x', role=User.Role.ADM, company=self.b
        )

        Sector.objects.create(company=self.a, name='Financeiro A')
        Sector.objects.create(company=self.b, name='Financeiro B')
        Contact.objects.create(company=self.a, phone='5516000000001', name='Contato A')
        Contact.objects.create(company=self.b, phone='5516000000002', name='Contato B')

        c_a = Contact.objects.create(company=self.a, phone='5516111111111', name='Cliente A')
        c_b = Contact.objects.create(company=self.b, phone='5516222222222', name='Cliente B')
        self.conv_a = Conversation.objects.create(
            company=self.a, contact=c_a, external_id=c_a.phone, last_message_text='msg da A'
        )
        self.conv_b = Conversation.objects.create(
            company=self.b, contact=c_b, external_id=c_b.phone, last_message_text='msg da B'
        )

    def test_contacts_screen_shows_only_own_company(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(reverse('contacts'))
        self.assertContains(r, 'Contato A')
        self.assertNotContains(r, 'Contato B')
        # A empresa A tem 2 contatos proprios; os da B nao entram na contagem.
        self.assertContains(r, '2 contato(s) cadastrado(s).')

    def test_sectors_screen_shows_only_own_company(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(reverse('sectors'))
        self.assertContains(r, 'Financeiro A')
        self.assertNotContains(r, 'Financeiro B')

    def test_attendants_screen_shows_only_own_company(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(reverse('attendants'))
        self.assertContains(r, 'adm@a.com')
        self.assertNotContains(r, 'adm@b.com')

    def test_permissions_screen_shows_only_own_people(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(reverse('permissions'))
        self.assertContains(r, 'adm@a.com')
        self.assertNotContains(r, 'adm@b.com')

    def test_conversation_list_shows_only_own_company(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(reverse('conversation-list'))
        titles = [c['name'] for c in r.json()['conversations']]
        self.assertIn('Cliente A', titles)
        self.assertNotIn('Cliente B', titles)

    def test_cannot_open_conversation_of_another_company(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(reverse('conversation-messages', args=[self.conv_b.id]))
        self.assertEqual(r.status_code, 403)

    def test_cannot_delete_contact_of_another_company(self):
        from accounts.models import Contact
        other = Contact.objects.get(phone='5516000000002')
        self.client.force_login(self.adm_a)
        self.client.post(reverse('contacts'), {'action': 'delete', 'contact_id': other.id})
        self.assertTrue(Contact.objects.filter(pk=other.pk).exists())

    def test_cannot_transfer_to_sector_of_another_company(self):
        from accounts.models import Sector
        sector_b = Sector.objects.get(name='Financeiro B')
        self.client.force_login(self.adm_a)
        r = self.client.post(
            reverse('conversation-transfer', args=[self.conv_a.id]),
            {'sector_id': sector_b.id},
        )
        self.assertEqual(r.status_code, 200)
        self.conv_a.refresh_from_db()
        self.assertIsNone(self.conv_a.sector_id)   # o setor de outra empresa e ignorado

    def test_dashboard_counts_only_own_company(self):
        from accounts.views import build_dashboard_context
        def ativas(ctx):
            return next(s['value'] for s in ctx['stats'] if s['label'] == 'Conversas ativas')

        # Cada empresa tem 1 conversa ativa: nenhum indicador soma o movimento da outra.
        self.assertEqual(ativas(build_dashboard_context(self.a)), '1')
        self.assertEqual(ativas(build_dashboard_context(self.b)), '1')

    def test_same_sector_name_allowed_in_both(self):
        from accounts.models import Sector
        self.client.force_login(self.adm_a)
        self.client.post(reverse('sectors'), {'name': 'Suporte', 'description': ''})
        self.client.force_login(self.adm_b)
        self.client.post(reverse('sectors'), {'name': 'Suporte', 'description': ''})
        self.assertEqual(Sector.objects.filter(name='Suporte').count(), 2)

    def test_duplicated_sector_name_in_same_company_still_blocked(self):
        from accounts.models import Sector
        self.client.force_login(self.adm_a)
        self.client.post(reverse('sectors'), {'name': 'Suporte', 'description': ''})
        r = self.client.post(reverse('sectors'), {'name': 'Suporte', 'description': ''})
        self.assertContains(r, 'Já existe um setor com este nome.')
        self.assertEqual(Sector.objects.filter(company=self.a, name='Suporte').count(), 1)

    def test_each_company_keeps_its_own_admin_rule(self):
        """A regra "deve existir ao menos um administrador" vale POR EMPRESA: o unico
        admin da empresa A nao pode ser rebaixado, mesmo havendo admin na B."""
        self.client.force_login(self.adm_a)
        outro = User.objects.create_user(
            email='joao@a.com', password='x', role=User.Role.USUARIO, company=self.a
        )
        # Rebaixar o proprio admin e bloqueado (nao pode mexer no proprio perfil).
        r = self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': self.adm_a.id, 'role': 'usuario',
        })
        self.adm_a.refresh_from_db()
        self.assertEqual(self.adm_a.role, User.Role.ADM)
        # Promover alguem da propria empresa funciona.
        self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': outro.id, 'role': 'adm',
        })
        outro.refresh_from_db()
        self.assertEqual(outro.role, User.Role.ADM)
        # Mas nao da para mexer em quem e de outra empresa.
        self.client.post(reverse('permissions'), {
            'form_type': 'profile-role', 'user_id': self.adm_b.id, 'role': 'leitor',
        })
        self.adm_b.refresh_from_db()
        self.assertEqual(self.adm_b.role, User.Role.ADM)


class MasterClientAccessTests(TestCase):
    """O master cria o PRIMEIRO ACESSO de cada cliente (o Administrador dele)."""

    def setUp(self):
        from accounts.models import Company
        self.acme = Company.objects.create(name='Acme', slug='acme')
        self.master = User.objects.create_user(
            email='master@beezap.com', password='x', role=User.Role.MASTER
        )
        self.client.force_login(self.master)

    def _create_admin(self, email='responsavel@acme.com', company=None):
        return self.client.post(reverse('clients'), {
            'action': 'create-admin',
            'company_id': (company or self.acme).id,
            'name': 'Maria Souza',
            'email': email,
            'password': 'senha1234',
            'phone': '(16) 99999-0001',
        })

    def test_creates_company_admin(self):
        from accounts.models import Attendant, Sector
        r = self._create_admin()
        self.assertRedirects(r, reverse('clients'))

        novo = User.objects.get(email='responsavel@acme.com')
        self.assertEqual(novo.role, User.Role.ADM)
        self.assertEqual(novo.company, self.acme)
        self.assertEqual(novo.get_full_name(), 'Maria Souza')

        attendant = Attendant.objects.get(user=novo)
        self.assertEqual(attendant.company, self.acme)
        self.assertEqual(attendant.name, 'Maria Souza')
        self.assertEqual(attendant.phone, '16999990001')
        # Obrigado a trocar a senha no primeiro acesso.
        self.assertTrue(attendant.must_change_password)
        # A empresa nova ja nasce com o setor Geral padrao.
        self.assertTrue(Sector.objects.filter(company=self.acme, name='Geral').exists())

    def test_new_admin_can_log_in_and_must_change_password(self):
        self._create_admin()
        self.client.logout()
        self.assertTrue(self.client.login(email='responsavel@acme.com', password='senha1234'))
        r = self.client.get(reverse('contacts'))
        self.assertRedirects(r, reverse('change-initial-password'))

    def test_duplicated_email_is_refused(self):
        User.objects.create_user(email='ocupado@acme.com', password='x', role=User.Role.USUARIO)
        r = self._create_admin(email='ocupado@acme.com')
        self.assertRedirects(r, reverse('clients'))
        self.assertIn(
            'Este e-mail já está em uso no sistema.',
            [str(m) for m in get_messages(r.wsgi_request)],
        )
        self.assertEqual(User.objects.filter(email='ocupado@acme.com').count(), 1)

    def test_client_admin_sees_only_own_company(self):
        """O admin criado administra a empresa dele — e so ela."""
        from accounts.models import Company, Contact
        outra = Company.objects.create(name='Outra', slug='outra')
        Contact.objects.create(company=outra, phone='5516999998888', name='Contato da Outra')
        self._create_admin()

        novo = User.objects.get(email='responsavel@acme.com')
        novo.attendant_profile.must_change_password = False
        novo.attendant_profile.save(update_fields=['must_change_password'])

        self.client.force_login(novo)
        r = self.client.get(reverse('contacts'))
        self.assertNotContains(r, 'Contato da Outra')
        # E nao alcanca a gestao de clientes.
        self.assertEqual(self.client.get(reverse('clients')).status_code, 403)

    def test_only_master_can_create_client_access(self):
        adm = User.objects.create_user(
            email='adm@acme.com', password='x', role=User.Role.ADM, company=self.acme
        )
        self.client.force_login(adm)
        r = self._create_admin(email='tentativa@acme.com')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(email='tentativa@acme.com').exists())


class MasterSupportModeTests(TestCase):
    """MODO SUPORTE: o master entra no painel do cliente SO para o WhatsApp.

    A instancia/token da W-API e a unica parte tecnica que nao fica com o cliente.
    Tudo o que e do negocio da empresa (setores, atendentes, permissoes, chatbot) e
    do ADM dela, e Conversas/Contatos/Dashboard nunca estiveram abertos (dados
    pessoais dos clientes finais). Ver accounts/permissions.WHATSAPP_ITEM.
    """

    def setUp(self):
        from accounts.models import Company
        self.acme = Company.objects.create(name='Acme', slug='acme')
        self.master = User.objects.create_user(
            email='master@beezap.com', password='x', role=User.Role.MASTER
        )
        self.client.force_login(self.master)

    def _enter(self, company=None):
        return self.client.post(reverse('clients'), {
            'action': 'enter', 'company_id': (company or self.acme).id,
        })

    def test_enter_redirects_to_client_settings(self):
        r = self._enter()
        self.assertRedirects(r, reverse('wapi-settings'))

    def test_only_whatsapp_and_platform_ai_open_in_support_mode(self):
        """No painel do cliente o master abre o WhatsApp dele e a IA da plataforma."""
        self._enter()
        for route in ('wapi-settings', 'openai-settings'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 200, route)

    def test_client_business_screens_are_blocked_in_support_mode(self):
        """Setores, atendentes, permissoes e atendimento sao do ADM do cliente."""
        self._enter()
        for route in ('sectors', 'attendants', 'permissions', 'atendimento'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 403, route)

    def test_conversations_and_contacts_stay_blocked(self):
        """Mesmo dentro do painel do cliente, o master nao le o atendimento dele."""
        self._enter()
        for route in ('conversations', 'contacts', 'dashboard'):
            r = self.client.get(reverse(route))
            self.assertNotEqual(r.status_code, 200, route)

    def test_settings_saved_go_to_that_company(self):
        from accounts.models import WapiConfiguration
        self._enter()
        self.client.post(reverse('wapi-settings'), {
            'form_type': 'config', 'instance_id': 'INSTANCIA-DO-ACME', 'token': 'TOK', 'webhook_token': '',
        })
        self.assertEqual(
            WapiConfiguration.for_company(self.acme).instance_id, 'INSTANCIA-DO-ACME'
        )

    def test_webhook_url_shown_is_the_client_one(self):
        self._enter()
        r = self.client.get(reverse('wapi-settings'))
        self.assertContains(r, 'webhook/wapi/acme/')

    def test_forged_post_does_not_create_sector_for_the_client(self):
        """Esconder o botao nao bastaria: o POST tambem tem que ser recusado."""
        from accounts.models import Sector
        self._enter()
        r = self.client.post(reverse('sectors'), {'name': 'Suporte Acme', 'description': ''})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Sector.objects.filter(name='Suporte Acme').exists())

    def test_leaving_blocks_config_again(self):
        self._enter()
        self.assertEqual(self.client.get(reverse('wapi-settings')).status_code, 200)
        self.client.post(reverse('clients'), {'action': 'leave'})
        # Sem empresa escolhida, a tela do WhatsApp manda de volta para Clientes.
        self.assertRedirects(self.client.get(reverse('wapi-settings')), reverse('clients'))
        self.assertEqual(self.client.get(reverse('atendimento')).status_code, 403)

    def test_support_banner_names_the_client(self):
        self._enter()
        r = self.client.get(reverse('clients'))
        self.assertContains(r, 'Você está no painel de Acme')
        self.assertContains(r, 'Modo suporte')

    def test_master_menu_in_support_mode_is_only_platform_plus_whatsapp(self):
        from accounts.permissions import nav_items_for
        labels = [i['label'] for i in nav_items_for(self.master, '', in_company=True)]
        self.assertEqual(labels, ['Clientes', 'Métricas', 'Inteligência (IA)', 'Gestores', 'WhatsApp'])

    def test_master_still_sees_no_conversation_in_support_mode(self):
        from accounts.models import Contact, Conversation
        from accounts.permissions import visible_conversations
        contact = Contact.objects.create(company=self.acme, phone='5516999990001')
        Conversation.objects.create(company=self.acme, contact=contact, external_id=contact.phone)
        self._enter()
        self.assertFalse(
            visible_conversations(self.master, Conversation.objects.all()).exists()
        )


class TechnicalSettingsAreMasterOnlyTests(TestCase):
    """O que e TECNICO nao fica com o cliente.

    - **WhatsApp (W-API)**: instancia e token de CADA empresa — so o master, e so
      dentro do painel daquele cliente.
    - **Inteligencia (IA)**: UMA configuracao da plataforma (a API Key e do master,
      que paga a conta da OpenAI) — so o master, fora do painel de cliente.
    - O **cliente** configura o chatbot de menu e escolhe o modo de primeiro
      atendimento (desligado / chatbot / IA), e ve apenas um aviso de STATUS.
    """

    def setUp(self):
        from accounts.models import Company
        self.acme = Company.objects.create(name='Acme', slug='acme')
        self.master = User.objects.create_user(
            email='master@beezap.com', password='x', role=User.Role.MASTER
        )
        self.adm = User.objects.create_user(
            email='adm@acme.com', password='x', role=User.Role.ADM, company=self.acme
        )

    # ---------- o cliente nao alcanca as telas tecnicas ----------

    def test_client_cannot_open_whatsapp_settings(self):
        self.client.force_login(self.adm)
        r = self.client.get(reverse('wapi-settings'))
        self.assertEqual(r.status_code, 403)

    def test_client_cannot_open_ai_settings(self):
        self.client.force_login(self.adm)
        r = self.client.get(reverse('openai-settings'))
        self.assertEqual(r.status_code, 403)

    def test_client_cannot_save_whatsapp_credentials(self):
        """Mesmo forjando o POST, o cliente nao grava credencial da W-API."""
        from accounts.models import WapiConfiguration
        self.client.force_login(self.adm)
        r = self.client.post(reverse('wapi-settings'), {
            'form_type': 'config', 'instance_id': 'HACK', 'token': 'HACK', 'webhook_token': '',
        })
        self.assertEqual(r.status_code, 403)
        self.assertEqual(WapiConfiguration.for_company(self.acme).instance_id, '')

    def test_client_cannot_save_api_key(self):
        from accounts.models import OpenAiConfiguration
        self.client.force_login(self.adm)
        r = self.client.post(reverse('openai-settings'), {
            'form_type': 'config', 'api_key': 'sk-hack', 'model': 'gpt-4o', 'max_turns': 3,
        })
        self.assertEqual(r.status_code, 403)
        self.assertEqual(OpenAiConfiguration.get_solo().api_key, '')

    def test_client_menu_has_no_technical_screens(self):
        """O botao Configuracoes do cliente leva ao Atendimento, nao ao WhatsApp."""
        from accounts.permissions import nav_items_for
        items = {i['label']: i['url_name'] for i in nav_items_for(self.adm, '')}
        self.assertEqual(items['Configurações'], 'atendimento')
        self.assertNotIn('Inteligência (IA)', items)
        self.assertNotIn('Clientes', items)

    # ---------- o cliente configura o que e do negocio dele ----------

    def test_client_can_open_and_save_chatbot(self):
        from accounts.models import MenuBotConfiguration
        self.client.force_login(self.adm)
        self.assertEqual(self.client.get(reverse('atendimento')).status_code, 200)
        r = self.client.post(reverse('atendimento'), {
            'form_type': 'chatbot',
            'greeting': '{saudacao}! Aqui e a Acme.',
            'menu_intro': 'Escolha uma opcao:',
            'confirmation_message': 'Encaminhando para {setor}.',
            'invalid_message': 'Nao entendi.',
            'handoff_message': 'Vou chamar um atendente.',
            'max_attempts': 3,
        })
        self.assertRedirects(r, reverse('atendimento'))
        self.assertEqual(
            MenuBotConfiguration.for_company(self.acme).greeting,
            '{saudacao}! Aqui e a Acme.',
        )

    def test_client_can_choose_any_mode_including_ai(self):
        """O cliente decide se usa IA, chatbot ou nada (foi a regra escolhida)."""
        from accounts.models import MenuBotConfiguration
        self.client.force_login(self.adm)
        for mode in (MenuBotConfiguration.MODE_MENU, MenuBotConfiguration.MODE_AI,
                     MenuBotConfiguration.MODE_OFF):
            self.client.post(reverse('atendimento-mode'), {'mode': mode, 'next': 'chatbot'})
            self.assertEqual(MenuBotConfiguration.for_company(self.acme).mode, mode)

    def test_mode_of_one_company_does_not_affect_the_other(self):
        from accounts.models import Company, MenuBotConfiguration
        outra = Company.objects.create(name='Outra', slug='outra')
        adm_outra = User.objects.create_user(
            email='adm@outra.com', password='x', role=User.Role.ADM, company=outra
        )
        self.client.force_login(self.adm)
        self.client.post(reverse('atendimento-mode'), {'mode': 'ai', 'next': 'chatbot'})
        self.client.force_login(adm_outra)
        self.client.post(reverse('atendimento-mode'), {'mode': 'menu', 'next': 'chatbot'})

        self.assertEqual(MenuBotConfiguration.for_company(self.acme).mode, 'ai')
        self.assertEqual(MenuBotConfiguration.for_company(outra).mode, 'menu')

    # ---------- status: informa sem expor credencial ----------

    def test_status_card_shows_pending_without_credentials(self):
        self.client.force_login(self.adm)
        r = self.client.get(reverse('atendimento'))
        self.assertContains(r, 'WhatsApp ainda não configurado')
        self.assertContains(r, 'Inteligência (IA) indisponível')

    def test_status_card_shows_ready_and_never_leaks_credentials(self):
        from accounts.models import OpenAiConfiguration, WapiConfiguration
        wapi = WapiConfiguration.for_company(self.acme)
        wapi.instance_id = 'INSTANCIA-SECRETA'
        wapi.token = 'TOKEN-SECRETO'
        wapi.save(update_fields=['instance_id', 'token'])
        ai = OpenAiConfiguration.get_solo()
        ai.api_key = 'sk-super-secreta'
        ai.save(update_fields=['api_key'])

        self.client.force_login(self.adm)
        r = self.client.get(reverse('atendimento'))
        self.assertContains(r, 'WhatsApp conectado')
        self.assertContains(r, 'Inteligência (IA) disponível')
        # Nada de credencial na tela do cliente.
        self.assertNotContains(r, 'INSTANCIA-SECRETA')
        self.assertNotContains(r, 'TOKEN-SECRETO')
        self.assertNotContains(r, 'sk-super-secreta')

    # ---------- o master configura ----------

    def test_master_opens_platform_ai_without_entering_a_client(self):
        """A tela de IA e da plataforma: nao depende de estar no painel de ninguem."""
        self.client.force_login(self.master)
        r = self.client.get(reverse('openai-settings'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Configuração da plataforma')

    def test_master_saves_the_single_api_key(self):
        from accounts.models import OpenAiConfiguration
        self.client.force_login(self.master)
        r = self.client.post(reverse('openai-settings'), {
            'form_type': 'config', 'api_key': 'sk-da-plataforma',
            'model': 'gpt-4.1-nano', 'instructions': '', 'max_turns': 3,
        })
        self.assertRedirects(r, reverse('openai-settings'))
        self.assertEqual(OpenAiConfiguration.get_solo().api_key, 'sk-da-plataforma')
        self.assertEqual(OpenAiConfiguration.objects.count(), 1)

    def test_master_whatsapp_screen_requires_entering_the_client(self):
        """Credencial da W-API e por empresa: sem escolher o cliente, volta a lista."""
        self.client.force_login(self.master)
        self.assertRedirects(self.client.get(reverse('wapi-settings')), reverse('clients'))

        self.client.post(reverse('clients'), {'action': 'enter', 'company_id': self.acme.id})
        self.assertEqual(self.client.get(reverse('wapi-settings')).status_code, 200)

    def test_master_menu_shows_ai_and_clients(self):
        from accounts.permissions import nav_items_for
        labels = [i['label'] for i in nav_items_for(self.master, '')]
        self.assertIn('Clientes', labels)
        self.assertIn('Inteligência (IA)', labels)

    def test_one_api_key_serves_every_company(self):
        """A mesma chave atende todos os clientes: o `chat_completion` nao recebe
        empresa nenhuma e sempre usa a configuracao da plataforma."""
        from accounts.models import Company, OpenAiConfiguration
        from gpt import client as gpt_client
        ai = OpenAiConfiguration.get_solo()
        ai.api_key = 'sk-unica'
        ai.save(update_fields=['api_key'])
        Company.objects.create(name='Outra', slug='outra')

        captured = {}

        class _Resp:
            status = 200

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _fake_urlopen(req, timeout=None):
            captured['auth'] = req.headers.get('Authorization')
            return _Resp()

        with patch.object(gpt_client.request, 'urlopen', _fake_urlopen):
            result = gpt_client.chat_completion([{'role': 'user', 'content': 'oi'}])
        self.assertTrue(result.success)
        self.assertIn('sk-unica', captured['auth'])


class AiFallbackUsesCompanyChatbotSectorTests(TestCase):
    """Quando a IA nao entende, ela encaminha para o fallback DA EMPRESA.

    A configuracao do GPT e da plataforma e por isso nao guarda setor; o destino e o
    mesmo que a empresa ja definiu no chatbot de menu e, na falta dele, o setor Geral.
    """

    def setUp(self):
        from accounts.models import Company, MenuBotConfiguration, Sector
        self.company = Company.objects.create(name='Acme', slug='acme')
        self.geral = Sector.ensure_general(self.company)
        self.triagem = Sector.objects.create(company=self.company, name='Triagem')
        self.menu_config = MenuBotConfiguration.for_company(self.company)

    def test_uses_chatbot_fallback_when_defined(self):
        from gpt.attendant import _resolve_fallback_sector
        self.menu_config.fallback_sector = self.triagem
        self.menu_config.save(update_fields=['fallback_sector'])
        self.assertEqual(_resolve_fallback_sector(self.company).pk, self.triagem.pk)

    def test_falls_back_to_general_sector_of_the_company(self):
        from gpt.attendant import _resolve_fallback_sector
        self.assertEqual(_resolve_fallback_sector(self.company).pk, self.geral.pk)

    def test_never_returns_a_sector_from_another_company(self):
        from accounts.models import Company, MenuBotConfiguration, Sector
        from gpt.attendant import _resolve_fallback_sector
        outra = Company.objects.create(name='Outra', slug='outra')
        Sector.objects.create(company=outra, name='Triagem')
        geral_outra = Sector.ensure_general(outra)
        MenuBotConfiguration.for_company(outra).save()

        resolved = _resolve_fallback_sector(outra)
        self.assertEqual(resolved.pk, geral_outra.pk)
        self.assertEqual(resolved.company, outra)   # o Geral da propria empresa
        self.assertNotEqual(resolved.pk, self.triagem.pk)


class MediaAccessTests(TestCase):
    """Arquivo de conversa (foto/audio/video/documento) e conteudo do CLIENTE.

    Antes, a midia ficava em `media/whatsapp/wapi_<id>.<ext>` e o Nginx servia a
    pasta inteira: qualquer um que adivinhasse o caminho baixava o arquivo, de
    qualquer empresa, sem login. Agora ela so sai pela view `message-media`, com as
    mesmas regras da conversa — e o gestor master tambem nao passa.
    """

    def setUp(self):
        from django.core.files.base import ContentFile
        from accounts.models import Company, Contact, Conversation, Message
        self.a = Company.objects.create(name='Midia A', slug='midia-a')
        self.b = Company.objects.create(name='Midia B', slug='midia-b')
        self.adm_a = User.objects.create_user(
            email='adm@midia-a.com', password='x', role=User.Role.ADM, company=self.a
        )
        self.adm_b = User.objects.create_user(
            email='adm@midia-b.com', password='x', role=User.Role.ADM, company=self.b
        )
        self.master = User.objects.create_user(
            email='master@midia.com', password='x', role=User.Role.MASTER
        )
        contact = Contact.objects.create(company=self.a, phone='5516999990001', name='Cliente A')
        self.conv = Conversation.objects.create(
            company=self.a, contact=contact, external_id=contact.phone
        )
        self.message = Message.objects.create(
            conversation=self.conv, direction='in', message_type='image',
            media_mimetype='image/jpeg', media_status='ok',
        )
        self.message.media_file.save('teste.jpg', ContentFile(b'conteudo-secreto'), save=True)
        self.url = reverse('message-media', args=[self.message.pk])

    def tearDown(self):
        self.message.media_file.delete(save=False)

    def test_owner_company_can_open_the_file(self):
        self.client.force_login(self.adm_a)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b''.join(r.streaming_content), b'conteudo-secreto')

    def test_other_company_cannot_open_the_file(self):
        self.client.force_login(self.adm_b)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_master_cannot_open_the_file(self):
        """O master administra os clientes; nao le nem baixa o atendimento deles."""
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_master_in_support_mode_cannot_open_the_file(self):
        """Nem dentro do painel do cliente (modo suporte)."""
        from accounts.tenancy import ACTIVE_COMPANY_SESSION_KEY
        self.client.force_login(self.master)
        session = self.client.session
        session[ACTIVE_COMPANY_SESSION_KEY] = self.a.pk
        session.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)

    def test_serializer_points_to_the_protected_url(self):
        """O chat recebe a URL da view, nunca o caminho cru em /media/."""
        self.assertEqual(self.message.resolved_media_url, self.url)
        self.assertNotIn('/media/', self.message.resolved_media_url)

    def test_downloaded_media_filename_is_not_guessable(self):
        """Nome de arquivo aleatorio: sem o `wapi_<id>` sequencial de antes."""
        from unittest.mock import MagicMock
        from unittest.mock import patch as _patch
        from accounts.models import Message
        from wapi.services import _download_to_media_file
        message = Message.objects.create(
            conversation=self.conv, direction='in', message_type='image',
            media_mimetype='image/jpeg',
        )
        fake = MagicMock()
        fake.__enter__.return_value.read.return_value = b'bytes-da-foto'
        fake.__enter__.return_value.headers.get.return_value = 'image/jpeg'
        with _patch('wapi.services.request.urlopen', return_value=fake):
            saved = _download_to_media_file(message, 'http://exemplo/arquivo', 'image/jpeg')
        self.assertTrue(saved)
        name = message.media_file.name or ''
        self.assertTrue(name)
        self.assertNotIn('wapi_%s' % message.pk, name)
        message.media_file.delete(save=False)


class PublicMediaLinkTests(TestCase):
    """Link assinado que a W-API usa para baixar a midia que ENVIAMOS.

    E o unico caminho publico que sobrou, porque a W-API roda na nuvem e busca o
    arquivo pela URL. Em troca ele e assinado, vale para uma mensagem so e expira.
    """

    def setUp(self):
        from django.core.files.base import ContentFile
        from accounts.models import Company, Contact, Conversation, Message
        self.company = Company.objects.create(name='Envio', slug='envio-midia')
        contact = Contact.objects.create(company=self.company, phone='5516999990002', name='Cliente')
        conv = Conversation.objects.create(
            company=self.company, contact=contact, external_id=contact.phone
        )
        self.message = Message.objects.create(
            conversation=conv, direction='out', message_type='image',
            media_mimetype='image/jpeg', media_status='ok',
        )
        self.message.media_file.save('saida.jpg', ContentFile(b'foto-enviada'), save=True)

    def tearDown(self):
        self.message.media_file.delete(save=False)

    def test_valid_token_serves_the_file_without_login(self):
        from accounts.views import _media_link_token
        url = reverse('media-public', args=[_media_link_token(self.message)])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b''.join(r.streaming_content), b'foto-enviada')

    def test_invalid_token_is_404(self):
        self.assertEqual(self.client.get(reverse('media-public', args=['abc'])).status_code, 404)

    def test_expired_token_is_404(self):
        from unittest.mock import patch as _patch
        from accounts.views import _media_link_token
        url = reverse('media-public', args=[_media_link_token(self.message)])
        with _patch('accounts.views.MEDIA_LINK_MAX_AGE', -1):
            self.assertEqual(self.client.get(url).status_code, 404)


class MasterDoesNotSeeGroupNamesTests(TestCase):
    """Nome de grupo de WhatsApp e conteudo do cliente, nao configuracao."""

    def setUp(self):
        from accounts.models import Company, Conversation
        from accounts.tenancy import ACTIVE_COMPANY_SESSION_KEY
        self.company = Company.objects.create(name='Grupos', slug='grupos-cli')
        self.adm = User.objects.create_user(
            email='adm@grupos.com', password='x', role=User.Role.ADM, company=self.company
        )
        self.master = User.objects.create_user(
            email='master@grupos.com', password='x', role=User.Role.MASTER
        )
        Conversation.objects.create(
            company=self.company, chat_type='group',
            external_id='120363000000000001@g.us', name='Obra Rua das Flores',
        )
        self.session_key = ACTIVE_COMPANY_SESSION_KEY

    def _enter_support_mode(self):
        self.client.force_login(self.master)
        session = self.client.session
        session[self.session_key] = self.company.pk
        session.save()

    def test_company_admin_still_sees_the_groups_tab(self):
        self.client.force_login(self.adm)
        r = self.client.get(reverse('permissions'))
        self.assertContains(r, 'Obra Rua das Flores')

    def test_master_in_support_mode_does_not_see_group_names(self):
        """A tela inteira ficou fora do alcance dele — nome de grupo nem chega perto."""
        self._enter_support_mode()
        r = self.client.get(reverse('permissions'))
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(r, 'Obra Rua das Flores', status_code=403)

    def test_master_cannot_rename_a_group_by_forged_post(self):
        from accounts.models import Conversation
        self._enter_support_mode()
        group = Conversation.objects.get(external_id='120363000000000001@g.us')
        r = self.client.post(reverse('permissions'), {
            'form_type': 'group-name', 'group_id': group.pk, 'name': 'Renomeado pelo master',
        })
        self.assertEqual(r.status_code, 403)
        group.refresh_from_db()
        self.assertEqual(group.name, 'Obra Rua das Flores')

    def test_master_cannot_remove_a_group_by_forged_post(self):
        from accounts.models import Conversation
        self._enter_support_mode()
        group = Conversation.objects.get(external_id='120363000000000001@g.us')
        r = self.client.post(reverse('permissions'), {
            'form_type': 'group-remove', 'group_id': group.pk,
        })
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Conversation.objects.filter(pk=group.pk).exists())


class ClientMetricsTests(TestCase):
    """Painel de METRICAS do master: numeros do cliente, nunca o conteudo dele."""

    def setUp(self):
        from accounts.models import (
            Company, Contact, Conversation, Message, Sector, WapiWebhookEvent,
        )
        self.company = Company.objects.create(name='Metrica SA', slug='metrica-sa')
        self.outra = Company.objects.create(name='Outra SA', slug='outra-sa')
        self.master = User.objects.create_user(
            email='master@metricas.com', password='x', role=User.Role.MASTER
        )
        self.adm = User.objects.create_user(
            email='adm@metrica.com', password='x', role=User.Role.ADM, company=self.company
        )
        Sector.objects.create(company=self.company, name='Suporte')

        contact = Contact.objects.create(
            company=self.company, phone='5516988880000', name='Joana Segredo'
        )
        self.conv = Conversation.objects.create(
            company=self.company, contact=contact, external_id=contact.phone, status='open'
        )
        Message.objects.create(
            conversation=self.conv, direction='in', message_type='text',
            text='meu cartao de credito e 1234',
        )
        Message.objects.create(
            conversation=self.conv, direction='out', message_type='text', text='resposta secreta',
        )
        Message.objects.create(
            conversation=self.conv, direction='out', message_type='text',
            text='resposta da IA', is_ai=True,
        )
        Conversation.objects.create(
            company=self.company, chat_type='group',
            external_id='120363000000000009@g.us', name='Grupo Confidencial',
        )
        WapiWebhookEvent.objects.create(company=self.company, event_type='message')

        # Dados da OUTRA empresa: nao podem entrar na conta desta.
        outro_contato = Contact.objects.create(
            company=self.outra, phone='5516977770000', name='De Outra'
        )
        conv_outra = Conversation.objects.create(
            company=self.outra, contact=outro_contato, external_id=outro_contato.phone
        )
        Message.objects.create(
            conversation=conv_outra, direction='in', message_type='text', text='da outra',
        )

        self.url = reverse('client-metrics', args=[self.company.pk])

    # ----- Quem entra -----

    def test_master_opens_the_metrics_screen(self):
        self.client.force_login(self.master)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Métricas do cliente')
        self.assertContains(r, 'Metrica SA')

    def test_company_admin_cannot_open_the_metrics_screen(self):
        """A tela e de gestao da plataforma; o cliente tem o Dashboard dele."""
        self.client.force_login(self.adm)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    # ----- O que a tela mostra (e o que nao mostra) -----

    def test_screen_never_shows_conversation_content(self):
        self.client.force_login(self.master)
        r = self.client.get(self.url)
        self.assertNotContains(r, 'meu cartao de credito')
        self.assertNotContains(r, 'resposta secreta')
        self.assertNotContains(r, 'Joana Segredo')
        self.assertNotContains(r, 'Grupo Confidencial')
        self.assertNotContains(r, '5516988880000')

    def test_screen_never_shows_credentials(self):
        from accounts.models import WapiConfiguration
        config = WapiConfiguration.for_company(self.company)
        config.instance_id = 'INSTANCIA-SECRETA'
        config.token = 'TOKEN-SECRETO'
        config.save()
        self.client.force_login(self.master)
        r = self.client.get(self.url)
        self.assertNotContains(r, 'INSTANCIA-SECRETA')
        self.assertNotContains(r, 'TOKEN-SECRETO')
        self.assertContains(r, 'Configuradas')

    # ----- Os numeros -----

    def test_metrics_count_only_this_company(self):
        from accounts.views import build_company_metrics
        m = build_company_metrics(self.company)
        self.assertEqual(m['mensagens']['recebidas'], 1)   # a da outra empresa fica fora
        self.assertEqual(m['mensagens']['enviadas'], 2)
        self.assertEqual(m['mensagens']['automaticas'], 1)
        self.assertEqual(m['conversas']['total'], 2)       # a direta + o grupo
        self.assertEqual(m['conversas']['grupos'], 1)
        self.assertEqual(m['equipe']['contatos'], 1)
        # "Suporte" + o setor "Geral", que toda empresa ganha automaticamente.
        self.assertEqual(m['equipe']['setores'], 2)
        self.assertEqual(m['canal']['eventos'], 1)

    def test_metrics_bring_the_last_message_dates(self):
        from accounts.views import build_company_metrics
        m = build_company_metrics(self.company)
        self.assertIsNotNone(m['mensagens']['ultima_recebida'])
        self.assertIsNotNone(m['mensagens']['ultima_enviada'])

    def test_system_dividers_do_not_count_as_messages(self):
        from accounts.models import Message
        from accounts.views import build_company_metrics
        Message.objects.create(
            conversation=self.conv, direction='out', message_type='system',
            text='Atendimento encerrado',
        )
        m = build_company_metrics(self.company)
        self.assertEqual(m['mensagens']['enviadas'], 2)

    def test_channel_shows_pending_when_there_is_no_credential(self):
        from accounts.views import build_company_metrics
        self.assertFalse(build_company_metrics(self.company)['canal']['configurado'])


class ClientConnectionCheckTests(TestCase):
    """Botao "Testar conexao": estado do canal do cliente, sem expor credencial."""

    def setUp(self):
        from accounts.models import Company
        self.company = Company.objects.create(name='Canal SA', slug='canal-sa')
        self.master = User.objects.create_user(
            email='master@canal.com', password='x', role=User.Role.MASTER
        )
        self.adm = User.objects.create_user(
            email='adm@canal.com', password='x', role=User.Role.ADM, company=self.company
        )
        self.url = reverse('client-connection-check', args=[self.company.pk])

    def test_master_gets_the_channel_state(self):
        from unittest.mock import patch as _patch
        from wapi.client import WapiHealth
        health = WapiHealth(configured=True, connected=True, label='Conectado', detail='')
        self.client.force_login(self.master)
        with _patch('accounts.views.wapi_check_connection', return_value=health):
            r = self.client.post(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['label'], 'Conectado')
        self.assertTrue(r.json()['connected'])

    def test_company_admin_cannot_use_it(self):
        self.client.force_login(self.adm)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_without_credentials_it_says_not_configured_without_calling_the_api(self):
        from wapi.client import check_connection
        health = check_connection(company=self.company)
        self.assertFalse(health.configured)
        self.assertEqual(health.label, 'Nao configurado')

    def test_reads_connected_from_the_status_body(self):
        from unittest.mock import patch as _patch
        from accounts.models import WapiConfiguration
        from wapi.client import check_connection
        config = WapiConfiguration.for_company(self.company)
        config.instance_id, config.token = 'i', 't'
        config.save()
        with _patch('wapi.client._wapi_get', return_value=(True, 200, {'connected': True}, None)):
            self.assertTrue(check_connection(company=self.company).connected)
        with _patch('wapi.client._wapi_get', return_value=(True, 200, {'status': 'disconnected'}, None)):
            self.assertFalse(check_connection(company=self.company).connected)

    def test_rejected_credential_is_reported(self):
        from unittest.mock import patch as _patch
        from accounts.models import WapiConfiguration
        from wapi.client import check_connection
        config = WapiConfiguration.for_company(self.company)
        config.instance_id, config.token = 'i', 't'
        config.save()
        with _patch('wapi.client._wapi_get', return_value=(False, 401, None, 'erro')):
            health = check_connection(company=self.company)
        self.assertFalse(health.connected)
        self.assertEqual(health.label, 'Credencial recusada')

    def test_falls_back_to_the_groups_probe_when_status_route_is_missing(self):
        from unittest.mock import patch as _patch
        from accounts.models import WapiConfiguration
        from wapi.client import check_connection
        config = WapiConfiguration.for_company(self.company)
        config.instance_id, config.token = 'i', 't'
        config.save()
        respostas = [(False, 404, None, 'erro'), (True, 200, [], None)]
        with _patch('wapi.client._wapi_get', side_effect=respostas):
            health = check_connection(company=self.company)
        self.assertTrue(health.connected)
        self.assertEqual(health.label, 'Conectado')


class CompanyExportTests(TestCase):
    """Portabilidade: o CLIENTE leva os dados dele num ZIP.

    Quem exporta e o Administrador da propria empresa. O gestor master nao —
    um ZIP com todas as conversas seria ler o atendimento de uma vez so, o
    contrario da regra do projeto.
    """

    def setUp(self):
        from django.core.files.base import ContentFile
        from accounts.models import Company, Contact, Conversation, Message, Sector
        self.company = Company.objects.create(name='Exporta SA', slug='exporta-sa')
        self.outra = Company.objects.create(name='Vizinha SA', slug='vizinha-sa')
        self.adm = User.objects.create_user(
            email='adm@exporta.com', password='x', role=User.Role.ADM, company=self.company
        )
        self.adm_outra = User.objects.create_user(
            email='adm@vizinha.com', password='x', role=User.Role.ADM, company=self.outra
        )
        self.master = User.objects.create_user(
            email='master@exporta.com', password='x', role=User.Role.MASTER
        )
        Sector.objects.create(company=self.company, name='Cobranca')

        contato = Contact.objects.create(
            company=self.company, phone='5516900000001', name='Cliente Exporta'
        )
        self.conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id=contato.phone
        )
        Message.objects.create(
            conversation=self.conv, direction='in', message_type='text',
            text='mensagem que precisa ir junto',
        )
        self.com_midia = Message.objects.create(
            conversation=self.conv, direction='out', message_type='image',
            media_mimetype='image/jpeg', text='foto enviada',
        )
        self.com_midia.media_file.save('a1b2c3.jpg', ContentFile(b'bytes-da-foto'), save=True)

        # Dado da OUTRA empresa: nao pode aparecer no ZIP desta.
        c2 = Contact.objects.create(company=self.outra, phone='5516900000002', name='Segredo Vizinho')
        conv2 = Conversation.objects.create(
            company=self.outra, contact=c2, external_id=c2.phone
        )
        Message.objects.create(
            conversation=conv2, direction='in', message_type='text', text='conversa da vizinha',
        )

        self.page_url = reverse('company-data')
        self.export_url = reverse('company-export')

    def tearDown(self):
        self.com_midia.media_file.delete(save=False)

    def _zip_from_response(self, response):
        import io
        import zipfile
        raw = b''.join(response.streaming_content)
        return zipfile.ZipFile(io.BytesIO(raw))

    # ----- Quem pode -----

    def test_company_admin_sees_the_page(self):
        self.client.force_login(self.adm)
        r = self.client.get(self.page_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Baixar uma cópia dos seus dados')

    def test_master_cannot_open_the_page(self):
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(self.page_url).status_code, 403)

    def test_master_cannot_download_even_in_support_mode(self):
        from accounts.tenancy import ACTIVE_COMPANY_SESSION_KEY
        self.client.force_login(self.master)
        session = self.client.session
        session[ACTIVE_COMPANY_SESSION_KEY] = self.company.pk
        session.save()
        self.assertEqual(self.client.post(self.export_url).status_code, 403)

    def test_reader_profile_cannot_download(self):
        leitor = User.objects.create_user(
            email='leitor@exporta.com', password='x', role=User.Role.LEITOR, company=self.company
        )
        self.client.force_login(leitor)
        self.assertEqual(self.client.post(self.export_url).status_code, 403)

    def test_export_needs_login(self):
        self.assertEqual(self.client.post(self.export_url).status_code, 302)

    # ----- O conteudo do ZIP -----

    def test_zip_has_all_the_files(self):
        self.client.force_login(self.adm)
        bundle = self._zip_from_response(self.client.post(self.export_url))
        nomes = bundle.namelist()
        for esperado in ('LEIA-ME.txt', 'empresa.json', 'setores.csv', 'atendentes.csv',
                         'usuarios.csv', 'contatos.csv', 'conversas.csv', 'mensagens.csv'):
            self.assertIn(esperado, nomes)

    def test_zip_carries_the_conversation_history(self):
        self.client.force_login(self.adm)
        bundle = self._zip_from_response(self.client.post(self.export_url))
        mensagens = bundle.read('mensagens.csv').decode('utf-8-sig')
        self.assertIn('mensagem que precisa ir junto', mensagens)
        self.assertIn('Cliente Exporta', bundle.read('contatos.csv').decode('utf-8-sig'))

    def test_zip_carries_the_media_files(self):
        self.client.force_login(self.adm)
        bundle = self._zip_from_response(self.client.post(self.export_url))
        midias = [n for n in bundle.namelist() if n.startswith('midias/')]
        self.assertEqual(len(midias), 1)
        self.assertEqual(bundle.read(midias[0]), b'bytes-da-foto')
        # A linha da mensagem aponta para o arquivo dentro do ZIP.
        self.assertIn(midias[0], bundle.read('mensagens.csv').decode('utf-8-sig'))

    def test_zip_never_contains_another_company_data(self):
        self.client.force_login(self.adm)
        bundle = self._zip_from_response(self.client.post(self.export_url))
        tudo = ' '.join(
            bundle.read(nome).decode('utf-8-sig', 'ignore')
            for nome in bundle.namelist() if not nome.startswith('midias/')
        )
        self.assertNotIn('Segredo Vizinho', tudo)
        self.assertNotIn('conversa da vizinha', tudo)
        self.assertNotIn('adm@vizinha.com', tudo)

    def test_zip_never_contains_passwords(self):
        self.client.force_login(self.adm)
        bundle = self._zip_from_response(self.client.post(self.export_url))
        usuarios = bundle.read('usuarios.csv').decode('utf-8-sig')
        self.assertIn('adm@exporta.com', usuarios)
        self.assertNotIn('pbkdf2', usuarios)
        self.assertNotIn('senha', usuarios.split('\n')[0].lower())

    def test_each_company_exports_only_its_own(self):
        self.client.force_login(self.adm_outra)
        bundle = self._zip_from_response(self.client.post(self.export_url))
        contatos = bundle.read('contatos.csv').decode('utf-8-sig')
        self.assertIn('Segredo Vizinho', contatos)
        self.assertNotIn('Cliente Exporta', contatos)

    def test_missing_file_on_disk_does_not_break_the_export(self):
        """Midia perdida no disco nao pode derrubar a exportacao inteira."""
        import os
        caminho = self.com_midia.media_file.path
        os.remove(caminho)
        self.client.force_login(self.adm)
        r = self.client.post(self.export_url)
        self.assertEqual(r.status_code, 200)
        bundle = self._zip_from_response(r)
        self.assertIn('mensagens.csv', bundle.namelist())
        self.assertEqual([n for n in bundle.namelist() if n.startswith('midias/')], [])

    def test_downloaded_file_is_a_named_zip(self):
        self.client.force_login(self.adm)
        r = self.client.post(self.export_url)
        self.assertEqual(r['Content-Type'], 'application/zip')
        self.assertIn('exporta-sa', r['Content-Disposition'])
        self.assertIn('.zip', r['Content-Disposition'])


class SafeCompanyDeletionTests(TestCase):
    """Excluir cliente e irreversivel: exige desativar antes e digitar o nome."""

    def setUp(self):
        from accounts.models import Company
        self.company = Company.objects.create(name='Encerrar SA', slug='encerrar-sa')
        self.master = User.objects.create_user(
            email='master@encerrar.com', password='x', role=User.Role.MASTER
        )
        self.client.force_login(self.master)
        self.url = reverse('clients')

    def _delete(self, **extra):
        payload = {'action': 'delete', 'company_id': self.company.pk}
        payload.update(extra)
        return self.client.post(self.url, payload, follow=True)

    def test_active_company_is_not_deleted(self):
        from accounts.models import Company
        r = self._delete(confirm_name='Encerrar SA')
        self.assertTrue(Company.objects.filter(pk=self.company.pk).exists())
        self.assertContains(r, 'Desative')

    def test_wrong_name_does_not_delete(self):
        from accounts.models import Company
        self.company.is_active = False
        self.company.save(update_fields=['is_active'])
        r = self._delete(confirm_name='outro nome')
        self.assertTrue(Company.objects.filter(pk=self.company.pk).exists())
        self.assertContains(r, 'Nada foi apagado')

    def test_deactivated_and_confirmed_is_deleted(self):
        from accounts.models import Company
        self.company.is_active = False
        self.company.save(update_fields=['is_active'])
        self._delete(confirm_name='encerrar sa')   # confere sem diferenciar maiuscula
        self.assertFalse(Company.objects.filter(pk=self.company.pk).exists())

    def test_default_company_is_never_deleted(self):
        from accounts.models import Company
        padrao = Company.get_default()
        self.client.post(self.url, {
            'action': 'delete', 'company_id': padrao.pk, 'confirm_name': padrao.display_name,
        })
        self.assertTrue(Company.objects.filter(pk=padrao.pk).exists())


class DeletedCompanyLeavesNoFilesTests(TestCase):
    """Excluir a empresa tem de levar os ARQUIVOS junto.

    O `delete()` em cascata limpa o banco, mas o Django nao apaga o arquivo em
    disco: sem isso, fotos e documentos do cliente ficariam no servidor para
    sempre, sem ninguem conseguir ver nem remover pela interface.
    """

    def setUp(self):
        from django.core.files.base import ContentFile
        from accounts.models import Company, Contact, Conversation, Message
        self.company = Company.objects.create(name='Sai Fora SA', slug='sai-fora')
        self.master = User.objects.create_user(
            email='master@saifora.com', password='x', role=User.Role.MASTER
        )
        contato = Contact.objects.create(company=self.company, phone='5516911110000')
        conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id=contato.phone
        )
        self.message = Message.objects.create(
            conversation=conv, direction='in', message_type='image', media_mimetype='image/jpeg',
        )
        self.message.media_file.save('sumir.jpg', ContentFile(b'foto'), save=True)
        self.caminho = self.message.media_file.path

    def test_media_files_are_removed_from_disk(self):
        import os
        from accounts.models import Company
        self.assertTrue(os.path.exists(self.caminho))

        self.company.is_active = False
        self.company.save(update_fields=['is_active'])
        self.client.force_login(self.master)
        self.client.post(reverse('clients'), {
            'action': 'delete', 'company_id': self.company.pk, 'confirm_name': 'Sai Fora SA',
        })

        self.assertFalse(Company.objects.filter(pk=self.company.pk).exists())
        self.assertFalse(os.path.exists(self.caminho))


class MasterAccountsScreenTests(TestCase):
    """Tela GESTORES: um master cadastra outro master, sem precisar do shell do VPS."""

    def setUp(self):
        self.master = User.objects.create_user(
            email='dono@beezap.com', password='x', role=User.Role.MASTER,
            recovery_phone='5511999990000',
        )
        self.client.force_login(self.master)

    def _create(self, **extra):
        dados = {
            'action': 'create',
            'name': 'Socio Novo',
            'email': 'socio@beezap.com',
            'password': 'senhaforte1',
            'phone': '5511988887777',
        }
        dados.update(extra)
        return self.client.post(reverse('masters'), dados)

    def test_master_creates_another_master(self):
        self._create()
        novo = User.objects.get(email='socio@beezap.com')
        self.assertEqual(novo.role, User.Role.MASTER)
        self.assertIsNone(novo.company)          # master fica ACIMA das empresas
        self.assertEqual(novo.recovery_phone, '5511988887777')
        self.assertTrue(novo.must_change_password)
        self.assertTrue(novo.check_password('senhaforte1'))

    def test_new_master_is_forced_to_change_the_initial_password(self):
        self._create()
        self.client.logout()
        self.client.login(email='socio@beezap.com', password='senhaforte1')
        r = self.client.get(reverse('clients'))
        self.assertRedirects(r, reverse('change-initial-password'))

        self.client.post(reverse('change-initial-password'), {
            'new_password': 'outrasenha9', 'confirm_password': 'outrasenha9',
        })
        novo = User.objects.get(email='socio@beezap.com')
        self.assertFalse(novo.must_change_password)
        self.assertTrue(novo.check_password('outrasenha9'))
        self.assertEqual(self.client.get(reverse('clients')).status_code, 200)

    def test_whatsapp_is_required_because_it_is_the_only_recovery_path(self):
        r = self._create(phone='')
        self.assertFalse(User.objects.filter(email='socio@beezap.com').exists())
        self.assertContains(r, 'WhatsApp')

    def test_email_must_be_unique_in_the_whole_system(self):
        from accounts.models import Company
        empresa = Company.objects.create(name='Acme', slug='acme-master')
        User.objects.create_user(email='ocupado@x.com', password='x', company=empresa)
        self._create(email='ocupado@x.com')
        self.assertEqual(User.objects.filter(email='ocupado@x.com').count(), 1)

    def test_client_profiles_cannot_open_or_post(self):
        """A tela e da plataforma: nenhum perfil de cliente entra, nem por POST."""
        from accounts.models import Company
        empresa = Company.objects.create(name='Acme', slug='acme-master-2')
        adm = User.objects.create_user(
            email='adm@acme.com', password='x', role=User.Role.ADM, company=empresa
        )
        self.client.force_login(adm)
        self.assertEqual(self.client.get(reverse('masters')).status_code, 403)
        self.assertEqual(self._create().status_code, 403)
        self.assertFalse(User.objects.filter(email='socio@beezap.com').exists())

    def test_cannot_deactivate_or_delete_yourself(self):
        self.client.post(reverse('masters'), {
            'action': 'toggle-active', 'master_id': self.master.pk,
        })
        self.master.refresh_from_db()
        self.assertTrue(self.master.is_active)

        self.client.post(reverse('masters'), {'action': 'delete', 'master_id': self.master.pk})
        self.assertTrue(User.objects.filter(pk=self.master.pk).exists())

    def test_platform_never_runs_out_of_active_masters(self):
        """Com dois ativos da para desativar um; o ultimo ativo nunca cai."""
        self._create()
        novo = User.objects.get(email='socio@beezap.com')
        self.client.post(reverse('masters'), {'action': 'toggle-active', 'master_id': novo.pk})
        novo.refresh_from_db()
        self.assertFalse(novo.is_active)

        # Sobrou so o dono ativo. Reativando o segundo e entrando com ele (ja com a
        # senha trocada, senao o middleware prende na tela de troca), a trava do
        # "ultimo master ativo" tem que valer para ele tambem.
        novo.is_active = True
        novo.must_change_password = False
        novo.save(update_fields=['is_active', 'must_change_password'])
        self.client.force_login(novo)
        self.client.post(reverse('masters'), {
            'action': 'toggle-active', 'master_id': self.master.pk,
        })
        self.master.refresh_from_db()
        self.assertFalse(self.master.is_active)   # ainda ha outro ativo (novo)

        self.client.post(reverse('masters'), {'action': 'toggle-active', 'master_id': novo.pk})
        novo.refresh_from_db()
        self.assertTrue(novo.is_active)           # seria o ultimo: recusado

    def test_delete_requires_deactivating_first(self):
        self._create()
        novo = User.objects.get(email='socio@beezap.com')
        self.client.post(reverse('masters'), {'action': 'delete', 'master_id': novo.pk})
        self.assertTrue(User.objects.filter(pk=novo.pk).exists())

        self.client.post(reverse('masters'), {'action': 'toggle-active', 'master_id': novo.pk})
        self.client.post(reverse('masters'), {'action': 'delete', 'master_id': novo.pk})
        self.assertFalse(User.objects.filter(pk=novo.pk).exists())

    def test_reset_password_marks_it_as_provisional(self):
        self._create()
        novo = User.objects.get(email='socio@beezap.com')
        novo.must_change_password = False
        novo.save(update_fields=['must_change_password'])

        self.client.post(reverse('masters'), {
            'action': 'reset-password', 'master_id': novo.pk, 'password': 'trocadaagora1',
        })
        novo.refresh_from_db()
        self.assertTrue(novo.check_password('trocadaagora1'))
        self.assertTrue(novo.must_change_password)

    def test_short_reset_password_is_refused(self):
        self._create()
        novo = User.objects.get(email='socio@beezap.com')
        self.client.post(reverse('masters'), {
            'action': 'reset-password', 'master_id': novo.pk, 'password': '123',
        })
        novo.refresh_from_db()
        self.assertTrue(novo.check_password('senhaforte1'))

    def test_saving_the_recovery_whatsapp(self):
        self._create()
        novo = User.objects.get(email='socio@beezap.com')
        self.client.post(reverse('masters'), {
            'action': 'save-phone', 'master_id': novo.pk, 'phone': '55 (11) 96666-5555',
        })
        novo.refresh_from_db()
        self.assertEqual(novo.recovery_phone, '5511966665555')

    def test_menu_shows_gestores_for_master_only(self):
        from accounts.permissions import nav_items_for
        labels = [i['label'] for i in nav_items_for(self.master, '')]
        self.assertEqual(labels, ['Clientes', 'Métricas', 'Inteligência (IA)', 'Gestores'])


class MasterPasswordRecoveryTests(TestCase):
    """O master recupera a senha como todo mundo: codigo no WhatsApp dele.

    Ele nao tem empresa nem perfil de atendente, entao o telefone vem do
    `recovery_phone` (tela Gestores) e o envio sai pela instancia da EMPRESA PADRAO
    — ver `create_and_send_password_recovery_code`.
    """

    def setUp(self):
        self.master = User.objects.create_user(
            email='dono@beezap.com', password='antiga123', role=User.Role.MASTER,
            recovery_phone='5511977776666',
        )

    def test_recovery_uses_the_master_own_whatsapp(self):
        from accounts.views import get_user_recovery_phone
        self.assertEqual(get_user_recovery_phone(self.master), '5511977776666')

    def test_code_is_sent_by_the_default_company_instance(self):
        enviados = []

        def _fake_send(phone, message, company):
            enviados.append((phone, message, company))
            return SimpleNamespace(success=True)

        with patch('accounts.views.send_text_message', side_effect=_fake_send):
            self.client.post(reverse('password-recovery-request'), {'email': 'dono@beezap.com'})

        self.assertEqual(len(enviados), 1)
        self.assertEqual(enviados[0][0], '5511977776666')
        from accounts.models import Company
        self.assertEqual(enviados[0][2], Company.get_default())
        self.assertTrue(PasswordResetCode.objects.filter(user=self.master, used_at__isnull=True).exists())

    def test_master_without_phone_gets_no_code(self):
        """Sem WhatsApp cadastrado nao ha por onde mandar."""
        self.master.recovery_phone = ''
        self.master.save(update_fields=['recovery_phone'])
        with patch('accounts.views.send_text_message') as enviar:
            self.client.post(reverse('password-recovery-request'), {'email': 'dono@beezap.com'})
        enviar.assert_not_called()
        self.assertFalse(PasswordResetCode.objects.filter(user=self.master).exists())


class CompanyAiUsageModelTests(TestCase):
    """Contador de IA por empresa e por mes (medicao, sem limite nem bloqueio)."""

    def setUp(self):
        from accounts.models import Company
        self.acme = Company.objects.create(name='Acme', slug='acme')
        self.beta = Company.objects.create(name='Beta', slug='beta')

    def test_first_call_creates_the_month_and_sums(self):
        from accounts.models import CompanyAiUsage
        CompanyAiUsage.record(self.acme, 100, 40, 140)
        ano, mes = CompanyAiUsage.reference()
        linha = CompanyAiUsage.objects.get(company=self.acme, year=ano, month=mes)
        self.assertEqual(linha.total_requests, 1)
        self.assertEqual(linha.total_prompt_tokens, 100)
        self.assertEqual(linha.total_completion_tokens, 40)
        self.assertEqual(linha.total_tokens, 140)
        self.assertIsNotNone(linha.first_used_at)
        self.assertIsNotNone(linha.last_used_at)

    def test_more_calls_reuse_the_same_month_row(self):
        from accounts.models import CompanyAiUsage
        CompanyAiUsage.record(self.acme, 10, 5, 15)
        CompanyAiUsage.record(self.acme, 20, 10, 30)
        self.assertEqual(CompanyAiUsage.objects.filter(company=self.acme).count(), 1)
        totais = CompanyAiUsage.month_totals(self.acme)
        self.assertEqual(totais['chamadas'], 2)
        self.assertEqual(totais['tokens'], 45)

    def test_total_is_calculated_when_the_api_does_not_send_it(self):
        from accounts.models import CompanyAiUsage
        CompanyAiUsage.record(self.acme, 7, 3)
        self.assertEqual(CompanyAiUsage.month_totals(self.acme)['tokens'], 10)

    def test_one_company_never_counts_on_another(self):
        from accounts.models import CompanyAiUsage
        CompanyAiUsage.record(self.acme, 100, 100, 200)
        self.assertEqual(CompanyAiUsage.month_totals(self.acme)['tokens'], 200)
        self.assertEqual(CompanyAiUsage.month_totals(self.beta)['tokens'], 0)

    def test_month_without_use_comes_zeroed(self):
        """A tela nao precisa tratar mes sem consumo — vem zero, nao None."""
        from accounts.models import CompanyAiUsage
        totais = CompanyAiUsage.month_totals(self.beta)
        self.assertEqual(totais['tokens'], 0)
        self.assertEqual(totais['chamadas'], 0)
        self.assertIsNone(totais['ultimo_uso'])

    def test_previous_month_crosses_the_year(self):
        from accounts.models import CompanyAiUsage
        self.assertEqual(CompanyAiUsage.previous_reference(2027, 1), (2026, 12))
        self.assertEqual(CompanyAiUsage.previous_reference(2026, 8), (2026, 7))

    def test_all_time_totals_add_up_the_months(self):
        from accounts.models import CompanyAiUsage
        ano, mes = CompanyAiUsage.reference()
        ano_ant, mes_ant = CompanyAiUsage.previous_reference(ano, mes)
        CompanyAiUsage.objects.create(
            company=self.acme, year=ano_ant, month=mes_ant,
            total_requests=3, total_tokens=300,
        )
        CompanyAiUsage.record(self.acme, 50, 50, 100)
        acumulado = CompanyAiUsage.all_time_totals(self.acme)
        self.assertEqual(acumulado['tokens'], 400)
        self.assertEqual(acumulado['chamadas'], 4)
        # O mes atual continua separado do acumulado.
        self.assertEqual(CompanyAiUsage.month_totals(self.acme)['tokens'], 100)

    def test_without_company_nothing_is_recorded(self):
        """Chamada da plataforma (ex.: teste de conexao) conta so no total geral."""
        from accounts.models import CompanyAiUsage
        self.assertIsNone(CompanyAiUsage.record(None, 10, 10, 20))
        self.assertEqual(CompanyAiUsage.objects.count(), 0)

    def test_deleting_the_company_removes_its_usage(self):
        from accounts.models import CompanyAiUsage
        CompanyAiUsage.record(self.acme, 10, 10, 20)
        self.acme.delete()
        self.assertEqual(CompanyAiUsage.objects.count(), 0)


class GptUsagePerCompanyTests(TestCase):
    """A mesma chamada ao GPT conta na PLATAFORMA e na empresa que a gerou."""

    def setUp(self):
        from accounts.models import Company, OpenAiConfiguration
        self.company = Company.objects.create(name='Acme', slug='acme')
        config = OpenAiConfiguration.get_solo()
        config.api_key = 'sk-teste'
        config.save(update_fields=['api_key'])

    def _fake_urlopen(self):
        class _Resp:
            status = 200

            def read(self):
                return (
                    b'{"choices":[{"message":{"content":"ok"}}],'
                    b'"usage":{"prompt_tokens":30,"completion_tokens":12,"total_tokens":42}}'
                )

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _urlopen(req, timeout=None):
            return _Resp()

        return _urlopen

    def test_call_with_company_counts_in_both_counters(self):
        from accounts.models import CompanyAiUsage, OpenAiConfiguration
        from gpt import client as gpt_client
        with patch.object(gpt_client.request, 'urlopen', self._fake_urlopen()):
            result = gpt_client.chat_completion(
                [{'role': 'user', 'content': 'oi'}], company=self.company
            )
        self.assertTrue(result.success)
        self.assertEqual(OpenAiConfiguration.get_solo().total_tokens, 42)
        self.assertEqual(CompanyAiUsage.month_totals(self.company)['tokens'], 42)
        self.assertEqual(CompanyAiUsage.month_totals(self.company)['chamadas'], 1)

    def test_call_without_company_counts_only_in_the_platform(self):
        from accounts.models import CompanyAiUsage, OpenAiConfiguration
        from gpt import client as gpt_client
        with patch.object(gpt_client.request, 'urlopen', self._fake_urlopen()):
            gpt_client.chat_completion([{'role': 'user', 'content': 'oi'}])
        self.assertEqual(OpenAiConfiguration.get_solo().total_tokens, 42)
        self.assertEqual(CompanyAiUsage.objects.count(), 0)

    def test_the_virtual_attendant_reports_the_conversation_company(self):
        """O disparo real da IA passa a empresa da conversa — e o que faz a medicao
        por cliente existir de verdade (nao so na API do cliente do GPT)."""
        from accounts.models import (
            CompanyAiUsage, Contact, Conversation, MenuBotConfiguration, Message,
        )
        from gpt import attendant, client as gpt_client_module

        config = MenuBotConfiguration.for_company(self.company)
        config.mode = MenuBotConfiguration.MODE_AI
        config.save(update_fields=['mode'])

        contato = Contact.objects.create(company=self.company, phone='5516999990000')
        conversa = Conversation.objects.create(
            company=self.company, contact=contato, external_id=contato.phone, status='open',
        )
        Message.objects.create(
            conversation=conversa, direction='in', message_type='text', text='ola',
        )

        with patch.object(gpt_client_module.request, 'urlopen', self._fake_urlopen()), \
                patch('gpt.attendant._send_ai_reply'):
            attendant.handle_incoming_for_ai(conversa.id)

        self.assertEqual(CompanyAiUsage.month_totals(self.company)['tokens'], 42)


class PlatformMetricsScreenTests(TestCase):
    """Tela METRICAS (todos os clientes): numeros da carteira, sem conteudo."""

    def setUp(self):
        from accounts.models import (
            Company, CompanyAiUsage, Contact, Conversation, MenuBotConfiguration,
            Message, WapiWebhookEvent,
        )
        self.acme = Company.objects.create(name='Acme Cliente', slug='acme')
        self.beta = Company.objects.create(name='Beta Cliente', slug='beta')
        self.master = User.objects.create_user(
            email='master@plataforma.com', password='x', role=User.Role.MASTER
        )
        self.adm = User.objects.create_user(
            email='adm@acme.com', password='x', role=User.Role.ADM, company=self.acme
        )

        contato = Contact.objects.create(
            company=self.acme, phone='5516988887777', name='Joana Segredo'
        )
        self.conversa = Conversation.objects.create(
            company=self.acme, contact=contato, external_id=contato.phone, status='open',
        )
        Message.objects.create(
            conversation=self.conversa, direction='in', message_type='text',
            text='meu cartao e 1234',
        )
        Message.objects.create(
            conversation=self.conversa, direction='out', message_type='text',
            text='resposta da IA', is_ai=True,
        )
        Conversation.objects.create(
            company=self.acme, chat_type='group',
            external_id='120363000000000009@g.us', name='Grupo Confidencial',
        )
        Conversation.objects.create(
            company=self.beta, chat_type='private', external_id='5516900000000',
            status='pending',
        )
        WapiWebhookEvent.objects.create(company=self.acme, event_type='message')

        CompanyAiUsage.record(self.acme, 300, 100, 400)
        CompanyAiUsage.record(self.beta, 10, 5, 15)

        config = MenuBotConfiguration.for_company(self.acme)
        config.mode = MenuBotConfiguration.MODE_AI
        config.save(update_fields=['mode'])

        self.url = reverse('platform-metrics')

    # ----- Quem entra -----

    def test_master_opens_the_screen(self):
        self.client.force_login(self.master)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Métricas dos clientes')
        self.assertContains(r, 'Acme Cliente')
        self.assertContains(r, 'Beta Cliente')

    def test_company_admin_cannot_open_the_screen(self):
        self.client.force_login(self.adm)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_the_menu_of_the_master_has_the_screen(self):
        from accounts.permissions import nav_items_for
        labels = [i['label'] for i in nav_items_for(self.master, '')]
        self.assertIn('Métricas', labels)

    # ----- O que a tela NAO mostra -----

    def test_screen_never_shows_conversation_content(self):
        self.client.force_login(self.master)
        r = self.client.get(self.url)
        self.assertNotContains(r, 'meu cartao e 1234')
        self.assertNotContains(r, 'Joana Segredo')
        self.assertNotContains(r, 'Grupo Confidencial')
        self.assertNotContains(r, '5516988887777')

    def test_screen_never_shows_credentials(self):
        from accounts.models import OpenAiConfiguration, WapiConfiguration
        wapi = WapiConfiguration.for_company(self.acme)
        wapi.instance_id = 'INSTANCIA-SECRETA'
        wapi.token = 'TOKEN-SECRETO'
        wapi.save()
        ia = OpenAiConfiguration.get_solo()
        ia.api_key = 'sk-SECRETA'
        ia.save(update_fields=['api_key'])

        self.client.force_login(self.master)
        r = self.client.get(self.url)
        self.assertNotContains(r, 'INSTANCIA-SECRETA')
        self.assertNotContains(r, 'TOKEN-SECRETO')
        self.assertNotContains(r, 'sk-SECRETA')
        self.assertContains(r, 'Configurado')

    # ----- Os numeros -----

    def test_one_row_per_company_with_its_own_numbers(self):
        from accounts.views import build_platform_metrics
        m = build_platform_metrics()
        linhas = {linha['company'].slug: linha for linha in m['linhas']}
        # A "Empresa padrao" da migracao 0031 tambem e um cliente e entra na lista.
        self.assertLessEqual({'acme', 'beta'}, set(linhas))
        self.assertEqual(linhas['acme']['conversas_ativas'], 2)   # direta + grupo
        self.assertEqual(linhas['acme']['mensagens_30d'], 2)
        self.assertEqual(linhas['acme']['automaticas'], 1)
        self.assertEqual(linhas['acme']['ia_tokens_mes'], 400)
        self.assertEqual(linhas['acme']['modo'], 'ai')
        self.assertEqual(linhas['beta']['conversas_aguardando'], 1)
        self.assertEqual(linhas['beta']['mensagens_30d'], 0)
        self.assertEqual(linhas['beta']['ia_tokens_mes'], 15)

    def test_platform_totals_add_up_the_clients(self):
        from accounts.views import build_platform_metrics
        from accounts.models import Company
        totais = build_platform_metrics()['totais']
        # Inclui a "Empresa padrao" (criada na migracao 0031), que tambem e cliente.
        self.assertEqual(totais['clientes'], Company.objects.count())
        self.assertEqual(totais['clientes_ativos'], Company.objects.filter(is_active=True).count())
        self.assertEqual(totais['ia_tokens_mes'], 415)
        self.assertEqual(totais['ia_chamadas_mes'], 2)
        self.assertEqual(totais['clientes_com_ia'], 1)
        self.assertEqual(totais['conversas_ativas'], 3)

    def test_biggest_ai_consumer_comes_first(self):
        from accounts.views import build_platform_metrics
        slugs = [linha['company'].slug for linha in build_platform_metrics()['linhas']]
        self.assertEqual(slugs[0], 'acme')

    def test_inactive_company_goes_to_the_end_but_still_appears(self):
        from accounts.views import build_platform_metrics
        self.acme.is_active = False
        self.acme.save(update_fields=['is_active'])
        slugs = [
            linha['company'].slug for linha in build_platform_metrics()['linhas']
            if linha['company'].slug in ('acme', 'beta')
        ]
        self.assertEqual(slugs, ['beta', 'acme'])

    def test_system_dividers_do_not_count_as_messages(self):
        from accounts.models import Message
        from accounts.views import build_platform_metrics
        Message.objects.create(
            conversation=self.conversa, direction='out', message_type='system',
            text='Atendimento encerrado',
        )
        linhas = {l['company'].slug: l for l in build_platform_metrics()['linhas']}
        self.assertEqual(linhas['acme']['mensagens_30d'], 2)

    def test_platform_counter_is_shown_apart_from_the_client_sum(self):
        """O total da plataforma inclui teste de conexao e o que veio antes da
        medicao por empresa — por isso aparece separado, nao somado."""
        from accounts.models import OpenAiConfiguration
        from accounts.views import build_platform_metrics
        config = OpenAiConfiguration.get_solo()
        config.record_usage(500, 500, 1000)
        m = build_platform_metrics()
        self.assertEqual(m['plataforma']['tokens'], 1000)
        self.assertEqual(m['totais']['ia_tokens_mes'], 415)


class AccentTextColorTests(SimpleTestCase):
    """Cor do texto das INICIAIS calculada pela cor de destaque da empresa.

    Bug real que motivou isto: um cliente cadastrado com destaque `#000000` ficava
    com as iniciais em preto sobre preto na barra lateral — invisiveis.
    """

    def test_dark_accent_gets_light_text(self):
        from accounts.models import readable_text_color
        self.assertEqual(readable_text_color('#000000'), '#ffffff')
        self.assertEqual(readable_text_color('#111827'), '#ffffff')

    def test_light_accent_gets_dark_text(self):
        from accounts.models import readable_text_color
        self.assertEqual(readable_text_color('#ffffff'), '#0b1f3d')
        self.assertEqual(readable_text_color('#f7b500'), '#0b1f3d')

    def test_choice_is_by_real_contrast_not_by_a_threshold(self):
        """Verde claro le melhor com texto escuro — com limiar fixo vinha branco."""
        from accounts.models import readable_text_color
        self.assertEqual(readable_text_color('#21c25e'), '#0b1f3d')
        self.assertEqual(readable_text_color('#1f7a53'), '#ffffff')

    def test_short_form_and_invalid_values_do_not_break(self):
        from accounts.models import readable_text_color
        self.assertEqual(readable_text_color('#000'), '#ffffff')
        for valor in ('', None, 'xyz', '#12', 'nao-e-cor'):
            self.assertEqual(readable_text_color(valor), '#0b1f3d')


class CompanyInitialsContrastTests(TestCase):
    """A barra lateral leva a cor calculada junto do fundo (nao so no CSS)."""

    def test_company_without_accent_falls_back_to_the_default_color(self):
        from accounts.models import Company
        company = Company.objects.create(name='Sem Cor', slug='sem-cor')
        # Sem destaque cadastrado vale o padrao do sistema (verde escuro) -> texto claro.
        self.assertEqual(company.accent_text_color, '#ffffff')

    def test_sidebar_of_a_company_with_black_accent_shows_readable_initials(self):
        from accounts.models import Company
        company = Company.objects.create(name='PPM Teste', slug='ppm-teste', accent_color='#000000')
        adm = User.objects.create_user(
            email='adm@ppmteste.com', password='x', role=User.Role.ADM, company=company
        )
        self.client.force_login(adm)
        r = self.client.get(reverse('conversations'))
        self.assertEqual(r.status_code, 200)
        # Iniciais "PT" (PPM Teste) com fundo preto e texto branco, na mesma tag.
        self.assertContains(r, 'background: #000000; color: #ffffff')


class CompanyBrandScreenTests(TestCase):
    """Aba MARCA: o proprio ADM cadastra o logo e a cor que aparecem no menu.

    Antes isso era exclusivo do gestor master (tela Clientes), entao trocar de logo
    virava pedido de suporte. Logo e cor sao identidade do negocio do cliente — nao
    credencial —, por isso ficam com ele.
    """

    def setUp(self):
        from accounts.models import Company
        self.company = Company.objects.create(name='Marca SA', slug='marca-sa', accent_color='#000000')
        self.outra = Company.objects.create(name='Outra SA', slug='outra-marca')
        self.adm = User.objects.create_user(
            email='adm@marca.com', password='x', role=User.Role.ADM, company=self.company
        )
        self.leitor = User.objects.create_user(
            email='leitor@marca.com', password='x', role=User.Role.LEITOR, company=self.company
        )
        self.master = User.objects.create_user(
            email='master@marca.com', password='x', role=User.Role.MASTER
        )
        self.url = reverse('company-brand')

    def _png(self, nome='logo.png', tamanho=32):
        from django.core.files.uploadedfile import SimpleUploadedFile
        # Conteudo nao precisa ser PNG de verdade: o campo e FileField e a validacao
        # olha extensao e tamanho (sem Pillow no projeto).
        return SimpleUploadedFile(nome, b'x' * tamanho, content_type='image/png')

    # ----- Quem entra -----

    def test_company_admin_opens_the_screen(self):
        self.client.force_login(self.adm)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Marca da empresa')

    def test_master_cannot_open_or_post(self):
        """Negocio do cliente: o master leva 403 inclusive por POST forjado."""
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        r = self.client.post(self.url, {'action': 'save', 'logo': self._png()})
        self.assertEqual(r.status_code, 403)
        self.company.refresh_from_db()
        self.assertFalse(self.company.logo)

    def test_read_only_profile_sees_but_does_not_save(self):
        """O leitor so chega aqui se o admin liberar o botao Configuracoes para ele
        (por padrao o perfil nao tem essa tela). Liberado, ele VE e nao ALTERA."""
        from accounts.models import UserMenuPermission
        UserMenuPermission.objects.create(
            user=self.leitor, allowed_keys=['conversations', 'settings']
        )
        self.client.force_login(self.leitor)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        r = self.client.post(self.url, {'action': 'save', 'logo': self._png()})
        self.assertEqual(r.status_code, 403)
        self.company.refresh_from_db()
        self.assertFalse(self.company.logo)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    # ----- Upload -----

    def test_admin_uploads_the_logo_and_the_sidebar_uses_it(self):
        self.client.force_login(self.adm)
        r = self.client.post(self.url, {'action': 'save', 'accent_color': '#1f7a53',
                                        'logo': self._png()})
        self.assertEqual(r.status_code, 302)
        self.company.refresh_from_db()
        self.assertTrue(self.company.logo)
        self.assertEqual(self.company.accent_color, '#1f7a53')
        # A barra lateral de qualquer tela passa a mostrar o arquivo.
        sidebar = self.client.get(reverse('conversations')).content.decode('utf-8', 'ignore')
        self.assertIn(self.company.logo.url, sidebar)
        self.company.logo.delete(save=False)

    def test_upload_only_touches_the_own_company(self):
        """A empresa vem de quem esta logado — nao ha id de empresa no endpoint."""
        self.client.force_login(self.adm)
        self.client.post(self.url, {'action': 'save', 'logo': self._png()})
        self.outra.refresh_from_db()
        self.assertFalse(self.outra.logo)
        self.company.refresh_from_db()
        self.company.logo.delete(save=False)

    def test_wrong_extension_is_refused_with_a_clear_message(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.adm)
        r = self.client.post(self.url, {
            'action': 'save',
            'logo': SimpleUploadedFile('logo.txt', b'x', content_type='text/plain'),
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'PNG, JPG, WEBP ou SVG')
        self.company.refresh_from_db()
        self.assertFalse(self.company.logo)

    def test_file_over_the_size_limit_is_refused(self):
        from accounts.forms import CompanyBrandForm
        self.client.force_login(self.adm)
        grande = self._png(tamanho=(CompanyBrandForm.LOGO_MAX_MB * 1024 * 1024) + 10)
        r = self.client.post(self.url, {'action': 'save', 'logo': grande})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'no máximo')
        self.company.refresh_from_db()
        self.assertFalse(self.company.logo)

    def test_invalid_color_is_refused(self):
        self.client.force_login(self.adm)
        r = self.client.post(self.url, {'action': 'save', 'accent_color': 'azul'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Escolha uma cor válida.')

    # ----- Remocao -----

    def test_removing_the_logo_brings_the_initials_back(self):
        self.client.force_login(self.adm)
        self.client.post(self.url, {'action': 'save', 'logo': self._png()})
        self.company.refresh_from_db()
        self.assertTrue(self.company.logo)

        r = self.client.post(self.url, {'action': 'remove-logo'})
        self.assertEqual(r.status_code, 302)
        self.company.refresh_from_db()
        self.assertFalse(self.company.logo)
        # Sem logo, a barra lateral volta para as iniciais na cor de destaque.
        sidebar = self.client.get(reverse('conversations')).content.decode('utf-8', 'ignore')
        self.assertIn('sidebar-initials', sidebar)

    def test_removing_when_there_is_no_logo_does_not_break(self):
        self.client.force_login(self.adm)
        r = self.client.post(self.url, {'action': 'remove-logo'}, follow=True)
        self.assertEqual(r.status_code, 200)

    # ----- A aba -----

    def test_the_tab_appears_for_the_client_and_not_for_the_master(self):
        self.client.force_login(self.adm)
        pagina = self.client.get(reverse('atendimento')).content.decode('utf-8', 'ignore')
        self.assertIn(self.url, pagina)

        self.client.force_login(self.master)
        # O master nem alcanca a area de Atendimento (403); a aba nao existe para ele.
        self.assertEqual(self.client.get(reverse('atendimento')).status_code, 403)


class WebhookEventsPanelPrivacyTests(TestCase):
    """A tela WhatsApp e do gestor master — e nao pode mostrar o atendimento.

    Regressao de um buraco real: o painel "Ultimos eventos recebidos" renderizava o
    TEXTO da mensagem (`short_text`), o TELEFONE e o NOME do contato. Como a tela e
    exclusiva do master (`require_master_in_company`), a unica pessoa que a abria era
    justamente a que, pela regra do produto, nao le o atendimento de ninguem
    (docs/CONTEXTO.md secao 16).

    De quebra, o endpoint do poll exigia `role == 'adm'`, entao devolvia 403 para o
    master: o painel nunca atualizava e o JavaScript engolia o erro a cada 5s.
    """

    SEGREDO = 'Preciso do orcamento de 12 mil reais'
    TELEFONE = '5519988887777'
    CONTATO = 'Joao da Silva'

    def setUp(self):
        from .models import Company, WapiConfiguration, WapiWebhookEvent
        self.company = Company.objects.create(name='Cliente Alfa', slug='cliente-alfa')
        self.master = User.objects.create_user(
            email='master-eventos@x.com', password='SenhaForte123', role=User.Role.MASTER,
        )
        WapiConfiguration.objects.create(
            company=self.company, instance_id='INST-1', token='TOKEN-1',
        )
        WapiWebhookEvent.objects.create(
            company=self.company, event_type='message', message_type='text',
            phone=self.TELEFONE, contact_name=self.CONTATO, message_text=self.SEGREDO,
        )

    def _entrar_no_painel(self):
        self.client.force_login(self.master)
        session = self.client.session
        session['active_company_id'] = self.company.pk
        session.save()

    def test_tela_nao_mostra_texto_telefone_nem_nome_do_contato(self):
        self._entrar_no_painel()
        response = self.client.get(reverse('wapi-settings'))
        self.assertEqual(response.status_code, 200)
        corpo = response.content.decode()
        self.assertNotIn(self.SEGREDO, corpo)
        self.assertNotIn(self.TELEFONE, corpo)
        self.assertNotIn(self.CONTATO, corpo)

    def test_tela_ainda_mostra_que_a_mensagem_chegou(self):
        """O painel continua util: o master confirma que o canal esta recebendo."""
        self._entrar_no_painel()
        response = self.client.get(reverse('wapi-settings'))
        self.assertContains(response, 'Recebida em')

    def test_poll_de_eventos_responde_ao_master_no_painel(self):
        self._entrar_no_painel()
        response = self.client.get(
            reverse('wapi-webhook-events'),
            headers={'x-requested-with': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_poll_de_eventos_nao_devolve_conteudo_de_atendimento(self):
        self._entrar_no_painel()
        response = self.client.get(reverse('wapi-webhook-events'))
        evento = response.json()['events'][0]
        self.assertNotIn('message_text', evento)
        self.assertNotIn('phone', evento)
        self.assertNotIn('contact_name', evento)
        self.assertEqual(evento['message_type'], 'text')
        self.assertEqual(evento['direction'], 'Recebida')

    def test_poll_de_eventos_barra_o_adm_do_cliente(self):
        """A tela e do master; o endpoint que a alimenta tem a MESMA guarda."""
        adm = User.objects.create_user(
            email='adm-eventos@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.company,
        )
        adm.attendant_profile.must_change_password = False
        adm.attendant_profile.save(update_fields=['must_change_password'])
        self.client.force_login(adm)
        response = self.client.get(reverse('wapi-webhook-events'))
        self.assertEqual(response.status_code, 403)

    def test_poll_de_eventos_barra_o_master_fora_do_painel(self):
        self.client.force_login(self.master)
        response = self.client.get(reverse('wapi-webhook-events'))
        self.assertEqual(response.status_code, 403)


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
        from .models import (Attendant as Att, Contact, Conversation, Sector,
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
        from .models import UserMenuPermission
        UserMenuPermission.objects.filter(user=self.user).update(allowed_keys=[])
        response = self.client.post(
            reverse('conversation-name-contact'),
            {'number': '5519333334444', 'name': 'Nome Novo'},
        )
        self.assertEqual(response.status_code, 403)


class MasterCannotTouchClientAttendanceTests(TestCase):
    """O master nao OPERA o atendimento — nem por endpoint AJAX, no modo suporte.

    Regressao: `conversation-name-contact` nao tinha guarda nenhuma de master, entao
    ele gravava contato dentro da empresa do cliente por POST, enquanto a tela
    Contatos devolvia 403 para ele. `deny_master_json` (que a secao 16 ja descrevia
    como instalada, mas que nunca era chamada) passou a ser realmente aplicada nos
    endpoints de atendimento.
    """

    def setUp(self):
        from .models import Company, Contact, Conversation
        self.company = Company.objects.create(name='Cliente Beta', slug='cliente-beta')
        self.master = User.objects.create_user(
            email='master-ajax@x.com', password='SenhaForte123', role=User.Role.MASTER,
        )
        contato = Contact.objects.create(
            company=self.company, name='', phone='5519222223333',
        )
        self.conversation = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519222223333',
            chat_type='private',
        )
        self.client.force_login(self.master)
        session = self.client.session
        session['active_company_id'] = self.company.pk
        session.save()

    def test_master_nao_nomeia_contato_do_cliente(self):
        from .models import Contact
        response = self.client.post(
            reverse('conversation-name-contact'),
            {'number': '5519444445555', 'name': 'Criado pelo master'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Contact.objects.filter(company=self.company, name='Criado pelo master').exists()
        )

    def test_master_nao_lista_conversas_do_cliente(self):
        response = self.client.get(reverse('conversation-list'))
        self.assertEqual(response.status_code, 403)

    def test_master_nao_abre_mensagens_do_cliente(self):
        response = self.client.get(
            reverse('conversation-messages', args=[self.conversation.id])
        )
        self.assertEqual(response.status_code, 403)

    def test_master_nao_sincroniza_grupos_do_cliente(self):
        response = self.client.post(reverse('conversation-sync-groups'))
        self.assertEqual(response.status_code, 403)

    def test_master_nao_assume_nem_encerra_atendimento(self):
        for nome in ('conversation-take', 'conversation-close', 'conversation-transfer'):
            with self.subTest(endpoint=nome):
                response = self.client.post(reverse(nome, args=[self.conversation.id]))
                self.assertEqual(response.status_code, 403)


class AutomaticTextsUseClientBrandTests(TestCase):
    """Quem se apresenta ao cliente final e a EMPRESA, nunca o nome do sistema.

    Regressao dupla: os textos padrao do chatbot e da IA traziam 'BEEZAP' fixo, ou
    seja (a) o nome ANTIGO do produto depois da troca de marca e (b) o nome de um
    produto que o cliente final nao conhece — quem fala com ele e a empresa dele.
    """

    def setUp(self):
        from .models import Company, MenuBotConfiguration
        self.company = Company.objects.create(name='PPM Servicos', slug='ppm-servicos')
        self.config = MenuBotConfiguration.for_company(self.company)

    def test_saudacao_padrao_usa_o_nome_da_empresa(self):
        from chatbot.handler import build_menu_text
        texto = build_menu_text(self.config)
        self.assertIn('PPM Servicos', texto)
        self.assertNotIn('{empresa}', texto)
        self.assertNotIn('BEEZAP', texto)
        self.assertNotIn('BEEonBOARD', texto)

    def test_saudacao_padrao_ainda_resolve_a_saudacao_do_horario(self):
        from chatbot.handler import build_menu_text
        texto = build_menu_text(self.config)
        self.assertNotIn('{saudacao}', texto)
        self.assertTrue(
            any(p in texto for p in ('Bom dia', 'Boa tarde', 'Boa noite')),
            texto,
        )

    def test_empresa_e_resolvida_em_texto_escrito_pelo_cliente(self):
        """O ADM pode usar {empresa} em qualquer um dos textos da tela."""
        from chatbot.handler import (build_menu_text, render_confirmation,
                                     render_placeholders,
                                     resolved_handoff_message)
        from .models import Sector
        self.config.greeting = 'Aqui e a {empresa}, {saudacao}!'
        self.config.menu_intro = 'A {empresa} agradece o contato. Escolha:'
        self.config.confirmation_message = 'A {empresa} vai te levar ao setor {setor}.'
        self.config.handoff_message = 'Um atendente da {empresa} vai te chamar.'
        self.config.save()
        texto = build_menu_text(self.config)
        self.assertIn('Aqui e a PPM Servicos', texto)
        self.assertIn('A PPM Servicos agradece', texto)
        setor = Sector.objects.create(company=self.company, name='Comercial')
        confirmacao = render_confirmation(self.config, setor)
        self.assertIn('PPM Servicos', confirmacao)
        self.assertIn('Comercial', confirmacao)
        handoff = render_placeholders(resolved_handoff_message(self.config), self.config)
        self.assertIn('PPM Servicos', handoff)

    def test_prompt_da_ia_diz_em_nome_de_quem_ela_atende(self):
        from gpt.attendant import DEFAULT_INSTRUCTIONS, build_system_prompt
        from .models import OpenAiConfiguration
        self.assertNotIn('BEEZAP', DEFAULT_INSTRUCTIONS)
        prompt = build_system_prompt(OpenAiConfiguration.get_solo(), self.company)
        self.assertIn('PPM Servicos', prompt)
        self.assertNotIn('BEEZAP', prompt)
        self.assertNotIn('BEEonBOARD', prompt)

    def test_prompt_da_ia_nao_quebra_sem_empresa(self):
        """Retaguarda: sem empresa, o prompt sai sem a linha, nao com nome errado."""
        from gpt.attendant import build_system_prompt
        from .models import OpenAiConfiguration
        prompt = build_system_prompt(OpenAiConfiguration.get_solo(), None)
        self.assertNotIn('em nome da empresa', prompt)
        self.assertIn('Responda SEMPRE em JSON', prompt)

    def test_nenhum_texto_para_o_cliente_final_cita_o_sistema(self):
        """Varredura dos padroes: nenhum deles pode trazer nome de produto."""
        from chatbot import handler
        from gpt import attendant
        padroes = (
            handler.DEFAULT_GREETING, handler.DEFAULT_MENU_INTRO,
            handler.DEFAULT_INVALID_MESSAGE, handler.DEFAULT_CONFIRMATION_MESSAGE,
            handler.DEFAULT_HANDOFF_MESSAGE,
            attendant.DEFAULT_INSTRUCTIONS, attendant.HANDOFF_NOTICE,
        )
        for texto in padroes:
            for nome in ('BEEZAP', 'BEEZap', 'Beezap', 'BEEonBOARD'):
                with self.subTest(nome=nome, texto=texto[:40]):
                    self.assertNotIn(nome, texto)


class WebhookDoesNotWaitForMediaDownloadTests(TestCase):
    """O webhook NAO pode ficar preso baixando arquivo.

    O download rodava dentro da requisicao: `_download_to_media_file` faz
    `urlopen(timeout=60)` com DUAS tentativas, mais a chamada `download-media` a
    W-API antes dela. Com `--workers 2 --timeout 60`, duas fotos de link lento
    travavam o sistema INTEIRO — todas as empresas, todas as telas — e o gunicorn
    matava o worker no meio do download.

    A mensagem ja nasce `media_status='pending'` e o front ja mostra a midia aparecer
    no poll seguinte, entao o webhook so precisava nao esperar.
    """

    def _payload_de_imagem(self):
        return {
            'instanceId': 'INSTANCIA-TESTE',
            'chat': {'id': '5519988887777'},
            'sender': {'id': '5519988887777', 'pushName': 'Cliente'},
            'messageId': 'MSG-IMG-1',
            'msgContent': {
                'imageMessage': {
                    'mimetype': 'image/jpeg',
                    'mediaKey': 'CHAVE',
                    'directPath': '/v/caminho',
                    'caption': 'olha a foto',
                }
            },
        }

    def test_ingest_nao_baixa_a_midia_na_propria_requisicao(self):
        from wapi.services import ingest_wapi_payload
        with patch('wapi.services._try_download_media') as sincrono, \
                patch('wapi.services.download_incoming_media_async') as background:
            mensagem = ingest_wapi_payload(
                self._payload_de_imagem(), trigger_ai=False,
                company=default_company(),
            )
        self.assertIsNotNone(mensagem)
        self.assertFalse(
            sincrono.called,
            'o download nao pode acontecer dentro da requisicao do webhook',
        )
        self.assertTrue(background.called, 'o download tem que ir para background')

    def test_a_mensagem_e_a_conversa_ficam_prontas_sem_esperar_o_arquivo(self):
        """A lista de conversas mostra "Imagem" na hora, antes do download acabar."""
        from wapi.services import ingest_wapi_payload
        with patch('wapi.services.download_incoming_media_async'):
            mensagem = ingest_wapi_payload(
                self._payload_de_imagem(), trigger_ai=False,
                company=default_company(),
            )
        self.assertEqual(mensagem.message_type, 'image')
        self.assertEqual(mensagem.media_status, 'pending')
        mensagem.conversation.refresh_from_db()
        self.assertIn('Imagem', mensagem.conversation.last_message_text)
        self.assertIsNotNone(mensagem.conversation.last_message_at)

    def test_link_morto_nao_estoura_para_fora_da_thread(self):
        """A thread engole a falha: link expirado nao pode virar erro no webhook."""
        import threading as _threading
        from wapi.services import download_incoming_media_async, ingest_wapi_payload

        terminou = _threading.Event()

        def _explode(message, media):
            try:
                raise RuntimeError('link morto')
            finally:
                terminou.set()

        with patch('wapi.services.download_incoming_media_async'):
            mensagem = ingest_wapi_payload(
                self._payload_de_imagem(), trigger_ai=False,
                company=default_company(),
            )
        with patch('wapi.services._try_download_media', _explode):
            # Nao levanta: a excecao morre no worker, logada.
            self.assertTrue(download_incoming_media_async(mensagem, {}))
        self.assertTrue(terminou.wait(timeout=5))
        mensagem.refresh_from_db()
        # A mensagem continua no chat, so sem o arquivo local.
        self.assertEqual(mensagem.message_type, 'image')
        self.assertIn('Imagem', mensagem.conversation.last_message_text)

    def test_sincronizacao_de_eventos_antigos_continua_baixando_na_hora(self):
        """Na linha de comando, bloquear e o certo: o comando deve terminar FEITO."""
        from wapi.services import ingest_wapi_payload
        with patch('wapi.services._try_download_media') as sincrono, \
                patch('wapi.services.download_incoming_media_async') as background:
            ingest_wapi_payload(
                self._payload_de_imagem(), trigger_ai=False,
                company=default_company(), download_media_async=False,
            )
        self.assertTrue(sincrono.called)
        self.assertFalse(background.called)
