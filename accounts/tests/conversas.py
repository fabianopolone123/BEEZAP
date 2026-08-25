"""Tela Conversas: lista, mensagens, janela de carregamento, grupos,
midia, contatos e o ciclo de atendimento.
"""

from .base import (
    Attendant,
    SimpleNamespace,
    SimpleTestCase,
    TestCase,
    User,
    default_company,
    normalize_wapi_message_context,
    patch,
    reverse,
)


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
class ConversationTransferViewTests(TestCase):
    """Transferencia manual pelo painel de Conversas."""

    def setUp(self):
        from ..models import Contact, Conversation, Sector
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
        from ..models import Message
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
        from ..models import Conversation, Message
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
        from ..models import Contact, Conversation, Message
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
        from ..models import Conversation
        call_command('merge_contact_conversations')  # sem --apply
        self.assertEqual(Conversation.objects.filter(contact=self.contact).count(), 2)

    def test_apply_merges_into_single_chat_with_divider(self):
        from django.core.management import call_command
        from ..models import Conversation, Message
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
        cfg = WapiConfiguration.for_company(default_company())
        cfg.instance_id = 'i'; cfg.token = 't'; cfg.save()
        self.client.force_login(self.u)
        send_ok = SimpleNamespace(success=True, message_id='w1', error=None)
        with patch('accounts.views.conversations.send_text_message', return_value=send_ok) as mock_send:
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
        with _patch('accounts.views.conversations.MEDIA_LINK_MAX_AGE', -1):
            self.assertEqual(self.client.get(url).status_code, 404)
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
class ConversationCountsAggregateTests(TestCase):
    """Os contadores da tela Conversas saem numa consulta so — com o MESMO numero.

    Antes cada contador era um `.count()` proprio: 5 por status + 3 por tipo = 8
    consultas por carregamento E por poll da lista (12s, por aba). Para nao-admin o
    queryset de visibilidade traz `.distinct()` sobre joins com GroupAccess, entao
    cada uma refazia o join inteiro.

    O risco de trocar por `Count(filter=...)` e o numero mudar por causa da
    duplicacao do join — por isso os testes comparam contra a contagem ingenua.
    """

    def setUp(self):
        from ..models import (Attendant as Att, Contact, Conversation, GroupAccess,
                             Sector)
        self.company = default_company()
        self.adm = User.objects.create_user(
            email='adm-contadores@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.company,
        )
        self.adm.attendant_profile.must_change_password = False
        self.adm.attendant_profile.save(update_fields=['must_change_password'])
        self.user = User.objects.create_user(
            email='usuario-contadores@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=self.company,
        )
        self.attendant = Att.objects.create(
            company=self.company, user=self.user, name='Bia',
            must_change_password=False,
        )
        setor = Sector.objects.create(company=self.company, name='Suporte Contadores')
        setor.attendants.add(self.attendant)
        self.setor = setor

        def _conversa(sufixo, **campos):
            contato = Contact.objects.create(
                company=self.company, name=f'C{sufixo}', phone=f'55199000{sufixo:04d}',
            )
            return Conversation.objects.create(
                company=self.company, contact=contato,
                external_id=f'55199000{sufixo:04d}', chat_type='private', **campos,
            )

        # Um de cada estado que os chips contam.
        _conversa(1, sector=setor, status='pending', unread_count=3)
        _conversa(2, sector=setor, status='open', assigned_attendant=self.attendant)
        _conversa(3, sector=setor, status='closed', assigned_attendant=self.attendant)
        _conversa(4, sector=setor, status='open')
        # Um grupo liberado por DOIS caminhos ao mesmo tempo (setor E usuario): e
        # exatamente o caso que duplica linha no join e inflaria o contador.
        grupo = Conversation.objects.create(
            company=self.company, external_id='1203630001@g.us',
            chat_type='group', name='Grupo Contadores', status='open',
        )
        acesso = GroupAccess.objects.create(conversation=grupo)
        acesso.sectors.add(setor)
        acesso.users.add(self.user)

    def _contagem_ingenua(self, base, mapa):
        return {slug: base.filter(condicao).distinct().count()
                for slug, condicao in mapa.items()}

    def test_contadores_batem_com_a_contagem_ingenua_para_o_admin(self):
        from ..permissions import visible_conversations
        from ..views import (CONVERSATION_COUNT_Q, CONVERSATION_TYPE_COUNT_Q,
                            _conversation_counts, _conversation_type_counts)
        from ..models import Conversation
        base = visible_conversations(self.adm, Conversation.objects.all())
        self.assertEqual(
            _conversation_counts(base),
            self._contagem_ingenua(base, CONVERSATION_COUNT_Q),
        )
        self.assertEqual(
            _conversation_type_counts(base),
            self._contagem_ingenua(base, CONVERSATION_TYPE_COUNT_Q),
        )

    def test_contadores_batem_com_join_duplicado_de_grupo(self):
        """Grupo liberado por setor E por usuario: sem distinct, o numero inflaria."""
        from ..permissions import visible_conversations
        from ..views import (CONVERSATION_COUNT_Q, CONVERSATION_TYPE_COUNT_Q,
                            _conversation_counts, _conversation_type_counts)
        from ..models import Conversation
        base = visible_conversations(self.user, Conversation.objects.all())
        contados = _conversation_counts(base)
        self.assertEqual(contados, self._contagem_ingenua(base, CONVERSATION_COUNT_Q))
        tipos = _conversation_type_counts(base)
        self.assertEqual(tipos, self._contagem_ingenua(base, CONVERSATION_TYPE_COUNT_Q))
        # O grupo aparece UMA vez, nao duas.
        self.assertEqual(tipos['grupos'], 1)

    def test_os_oito_contadores_custam_duas_consultas(self):
        from ..permissions import visible_conversations
        from ..views import _conversation_counts, _conversation_type_counts
        from ..models import Conversation
        base = visible_conversations(self.adm, Conversation.objects.all())
        with self.assertNumQueries(2):
            _conversation_counts(base)
            _conversation_type_counts(base)

    def test_chips_da_tela_continuam_com_os_numeros_certos(self):
        self.client.force_login(self.adm)
        response = self.client.get(reverse('conversations'))
        self.assertEqual(response.status_code, 200)
        chips = {c['key']: c['count'] for c in response.context['filter_chips']}
        self.assertEqual(chips['todas'], 5)
        self.assertEqual(chips['nao-lidas'], 1)
        self.assertEqual(chips['em-atendimento'], 1)
        self.assertEqual(chips['finalizadas'], 1)
        self.assertEqual(response.context['waiting_count'], 2)
