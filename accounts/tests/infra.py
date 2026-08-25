"""Configuracao, comandos de management, versionamento de estaticos,
admin do Django, sinais e Dashboard.
"""

from .base import (
    Attendant,
    SimpleTestCase,
    TestCase,
    User,
    default_company,
    patch,
    reverse,
)


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
class DashboardResponseTimeTests(TestCase):
    """O tempo medio de resposta e calculado no banco, nao em memoria.

    Antes: `prefetch_related('messages')` sobre todas as conversas com atividade em
    30 dias, ordenando e filtrando em Python — trazia 30 dias de mensagens do cliente
    inteiro para produzir um unico numero.
    """

    def setUp(self):
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from ..models import Contact, Conversation, Message
        self.company = default_company()
        self.adm = User.objects.create_user(
            email='adm-dashboard@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=self.company,
        )
        contato = Contact.objects.create(
            company=self.company, name='Cliente Tempo', phone='5519666665555',
        )
        self.conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519666665555',
            chat_type='private', last_message_at=_tz.now(),
        )
        agora = _tz.now()
        entrada = Message.objects.create(
            conversation=self.conv, direction='in', message_type='text', text='oi',
        )
        Message.objects.filter(pk=entrada.pk).update(created_at=agora - _td(minutes=10))
        saida = Message.objects.create(
            conversation=self.conv, direction='out', message_type='text', text='ola',
        )
        Message.objects.filter(pk=saida.pk).update(created_at=agora - _td(minutes=8))

    def test_tempo_medio_de_resposta(self):
        from ..views import build_dashboard_context
        contexto = build_dashboard_context(self.company)
        tempo = next(s['value'] for s in contexto['stats']
                     if s['label'] == 'Tempo médio de resposta')
        self.assertEqual(tempo, '00:02:00')

    def test_conversa_iniciada_pelo_atendente_nao_conta(self):
        """Resposta ANTES da 1a mensagem do cliente nao e tempo de resposta."""
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from ..models import Contact, Conversation, Message
        from ..views import build_dashboard_context
        contato = Contact.objects.create(
            company=self.company, name='Prospect', phone='5519111110000',
        )
        conv = Conversation.objects.create(
            company=self.company, contact=contato, external_id='5519111110000',
            chat_type='private', last_message_at=_tz.now(),
        )
        agora = _tz.now()
        saida = Message.objects.create(
            conversation=conv, direction='out', message_type='text', text='oi, tudo bem?',
        )
        Message.objects.filter(pk=saida.pk).update(created_at=agora - _td(hours=5))
        entrada = Message.objects.create(
            conversation=conv, direction='in', message_type='text', text='tudo',
        )
        Message.objects.filter(pk=entrada.pk).update(created_at=agora - _td(hours=1))
        contexto = build_dashboard_context(self.company)
        tempo = next(s['value'] for s in contexto['stats']
                     if s['label'] == 'Tempo médio de resposta')
        # So a primeira conversa entra na media (2 minutos).
        self.assertEqual(tempo, '00:02:00')

    def test_sem_atendimento_mostra_placeholder(self):
        from ..models import Message
        from ..views import build_dashboard_context
        Message.objects.all().delete()
        contexto = build_dashboard_context(self.company)
        tempo = next(s['value'] for s in contexto['stats']
                     if s['label'] == 'Tempo médio de resposta')
        self.assertEqual(tempo, '--:--:--')
