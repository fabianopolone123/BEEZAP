"""Gestor master: Clientes, Gestores, Metricas, exportacao e o
isolamento entre empresas.
"""

from .base import (
    Attendant,
    PasswordResetCode,
    SimpleNamespace,
    SimpleTestCase,
    TestCase,
    User,
    _json,
    check_password,
    default_company,
    get_messages,
    patch,
    reverse,
)


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
        # A empresa padrao continua alcancavel por for_company (o get_solo() de
        # compatibilidade saiu: so os testes o usavam, e a API atual e for_company).
        self.assertEqual(
            WapiConfiguration.for_company(default_company()).pk, wapi_a.pk
        )

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
        """A busca filtra a LISTA de cartoes.

        Confere o queryset, nao o HTML inteiro: o seletor de contexto da barra
        lateral lista todos os clientes ativos de proposito (e navegacao global, nao
        resultado de busca), entao o nome de um cliente filtrado continua aparecendo
        na pagina — no seletor, nao na lista.
        """
        from accounts.models import Company
        Company.objects.create(name='Padaria Central', slug='padaria-central')
        Company.objects.create(name='Oficina Rapida', slug='oficina-rapida')
        r = self.client.get(reverse('clients'), {'q': 'Padaria'})
        listados = [c.display_name for c in r.context['companies']]
        self.assertIn('Padaria Central', listados)
        self.assertNotIn('Oficina Rapida', listados)
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
        with patch('accounts.views.conversations.send_text_message', return_value=send_ok) as mock_send:
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
        """A faixa de modo suporte nomeia o cliente e da a saida."""
        self._enter()
        r = self.client.get(reverse('clients'))
        self.assertContains(r, 'Modo suporte')
        self.assertContains(r, 'você está no painel de')
        self.assertContains(r, 'Acme')
        self.assertContains(r, 'Sair do painel')

    def test_support_banner_appears_on_every_screen(self):
        """O aviso vive no `base.html`, entao vale para TODA tela.

        Antes ele existia so na tela Clientes (numa faixa) e dentro da barra lateral
        (num cartao, com texto quase igual). Quem entrasse no painel e fosse direto
        para a tela do WhatsApp via o cartao da barra, mas nada no corpo da pagina.
        """
        self._enter()
        for rota in ('clients', 'wapi-settings', 'platform-metrics', 'masters'):
            with self.subTest(rota=rota):
                r = self.client.get(reverse(rota))
                self.assertContains(r, 'support-bar')
                self.assertContains(r, 'is-support-mode')

    def test_no_support_banner_outside_a_client_panel(self):
        r = self.client.get(reverse('clients'))
        self.assertNotContains(r, 'support-bar')
        self.assertNotContains(r, 'is-support-mode')

    def test_sidebar_never_wears_the_client_brand(self):
        """A barra lateral do master continua sendo a DELE, dentro do painel.

        Antes ela trocava logo, nome e cor de destaque pelos do cliente — e a tela
        passava a dizer "voce e a Acme" em vez de "voce esta olhando a Acme", o que
        e justamente o oposto do que um modo suporte deve comunicar.
        """
        self.acme.accent_color = '#ff00ff'
        self.acme.save(update_fields=['accent_color'])
        self._enter()
        r = self.client.get(reverse('clients'))
        marca = r.context['brand']
        self.assertEqual(marca['name'], 'BEEonBOARD')
        self.assertEqual(marca['logo_url'], '')
        self.assertEqual(marca['accent'], '')
        self.assertEqual(marca['initials'], '')
        # (a cor `#ff00ff` ainda aparece nesta tela, mas no CARTAO da Acme — dado da
        # empresa listada, nao identidade da barra lateral.)
        # E o cliente aparece como CONTEXTO, nao como marca.
        self.assertEqual(marca['support_company'].pk, self.acme.pk)


    def test_master_still_sees_no_conversation_in_support_mode(self):
        from accounts.models import Contact, Conversation
        from accounts.permissions import visible_conversations
        contact = Contact.objects.create(company=self.acme, phone='5516999990001')
        Conversation.objects.create(company=self.acme, contact=contact, external_id=contact.phone)
        self._enter()
        self.assertFalse(
            visible_conversations(self.master, Conversation.objects.all()).exists()
        )
