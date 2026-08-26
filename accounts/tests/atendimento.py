"""Atendimento automatico (IA e chatbot de menu), setores e
atendentes.
"""

from .base import (
    Attendant,
    SimpleNamespace,
    TestCase,
    User,
    _json,
    check_password,
    default_company,
    get_messages,
    patch,
    reverse,
)


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
        menubot = MenuBotConfiguration.for_company(default_company())
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
        from gpt.attendant import HANDOFF_NOTICE_TEMPLATE
        esperado = HANDOFF_NOTICE_TEMPLATE.format(setor=self.geral.name)
        self.config.fallback_sector = self.geral
        self.config.save()
        self.conv.ai_turns = 2  # com max_turns=3, o proximo turno sem decisao estoura
        self.conv.save(update_fields=['ai_turns'])
        _, mock_send = self._run(self._gpt(mensagem='Ainda nao entendi, pode explicar?'))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.sector_id, self.geral.id)
        self.assertEqual(self.conv.status, 'pending')
        # SEMPRE avisa o cliente antes de transferir (nunca em silencio), e a mensagem
        # e o aviso de handoff (nao a pergunta de esclarecimento do GPT) — NOMEANDO o
        # setor de destino.
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], esperado)
        self.assertIn(self.geral.name, mock_send.call_args.args[1])
        last_out = self.Message.objects.filter(
            conversation=self.conv, direction='out', is_ai=True
        ).order_by('-created_at').first()
        self.assertEqual(last_out.text, esperado)

    def test_handoff_creates_general_sector_when_no_fallback(self):
        # Sem fallback configurado E sem setor "Geral": o handoff deve CRIAR o "Geral"
        # e encaminhar a conversa para la (nunca deixar orfa/invisivel).
        from gpt.attendant import HANDOFF_NOTICE_TEMPLATE
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
        self.assertEqual(
            mock_send.call_args.args[1],
            HANDOFF_NOTICE_TEMPLATE.format(setor=Sector.GENERAL_SECTOR_NAME),
        )
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
        menubot = self.MenuBotConfiguration.for_company(default_company())
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