class DjangoAdminDoesNotExposeConversationsTests(TestCase):
    """Conversa e mensagem NAO ficam no admin do Django.

    Estavam registradas sem filtro de empresa e com `search_fields = ('text', ...)`:
    qualquer conta `is_staff` lia e PESQUISAVA o texto das conversas de todos os
    clientes por `/admin/` — um caminho que a secao 16 nem mencionava.
    """

    def test_conversa_e_mensagem_fora_do_admin(self):
        from django.contrib import admin as django_admin
        from ..models import Conversation, Message
        registrados = django_admin.site._registry
        self.assertNotIn(Conversation, registrados)
        self.assertNotIn(Message, registrados)

    def test_urls_do_admin_dessas_tabelas_nao_existem(self):
        from django.urls import NoReverseMatch, reverse as _reverse
        for nome in ('admin:accounts_conversation_changelist',
                     'admin:accounts_message_changelist'):
            with self.subTest(url=nome):
                with self.assertRaises(NoReverseMatch):
                    _reverse(nome)

    def test_cadastros_continuam_disponiveis_para_suporte(self):
        from django.urls import reverse as _reverse
        for nome in ('admin:accounts_company_changelist',
                     'admin:accounts_user_changelist',
                     'admin:accounts_contact_changelist'):
            with self.subTest(url=nome):
                self.assertTrue(_reverse(nome))
