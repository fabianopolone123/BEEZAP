from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import Attendant, User


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
