"""Views do BEEonBOARD, organizadas por ASSUNTO.

Era um unico `accounts/views.py` de 3.875 linhas com cinco responsabilidades
misturadas: autenticacao, conversas, telas do master, configuracoes e o webhook.
Num projeto onde varias sessoes de agente se alternam, um arquivo desse tamanho
custa contexto e esconde a estrutura.

    common.py         imports, guardas de acesso e helpers compartilhados
    auth.py           entrar, sair, recuperar senha, troca no primeiro acesso
    dashboard.py      indicadores da empresa
    conversations.py  Conversas: lista, mensagens, envio, midia, atendimento
    contacts.py       agenda de contatos
    settings.py       IA, chatbot/modo, WhatsApp, Permissoes, Atendentes, Setores
    company.py        aba Marca, Meus dados e exportacao (do proprio cliente)
    master.py         Clientes, Gestores e Metricas (do gestor master)
    push.py           aviso de nova mensagem (Web Push): service worker e inscricao
    webhook.py        porta de entrada das mensagens da W-API

Este `__init__` reexporta TODOS os nomes, entao `accounts/urls.py`, os testes e
qualquer `from .views import X` continuam funcionando exatamente como antes.
"""

from .common import *          # noqa: F401,F403
from .auth import *            # noqa: F401,F403
from .dashboard import *       # noqa: F401,F403
from .conversations import *   # noqa: F401,F403
from .contacts import *        # noqa: F401,F403
from .settings import *        # noqa: F401,F403
from .company import *         # noqa: F401,F403
from .master import *          # noqa: F401,F403
from .push import *            # noqa: F401,F403
from .webhook import *         # noqa: F401,F403

# `import *` ignora nomes que comecam com _, e o projeto tem varios helpers
# privados usados pelos testes (`_serialize_message`, `_build_name_map`,
# `_delete_company_media_files`, ...). Reexporta-los explicitamente mantem
# `accounts.views` com a MESMA superficie de antes da divisao.
from .common import (  # noqa: F401
    _current_attendant_name,
    _delete_company_media_files,
    _digits,
    _fmt_int,
    _format_conv_time,
    _remove_company_logo_file,
)
from .conversations import (  # noqa: F401
    _MENTION_RE,
    _build_name_map,
    _conversation_counts,
    _conversation_type_counts,
    _count_by_q,
    _filter_conversations_by_status,
    _filter_conversations_by_type,
    _host_reachable_by_wapi,
    _media_category_ok,
    _media_file_to_data_uri,
    _media_link_token,
    _resolve_mentions,
    _search_conversations,
    _serialize_contact_info,
    _serialize_conversation_item,
    _serialize_message,
    _serve_media_file,
)
from .settings import (  # noqa: F401
    _save_menu_options,
)
from .company import (  # noqa: F401
    _deny_master_export,
)
from .master import (  # noqa: F401
    _active_masters_besides,
    _format_recovery_phone,
)
from .dashboard import (  # noqa: F401
    _DASHBOARD_PALETTE,
    _format_hms,
)
