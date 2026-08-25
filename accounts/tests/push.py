"""Aviso de nova mensagem por Web Push: inscricao, destinatarios, texto e envio.

O pop-up dependia de um `setInterval` de 6s na tela Conversas, e o Chrome estrangula
timer de aba em segundo plano para 1x/minuto — o aviso chegava atrasado ou, se a pessoa
voltasse para a aba antes do tique, virava toast interno em vez de pop-up. Agora quem
avisa e o servidor, na hora que a mensagem entra pelo webhook.

O teste mais importante daqui e o de DESTINATARIOS: errar ali vaza conteudo de
atendimento para quem a tela esconde.
"""

from .base import (
    Attendant,
    TestCase,
    User,
    _json,
    default_company,
    patch,
    reverse,
)


class WebPushSubscriptionTests(TestCase):
    """Inscrição do navegador no aviso de nova mensagem (Web Push)."""

    def setUp(self):
        self.user = User.objects.create_user(
            company=default_company(), email='ana@x.com', password='x', role=User.Role.ADM)
        self.client.force_login(self.user)

    def _inscrever(self, endpoint='https://fcm.googleapis.com/fcm/send/abc'):
        return self.client.post(
            reverse('push-subscribe'),
            data=_json.dumps({'endpoint': endpoint,
                              'keys': {'p256dh': 'chave-publica', 'auth': 'segredo'}}),
            content_type='application/json',
        )

    def test_inscreve_o_navegador(self):
        from accounts.models import PushSubscription
        resposta = self._inscrever()
        self.assertEqual(resposta.status_code, 200)
        sub = PushSubscription.objects.get(user=self.user)
        self.assertEqual(sub.p256dh, 'chave-publica')
        self.assertEqual(sub.auth, 'segredo')

    def test_inscrever_duas_vezes_nao_duplica(self):
        from accounts.models import PushSubscription
        self._inscrever()
        self._inscrever()
        self.assertEqual(PushSubscription.objects.filter(user=self.user).count(), 1)

    def test_o_mesmo_navegador_troca_de_dono_ao_trocar_o_login(self):
        """Dois atendentes no mesmo computador: a inscrição é de quem está logado."""
        from accounts.models import PushSubscription
        self._inscrever()
        outro = User.objects.create_user(
            company=default_company(), email='bia@x.com', password='x', role=User.Role.USUARIO)
        self.client.force_login(outro)
        self._inscrever()
        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().user_id, outro.pk)

    def test_inscricao_incompleta_e_recusada(self):
        from accounts.models import PushSubscription
        resposta = self.client.post(
            reverse('push-subscribe'),
            data=_json.dumps({'endpoint': 'https://x/y'}),  # sem as chaves
            content_type='application/json',
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_cancelar_apaga_so_a_propria(self):
        from accounts.models import PushSubscription
        self._inscrever('https://fcm.googleapis.com/fcm/send/minha')
        outro = User.objects.create_user(
            company=default_company(), email='bia@x.com', password='x', role=User.Role.USUARIO)
        PushSubscription.objects.create(
            user=outro, endpoint='https://fcm.googleapis.com/fcm/send/dooutro',
            p256dh='p', auth='a')
        self.client.post(
            reverse('push-unsubscribe'),
            data=_json.dumps({'endpoint': 'https://fcm.googleapis.com/fcm/send/dooutro'}),
            content_type='application/json',
        )
        # O endpoint é de outra pessoa: não pode ser apagado por este usuário.
        self.assertTrue(PushSubscription.objects.filter(user=outro).exists())

    def test_precisa_estar_logado(self):
        self.client.logout()
        resposta = self._inscrever()
        self.assertIn(resposta.status_code, (302, 403))

    def test_service_worker_e_servido_na_raiz_do_prefixo(self):
        """O escopo do service worker é a pasta dele: em static/js/ não cobriria as telas."""
        resposta = self.client.get(reverse('service-worker'))
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('javascript', resposta['Content-Type'])
        corpo = resposta.content.decode()
        self.assertIn("addEventListener('push'", corpo)
        self.assertIn("addEventListener('notificationclick'", corpo)
        self.assertEqual(reverse('service-worker'), '/sw.js')


class WebPushRecipientsTests(TestCase):
    """QUEM recebe o aviso — a parte que, errada, vaza atendimento.

    A regra é a mesma de quem pode abrir a conversa (`can_see_conversation`), então
    alcance por setor, perfil personalizado e o gestor master (que administra mas
    nunca lê atendimento) valem também para o pop-up.
    """

    def setUp(self):
        from accounts.models import Company, Contact, Conversation, Sector
        self.Conversation = Conversation
        self.empresa = default_company()
        self.outra_empresa = Company.objects.create(name='Vizinha', slug='vizinha')
        self.vendas = Sector.objects.create(company=self.empresa, name='Vendas')
        contato = Contact.objects.create(
            company=self.empresa, name='Cliente', phone='5516988887777')
        self.conversa = Conversation.objects.create(
            company=self.empresa, contact=contato, external_id='5516988887777',
            chat_type='private', status='pending', sector=self.vendas)

    def _com_inscricao(self, email, role, company=None, sector=None):
        from accounts.models import PushSubscription
        user = User.objects.create_user(
            company=company or self.empresa, email=email, password='x', role=role)
        if sector is not None:
            att = Attendant.objects.filter(user=user).first() or Attendant.objects.create(
                company=user.company, user=user, name=email.split('@')[0],
                must_change_password=False)
            att.sectors.add(sector)
        PushSubscription.objects.create(
            user=user, endpoint='https://fcm.googleapis.com/fcm/send/' + email,
            p256dh='p', auth='a')
        return user

    def test_adm_da_empresa_recebe(self):
        from accounts.webpush import recipients_for
        adm = self._com_inscricao('adm@x.com', User.Role.ADM)
        self.assertIn(adm, recipients_for(self.conversa))

    def test_empresa_vizinha_nunca_recebe(self):
        from accounts.webpush import recipients_for
        vizinho = self._com_inscricao('adm@y.com', User.Role.ADM, company=self.outra_empresa)
        self.assertNotIn(vizinho, recipients_for(self.conversa))

    def test_gestor_master_nunca_recebe(self):
        from accounts.models import PushSubscription
        from accounts.webpush import recipients_for
        master = User.objects.create_user(
            email='master@x.com', password='x', role=User.Role.MASTER)
        PushSubscription.objects.create(
            user=master, endpoint='https://fcm.googleapis.com/fcm/send/master',
            p256dh='p', auth='a')
        self.assertNotIn(master, recipients_for(self.conversa))

    def test_usuario_inativo_nao_recebe(self):
        from accounts.webpush import recipients_for
        fora = self._com_inscricao('fora@x.com', User.Role.ADM)
        User.objects.filter(pk=fora.pk).update(is_active=False)
        self.assertNotIn(fora, recipients_for(self.conversa))

    def test_quem_nao_alcanca_a_conversa_nao_recebe(self):
        """Alcance por setor: o pop-up respeita a mesma regra da tela."""
        from accounts.models import Sector
        from accounts.webpush import recipients_for
        outro_setor = Sector.objects.create(company=self.empresa, name='Financeiro')
        de_fora = self._com_inscricao('longe@x.com', User.Role.USUARIO, sector=outro_setor)
        recebem = recipients_for(self.conversa)
        from accounts.permissions import can_see_conversation
        # A verdade vem da mesma função que a tela usa: o teste não reimplementa a regra.
        self.assertEqual(de_fora in recebem, can_see_conversation(de_fora, self.conversa))

    def test_sem_inscricao_nao_entra_na_lista(self):
        from accounts.webpush import recipients_for
        User.objects.create_user(
            company=self.empresa, email='semsino@x.com', password='x', role=User.Role.ADM)
        self.assertEqual([u.email for u in recipients_for(self.conversa)], [])


class WebPushPayloadTests(TestCase):
    """O que aparece escrito no pop-up."""

    def setUp(self):
        from accounts.models import Contact, Conversation, Message
        self.Message = Message
        self.empresa = default_company()
        contato = Contact.objects.create(
            company=self.empresa, name='Joana Silva', phone='5516988887777')
        self.direta = Conversation.objects.create(
            company=self.empresa, contact=contato, external_id='5516988887777',
            chat_type='private', status='pending')
        self.grupo = Conversation.objects.create(
            company=self.empresa, external_id='120363000000000001@g.us',
            chat_type='group', name='Equipe Vendas', status='pending')

    def _msg(self, conversa, texto, remetente=''):
        return self.Message.objects.create(
            conversation=conversa, direction='in', message_type='text',
            text=texto, sender_name=remetente)

    def test_direta_mostra_o_contato_e_o_texto(self):
        from accounts.webpush import build_payload
        dados = build_payload(self._msg(self.direta, 'Bom dia, tudo bem?'))
        self.assertEqual(dados['title'], 'Joana Silva')
        self.assertEqual(dados['body'], 'Bom dia, tudo bem?')
        self.assertIn('conversa=%s' % self.direta.pk, dados['url'])

    def test_grupo_mostra_quem_falou(self):
        from accounts.webpush import build_payload
        dados = build_payload(self._msg(self.grupo, 'chegou o pedido', 'Ivan'))
        self.assertEqual(dados['title'], 'Equipe Vendas')
        self.assertEqual(dados['body'], 'Ivan: chegou o pedido')

    def test_texto_longo_e_cortado(self):
        from accounts.webpush import PREVIEW_MAX, build_payload
        dados = build_payload(self._msg(self.direta, 'a' * 400))
        self.assertLessEqual(len(dados['body']), PREVIEW_MAX)

    def test_midia_sem_texto_usa_o_resumo_da_conversa(self):
        from accounts.webpush import build_payload
        self.direta.last_message_text = '📷 Imagem'
        self.direta.save(update_fields=['last_message_text'])
        dados = build_payload(self._msg(self.direta, ''))
        self.assertEqual(dados['body'], '📷 Imagem')

    def test_avisos_da_mesma_conversa_sao_agrupados(self):
        from accounts.webpush import build_payload
        um = build_payload(self._msg(self.direta, 'oi'))
        dois = build_payload(self._msg(self.direta, 'tem alguem?'))
        self.assertEqual(um['tag'], dois['tag'])


class WebPushSendTests(TestCase):
    """Envio: sem chave VAPID fica inerte; inscrição morta é apagada."""

    def setUp(self):
        from accounts.models import PushSubscription
        self.PushSubscription = PushSubscription
        self.user = User.objects.create_user(
            company=default_company(), email='adm@x.com', password='x', role=User.Role.ADM)
        self.sub = PushSubscription.objects.create(
            user=self.user, endpoint='https://fcm.googleapis.com/fcm/send/abc',
            p256dh='p', auth='a')

    def test_sem_chave_vapid_nao_envia_nada(self):
        from accounts import webpush
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY='', WEBPUSH_VAPID_PRIVATE_KEY=''):
            self.assertFalse(webpush.vapid_configured())
            self.assertEqual(webpush.send_to_users([self.user], {'title': 'x'}), 0)

    def test_envio_bem_sucedido_marca_a_data(self):
        from accounts import webpush
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY='pub', WEBPUSH_VAPID_PRIVATE_KEY='priv'):
            with patch.object(webpush, '_send_one', return_value='ok'):
                self.assertEqual(webpush.send_to_users([self.user], {'title': 'x'}), 1)
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.last_sent_at)

    def test_inscricao_morta_e_apagada(self):
        """404/410 = o navegador descartou a inscrição; tentar para sempre é lixo."""
        from accounts import webpush
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY='pub', WEBPUSH_VAPID_PRIVATE_KEY='priv'):
            with patch.object(webpush, '_send_one', return_value='gone'):
                self.assertEqual(webpush.send_to_users([self.user], {'title': 'x'}), 0)
        self.assertFalse(self.PushSubscription.objects.filter(pk=self.sub.pk).exists())

    def test_erro_de_envio_nao_apaga_a_inscricao(self):
        from accounts import webpush
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY='pub', WEBPUSH_VAPID_PRIVATE_KEY='priv'):
            with patch.object(webpush, '_send_one', return_value='erro'):
                webpush.send_to_users([self.user], {'title': 'x'})
        self.assertTrue(self.PushSubscription.objects.filter(pk=self.sub.pk).exists())

    def test_mensagem_enviada_por_nos_nao_avisa_ninguem(self):
        from accounts.models import Contact, Conversation, Message
        from accounts.webpush import notify_new_message_async
        contato = Contact.objects.create(
            company=default_company(), name='C', phone='5516988887777')
        conversa = Conversation.objects.create(
            company=default_company(), contact=contato, external_id='5516988887777',
            chat_type='private')
        saida = Message.objects.create(
            conversation=conversa, direction='out', message_type='text',
            text='ola', from_me=True)
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY='pub', WEBPUSH_VAPID_PRIVATE_KEY='priv'):
            self.assertFalse(notify_new_message_async(saida))

    def test_webhook_ao_vivo_dispara_o_aviso(self):
        from wapi.services import ingest_wapi_payload
        payload = {
            'instanceId': 'LITE-X', 'connectedPhone': '556792393858', 'isGroup': False,
            'messageId': 'MSG-PUSH-1', 'fromMe': False,
            'chat': {'id': '5516988887777'},
            'sender': {'id': '5516988887777', 'pushName': 'Joana'},
            'msgContent': {'conversation': 'bom dia'},
        }
        with patch('wapi.services.notify_new_message_async') as avisar:
            ingest_wapi_payload(payload, trigger_ai=True, company=default_company())
        self.assertTrue(avisar.called)

    def test_sincronizacao_de_historico_nao_avisa(self):
        """Reprocessar evento antigo não pode disparar pop-up de mensagem velha."""
        from wapi.services import ingest_wapi_payload
        payload = {
            'instanceId': 'LITE-X', 'connectedPhone': '556792393858', 'isGroup': False,
            'messageId': 'MSG-PUSH-2', 'fromMe': False,
            'chat': {'id': '5516988887777'},
            'sender': {'id': '5516988887777', 'pushName': 'Joana'},
            'msgContent': {'conversation': 'mensagem de semanas atras'},
        }
        with patch('wapi.services.notify_new_message_async') as avisar:
            ingest_wapi_payload(payload, trigger_ai=False, company=default_company())
        self.assertFalse(avisar.called)