class AiTurnCountingTests(TestCase):
    """O limite `max_turns` conta TENTATIVA DE TRIAGEM, nao resposta da IA.

    Caso real que originou estes testes (26/08/2026), com `max_turns=3`:

        cliente: "teste"                                   -> turno 1
        cliente: "nao sei me faa ai"                       -> turno 2
        cliente: "queria ver algo relacionado a contas e
                  pagamentos, com quem eu falo?"           -> ESTOUROU

    Na terceira mensagem o cliente finalmente disse o que queria, e o sistema
    descartou a resposta do GPT para transferir ao "Geral" dizendo "nao consegui
    entender bem a sua solicitacao". O "teste" — que nao e pedido nenhum — tinha
    queimado uma das tres tentativas.
    """

    def setUp(self):
        from accounts.models import (
            Contact, Conversation, MenuBotConfiguration, Message, OpenAiConfiguration, Sector,
        )
        self.Conversation = Conversation
        self.Message = Message
        company = default_company()
        self.company = company

        menubot = MenuBotConfiguration.for_company(company)
        menubot.mode = MenuBotConfiguration.MODE_AI
        menubot.save()

        self.config = OpenAiConfiguration.get_solo()
        self.config.api_key = 'sk-test'
        self.config.max_turns = 3
        self.config.save()

        self.geral, _ = Sector.objects.get_or_create(company=company, name='Geral')
        self.financeiro = Sector.objects.create(company=company, name='Financeiro')

        contact = Contact.objects.create(company=company, name='Cliente', phone='5516988887777')
        self.conv = Conversation.objects.create(
            company=company, contact=contact, external_id='5516988887777',
            chat_type='private', status='open',
        )

    def _turno(self, texto_cliente, gpt_mensagem, gpt_setor='', gpt_atendente=''):
        """Uma troca completa: cliente escreve, a IA processa. Devolve o que foi
        ENVIADO ao cliente (lista) e o prompt de sistema usado na chamada."""
        import json as _json
        from gpt.attendant import handle_incoming_for_ai
        from gpt.client import GptResult

        self.Message.objects.create(conversation=self.conv, direction='in',
                                    message_type='text', text=texto_cliente)
        resultado = GptResult(
            success=True, model='gpt-4.1-nano', total_tokens=10,
            text=_json.dumps({'mensagem': gpt_mensagem, 'setor': gpt_setor,
                              'atendente': gpt_atendente}),
        )
        enviados = []

        def _fake_send(destino, texto, company=None):
            enviados.append(texto)
            return SimpleNamespace(success=True, message_id='wamid-1', error=None)

        with patch('gpt.client.chat_completion', return_value=resultado) as mock_gpt, \
             patch('wapi.client.send_text_message', side_effect=_fake_send):
            handle_incoming_for_ai(self.conv.id)
        self.conv.refresh_from_db()
        prompt = ''
        if mock_gpt.call_args:
            prompt = mock_gpt.call_args.args[0][0]['content']
        return enviados, prompt

    def test_saudacao_nao_gasta_tentativa(self):
        enviados, _ = self._turno('teste', 'Boa tarde! Como posso ajudar?')
        self.assertEqual(self.conv.ai_turns, 0)          # "teste" nao e pedido
        self.assertEqual(enviados, ['Boa tarde! Como posso ajudar?'])
        self.assertIsNone(self.conv.sector_id)

    def test_pedido_de_verdade_gasta_tentativa(self):
        self._turno('preciso da segunda via do boleto', 'Voce quer a segunda via de qual mes?')
        self.assertEqual(self.conv.ai_turns, 1)

    def test_saudacao_com_pedido_junto_gasta_tentativa(self):
        # "bom dia" sozinho nao conta; "bom dia" + pedido conta (comparacao exata,
        # nunca "contem").
        self._turno('bom dia, preciso falar sobre pagamento', 'Claro! Sobre qual pagamento?')
        self.assertEqual(self.conv.ai_turns, 1)

    def test_transcricao_real_chega_ao_setor_certo(self):
        # A conversa que quebrou. "teste" e ping e NAO gasta tentativa; "nao sei me
        # faa ai" gasta (ali a IA ja esta triando — o cliente e que nao ajudou). Com
        # isso a terceira mensagem ainda encontra tentativa disponivel, em vez de
        # chegar com o limite estourado como acontecia antes.
        self._turno('teste', 'Boa tarde! Sou o atendente virtual, como posso ajudar?')
        self.assertEqual(self.conv.ai_turns, 0)
        self._turno('nao sei me faa ai', 'Claro! Pode me dizer qual e o assunto?')
        self.assertEqual(self.conv.ai_turns, 1)
        enviados, _ = self._turno(
            'queria ver algo relacionado a contas e pagamentos com quem eu falo?',
            'Vou te encaminhar para o Financeiro.', gpt_setor='Financeiro',
        )
        self.assertEqual(self.conv.sector_id, self.financeiro.id)
        self.assertEqual(self.conv.status, 'pending')
        # A fala do GPT chega ao cliente — nao a desculpa generica.
        self.assertEqual(enviados, ['Vou te encaminhar para o Financeiro.'])

    def test_ultimo_turno_avisa_o_modelo(self):
        from gpt.attendant import FINAL_TURN_RULE
        self.conv.ai_turns = 2  # com max_turns=3, a proxima tentativa e a ultima
        self.conv.save(update_fields=['ai_turns'])
        _, prompt = self._turno('quero falar sobre pagamento', 'Sobre qual pagamento?')
        self.assertIn(FINAL_TURN_RULE, prompt)

    def test_prompt_nao_avisa_ultimo_turno_fora_da_hora(self):
        from gpt.attendant import FINAL_TURN_RULE
        _, prompt = self._turno('quero falar sobre pagamento', 'Sobre qual pagamento?')
        self.assertNotIn(FINAL_TURN_RULE, prompt)

    def test_saudacao_nao_dispara_aviso_de_ultimo_turno(self):
        # Mensagem sem intencao nao gasta tentativa, entao tambem nao e "a ultima".
        from gpt.attendant import FINAL_TURN_RULE
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        _, prompt = self._turno('oi', 'Ola! Como posso ajudar?')
        self.assertNotIn(FINAL_TURN_RULE, prompt)
        self.assertEqual(self.conv.ai_turns, 2)

    def test_regra_de_triagem_vai_sempre_no_prompt(self):
        # A REGRA DE TRIAGEM e anexada pelo codigo, como a de formato JSON — nao vive
        # no prompt editavel. Quem ja salvou um prompt proprio guardou uma copia do
        # padrao ANTIGO, e uma regra estrutural nao pode depender disso.
        from gpt.attendant import TRIAGE_RULE
        self.config.instructions = 'Prompt custom que nao fala nada de setor.'
        self.config.save()
        _, prompt = self._turno('quero falar sobre pagamento', 'Sobre qual pagamento?')
        self.assertIn(TRIAGE_RULE, prompt)

    def test_regra_de_triagem_proibe_perguntar_o_setor_ao_cliente(self):
        # Caso real: "Pode me informar qual setor posso encaminhar sua solicitacao?".
        # Escolher o destino e trabalho da IA — o cliente nao conhece os setores.
        from gpt.attendant import TRIAGE_RULE
        self.assertIn('NUNCA pergunte ao cliente para qual SETOR', TRIAGE_RULE)
        self.assertIn('ASSUNTO', TRIAGE_RULE)
        # E manda oferecer as opcoes quando o cliente diz que nao sabe, em vez de
        # repetir a mesma pergunta ate estourar o limite.
        self.assertIn('nao tem certeza', TRIAGE_RULE)
        self.assertIn('ofereca', TRIAGE_RULE)

    def test_pergunta_com_setor_na_mesma_resposta_nao_encaminha(self):
        # Defeito visto em producao (conv=29): o modelo devolveu a PERGUNTA e o setor
        # juntos ("Qual dessas opcoes voce gostaria de explorar?" + setor "Geral").
        # O sistema encaminhou no mesmo segundo e, quando o cliente respondeu 13s
        # depois, a conversa ja tinha saido da IA — ninguem respondeu mais nada.
        pergunta = 'Claro, posso ajudar com servicos, pagamentos ou assuntos gerais. Qual delas?'
        enviados, _ = self._turno('nao sei ao certo', pergunta, gpt_setor='Geral')
        self.assertIsNone(self.conv.sector_id)      # NAO encaminhou
        self.assertEqual(self.conv.status, 'open')  # segue com a IA
        self.assertEqual(enviados, [pergunta])      # e a pergunta chegou ao cliente
        self.assertEqual(self.conv.ai_turns, 1)     # a tentativa foi contada

    def test_afirmacao_com_setor_encaminha_normalmente(self):
        # A trava e so para PERGUNTA: quando o modelo afirma o destino, encaminha.
        enviados, _ = self._turno('quero falar sobre pagamento',
                                  'Vou te encaminhar para o Financeiro.',
                                  gpt_setor='Financeiro')
        self.assertEqual(self.conv.sector_id, self.financeiro.id)
        self.assertEqual(self.conv.status, 'pending')
        self.assertEqual(enviados, ['Vou te encaminhar para o Financeiro.'])

    def test_pergunta_com_atendente_na_mesma_resposta_nao_encaminha(self):
        from accounts.models import Attendant, User, Sector
        user = User.objects.create_user(company=self.company, email='at@x.com',
                                        password='x', role='usuario')
        att = Attendant.objects.create(company=self.company, user=user, name='Claudeci')
        att.sectors.add(self.financeiro)
        pergunta = 'Voce quer falar com a Claudeci mesmo?'
        enviados, _ = self._turno('quero falar com alguem', pergunta,
                                  gpt_atendente='Claudeci')
        self.assertIsNone(self.conv.sector_id)      # NAO encaminhou
        self.assertEqual(self.conv.status, 'open')
        self.assertEqual(enviados, [pergunta])

    def test_encaminhar_com_fala_vazia_ainda_avisa_o_cliente(self):
        # O modelo respondeu {"mensagem": "", "setor": "Financeiro"} — coisa que ele
        # faz justamente quando ja decidiu o destino. `_send_ai_reply` devolvia False
        # (texto vazio) e o encaminhamento acontecia mesmo assim: o cliente ficava sem
        # nenhuma resposta, olhando a conversa parada. Era o "mandou para o Geral sem
        # falar nada" relatado em producao.
        from gpt.attendant import HANDOFF_NOTICE_TEMPLATE
        enviados, _ = self._turno('quero falar sobre pagamento', '', gpt_setor='Financeiro')
        self.assertEqual(self.conv.sector_id, self.financeiro.id)
        self.assertEqual(
            enviados, [HANDOFF_NOTICE_TEMPLATE.format(setor=self.financeiro.name)]
        )

    def test_sem_fala_e_sem_destino_cai_para_humano(self):
        # Nem falou nem encaminhou: a conversa ficaria muda e fora de qualquer fila.
        from gpt.attendant import HANDOFF_NOTICE_TEMPLATE
        enviados, _ = self._turno('quero falar sobre pagamento', '')
        self.assertEqual(self.conv.sector_id, self.geral.id)
        self.assertEqual(self.conv.status, 'pending')
        self.assertEqual(enviados, [HANDOFF_NOTICE_TEMPLATE.format(setor=self.geral.name)])

    def test_setor_inexistente_na_empresa_nao_encaminha_calado(self):
        # O modelo pediu um setor que nao existe com aquele nome na empresa: a decisao
        # e ignorada (nao ha para onde mandar), mas o cliente TEM que ouvir alguma
        # coisa e o motivo tem que sair no log para o diagnostico.
        with self.assertLogs('beezap.gpt', level='WARNING') as logs:
            enviados, _ = self._turno('quero falar sobre pagamento',
                                      'Vou te encaminhar.', gpt_setor='Contas a Pagar')
        self.assertIsNone(self.conv.sector_id)   # nao existe: nao encaminhou
        self.assertEqual(enviados, ['Vou te encaminhar.'])
        self.assertTrue(any('setor inexistente' in linha for linha in logs.output))

    def test_handoff_nomeia_o_setor_de_destino(self):
        from gpt.attendant import HANDOFF_NOTICE_TEMPLATE
        self.conv.ai_turns = 2
        self.conv.save(update_fields=['ai_turns'])
        enviados, _ = self._turno('continuo sem saber explicar direito',
                                  'Pode detalhar um pouco mais?')
        self.assertEqual(self.conv.sector_id, self.geral.id)
        # Nunca mais a desculpa falsa: o aviso diz PARA ONDE o cliente esta indo.
        self.assertEqual(enviados, [HANDOFF_NOTICE_TEMPLATE.format(setor=self.geral.name)])
        self.assertIn(self.geral.name, enviados[0])
        self.assertNotIn('nao consegui entender', enviados[0])

    def test_teto_absoluto_encerra_loop_de_saudacao(self):
        # Saudacao nao gasta tentativa — mas nao pode manter a IA respondendo para
        # sempre. Depois de MAX_REPLIES_PER_SEGMENT falas, cai para a fila humana.
        from gpt.attendant import MAX_REPLIES_PER_SEGMENT, HANDOFF_NOTICE_TEMPLATE
        for _ in range(MAX_REPLIES_PER_SEGMENT):
            self._turno('oi', 'Ola! Como posso ajudar?')
        self.assertEqual(self.conv.ai_turns, 0)  # nenhuma tentativa foi gasta
        enviados, _ = self._turno('oi', 'Ola! Como posso ajudar?')
        self.assertEqual(self.conv.sector_id, self.geral.id)
        self.assertEqual(self.conv.status, 'pending')
        self.assertEqual(enviados, [HANDOFF_NOTICE_TEMPLATE.format(setor=self.geral.name)])


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

        self.config = MenuBotConfiguration.for_company(default_company())
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
class AdminAttendantSignalIsQuietTests(TestCase):
    """O sinal do atendente-admin so roda quando o perfil pode ter mudado.

    Antes ele rodava em TODO save de usuario — inclusive `last_login` (a cada login)
    e `password` (a cada troca de senha). Cada passada fazia `get_or_create` do
    atendente mais `sectors.add(*todos_os_setores)`: consultas gastas em toda entrada
    de admin no sistema, sem nunca mudar nada.
    """

    def setUp(self):
        from ..models import Sector
        self.company = default_company()
        Sector.objects.create(company=self.company, name='Setor Sinal A')
        Sector.objects.create(company=self.company, name='Setor Sinal B')
        self.adm = User.objects.create_user(
            email='adm-sinal@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.company,
        )

    def test_login_nao_dispara_o_provisionamento(self):
        from unittest.mock import patch as _patch
        with _patch('accounts.signals.ensure_admin_attendant') as provisiona:
            self.adm.save(update_fields=['last_login'])
        self.assertFalse(provisiona.called)

    def test_troca_de_senha_nao_dispara_o_provisionamento(self):
        from unittest.mock import patch as _patch
        with _patch('accounts.signals.ensure_admin_attendant') as provisiona:
            self.adm.save(update_fields=['password'])
        self.assertFalse(provisiona.called)

    def test_mudanca_de_perfil_dispara_o_provisionamento(self):
        """O que importa continua funcionando: virar adm provisiona o atendente."""
        from ..models import Attendant as Att, Sector
        pessoa = User.objects.create_user(
            email='virou-adm@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=self.company,
        )
        self.assertFalse(Att.objects.filter(user=pessoa).exists())
        pessoa.role = User.Role.ADM
        pessoa.save(update_fields=['role'])
        atendente = Att.objects.get(user=pessoa)
        # E entra em todos os setores da empresa (para poder Assumir qualquer fila).
        self.assertEqual(
            set(atendente.sectors.values_list('name', flat=True)),
            set(Sector.objects.filter(company=self.company).values_list('name', flat=True)),
        )

    def test_save_completo_continua_provisionando(self):
        from unittest.mock import patch as _patch
        with _patch('accounts.signals.ensure_admin_attendant') as provisiona:
            self.adm.save()
        self.assertTrue(provisiona.called)