class InvalidIdsDoNotBreakTests(TestCase):
    """Id nao numerico em formulario responde mensagem, nao 500.

    `Model.objects.filter(pk='abc')` levanta `ValueError` no Django. Varios POSTs
    liam id cru do formulario, entao um valor forjado (ou um bug de front) virava
    erro 500 em vez de "nao encontrado".
    """

    def setUp(self):
        self.master = User.objects.create_user(
            email='master-ids@x.com', password='SenhaForte123', role=User.Role.MASTER,
        )
        self.adm = User.objects.create_user(
            email='adm-ids@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=default_company(),
        )
        self.adm.attendant_profile.must_change_password = False
        self.adm.attendant_profile.save(update_fields=['must_change_password'])

    def test_id_valido_aceita_so_numero(self):
        from ..views import id_valido
        self.assertEqual(id_valido('42'), 42)
        self.assertEqual(id_valido(' 7 '), 7)
        self.assertIsNone(id_valido('abc'))
        self.assertIsNone(id_valido(''))
        self.assertIsNone(id_valido(None))
        self.assertIsNone(id_valido('1; DROP TABLE'))
        self.assertIsNone(id_valido('-3'))

    def test_clientes_com_id_invalido(self):
        self.client.force_login(self.master)
        for acao in ('delete', 'toggle-active', 'enter', 'create-admin'):
            with self.subTest(acao=acao):
                response = self.client.post(
                    reverse('clients'), {'action': acao, 'company_id': 'abc'}
                )
                self.assertEqual(response.status_code, 302)

    def test_gestores_com_id_invalido(self):
        self.client.force_login(self.master)
        response = self.client.post(
            reverse('masters'), {'action': 'delete', 'master_id': 'xyz'}
        )
        self.assertEqual(response.status_code, 302)

    def test_permissoes_com_id_invalido(self):
        self.client.force_login(self.adm)
        casos = [
            {'form_type': 'user', 'user_id': 'nao-e-numero'},
            {'form_type': 'user-reset', 'user_id': 'nao-e-numero'},
            {'form_type': 'view-user', 'user_id': 'nao-e-numero'},
            {'form_type': 'view-user-reset', 'user_id': 'nao-e-numero'},
            {'form_type': 'profile-role', 'user_id': 'nao-e-numero', 'role': 'adm'},
            {'form_type': 'group-name', 'group_id': 'nao-e-numero', 'name': 'x'},
            {'form_type': 'group-remove', 'group_id': 'nao-e-numero'},
        ]
        for dados in casos:
            with self.subTest(form_type=dados['form_type']):
                response = self.client.post(reverse('permissions'), dados)
                self.assertIn(response.status_code, (302, 400))

    def test_permissoes_com_usuario_invalido_na_query(self):
        self.client.force_login(self.adm)
        response = self.client.get(reverse('permissions') + '?user=abc&tab=botoes')
        self.assertEqual(response.status_code, 200)

    def test_contatos_com_id_invalido(self):
        self.client.force_login(self.adm)
        response = self.client.post(
            reverse('contacts'), {'action': 'delete', 'contact_id': 'abc'}
        )
        self.assertEqual(response.status_code, 302)

    def test_atendentes_com_id_invalido(self):
        self.client.force_login(self.adm)
        response = self.client.post(
            reverse('attendants'),
            {'attendant_id': 'abc', 'name': 'X', 'email': 'x@y.com', 'phone': ''},
        )
        self.assertIn(response.status_code, (200, 302))
class ProductionSettingsTests(SimpleTestCase):
    """Configuracao de producao: cookies proprios e chave obrigatoria."""

    def test_cookie_de_sessao_tem_nome_proprio(self):
        """O dominio serve varios sistemas Django; `sessionid` colide entre eles."""
        from django.conf import settings
        self.assertNotEqual(settings.SESSION_COOKIE_NAME, 'sessionid')
        self.assertNotEqual(settings.CSRF_COOKIE_NAME, 'csrftoken')
        self.assertIn('beeonboard', settings.SESSION_COOKIE_NAME)
        self.assertIn('beeonboard', settings.CSRF_COOKIE_NAME)

    def test_sqlite_roda_com_wal_e_timeout(self):
        """2 workers + threads de background gravando na mesma base."""
        from django.conf import settings
        opcoes = settings.DATABASES['default'].get('OPTIONS') or {}
        if 'sqlite' not in settings.DATABASES['default']['ENGINE']:
            self.skipTest('so vale para SQLite')
        self.assertGreaterEqual(opcoes.get('timeout', 0), 10)
        self.assertIn('WAL', opcoes.get('init_command', ''))
