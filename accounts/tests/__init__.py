"""Testes do BEEonBOARD, organizados por ASSUNTO.

Era um unico `accounts/tests.py` de 6.701 linhas com 79 classes. Num projeto onde
varias sessoes de agente se alternam, achar o teste da tela em que se esta mexendo
custava rolar um arquivo de 6 mil linhas — e o arquivo inteiro entrava no contexto.

    base.py          imports comuns e o helper `default_company()`
    acesso.py        Entrar, sair, recuperar senha e a troca no primeiro acesso.
    permissoes.py    Permissoes de menu, perfil somente-leitura e alcance de
    conversas.py     Tela Conversas: lista, mensagens, janela de carregamento, grupos,
    atendimento.py   Atendimento automatico (IA e chatbot de menu), setores e
    wapi.py          Recebimento da W-API: parser, webhook e ingestao das mensagens.
    master.py        Gestor master: Clientes, Gestores, Metricas, exportacao e o
    push.py          Aviso de nova mensagem (Web Push): inscricao e destinatarios.
    infra.py         Configuracao, comandos de management, versionamento de estaticos,

O `manage.py test accounts` continua descobrindo tudo, e
`manage.py test accounts.tests.NomeDaClasse` continua funcionando porque este
`__init__` reexporta as classes.
"""

from .acesso import *  # noqa: F401,F403
from .permissoes import *  # noqa: F401,F403
from .conversas import *  # noqa: F401,F403
from .atendimento import *  # noqa: F401,F403
from .wapi import *  # noqa: F401,F403
from .master import *  # noqa: F401,F403
from .push import *  # noqa: F401,F403
from .infra import *  # noqa: F401,F403