class ConversationPaginationTests(TestCase):
    """A tela Conversas carrega em JANELA, nao a base inteira.

    Antes: `conversations_view` serializava TODAS as conversas visiveis dentro do
    HTML, `conversation_list_view` repetia a lista completa a cada 12s e
    `conversation_messages_view` fazia `list()` da conversa inteira a cada 6s — por
    aba aberta. Um grupo com anos de historico era lido por completo dez vezes por
    minuto.
    """

    def setUp(self):
        from ..models import Contact, Conversation
        self.company = default_company()
        self.adm = User.objects.create_user(
            email='adm-pag@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.company,
        )
        self.adm.attendant_profile.must_change_password = False
        self.adm.attendant_profile.save(update_fields=['must_change_password'])
        # 70 conversas: mais que a pagina de 60.
        for i in range(70):
            contato = Contact.objects.create(
                company=self.company, name='Cliente %02d' % i,
                phone='5519%09d' % i,
            )
            Conversation.objects.create(
                company=self.company, contact=contato,
                external_id='5519%09d' % i, chat_type='private',
                last_message_text='mensagem %d' % i,
            )
        self.client.force_login(self.adm)

    def test_tela_traz_so_a_primeira_pagina(self):
        from ..views import CONVERSATION_PAGE_SIZE
        response = self.client.get(reverse('conversations'))
        self.assertEqual(len(response.context['conversations']), CONVERSATION_PAGE_SIZE)
        self.assertTrue(response.context['has_more_conversations'])

    def test_contadores_continuam_mostrando_o_total_real(self):
        """A janela e da LISTA; o contador tem que dizer quantas existem."""
        response = self.client.get(reverse('conversations'))
        chips = {c['key']: c['count'] for c in response.context['filter_chips']}
        self.assertEqual(chips['todas'], 70)

    def test_endpoint_da_lista_pagina_e_avisa_que_ha_mais(self):
        response = self.client.get(reverse('conversation-list'))
        dados = response.json()
        self.assertEqual(len(dados['conversations']), 60)
        self.assertTrue(dados['has_more'])
        self.assertEqual(dados['counts']['todas'], 70)

    def test_carregar_mais_traz_o_resto(self):
        response = self.client.get(reverse('conversation-list') + '?limite=120')
        dados = response.json()
        self.assertEqual(len(dados['conversations']), 70)
        self.assertFalse(dados['has_more'])

    def test_limite_tem_teto(self):
        """`?limite=` nao pode ser um jeito de pedir a base inteira por URL."""
        from ..views import MAX_PAGE_SIZE, tamanho_da_pagina
        self.assertEqual(tamanho_da_pagina('999999', 60), MAX_PAGE_SIZE)
        self.assertEqual(tamanho_da_pagina('abc', 60), 60)
        self.assertEqual(tamanho_da_pagina('', 60), 60)
        self.assertEqual(tamanho_da_pagina('10', 60), 10)
