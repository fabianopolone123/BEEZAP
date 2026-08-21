"""Entrar, sair, recuperar senha e a troca no primeiro acesso.
"""

from .base import (
    Attendant,
    PasswordResetCode,
    SimpleNamespace,
    TestCase,
    User,
    check_password,
    default_company,
    patch,
    reverse,
)


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

    @patch('accounts.views.common.secrets.randbelow', return_value=123456)
    @patch('accounts.views.common.send_text_message')
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

    @patch('accounts.views.common.send_text_message')
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

    @patch('accounts.views.common.send_text_message')
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

    @patch('accounts.views.common.secrets.randbelow', return_value=123456)
    @patch('accounts.views.common.send_text_message')
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

    @patch('accounts.views.common.secrets.randbelow', return_value=123456)
    @patch('accounts.views.common.send_text_message')
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

    @patch('accounts.views.common.secrets.randbelow', return_value=123456)
    @patch('accounts.views.common.send_text_message')
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
class LogoutRequiresPostTests(TestCase):
    """Sair da conta so por POST.

    Por GET, qualquer pagina de terceiros derrubava a sessao de quem a abrisse com um
    `<img src=".../logout/">` — o navegador segue a imagem e o Django processava a
    saida. O proprio Django tirou o GET do `LogoutView` na versao 5 por isso.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='sai@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=default_company(),
        )
        self.user.attendant_profile.must_change_password = False
        self.user.attendant_profile.save(update_fields=['must_change_password'])

    def test_get_nao_derruba_a_sessao(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)
        # Continua logado: a proxima tela abre normalmente.
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_post_sai_normalmente(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('login'))

    def test_a_tela_oferece_o_botao_como_formulario(self):
        self.client.force_login(self.user)
        corpo = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('<form method="post" action="%s"' % reverse('logout'), corpo)
        self.assertIn('csrfmiddlewaretoken', corpo)
class EmailIsAlwaysLowercaseTests(TestCase):
    """E-mail duplicado por CAIXA nao pode virar 500 no login.

    `User.email` e unico no banco de forma sensivel a caixa, mas o login busca com
    `email__iexact`. Sem normalizar, `Joao@x.com` e `joao@x.com` coexistiam (via
    shell ou admin do Django) e o login estourava `MultipleObjectsReturned`.
    """

    def test_save_normaliza_para_minusculo(self):
        user = User.objects.create_user(
            email='Maiuscula@Exemplo.COM', password='SenhaForte123',
            role=User.Role.USUARIO, company=default_company(),
        )
        user.refresh_from_db()
        self.assertEqual(user.email, 'maiuscula@exemplo.com')

    def test_login_funciona_com_qualquer_caixa(self):
        User.objects.create_user(
            email='Pessoa@Exemplo.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=default_company(),
        )
        for tentativa in ('pessoa@exemplo.com', 'Pessoa@Exemplo.com', 'PESSOA@EXEMPLO.COM'):
            with self.subTest(email=tentativa):
                self.assertTrue(
                    self.client.login(email=tentativa, password='SenhaForte123')
                )
                self.client.logout()

    def test_backend_nao_estoura_com_duas_contas_de_caixas_diferentes(self):
        """Contas antigas gravadas antes da normalizacao continuam logando."""
        from django.contrib.auth import authenticate
        User.objects.create_user(
            email='dupla@exemplo.com', password='SenhaUm123',
            role=User.Role.USUARIO, company=default_company(),
        )
        # Grava a segunda por UPDATE, driblando o save() (simula o estado legado).
        outra = User.objects.create_user(
            email='outra@exemplo.com', password='SenhaDois123',
            role=User.Role.USUARIO, company=default_company(),
        )
        User.objects.filter(pk=outra.pk).update(email='Dupla@exemplo.com')
        # Antes isto levantava MultipleObjectsReturned (500 na tela de login).
        self.assertIsNotNone(authenticate(email='dupla@exemplo.com', password='SenhaUm123'))
        self.assertIsNotNone(authenticate(email='dupla@exemplo.com', password='SenhaDois123'))
        self.assertIsNone(authenticate(email='dupla@exemplo.com', password='ErradaTotal'))