class MasterContextSwitcherTests(TestCase):
    """Seletor de contexto: em que cliente estou, e trocar em um clique.

    Antes, para ir de um cliente a outro o master tinha que sair do painel, voltar
    para a tela Clientes e entrar no proximo — tres passos para algo que ele faz o
    tempo todo.
    """

    def setUp(self):
        from accounts.models import Company
        self.master = User.objects.create_user(
            email='master-ctx@x.com', password='SenhaForte123', role=User.Role.MASTER,
        )
        self.acme = Company.objects.create(name='Acme Ltda', slug='acme-ctx')
        self.beta = Company.objects.create(name='Beta Comercio', slug='beta-ctx')
        self.parada = Company.objects.create(
            name='Parada SA', slug='parada-ctx', is_active=False,
        )
        self.client.force_login(self.master)

    def _entrar(self, empresa):
        return self.client.post(
            reverse('clients'), {'action': 'enter', 'company_id': empresa.pk}
        )

    def test_seletor_lista_os_clientes_ativos(self):
        r = self.client.get(reverse('clients'))
        nomes = [e['nome'] for e in r.context['brand']['switch_companies']]
        self.assertIn('Acme Ltda', nomes)
        self.assertIn('Beta Comercio', nomes)

    def test_seletor_nao_lista_empresa_desativada(self):
        """Entrar no painel de empresa desativada nao faz sentido: o webhook dela
        nem recebe mais."""
        r = self.client.get(reverse('clients'))
        nomes = [e['nome'] for e in r.context['brand']['switch_companies']]
        self.assertNotIn('Parada SA', nomes)

    def test_seletor_marca_o_cliente_aberto(self):
        self._entrar(self.acme)
        r = self.client.get(reverse('clients'))
        ativos = [e['nome'] for e in r.context['brand']['switch_companies'] if e['ativa']]
        self.assertEqual(ativos, ['Acme Ltda'])

    def test_sem_painel_aberto_nenhum_fica_marcado(self):
        r = self.client.get(reverse('clients'))
        self.assertEqual(
            [e for e in r.context['brand']['switch_companies'] if e['ativa']], []
        )

    def test_troca_direta_de_um_cliente_para_outro(self):
        """Um POST leva de um painel para o outro, sem passar por 'sair'."""
        self._entrar(self.acme)
        self.assertEqual(
            self.client.session['active_company_id'], self.acme.pk
        )
        self._entrar(self.beta)
        self.assertEqual(
            self.client.session['active_company_id'], self.beta.pk
        )
        r = self.client.get(reverse('clients'))
        self.assertEqual(r.context['brand']['support_company'].pk, self.beta.pk)

    def test_entrar_abre_as_configuracoes_do_cliente(self):
        r = self._entrar(self.acme)
        self.assertRedirects(r, reverse('wapi-settings'))

    def test_cliente_comum_nao_recebe_seletor(self):
        """O seletor e do master; para o cliente a barra lateral nao muda em nada."""
        adm = User.objects.create_user(
            email='adm-ctx@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.acme,
        )
        adm.attendant_profile.must_change_password = False
        adm.attendant_profile.save(update_fields=['must_change_password'])
        self.client.force_login(adm)
        r = self.client.get(reverse('atendimento'))
        marca = r.context['brand']
        self.assertFalse(marca['is_master'])
        self.assertEqual(marca['switch_companies'], [])
        self.assertIsNone(marca['support_company'])
        self.assertNotContains(r, 'ctx-switch')
        # E a marca dele continua sendo a da propria empresa.
        self.assertEqual(marca['name'], 'Acme Ltda')