class MessageWindowTests(TestCase):
    """O chat carrega as ultimas N mensagens, e a janela nunca corta um atendimento.

    O corte por janela tinha um risco real: as abas "Conversa privada"/"Conversa do
    setor" dependem de ver o segmento COMPLETO para saber de quem ele e e em que setor
    terminou. Por isso a janela e estendida para tras ate a divisoria mais proxima.
    """

    def setUp(self):
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from ..models import Attendant as Att, Contact, Conversation, Message, Sector
        self.company = default_company()
        self.adm = User.objects.create_user(
            email='adm-janela@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.company,
        )
        self.adm.attendant_profile.must_change_password = False
        self.adm.attendant_profile.name = 'Chefe'
        self.adm.attendant_profile.save()
        self.setor = Sector.objects.create(company=self.company, name='Setor Janela')
        contato = Contact.objects.create(
            company=self.company, name='Cliente Janela', phone='5519888887777',
        )
        self.conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519888887777',
            chat_type='private', sector=self.setor,
        )
        base = _tz.now() - _td(days=1)
        self.criadas = []
        for i in range(80):
            msg = Message.objects.create(
                conversation=self.conv, direction='in', message_type='text',
                text='msg %02d' % i,
            )
            Message.objects.filter(pk=msg.pk).update(created_at=base + _td(minutes=i))
            self.criadas.append(msg)
        self.client.force_login(self.adm)

    def _mensagens(self, **params):
        url = reverse('conversation-messages', args=[self.conv.pk])
        if params:
            url += '?' + '&'.join('%s=%s' % kv for kv in params.items())
        return self.client.get(url).json()

    def test_traz_so_a_ultima_pagina(self):
        dados = self._mensagens()
        self.assertEqual(len(dados['messages']), 60)
        self.assertTrue(dados['has_older'])

    def test_traz_as_MAIS_RECENTES(self):
        """O chat abre no fim da conversa, entao a janela e o final do historico."""
        dados = self._mensagens()
        textos = [m['text'] for m in dados['messages']]
        self.assertEqual(textos[-1], 'msg 79')
        self.assertEqual(textos[0], 'msg 20')

    def test_carregar_anteriores_traz_o_resto(self):
        dados = self._mensagens(limite=200)
        self.assertEqual(len(dados['messages']), 80)
        self.assertFalse(dados['has_older'])

    def test_conversa_curta_nao_oferece_anteriores(self):
        from ..models import Contact, Conversation, Message
        contato = Contact.objects.create(
            company=self.company, name='Curta', phone='5519111112222',
        )
        conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519111112222',
            chat_type='private',
        )
        Message.objects.create(
            conversation=conv, direction='in', message_type='text', text='oi',
        )
        url = reverse('conversation-messages', args=[conv.pk])
        dados = self.client.get(url).json()
        self.assertEqual(len(dados['messages']), 1)
        self.assertFalse(dados['has_older'])

    def test_janela_nao_corta_um_atendimento_no_meio(self):
        """A janela e estendida para tras ate a divisoria, senao as abas por dono e
        por setor classificariam errado um atendimento partido."""
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from wapi.services import SYSTEM_NEW_SERVICE_TEXT
        from ..models import Contact, Conversation, Message

        contato = Contact.objects.create(
            company=self.company, name='Partido', phone='5519333334444',
        )
        conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519333334444',
            chat_type='private', sector=self.setor,
        )
        base = _tz.now() - _td(days=2)
        momento = [0]

        def _msg(texto, tipo='text', direcao='in', **extra):
            msg = Message.objects.create(
                conversation=conv, direction=direcao, message_type=tipo,
                text=texto, **extra
            )
            momento[0] += 1
            Message.objects.filter(pk=msg.pk).update(
                created_at=base + _td(minutes=momento[0])
            )
            return msg

        # Atendimento 1 (curto, antigo).
        _msg('antigo 1')
        _msg('antigo 2')
        # Divisoria + atendimento 2 com 70 mensagens: a janela de 60 comeca NO MEIO
        # dele, entao a divisoria tem que ser puxada junto.
        _msg(SYSTEM_NEW_SERVICE_TEXT, tipo='system', direcao='out')
        for i in range(70):
            _msg('novo %02d' % i)

        url = reverse('conversation-messages', args=[conv.pk])
        dados = self.client.get(url).json()
        textos = [m['text'] for m in dados['messages']]
        # A divisoria do atendimento atual esta na janela.
        self.assertIn(SYSTEM_NEW_SERVICE_TEXT, textos)
        # E o atendimento anterior ficou de fora (ainda ha historico antes).
        self.assertNotIn('antigo 1', textos)
        self.assertTrue(dados['has_older'])
        # Todas as mensagens da janela pertencem ao MESMO segmento (o atual).
        segmentos = {m['seg'] for m in dados['messages']}
        self.assertEqual(len(segmentos), 1)

    def test_mapa_de_nomes_do_grupo_usa_as_mensagens_da_janela(self):
        """`_build_name_map` recebe as mensagens ja carregadas — sem 2a varredura."""
        from ..models import Contact, Conversation, Message
        Contact.objects.create(
            company=self.company, name='Participante Fulano', phone='5519777770000',
        )
        grupo = Conversation.objects.create(
            company=self.company, external_id='1203630009@g.us',
            chat_type='group', name='Grupo Janela',
        )
        Message.objects.create(
            conversation=grupo, direction='in', message_type='text',
            text='oi pessoal', sender_id='5519777770000', is_group=True,
        )
        url = reverse('conversation-messages', args=[grupo.pk])
        dados = self.client.get(url).json()
        self.assertEqual(dados['messages'][0]['sender_name'], 'Participante Fulano')