class AssetVersioningTests(TestCase):
    """Todo CSS sai versionado, sem `?v=N` na mao.

    Regressao de um problema recorrente: o `?v=N` era incrementado manualmente e o
    MESMO arquivo era carregado por varios templates. No pente fino, `dashboard.css`
    estava com `?v=6` em 8 templates e SEM versao em outros 7 — ou seja, editar o CSS
    e bumpar limpava o cache de metade das telas e deixava a outra metade com o
    arquivo antigo no navegador. E era o sintoma "mudei o CSS e nao aparece" que o
    projeto ja tinha documentado como armadilha.
    """

    def test_a_tag_versiona_pela_data_do_arquivo(self):
        from django.template import Context, Template
        saida = Template(
            "{% load beeonboard_assets %}{% asset 'css/dashboard.css' %}"
        ).render(Context({}))
        self.assertIn('css/dashboard.css?v=', saida)

    def test_arquivo_inexistente_nao_derruba_a_pagina(self):
        from django.template import Context, Template
        saida = Template(
            "{% load beeonboard_assets %}{% asset 'css/nao-existe.css' %}"
        ).render(Context({}))
        self.assertIn('css/nao-existe.css', saida)
        self.assertNotIn('?v=', saida)

    def test_arquivos_diferentes_tem_versoes_diferentes(self):
        from accounts.templatetags.beeonboard_assets import _versao_do_arquivo
        um = _versao_do_arquivo('css/dashboard.css')
        outro = _versao_do_arquivo('css/conversations.css')
        self.assertTrue(um and outro)
        self.assertNotEqual(um, outro)

    def test_nenhum_template_usa_v_manual_em_css(self):
        """Se voltar a aparecer `?v=N` num link de CSS, este teste reprova."""
        import glob
        problemas = []
        for caminho in glob.glob('templates/**/*.html', recursive=True):
            with open(caminho, encoding='utf-8') as arquivo:
                for numero, linha in enumerate(arquivo, 1):
                    if '.css' in linha and '?v=' in linha:
                        problemas.append('%s:%d' % (caminho, numero))
        self.assertEqual(problemas, [], 'use {%% asset %%} em vez de ?v= na mao')

    def test_toda_tela_carrega_css_versionado(self):
        """Varre as telas principais e confere que o link saiu com versao."""
        import re
        adm = User.objects.create_user(
            email='adm-asset@x.com', password='SenhaForte123',
            role=User.Role.ADM, company=default_company(),
        )
        adm.attendant_profile.must_change_password = False
        adm.attendant_profile.save(update_fields=['must_change_password'])
        self.client.force_login(adm)
        for rota in ('dashboard', 'conversations', 'contacts', 'attendants',
                     'sectors', 'permissions', 'atendimento', 'company-brand',
                     'company-data'):
            with self.subTest(rota=rota):
                corpo = self.client.get(reverse(rota)).content.decode()
                links = re.findall(r'href="([^"]*\.css[^"]*)"', corpo)
                self.assertTrue(links, 'nenhum CSS na tela %s' % rota)
                for link in links:
                    self.assertIn('?v=', link, '%s sem versao em %s' % (link, rota))
