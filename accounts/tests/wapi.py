"""Recebimento da W-API: parser, webhook e ingestao das mensagens.
"""

from .base import (
    SimpleTestCase,
    TestCase,
    User,
    default_company,
    is_group_jid,
    is_ignorable_jid,
    is_status_or_broadcast,
    normalize_phone,
    normalize_wapi_message_context,
    patch,
    reverse,
)


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
        from ..models import Company, WapiConfiguration, WapiWebhookEvent
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
class PruneWapiEventsCommandTests(TestCase):
    """Expurgo dos eventos antigos do webhook.

    `WapiWebhookEvent` guarda o payload BRUTO de todo evento recebido e nada nunca
    apagava nada — e a tabela que mais cresce, e o mesmo JSON ainda fica duplicado em
    `Message.raw_payload` (que e o usado de verdade pelo retry de midia).
    """

    def setUp(self):
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from ..models import Company, WapiWebhookEvent
        self.company = Company.get_default()
        self.outra = Company.objects.create(name='Outra', slug='outra-prune')
        agora = _tz.now()

        def _evento(dias, company=None, texto='oi'):
            evento = WapiWebhookEvent.objects.create(
                company=company or self.company, event_type='message',
                message_text=texto, raw_payload={'grande': 'x' * 100},
            )
            WapiWebhookEvent.objects.filter(pk=evento.pk).update(
                received_at=agora - _td(days=dias)
            )
            return evento

        self.recente = _evento(1)
        self.medio = _evento(120)
        self.antigo = _evento(400)
        self.de_outra = _evento(120, company=self.outra)

    def _rodar(self, **kwargs):
        from io import StringIO
        from django.core.management import call_command
        saida = StringIO()
        call_command('prune_wapi_events', stdout=saida, **kwargs)
        return saida.getvalue()

    def test_dry_run_nao_altera_nada(self):
        from ..models import WapiWebhookEvent
        saida = self._rodar()
        self.assertIn('DRY-RUN', saida)
        self.assertEqual(WapiWebhookEvent.objects.count(), 4)
        self.antigo.refresh_from_db()
        self.assertTrue(self.antigo.raw_payload)

    def test_apply_esvazia_payload_antigo_e_preserva_a_linha(self):
        from ..models import WapiWebhookEvent
        self._rodar(apply=True)
        self.medio.refresh_from_db()
        self.assertEqual(self.medio.raw_payload, {})
        # A linha e as colunas ja extraidas continuam (o historico nao se perde).
        self.assertEqual(self.medio.message_text, 'oi')
        self.assertTrue(WapiWebhookEvent.objects.filter(pk=self.medio.pk).exists())

    def test_apply_preserva_o_evento_recente_inteiro(self):
        self._rodar(apply=True)
        self.recente.refresh_from_db()
        self.assertTrue(self.recente.raw_payload)

    def test_apply_apaga_a_linha_muito_antiga(self):
        from ..models import WapiWebhookEvent
        self._rodar(apply=True)
        self.assertFalse(WapiWebhookEvent.objects.filter(pk=self.antigo.pk).exists())

    def test_zero_em_dias_apagar_nao_apaga_nenhuma_linha(self):
        from ..models import WapiWebhookEvent
        self._rodar(apply=True, dias_apagar=0)
        self.assertTrue(WapiWebhookEvent.objects.filter(pk=self.antigo.pk).exists())
        self.antigo.refresh_from_db()
        self.assertEqual(self.antigo.raw_payload, {})

    def test_escopo_por_empresa(self):
        self._rodar(apply=True, empresa='outra-prune')
        self.de_outra.refresh_from_db()
        self.medio.refresh_from_db()
        self.assertEqual(self.de_outra.raw_payload, {})
        # A empresa que nao estava no escopo ficou intacta.
        self.assertTrue(self.medio.raw_payload)

    def test_empresa_inexistente_avisa_sem_quebrar(self):
        from io import StringIO
        from django.core.management import call_command
        erros = StringIO()
        call_command('prune_wapi_events', empresa='nao-existe', stderr=erros)
        self.assertIn('nao encontrada', erros.getvalue())
