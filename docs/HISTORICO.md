# Histórico

Registro curto das alterações feitas no projeto.

## 2026-06-25
- Criação da estrutura inicial do projeto Django.
- Criação da tela inicial de login com layout base.
- Organização dos arquivos soltos em `assets/`.
- Geração da versão transparente do logo.
- Ajustes visuais na tela de login, incluindo slogan e link de recuperação.
- Criação do `README.md` e do `.gitignore`.
- Inicialização do repositório Git e push para o GitHub.
- Criação do histórico de alterações e do arquivo de padrões do projeto.
- Início da autenticação com usuário por e-mail e perfis de acesso.
- Criação da dashboard inicial com layout didático e botões por nível de usuário.
- Remoção do texto explicativo do cartão de perfil no dashboard.
- Remoção do bloco de perfil logado da barra lateral no dashboard.
- Etapa 1 concluída: criada documentação inicial do projeto.
- Etapa extra concluída: criada documentação de regras Git em `docs/GIT.md`.
- Etapa documental concluída: removidos arquivos de plano/contexto duplicados e mantido `docs/HISTORICO.md` como registro oficial do que já foi feito e decidido.
- Configuracao inicial da W-API adicionada para ADM, com armazenamento de instance ID/token e base tecnica de envio de texto sem expor token na interface.
- Rota oficial de envio de texto da W-API confirmada e aplicada no cliente para instancias Lite usando `POST /send-text` com `instanceId` em query string.
- URL oficial de envio da W-API ajustada para `POST /v1/message/send-text` com `instanceId` em query string e retorno estruturado com `messageId` e `insertedId`.
- Tela de configuracao da W-API reorganizada em um unico card com duas colunas responsivas para configuracao e teste de envio.
- Ajuste visual dos alertas da tela W-API para mensagens mais compactas e simples para o usuario final.
- Notificacoes do sistema convertidas para toast compacto reaproveitavel no template base.
- Reduzido o espacamento vertical entre o titulo e o card principal da tela W-API.
- Ajustado o gap da pagina W-API para aproximar o card principal do cabecalho.
- Tela W-API passou a usar CSS especifico em `static/css/wapi_settings.css`.
- Documentados os padroes de interface, CSS por pagina e notificacoes toast no projeto.
- Ajustado o topo da tela W-API para reduzir espacos vazios acima do titulo e antes do card.
- Tela W-API passou a compactar o proprio container `dashboard-main` com classe especifica.
- Compactacao adicional do container W-API com reducao do padding superior e do espaco entre titulo e card.
- Corrigido o esticamento vertical do grid da tela W-API para manter titulo e card agrupados no topo.