class AuditarEmpresasCommandTests(TestCase):
    """Auditoria do vinculo de empresa: prova o isolamento em vez de supor.

    As views filtram por empresa em cada ponto, entao na pratica nao aparece registro
    cruzado — mas nada PROVAVA isso. O comando e somente leitura de proposito: mover
    dado de cliente e decisao consciente, nao correcao automatica.
    """

    def _rodar(self, **kwargs):
        from io import StringIO
        from django.core.management import call_command
        saida = StringIO()
        call_command('auditar_empresas', stdout=saida, **kwargs)
        return saida.getvalue()

    def test_banco_coerente_passa_limpo(self):
        saida = self._rodar()
        self.assertIn('Nenhuma incoerencia', saida)

    def test_detecta_atendente_com_empresa_diferente_do_usuario(self):
        from ..models import Attendant, Company
        outra = Company.objects.create(name='Empresa B', slug='empresa-b-aud')
        user = User.objects.create_user(
            email='cruzado@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=default_company(),
        )
        atendente = Attendant.objects.create(
            company=default_company(), user=user, name='Cruzado',
        )
        # Simula o estado torto: a empresa do atendente diverge da do usuario.
        Attendant.objects.filter(pk=atendente.pk).update(company=outra)
        saida = self._rodar()
        self.assertIn('Attendant com empresa diferente', saida)
        self.assertIn('[1]', saida)

    def test_detecta_conversa_com_setor_de_outra_empresa(self):
        from ..models import Company, Contact, Conversation, Sector
        outra = Company.objects.create(name='Empresa C', slug='empresa-c-aud')
        setor_de_fora = Sector.objects.create(company=outra, name='Estranho')
        contato = Contact.objects.create(
            company=default_company(), name='X', phone='5519000001111',
        )
        conv = Conversation.objects.create(
            company=default_company(), contact=contato,
            external_id='5519000001111', chat_type='private',
        )
        Conversation.objects.filter(pk=conv.pk).update(sector=setor_de_fora)
        saida = self._rodar()
        self.assertIn('Conversation com setor de outra empresa', saida)

    def test_detecta_usuario_operacional_sem_empresa(self):
        user = User.objects.create_user(
            email='sem-empresa@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=default_company(),
        )
        User.objects.filter(pk=user.pk).update(company=None)
        saida = self._rodar()
        self.assertIn('Usuario operacional SEM empresa', saida)

    def test_detalhe_lista_os_ids(self):
        user = User.objects.create_user(
            email='sem-empresa2@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=default_company(),
        )
        User.objects.filter(pk=user.pk).update(company=None)
        saida = self._rodar(detalhe=True)
        self.assertIn('ids: [%d]' % user.pk, saida)

    def test_nao_corrige_nada(self):
        """Somente leitura: o comando nao pode mexer no vinculo por conta propria."""
        from ..models import Attendant, Company
        outra = Company.objects.create(name='Empresa D', slug='empresa-d-aud')
        user = User.objects.create_user(
            email='intocado@x.com', password='SenhaForte123',
            role=User.Role.USUARIO, company=default_company(),
        )
        atendente = Attendant.objects.create(
            company=default_company(), user=user, name='Intocado',
        )
        Attendant.objects.filter(pk=atendente.pk).update(company=outra)
        self._rodar()
        atendente.refresh_from_db()
        self.assertEqual(atendente.company_id, outra.pk)


class TemplateCommentsDoNotLeakTests(SimpleTestCase):
    """Comentario de template nao pode virar texto na tela.

    O lexer do Django reconhece `{# ... #}` apenas quando abre e fecha na MESMA
    linha (o regex de tokens nao usa DOTALL). Um comentario multilinha nao vira
    token: sai como TEXTO LITERAL no HTML, e o usuario final LE o comentario.

    Isso estava acontecendo de verdade em tres telas — na de Clientes, o cartao de
    cada empresa exibia uma frase interna sobre exclusao de conversas. Para varias
    linhas o certo e `{% comment %} ... {% endcomment %}`.
    """

    def test_nenhum_comentario_atravessa_linhas(self):
        import glob
        import re
        abre = re.compile(r'\{#')
        fecha = re.compile(r'#\}')
        problemas = []
        for caminho in sorted(glob.glob('templates/**/*.html', recursive=True)):
            with open(caminho, encoding='utf-8') as arquivo:
                aberto_em = None
                for numero, linha in enumerate(arquivo, 1):
                    if aberto_em is None:
                        if abre.search(linha) and not fecha.search(linha):
                            aberto_em = numero
                    elif fecha.search(linha):
                        problemas.append('%s:%d' % (caminho, aberto_em))
                        aberto_em = None
                if aberto_em is not None:
                    problemas.append('%s:%d' % (caminho, aberto_em))
        self.assertEqual(
            problemas, [],
            'comentario {# #} de varias linhas vaza para o HTML; use {% comment %}',
        )


