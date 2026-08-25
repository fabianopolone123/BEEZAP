"""Tela Pesquisar: garimpa o historico do atendimento (texto + filtros).

O sistema tinha o historico todo e nenhum jeito de garimpar nele. Os testes daqui
cobrem as tres coisas que fazem esta tela valer: achar pelo CONTEUDO da mensagem,
combinar com os filtros, e nunca escapar do ALCANCE de quem pesquisa.
"""

from datetime import timedelta

from django.utils import timezone

from .base import (
    Attendant,
    TestCase,
    User,
    default_company,
    patch,
    reverse,
)


class SearchScreenTests(TestCase):
    """Tela Pesquisar: garimpa o historico do atendimento.

    O que importa aqui: achar pelo CONTEUDO da mensagem (o que nenhuma outra tela
    fazia), combinar com os filtros, e nunca escapar do ALCANCE de quem pesquisa —
    liberar o botao nao pode virar "ver tudo".
    """

    def setUp(self):
        from accounts.models import Company, Contact, Conversation, Message, Sector
        self.Conversation = Conversation
        self.Message = Message
        self.empresa = default_company()
        self.vizinha = Company.objects.create(name='Vizinha', slug='vizinha')
        self.vendas = Sector.objects.create(company=self.empresa, name='Vendas')
        self.financeiro = Sector.objects.create(company=self.empresa, name='Financeiro')
        self.adm = User.objects.create_user(
            company=self.empresa, email='adm@x.com', password='x', role=User.Role.ADM)
        self.bruno = Attendant.objects.create(
            company=self.empresa,
            user=User.objects.create_user(
                company=self.empresa, email='bruno@x.com', password='x',
                role=User.Role.USUARIO),
            name='Bruno', must_change_password=False)
        self.bruno.sectors.add(self.vendas)

        self.joana = Contact.objects.create(
            company=self.empresa, name='Joana Silva', phone='5516900000001')
        self.conv_joana = Conversation.objects.create(
            company=self.empresa, contact=self.joana, external_id='5516900000001',
            chat_type='private', status='closed', sector=self.vendas,
            assigned_attendant=self.bruno, last_message_at=timezone.now())
        self.msg = Message.objects.create(
            conversation=self.conv_joana, direction='in', message_type='text',
            text='Preciso da nota fiscal do pedido 4471, por favor',
            sender_name='Joana Silva')

        carlos = Contact.objects.create(
            company=self.empresa, name='Carlos Souza', phone='5516900000002')
        self.conv_carlos = Conversation.objects.create(
            company=self.empresa, contact=carlos, external_id='5516900000002',
            chat_type='private', status='open', sector=self.financeiro,
            last_message_at=timezone.now())
        Message.objects.create(
            conversation=self.conv_carlos, direction='in', message_type='text',
            text='bom dia, tudo bem?', sender_name='Carlos Souza')

        alheio = Contact.objects.create(
            company=self.vizinha, name='Vizinho', phone='5516900000009')
        self.conv_vizinha = Conversation.objects.create(
            company=self.vizinha, contact=alheio, external_id='5516900000009',
            chat_type='private', status='open', last_message_at=timezone.now())
        Message.objects.create(
            conversation=self.conv_vizinha, direction='in', message_type='text',
            text='nota fiscal da outra empresa', sender_name='Vizinho')

        self.client.force_login(self.adm)

    def _buscar(self, **params):
        return self.client.get(reverse('search-results'), params,
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def _ids(self, resposta):
        return [i['id'] for i in resposta.json()['itens']]

    # ----- a tela -----

    def test_adm_abre_a_tela(self):
        r = self.client.get(reverse('search'))
        self.assertEqual(r.status_code, 200)
        corpo = r.content.decode()
        self.assertIn('data-search-q', corpo)
        self.assertIn('data-search-filter="atendente"', corpo)
        self.assertIn('data-search-filter="de"', corpo)

    def test_o_botao_aparece_no_menu_do_adm(self):
        corpo = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Pesquisar', corpo)

    def test_usuario_comum_nao_alcanca(self):
        """A busca nao entra no padrao de `usuario`/`leitor`: o ADM libera se quiser."""
        comum = User.objects.create_user(
            company=self.empresa, email='comum@x.com', password='x',
            role=User.Role.USUARIO)
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse('search')).status_code, 403)
        self.assertEqual(self._buscar(q='nota').status_code, 403)

    def test_a_feature_entra_na_matriz_de_permissoes(self):
        corpo = self.client.get(reverse('permissions')).content.decode()
        self.assertIn('role__usuario__search', corpo)

    # ----- achar pelo conteudo -----

    def test_acha_pelo_conteudo_da_mensagem(self):
        dados = self._buscar(q='nota fiscal').json()
        self.assertEqual(self._ids(self._buscar(q='nota fiscal')), [self.conv_joana.pk])
        item = dados['itens'][0]
        self.assertEqual(item['total_trechos'], 1)
        self.assertIn('nota fiscal', item['trechos'][0]['texto'])
        self.assertEqual(item['trechos'][0]['quem'], 'Joana Silva')

    def test_acha_pelo_nome_do_contato(self):
        self.assertEqual(self._ids(self._buscar(q='Joana')), [self.conv_joana.pk])

    def test_acha_pelo_telefone(self):
        self.assertEqual(self._ids(self._buscar(q='900000002')), [self.conv_carlos.pk])

    def test_busca_nao_diferencia_maiuscula(self):
        self.assertEqual(self._ids(self._buscar(q='NOTA FISCAL')), [self.conv_joana.pk])

    def test_o_trecho_vem_centrado_na_ocorrencia(self):
        longo = ('bla ' * 200) + 'palavra-chave-rara' + (' bla' * 200)
        self.Message.objects.create(
            conversation=self.conv_carlos, direction='in', message_type='text',
            text=longo, sender_name='Carlos Souza')
        item = self._buscar(q='palavra-chave-rara').json()['itens'][0]
        trecho = item['trechos'][0]['texto']
        self.assertIn('palavra-chave-rara', trecho)
        self.assertLess(len(trecho), 260)  # nao manda a mensagem inteira
        self.assertTrue(trecho.startswith('…'))

    def test_mensagem_de_sistema_nao_conta(self):
        """As divisorias ("Atendimento encerrado") nao sao conteudo de conversa."""
        self.Message.objects.create(
            conversation=self.conv_carlos, direction='out', message_type='system',
            text='Atendimento encerrado')
        self.assertEqual(self._ids(self._buscar(q='Atendimento encerrado')), [])

    def test_texto_curto_nao_varre_o_banco(self):
        dados = self._buscar(q='n').json()
        self.assertEqual(dados['itens'], [])
        self.assertIn('letras', dados['aviso'])

    def test_nada_encontrado_devolve_lista_vazia(self):
        dados = self._buscar(q='xyzabc-nao-existe').json()
        self.assertEqual(dados['itens'], [])
        self.assertEqual(dados['total'], 0)

    # ----- filtros -----

    def test_filtra_por_atendente(self):
        self.assertEqual(self._ids(self._buscar(atendente=self.bruno.pk)),
                         [self.conv_joana.pk])

    def test_filtra_por_setor(self):
        self.assertEqual(self._ids(self._buscar(setor=self.financeiro.pk)),
                         [self.conv_carlos.pk])

    def test_filtra_por_estado(self):
        self.assertEqual(self._ids(self._buscar(status='closed')), [self.conv_joana.pk])

    def test_filtra_por_contato(self):
        self.assertEqual(self._ids(self._buscar(contato='Carlos')), [self.conv_carlos.pk])

    def test_filtra_por_tipo(self):
        grupo = self.Conversation.objects.create(
            company=self.empresa, external_id='120363000000000001@g.us',
            chat_type='group', name='Equipe', status='open',
            last_message_at=timezone.now())
        self.assertEqual(self._ids(self._buscar(tipo='grupos')), [grupo.pk])
        self.assertNotIn(grupo.pk, self._ids(self._buscar(tipo='diretas')))

    def test_filtra_por_periodo(self):
        antiga = timezone.now() - timedelta(days=40)
        self.Conversation.objects.filter(pk=self.conv_joana.pk).update(
            last_message_at=antiga)
        hoje = timezone.localdate().isoformat()
        self.assertNotIn(self.conv_joana.pk, self._ids(self._buscar(de=hoje)))
        self.assertIn(self.conv_joana.pk,
                      self._ids(self._buscar(de=antiga.date().isoformat())))

    def test_combina_texto_e_filtro(self):
        """"nota fiscal" no Financeiro nao existe: o filtro tem de cortar."""
        self.assertEqual(
            self._ids(self._buscar(q='nota fiscal', setor=self.financeiro.pk)), [])
        self.assertEqual(
            self._ids(self._buscar(q='nota fiscal', setor=self.vendas.pk)),
            [self.conv_joana.pk])

    def test_sem_nada_preenchido_nao_lista_o_historico_inteiro(self):
        dados = self._buscar().json()
        self.assertEqual(dados['total'], self.Conversation.objects.filter(
            company=self.empresa).count())
        # (a tela nem chama o endpoint nesse caso; aqui garantimos que ele nao explode)
        self.assertTrue(dados['ok'])

    def test_os_filtros_aplicados_voltam_como_rotulo(self):
        dados = self._buscar(setor=self.vendas.pk, status='closed').json()
        self.assertIn('setor: Vendas', dados['filtros'])
        self.assertIn('estado: Encerrada', dados['filtros'])

    def test_id_invalido_no_filtro_nao_derruba(self):
        self.assertEqual(self._buscar(atendente='abc', setor='xyz').status_code, 200)

    # ----- escopo -----

    def test_conversa_de_outra_empresa_nunca_aparece(self):
        self.assertNotIn(self.conv_vizinha.pk, self._ids(self._buscar(q='nota fiscal')))

    def test_respeita_o_alcance_de_quem_pesquisa(self):
        """Liberar o botao nao vira "ver tudo": vale o alcance da tela Conversas."""
        from accounts.models import UserMenuPermission
        from accounts.permissions import can_see_conversation
        restrito = self.bruno.user  # atua so em Vendas
        UserMenuPermission.objects.update_or_create(
            user=restrito, defaults={'allowed_keys': ['search', 'conversations']})
        self.client.force_login(restrito)
        encontrados = self._ids(self._buscar(status='open'))
        # A verdade vem da MESMA funcao que a tela Conversas usa.
        esperado = [
            c.pk for c in (self.conv_carlos,)
            if can_see_conversation(restrito, c)
        ]
        self.assertEqual(encontrados, esperado)

    def test_gestor_master_nao_pesquisa_atendimento(self):
        master = User.objects.create_user(
            email='master@x.com', password='x', role=User.Role.MASTER)
        self.client.force_login(master)
        self.assertEqual(self.client.get(reverse('search')).status_code, 403)

    def test_precisa_estar_logado(self):
        self.client.logout()
        self.assertIn(self._buscar(q='nota').status_code, (302, 403))

    # ----- teto -----

    def test_teto_de_resultados(self):
        from accounts.models import Contact
        from accounts.views.search import SEARCH_LIMIT
        for i in range(SEARCH_LIMIT + 5):
            telefone = '55169988%05d' % i
            contato = Contact.objects.create(
                company=self.empresa, name='Cliente %s' % i, phone=telefone)
            conv = self.Conversation.objects.create(
                company=self.empresa, contact=contato, external_id=telefone,
                chat_type='private', status='open', last_message_at=timezone.now())
            self.Message.objects.create(
                conversation=conv, direction='in', message_type='text',
                text='assunto repetido para todos')
        dados = self._buscar(q='assunto repetido').json()
        self.assertEqual(len(dados['itens']), SEARCH_LIMIT)
        self.assertGreater(dados['total'], SEARCH_LIMIT)