class MergeConversationsIsScopedByCompanyTests(TestCase):
    """`merge_contact_conversations` nao pode unificar conversas de empresas
    diferentes.

    O JID do WhatsApp e GLOBAL: duas empresas clientes podem falar com o mesmo
    grupo (ou o mesmo @lid). A chave de agrupamento das conversas SEM contato era
    `(external_id, chat_type)`, sem a empresa — o comando juntaria o atendimento das
    duas numa conversa so. E o unico comando que escreve unificando conversas.

    A duplicata que motivou o teste apareceu de verdade: o `inspect_wapi_groups`
    listou o grupo `120363257947973768@g.us` DUAS vezes na mesma empresa (dois
    webhooks do mesmo grupo novo chegando juntos criam duas conversas, porque
    `get_or_create_conversation` consulta e depois cria).
    """

    GRUPO = '120363257947973768@g.us'

    def setUp(self):
        from accounts.models import Company, Conversation
        self.Conversation = Conversation
        self.empresa_a = default_company()
        self.empresa_b = Company.objects.create(name='Vizinha', slug='vizinha')

    def _criar(self, empresa, nome='', tipo='group', external_id=None):
        return self.Conversation.objects.create(
            company=empresa, external_id=external_id or self.GRUPO, chat_type=tipo,
            name=nome, status='open',
        )

    def test_nao_junta_o_mesmo_grupo_de_empresas_diferentes(self):
        from django.core.management import call_command
        a = self._criar(self.empresa_a, 'Grupo da A')
        b = self._criar(self.empresa_b, 'Grupo da B')
        call_command('merge_contact_conversations', '--apply')
        self.assertTrue(self.Conversation.objects.filter(pk=a.pk).exists())
        self.assertTrue(self.Conversation.objects.filter(pk=b.pk).exists())
        self.assertEqual(
            self.Conversation.objects.filter(external_id=self.GRUPO).count(), 2
        )

    def test_junta_a_duplicata_dentro_da_mesma_empresa(self):
        """Usa uma DIRETA por @lid: a duplicata de GRUPO na mesma empresa nao existe
        mais (a migration 0039 travou no banco), mas a de @lid continua possivel e
        passa pela mesma chave de agrupamento. Bancos antigos podem ter duplicata de
        grupo de antes da trava, e o comando segue dando conta dela."""
        from django.core.management import call_command
        from accounts.models import Message
        antiga = self._criar(self.empresa_a, 'Compra e vendas', tipo='private',
                             external_id='183545595199545@lid')
        nova = self._criar(self.empresa_a, tipo='private',
                           external_id='183545595199545@lid')
        Message.objects.create(
            conversation=nova, direction='in', message_type='text', text='oi')
        # Uma conversa de OUTRA empresa no meio nao pode ser arrastada.
        vizinha = self._criar(self.empresa_b, 'Grupo da B')

        call_command('merge_contact_conversations', '--apply')

        self.assertEqual(
            self.Conversation.objects.filter(company=self.empresa_a,
                                             external_id='183545595199545@lid').count(), 1)
        self.assertTrue(self.Conversation.objects.filter(pk=antiga.pk).exists())
        self.assertFalse(self.Conversation.objects.filter(pk=nova.pk).exists())
        self.assertTrue(self.Conversation.objects.filter(pk=vizinha.pk).exists())
        # A mensagem da duplicata foi para a conversa canonica.
        self.assertTrue(Message.objects.filter(conversation=antiga, text='oi').exists())