class AutoReplyLockIsSharedBetweenProcessesTests(TestCase):
    """A trava do atendimento automatico vale ENTRE PROCESSOS.

    Antes era um `set()` na memoria do worker. Com `--workers 2`, cada worker tinha o
    seu — uma rajada caindo em processos diferentes passava pelas duas travas e o
    cliente recebia o menu (ou a resposta da IA) DUAS vezes.
    """

    def setUp(self):
        from ..models import Contact, Conversation
        self.company = default_company()
        contato = Contact.objects.create(
            company=self.company, name='Cliente Trava', phone='5519555554444',
        )
        self.conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519555554444',
            chat_type='private',
        )

    def test_segunda_tentativa_nao_toma_a_trava(self):
        from wapi import autoreply_lock
        self.assertTrue(autoreply_lock.acquire(self.conv.pk))
        self.assertFalse(autoreply_lock.acquire(self.conv.pk))

    def test_liberar_devolve_a_trava(self):
        from wapi import autoreply_lock
        autoreply_lock.acquire(self.conv.pk)
        autoreply_lock.release(self.conv.pk)
        self.assertTrue(autoreply_lock.acquire(self.conv.pk))

    def test_trava_expira_para_worker_morto_nao_travar_a_conversa(self):
        """O gunicorn mata worker no timeout; a conversa nao pode ficar presa."""
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from wapi import autoreply_lock
        from ..models import Conversation
        autoreply_lock.acquire(self.conv.pk)
        antiga = _tz.now() - autoreply_lock.LOCK_TTL - _td(seconds=1)
        Conversation.objects.filter(pk=self.conv.pk).update(auto_reply_lock_at=antiga)
        self.assertTrue(autoreply_lock.acquire(self.conv.pk))

    def test_travas_de_conversas_diferentes_nao_se_atrapalham(self):
        from ..models import Contact, Conversation
        from wapi import autoreply_lock
        outro = Contact.objects.create(
            company=self.company, name='Outro', phone='5519111112222',
        )
        conv2 = Conversation.objects.create(
            company=self.company, contact=outro, external_id='5519111112222',
            chat_type='private',
        )
        self.assertTrue(autoreply_lock.acquire(self.conv.pk))
        self.assertTrue(autoreply_lock.acquire(conv2.pk))

    def test_chatbot_reprocessa_quando_chega_mensagem_no_meio(self):
        """A escolha digitada durante o processamento nao pode ser descartada."""
        from unittest.mock import patch as _patch
        from chatbot import handler
        from ..models import Message

        chamadas = []

        def _finge_atendimento(conversation_id):
            chamadas.append(conversation_id)
            if len(chamadas) == 1:
                # Simula o cliente mandando "1" enquanto o bot processava.
                Message.objects.create(
                    conversation=self.conv, direction='in',
                    message_type='text', text='1',
                )

        Message.objects.create(
            conversation=self.conv, direction='in', message_type='text', text='oi',
        )
        with _patch.object(handler, 'handle_incoming_for_menu', _finge_atendimento):
            handler._processar_com_reprocesso(self.conv.pk)
        self.assertEqual(len(chamadas), 2, 'deveria reprocessar a mensagem nova')

    def test_reprocesso_para_quando_nao_chega_nada_novo(self):
        from unittest.mock import patch as _patch
        from chatbot import handler
        chamadas = []
        with _patch.object(handler, 'handle_incoming_for_menu',
                           lambda cid: chamadas.append(cid)):
            handler._processar_com_reprocesso(self.conv.pk)
        self.assertEqual(len(chamadas), 1)