class FrontEndCsrfTokenTests(SimpleTestCase):
    """Nenhuma tela pode ler o cookie CSRF pelo nome PADRAO do Django.

    `CSRF_COOKIE_NAME` e proprio (`beeonboard_csrftoken`) porque o dominio serve
    varios sistemas Django. Um JS que procure `csrftoken` no `document.cookie`
    recebe vazio, manda o header em branco e leva 403 — e como a resposta do 403
    vem em HTML, o `r.json()` estoura e o usuario ve so um erro generico. Foi o que
    aconteceu nas telas Permissoes, Setores e Metricas do cliente. O certo e usar o
    token RENDERIZADO (`{{ csrf_token }}`), que nao depende do nome do cookie.
    """

    def test_nenhum_front_le_o_cookie_csrf_pelo_nome_fixo(self):
        import glob
        problemas = []
        alvos = (glob.glob('templates/**/*.html', recursive=True)
                 + glob.glob('static/js/**/*.js', recursive=True))
        for caminho in sorted(alvos):
            with open(caminho, encoding='utf-8') as arquivo:
                for numero, linha in enumerate(arquivo, 1):
                    if 'csrftoken' in linha:  # minusculo = nome do cookie
                        problemas.append('%s:%d' % (caminho, numero))
        self.assertEqual(
            problemas, [],
            'use o token renderizado ({{ csrf_token }}), nao o cookie "csrftoken"',
        )


class InspectWapiGroupsCommandTests(TestCase):
    """`inspect_wapi_groups` tem que dizer POR QUE um grupo esta sem nome.

    A tela mostrava "Grupo 120363183095447474" e "Grupo 556784455916-1560176734@g.us"
    e nao havia como saber a causa: o comando so imprimia a resposta da W-API, e
    comparar na mao com o `external_id` de cada conversa (que pode vir com ou sem
    `@g.us`, e a comparacao e por DIGITOS) e justamente a parte que erra.

    As tres causas: (1) a W-API tem o nome, mas a busca da criacao falhou; (2) a
    W-API devolve o grupo sem nome em campo conhecido; (3) a W-API nao lista o chat
    (canal/comunidade que chegou com id "pelado", ou a conta saiu do grupo).
    """

    def setUp(self):
        from accounts.models import Conversation
        self.empresa = default_company()
        self.Conversation = Conversation
        self.com_nome = Conversation.objects.create(
            company=self.empresa, external_id='120363000000000001@g.us',
            chat_type='group', name='Equipe Vendas')
        self.causa1 = Conversation.objects.create(
            company=self.empresa, external_id='120363000000000002@g.us',
            chat_type='group', name='')
        self.causa2 = Conversation.objects.create(
            company=self.empresa, external_id='556784455916-1560176734@g.us',
            chat_type='group', name='')
        self.causa3 = Conversation.objects.create(
            company=self.empresa, external_id='120363183095447474',
            chat_type='group', name='')

    def _rodar(self, resposta):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        with patch('wapi.services.get_all_groups_safe', return_value=resposta):
            call_command('inspect_wapi_groups', stdout=out)
        return out.getvalue()

    def test_separa_as_tres_causas(self):
        saida = self._rodar([
            {'id': '120363000000000001@g.us', 'name': 'Equipe Vendas'},
            {'id': '120363000000000002@g.us', 'name': 'Financeiro'},
            # Devolvido pela W-API, mas sem nome em nenhum campo conhecido.
            {'id': '556784455916-1560176734@g.us', 'participants': 12},
            # O grupo "pelado" 120363183095447474 nao vem na lista.
        ])
        linhas = {}
        for linha in saida.splitlines():
            if linha.startswith('- external_id='):
                chave = linha.split('external_id=', 1)[1].split(' |', 1)[0]
                linhas[chave] = linha
        self.assertIn('ok (nome: Equipe Vendas)', linhas['120363000000000001@g.us'])
        self.assertIn('causa 1', linhas['120363000000000002@g.us'])
        self.assertIn('Financeiro', linhas['120363000000000002@g.us'])
        self.assertIn('causa 2', linhas['556784455916-1560176734@g.us'])
        self.assertIn('causa 3', linhas['120363183095447474'])

    def test_id_pelado_casa_com_o_jid_da_wapi(self):
        """A comparacao e por DIGITOS: sem sufixo no banco e com sufixo na W-API."""
        saida = self._rodar([{'id': '120363183095447474@g.us', 'subject': 'Obra Centro'}])
        self.assertIn('Obra Centro', saida)
        self.assertNotIn('causa 3', saida.split('120363183095447474 |')[1].split('\n')[0])

    def test_falha_na_wapi_avisa_e_para(self):
        saida = self._rodar(None)
        self.assertIn('Falha ao chamar get-all-groups', saida)
        self.assertNotIn('Conversas de grupo no banco', saida)