class MasterMenuGroupsTests(TestCase):
    """O menu do master vem em GRUPOS rotulados, e nao muda de forma.

    Antes, ao entrar no painel de um cliente, o menu simplesmente ganhava um item
    ('WhatsApp') no meio dos itens da plataforma — nada dizia que aquele item era
    daquele cliente.
    """

    def setUp(self):
        from accounts.models import Company
        self.master = User.objects.create_user(
            email='master-menu@x.com', password='SenhaForte123', role=User.Role.MASTER,
        )
        self.acme = Company.objects.create(name='Acme Ltda', slug='acme-menu')
        self.client.force_login(self.master)

    def test_fora_do_painel_so_o_grupo_da_plataforma(self):
        from accounts.permissions import nav_groups_for
        grupos = nav_groups_for(self.master, '', in_company=False)
        self.assertEqual([g['label'] for g in grupos], ['Plataforma'])
        self.assertEqual(
            [i['label'] for i in grupos[0]['items']],
            ['Clientes', 'Métricas', 'Inteligência (IA)', 'Gestores'],
        )

    def test_no_painel_aparece_um_segundo_grupo_com_o_nome_do_cliente(self):
        from accounts.permissions import nav_groups_for
        grupos = nav_groups_for(
            self.master, '', in_company=True, support_company_name='Acme Ltda',
        )
        self.assertEqual(
            [g['label'] for g in grupos], ['Plataforma', 'Cliente · Acme Ltda']
        )
        # Os itens da plataforma NAO se mexeram.
        self.assertEqual(
            [i['label'] for i in grupos[0]['items']],
            ['Clientes', 'Métricas', 'Inteligência (IA)', 'Gestores'],
        )
        self.assertEqual([i['label'] for i in grupos[1]['items']], ['WhatsApp'])

    def test_o_grupo_do_cliente_e_marcado_para_o_css(self):
        from accounts.permissions import nav_groups_for
        grupos = nav_groups_for(
            self.master, '', in_company=True, support_company_name='Acme Ltda',
        )
        self.assertTrue(grupos[1].get('is_client'))
        self.assertFalse(grupos[0].get('is_client'))

    def test_quem_nao_e_master_tem_um_grupo_unico_sem_rotulo(self):
        """Para o cliente, a barra lateral fica exatamente como sempre foi."""
        from accounts.permissions import nav_groups_for
        adm = User.objects.create_user(
            email='adm-menu@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.acme,
        )
        grupos = nav_groups_for(adm, 'Conversas')
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['label'], '')
        self.assertIn('Conversas', [i['label'] for i in grupos[0]['items']])

    def test_a_tela_renderiza_os_grupos(self):
        self.client.post(
            reverse('clients'), {'action': 'enter', 'company_id': self.acme.pk}
        )
        r = self.client.get(reverse('wapi-settings'))
        self.assertContains(r, 'nav-group-label')
        self.assertContains(r, 'Plataforma')
        self.assertContains(r, 'Cliente · Acme Ltda')
        self.assertContains(r, 'nav-group-client')


    def test_master_menu_in_support_mode_is_only_platform_plus_whatsapp(self):
        """O ALCANCE do menu nao mudou com os grupos: e o mesmo conjunto de telas."""
        from accounts.permissions import nav_groups_for
        grupos = nav_groups_for(self.master, '', in_company=True,
                                support_company_name='Acme')
        labels = [i['label'] for g in grupos for i in g['items']]
        self.assertEqual(labels, ['Clientes', 'Métricas', 'Inteligência (IA)', 'Gestores', 'WhatsApp'])

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
        # DIZ o que esta cadastrado, com nome: antes o bloco falava so "conectado" /
        # "disponivel" e ficava a duvida "falta algo meu?".
        self.assertContains(r, 'WhatsApp configurado')
        self.assertContains(r, 'Instância e token cadastrados')
        self.assertContains(r, 'Inteligência (IA) liberada')
        self.assertContains(r, 'API Key do GPT cadastrada')
        # Os dois prontos: o aviso afirmativo aparece.
        self.assertContains(r, 'Tudo configurado')
        # E "configurado", nao "conectado": esta tela so sabe que a credencial existe;
        # se a instancia caiu no WhatsApp, prometer conexao aqui seria mentira.
        self.assertNotContains(r, 'WhatsApp conectado')
        # Nada de credencial na tela do cliente.
        self.assertNotContains(r, 'INSTANCIA-SECRETA')
        self.assertNotContains(r, 'TOKEN-SECRETO')
        self.assertNotContains(r, 'sk-super-secreta')

    def test_status_card_diz_o_que_falta_quando_nao_ha_credencial(self):
        """Sem credencial, o cliente tem de saber que a pendencia NAO e dele."""
        self.client.force_login(self.adm)
        r = self.client.get(reverse('atendimento'))
        self.assertContains(r, 'WhatsApp ainda não configurado')
        self.assertContains(r, 'A IA ainda não foi liberada pelo administrador da plataforma.')
        self.assertNotContains(r, 'Tudo configurado')

    def test_selo_de_estado_nas_telas_do_master(self):
        """O master ve de longe se a credencial existe, sem ler o texto miudo."""
        from accounts.models import OpenAiConfiguration
        self.client.force_login(self.master)
        corpo = self.client.get(reverse('openai-settings')).content.decode()
        self.assertIn('Sem API Key', corpo)
        ai = OpenAiConfiguration.get_solo()
        ai.api_key = 'sk-abc'
        ai.save(update_fields=['api_key'])
        corpo = self.client.get(reverse('openai-settings')).content.decode()
        self.assertIn('API Key cadastrada', corpo)
        self.assertNotIn('sk-abc', corpo)

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
        with _patch('accounts.views.master.wapi_check_connection', return_value=health):
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

        with patch('accounts.views.common.send_text_message', side_effect=_fake_send):
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
        with patch('accounts.views.common.send_text_message') as enviar:
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
class MasterCannotTouchClientAttendanceTests(TestCase):
    """O master nao OPERA o atendimento — nem por endpoint AJAX, no modo suporte.

    Regressao: `conversation-name-contact` nao tinha guarda nenhuma de master, entao
    ele gravava contato dentro da empresa do cliente por POST, enquanto a tela
    Contatos devolvia 403 para ele. `deny_master_json` (que a secao 16 ja descrevia
    como instalada, mas que nunca era chamada) passou a ser realmente aplicada nos
    endpoints de atendimento.
    """

    def setUp(self):
        from ..models import Company, Contact, Conversation
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
        from ..models import Contact
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
        from ..models import Company, MenuBotConfiguration
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
        from ..models import Sector
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
        from ..models import OpenAiConfiguration
        self.assertNotIn('BEEZAP', DEFAULT_INSTRUCTIONS)
        prompt = build_system_prompt(OpenAiConfiguration.get_solo(), self.company)
        self.assertIn('PPM Servicos', prompt)
        self.assertNotIn('BEEZAP', prompt)
        self.assertNotIn('BEEonBOARD', prompt)

    def test_prompt_da_ia_nao_quebra_sem_empresa(self):
        """Retaguarda: sem empresa, o prompt sai sem a linha, nao com nome errado."""
        from gpt.attendant import build_system_prompt
        from ..models import OpenAiConfiguration
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
class ClientListCountsUseSubqueryTests(TestCase):
    """A tela Clientes conta sem multiplicar linhas.

    Dois `Count` sobre relacoes DIFERENTES no mesmo `annotate` fazem o banco cruzar
    usuarios x conversas de cada empresa. O `distinct=True` corrigia o numero, nao o
    custo. Trocado por `Subquery`, que tambem precisa continuar dando o numero certo.
    """

    def setUp(self):
        from ..models import Company, Contact, Conversation
        self.company = Company.objects.create(name='Conta Certa', slug='conta-certa')
        self.master = User.objects.create_user(
            email='master-contagem@x.com', password='SenhaForte123',
            role=User.Role.MASTER,
        )
        for i in range(3):
            User.objects.create_user(
                email=f'pessoa{i}@conta-certa.com', password='SenhaForte123',
                role=User.Role.USUARIO, company=self.company,
            )
        for i in range(4):
            contato = Contact.objects.create(
                company=self.company, name=f'Cli {i}', phone=f'551955500{i:03d}',
            )
            Conversation.objects.create(
                company=self.company, contact=contato,
                external_id=f'551955500{i:03d}', chat_type='private',
            )
        self.client.force_login(self.master)

    def test_contagens_por_empresa_estao_certas(self):
        response = self.client.get(reverse('clients'))
        self.assertEqual(response.status_code, 200)
        empresa = next(
            c for c in response.context['companies'] if c.pk == self.company.pk
        )
        self.assertEqual(empresa.users_count, 3)
        self.assertEqual(empresa.conversations_count, 4)

    def test_empresa_sem_movimento_conta_zero_e_nao_none(self):
        from ..models import Company
        vazia = Company.objects.create(name='Sem Nada', slug='sem-nada')
        response = self.client.get(reverse('clients'))
        empresa = next(c for c in response.context['companies'] if c.pk == vazia.pk)
        self.assertEqual(empresa.users_count, 0)
        self.assertEqual(empresa.conversations_count, 0)