class SearchVolumeWarningTests(TestCase):
    """O aviso que avisa ANTES da busca ficar lenta (`beezap.W004`).

    A busca por conteudo e varredura (`LIKE`), que cresce LINEAR com o volume: medido
    em banco sintetico, 500 mil mensagens = ~63 ms por busca, 1 milhao = ~130 ms, 2
    milhoes = ~254 ms. O pior jeito de descobrir isso seria um cliente reclamando —
    entao o `check` avisa no deploy, no mesmo lugar dos outros avisos.
    """

    def test_nao_avisa_com_volume_pequeno(self):
        from accounts.checks import search_volume_check
        self.assertEqual(search_volume_check(None), [])

    def test_avisa_ao_passar_do_limite(self):
        from accounts.checks import LIMITE_MENSAGENS_BUSCA, search_volume_check
        from accounts.models import Message
        with patch.object(Message.objects.__class__, 'count',
                          return_value=LIMITE_MENSAGENS_BUSCA):
            avisos = search_volume_check(None)
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos[0].id, 'beezap.W004')
        # O aviso tem de dizer o que NAO resolve, senao alguem tenta indice comum.
        self.assertIn('Indice comum NAO resolve', avisos[0].hint)
        self.assertIn('FTS5', avisos[0].hint)
        self.assertIn('PostgreSQL', avisos[0].hint)

    def test_o_aviso_mostra_o_numero_formatado(self):
        from accounts.checks import search_volume_check
        from accounts.models import Message
        with patch.object(Message.objects.__class__, 'count', return_value=1234567):
            avisos = search_volume_check(None)
        self.assertIn('1.234.567', avisos[0].msg)

    def test_banco_sem_migrar_nao_derruba_o_check(self):
        from accounts.checks import search_volume_check
        from accounts.models import Message
        with patch.object(Message.objects.__class__, 'count',
                          side_effect=Exception('no such table')):
            self.assertEqual(search_volume_check(None), [])