class MenuOptionsSaveIsAtomicTests(TestCase):
    """Salvar o menu do chatbot e tudo-ou-nada.

    `_save_menu_options` apaga as opcoes antigas antes de criar as novas: uma falha no
    meio deixaria o menu do cliente VAZIO, e o chatbot passaria a mandar um menu sem
    opcao nenhuma para o cliente final dele.
    """

    def setUp(self):
        from ..models import MenuBotConfiguration, MenuOption, Sector
        self.company = default_company()
        self.config = MenuBotConfiguration.for_company(self.company)
        self.setor = Sector.objects.create(company=self.company, name='Financeiro Atomico')
        MenuOption.objects.create(
            config=self.config, order=1, label='Antiga', sector=self.setor,
        )

    def test_falha_no_meio_preserva_o_menu_antigo(self):
        from unittest.mock import patch as _patch
        from django.http import QueryDict
        from ..models import MenuOption
        from ..views import _save_menu_options

        post = QueryDict(mutable=True)
        post.setlist('option_label', ['Nova A', 'Nova B'])
        post.setlist('option_sector', [str(self.setor.pk), str(self.setor.pk)])

        with _patch('accounts.views.settings.MenuOption.objects.create',
                    side_effect=RuntimeError('falhou no meio')):
            with self.assertRaises(RuntimeError):
                _save_menu_options(self.config, post)

        # A opcao antiga voltou: nada foi perdido.
        rotulos = list(MenuOption.objects.filter(config=self.config)
                       .values_list('label', flat=True))
        self.assertEqual(rotulos, ['Antiga'])

    def test_salvamento_normal_substitui_as_opcoes(self):
        from django.http import QueryDict
        from ..models import MenuOption
        from ..views import _save_menu_options
        post = QueryDict(mutable=True)
        post.setlist('option_label', ['Nova A', '', 'Nova B'])
        post.setlist('option_sector', [str(self.setor.pk), '', str(self.setor.pk)])
        _save_menu_options(self.config, post)
        opcoes = list(MenuOption.objects.filter(config=self.config).order_by('order'))
        self.assertEqual([o.label for o in opcoes], ['Nova A', 'Nova B'])
        # Renumeradas na ordem enviada, ignorando a linha vazia.
        self.assertEqual([o.order for o in opcoes], [1, 2])

    def test_setor_com_id_invalido_nao_quebra(self):
        from django.http import QueryDict
        from ..models import MenuOption
        from ..views import _save_menu_options
        post = QueryDict(mutable=True)
        post.setlist('option_label', ['Sem setor'])
        post.setlist('option_sector', ['abc'])
        _save_menu_options(self.config, post)
        opcao = MenuOption.objects.get(config=self.config)
        self.assertEqual(opcao.label, 'Sem setor')
        self.assertIsNone(opcao.sector)