class CompanyDeletionRemovesLogoTests(TestCase):
    """Excluir a empresa apaga TODOS os arquivos dela, inclusive o logo.

    `_delete_company_media_files` percorria so `Message.media_file`, entao o arquivo
    de `media/empresas/logos/` sobrava no disco para sempre depois de o cliente sair.
    """

    def _empresa_com_logo(self):
        from django.core.files.base import ContentFile
        from ..models import Company
        company = Company.objects.create(name='Com Logo', slug='com-logo')
        company.logo.save('logo-teste.png', ContentFile(b'PNG-falso'), save=True)
        return company

    def test_exclusao_apaga_o_arquivo_do_logo(self):
        import os
        from ..views import _delete_company_media_files
        company = self._empresa_com_logo()
        caminho = company.logo.path
        self.assertTrue(os.path.exists(caminho))
        removidos = _delete_company_media_files(company)
        self.assertGreaterEqual(removidos, 1)
        self.assertFalse(os.path.exists(caminho))

    def test_empresa_sem_logo_nao_quebra(self):
        from ..models import Company
        from ..views import _delete_company_media_files
        company = Company.objects.create(name='Sem Logo', slug='sem-logo-x')
        self.assertEqual(_delete_company_media_files(company), 0)

    def test_master_trocando_o_logo_apaga_o_antigo(self):
        import os
        from django.core.files.uploadedfile import SimpleUploadedFile
        company = self._empresa_com_logo()
        antigo = company.logo.path
        master = User.objects.create_user(
            email='master-logo@x.com', password='SenhaForte123', role=User.Role.MASTER,
        )
        self.client.force_login(master)
        response = self.client.post(reverse('clients'), {
            'company_id': company.pk,
            'name': company.name,
            'slug': company.slug,
            'logo': SimpleUploadedFile('novo.png', b'PNG-novo', content_type='image/png'),
            'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        company.refresh_from_db()
        self.assertFalse(os.path.exists(antigo), 'o logo antigo ficou orfao no disco')
        self.assertTrue(company.logo)
class WapiEnvFallbackOnlyForDefaultCompanyTests(TestCase):
    """Credencial do `.env` NUNCA vale para um cliente que nao a cadastrou.

    `WAPI_INSTANCE_ID`/`WAPI_TOKEN` sao heranca da epoca de um cliente unico. Sem
    restringir o fallback, uma empresa nova ainda sem credencial enviaria mensagem
    pela instancia do `.env` — ou seja, PELO WHATSAPP DE OUTRO CLIENTE. Isso anularia
    a garantia que a Parte 2 do multiempresa construiu ao tornar `company`
    obrigatorio em todas as funcoes de `wapi/client.py`.
    """

    def setUp(self):
        from ..models import Company, WapiConfiguration
        self.padrao = Company.get_default()
        self.nova = Company.objects.create(name='Cliente Novo', slug='cliente-novo')
        self.config_padrao = WapiConfiguration.for_company(self.padrao)
        self.config_nova = WapiConfiguration.for_company(self.nova)

    def test_empresa_padrao_ainda_cai_para_o_ambiente(self):
        """Instalacao antiga de um cliente unico continua funcionando."""
        from django.test import override_settings
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            self.assertEqual(self.config_padrao.resolved_instance_id(), 'DO-ENV')
            self.assertEqual(self.config_padrao.resolved_token(), 'TOKEN-ENV')
            self.assertTrue(self.config_padrao.has_token)

    def test_cliente_novo_sem_credencial_nao_usa_a_do_ambiente(self):
        from django.test import override_settings
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            self.assertEqual(self.config_nova.resolved_instance_id(), '')
            self.assertEqual(self.config_nova.resolved_token(), '')
            self.assertFalse(self.config_nova.has_token)

    def test_credencial_propria_sempre_vence(self):
        from django.test import override_settings
        self.config_nova.instance_id = 'PROPRIA'
        self.config_nova.token = 'TOKEN-PROPRIO'
        self.config_nova.save()
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            self.assertEqual(self.config_nova.resolved_instance_id(), 'PROPRIA')
            self.assertEqual(self.config_nova.resolved_token(), 'TOKEN-PROPRIO')

    def test_envio_pelo_cliente_sem_credencial_nao_sai(self):
        """O cliente da W-API aborta em vez de enviar pela instancia errada."""
        from django.test import override_settings
        from wapi.client import send_text_message
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            resultado = send_text_message(
                '5519999998888', 'oi', company=self.nova,
            )
        self.assertFalse(resultado.success)
        self.assertIn('Configure a W-API', resultado.error)

    def test_status_para_o_cliente_diz_nao_configurado(self):
        from django.test import override_settings
        from ..views import build_service_status
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            status = build_service_status(self.nova)
        self.assertFalse(status['whatsapp_ok'])
        self.assertIn('ainda não configurado', status['whatsapp_label'])

    def test_webhook_token_do_ambiente_tambem_e_so_da_padrao(self):
        from django.test import override_settings
        with override_settings(WAPI_WEBHOOK_TOKEN='SEGREDO-ENV'):
            self.assertEqual(
                self.config_padrao.resolved_webhook_token(), 'SEGREDO-ENV'
            )
            self.assertEqual(self.config_nova.resolved_webhook_token(), '')
            self.assertFalse(self.config_nova.has_webhook_token)

    def test_check_avisa_quando_o_env_tem_credencial_com_varias_empresas(self):
        from django.test import override_settings
        from ..checks import wapi_env_credentials_check
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            avisos = wapi_env_credentials_check(None)
        self.assertEqual([a.id for a in avisos], ['beezap.W002'])

    def test_check_fica_calado_com_uma_empresa_so(self):
        from django.test import override_settings
        from ..models import Company
        from ..checks import wapi_env_credentials_check
        Company.objects.exclude(pk=self.padrao.pk).delete()
        with override_settings(WAPI_INSTANCE_ID='DO-ENV', WAPI_TOKEN='TOKEN-ENV'):
            self.assertEqual(wapi_env_credentials_check(None), [])

    def test_check_fica_calado_sem_credencial_no_env(self):
        from django.test import override_settings
        from ..checks import wapi_env_credentials_check
        with override_settings(WAPI_INSTANCE_ID='', WAPI_TOKEN=''):
            self.assertEqual(wapi_env_credentials_check(None), [])

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