class AttendantAccessActionsTests(TestCase):
    """Tela Atendentes: o ADM pode INATIVAR/REATIVAR e EXCLUIR uma pessoa.

    Antes a tela so cadastrava e editava: quem saia da empresa continuava com
    login valido e sem nenhuma forma de tirar o acesso pela interface. Inativar
    guarda o cadastro (da para voltar); excluir apaga o acesso junto, porque
    `Attendant.user` e OneToOne com CASCADE e apagar so o atendente deixaria a
    conta de pe (e, no caso do adm, o sinal recriaria o atendente).

    As conversas que estavam com a pessoa voltam para a FILA nos dois casos: a tela
    Conversas separa "Conversando" de "Aguardando" pelo VINCULO, entao um vinculo
    com alguem que nao entra mais no sistema esconde a conversa de todas as filas.
    """

    def setUp(self):
        from accounts.models import Contact, Conversation, Message, Sector
        self.Conversation = Conversation
        self.Message = Message
        self.empresa = default_company()
        self.adm = User.objects.create_user(
            company=self.empresa, email='adm@x.com', password='x', role=User.Role.ADM)
        self.pessoa_user = User.objects.create_user(
            company=self.empresa, email='joao@x.com', password='x', role=User.Role.USUARIO)
        self.pessoa = Attendant.objects.create(
            company=self.empresa, user=self.pessoa_user, name='Joao', must_change_password=False)
        self.vendas = Sector.objects.create(company=self.empresa, name='Vendas')
        self.pessoa.sectors.add(self.vendas)
        contato = Contact.objects.create(
            company=self.empresa, name='Cliente', phone='5516988887777')
        self.conversa = Conversation.objects.create(
            company=self.empresa, contact=contato, external_id='5516988887777',
            chat_type='private', status='open', sector=self.vendas,
            assigned_attendant=self.pessoa,
        )
        Message.objects.create(
            conversation=self.conversa, direction='outbound', message_type='text',
            text='Bom dia', sender_name='Joao', from_me=True,
        )
        self.client.force_login(self.adm)

    def _post(self, action, attendant_id):
        return self.client.post(
            reverse('attendants'),
            {'action': action, 'attendant_id': str(attendant_id)},
            follow=True,
        )

    def _avisos(self, resposta):
        return ' '.join(str(m) for m in get_messages(resposta.wsgi_request))

    # ----- inativar / reativar -----

    def test_adm_inativa_e_a_conversa_volta_para_a_fila(self):
        resposta = self._post('toggle-active', self.pessoa.id)
        self.pessoa_user.refresh_from_db()
        self.conversa.refresh_from_db()
        self.assertFalse(self.pessoa_user.is_active)
        self.assertIsNone(self.conversa.assigned_attendant_id)
        # O cadastro fica guardado: quem inativa pode voltar atras.
        self.assertTrue(Attendant.objects.filter(pk=self.pessoa.pk).exists())
        self.assertIn('para a fila', self._avisos(resposta).lower())

    def test_inativo_nao_entra_mais_no_sistema(self):
        self._post('toggle-active', self.pessoa.id)
        self.client.logout()
        self.assertFalse(self.client.login(email='joao@x.com', password='x'))

    def test_adm_reativa_a_mesma_pessoa(self):
        self._post('toggle-active', self.pessoa.id)
        self._post('toggle-active', self.pessoa.id)
        self.pessoa_user.refresh_from_db()
        self.assertTrue(self.pessoa_user.is_active)

    def test_conversa_encerrada_nao_e_devolvida_para_a_fila(self):
        self.conversa.status = 'closed'
        self.conversa.save(update_fields=['status'])
        self._post('toggle-active', self.pessoa.id)
        self.conversa.refresh_from_db()
        # Historico encerrado continua mostrando quem atendeu.
        self.assertEqual(self.conversa.assigned_attendant_id, self.pessoa.pk)

    # ----- excluir -----

    def test_adm_exclui_e_o_acesso_cai_junto(self):
        self._post('delete', self.pessoa.id)
        self.assertFalse(Attendant.objects.filter(pk=self.pessoa.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.pessoa_user.pk).exists())
        self.conversa.refresh_from_db()
        self.assertIsNone(self.conversa.assigned_attendant_id)

    def test_excluir_preserva_o_historico_das_mensagens(self):
        self._post('delete', self.pessoa.id)
        mensagem = self.Message.objects.get(conversation=self.conversa)
        self.assertEqual(mensagem.text, 'Bom dia')
        self.assertEqual(mensagem.sender_name, 'Joao')

    # ----- guardas -----

    def test_nao_mexe_no_proprio_acesso(self):
        eu = Attendant.objects.get(user=self.adm)  # o sinal provisiona o adm
        for action in ('toggle-active', 'delete'):
            with self.subTest(action=action):
                resposta = self._post(action, eu.id)
                self.adm.refresh_from_db()
                self.assertTrue(self.adm.is_active)
                self.assertTrue(User.objects.filter(pk=self.adm.pk).exists())
                self.assertIn('proprio acesso', self._avisos(resposta))

    def test_nao_deixa_a_empresa_sem_administrador_ativo(self):
        outro_adm = User.objects.create_user(
            company=self.empresa, email='adm2@x.com', password='x', role=User.Role.ADM)
        atendente_adm = Attendant.objects.get(user=outro_adm)
        # Este passa a ser o unico adm ativo: o proprio logado deixa de ser adm.
        User.objects.filter(pk=self.adm.pk).update(role=User.Role.USUARIO)
        for action in ('toggle-active', 'delete'):
            with self.subTest(action=action):
                self._post(action, atendente_adm.id)
                self.assertTrue(User.objects.filter(pk=outro_adm.pk, is_active=True).exists())

    def test_exclui_outro_adm_quando_ainda_sobra_um_ativo(self):
        outro_adm = User.objects.create_user(
            company=self.empresa, email='adm2@x.com', password='x', role=User.Role.ADM)
        atendente_adm = Attendant.objects.get(user=outro_adm)
        self._post('delete', atendente_adm.id)
        self.assertFalse(User.objects.filter(pk=outro_adm.pk).exists())

    def test_atendente_de_outra_empresa_nao_e_alcancado(self):
        from accounts.models import Company
        outra = Company.objects.create(name='Outra', slug='outra')
        vizinho_user = User.objects.create_user(
            company=outra, email='vizinho@y.com', password='x', role=User.Role.USUARIO)
        vizinho = Attendant.objects.create(
            company=outra, user=vizinho_user, name='Vizinho', must_change_password=False)
        self._post('delete', vizinho.id)
        self.assertTrue(Attendant.objects.filter(pk=vizinho.pk).exists())
        self.assertTrue(User.objects.filter(pk=vizinho_user.pk).exists())

    def test_usuario_com_o_botao_atendentes_nao_inativa_nem_exclui(self):
        """Cadastrar/editar segue liberado pelo botao; tirar acesso e do ADM."""
        from accounts.models import UserMenuPermission
        UserMenuPermission.objects.update_or_create(
            user=self.pessoa_user, defaults={'allowed_keys': ['attendants']})
        colega_user = User.objects.create_user(
            company=self.empresa, email='ana@x.com', password='x', role=User.Role.USUARIO)
        colega = Attendant.objects.create(
            company=self.empresa, user=colega_user, name='Ana', must_change_password=False)
        self.client.force_login(self.pessoa_user)
        resposta = self._post('delete', colega.id)
        self.assertTrue(Attendant.objects.filter(pk=colega.pk).exists())
        self.assertIn('administrador', self._avisos(resposta).lower())

    def test_leitor_nem_chega_no_post(self):
        from accounts.models import UserMenuPermission
        leitor = User.objects.create_user(
            company=self.empresa, email='leo@x.com', password='x', role=User.Role.LEITOR)
        UserMenuPermission.objects.update_or_create(
            user=leitor, defaults={'allowed_keys': ['attendants']})
        self.client.force_login(leitor)
        resposta = self.client.post(
            reverse('attendants'),
            {'action': 'delete', 'attendant_id': str(self.pessoa.id)},
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(Attendant.objects.filter(pk=self.pessoa.pk).exists())

    def test_botoes_aparecem_so_para_o_adm(self):
        from accounts.models import UserMenuPermission
        # Confere so marcas que existem no HTML dos botoes/modal — o seletor do JS
        # cita os mesmos nomes e daria falso positivo.
        botao_inativar = 'data-access-action="toggle-active"'
        botao_excluir = 'data-access-action="delete"'
        modal = 'class="attendants-confirm-text"'
        corpo = self.client.get(reverse('attendants')).content.decode()
        self.assertIn(botao_inativar, corpo)
        self.assertIn(botao_excluir, corpo)
        self.assertIn(modal, corpo)
        UserMenuPermission.objects.update_or_create(
            user=self.pessoa_user, defaults={'allowed_keys': ['attendants']})
        self.client.force_login(self.pessoa_user)
        corpo = self.client.get(reverse('attendants')).content.decode()
        # Nem os botoes da linha nem o modal de confirmacao chegam a existir.
        self.assertNotIn(botao_inativar, corpo)
        self.assertNotIn(botao_excluir, corpo)
        self.assertNotIn(modal, corpo)

    def test_id_invalido_nao_derruba_a_tela(self):
        resposta = self._post('delete', 'nao-e-numero')
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(Attendant.objects.filter(pk=self.pessoa.pk).exists())
