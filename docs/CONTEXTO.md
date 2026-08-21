# Contexto do Projeto BEEonBOARD (handoff)

Documento único para retomar o projeto do zero (ex.: nova sessão do Claude/Codex).
Leia também: `CODEX_PADROES.md`, `GIT.md`, `HISTORICO.md`, `DEPLOY.md`,
`WAPI_MEDIA_INTEGRATION.md`.

---

## 1. Visão geral

- **BEEonBOARD**: sistema Django de atendimento/automação de WhatsApp via **W-API**.
- **Marca**: o sistema se chama **BEEonBOARD** (*conecta • organiza • potencializa*);
  antes chamava-se BEEZap. O logo é `static/images/logo-beeonboard.png` (PNG com fundo
  transparente, aparece na barra lateral, no login e na troca de senha inicial) e o
  nome vem de **`PLATFORM_NAME` em `accounts/context_processors.py`** — trocar o nome
  exibido é mudar essa constante e os `{% block title %}` dos templates. Os arquivos
  originais da marca (e o logo antigo) ficam em `assets/branding/`.
  O **endereço** acompanhou a marca: o prefixo de URL passou de `/beezap/` para
  **`/beeonboard/`** (o antigo redireciona 301, para bookmark não morrer). **Mas os
  identificadores TÉCNICOS continuam `beezap` de propósito** — renomeá-los não é
  necessário para trocar o endereço e só arriscaria o ambiente no ar: o serviço
  systemd **`beezap`**, a pasta **`/var/www/beezap`**, os loggers `beezap.*`, o check
  `beezap.W001`, o header `X-BEEZAP-WEBHOOK-TOKEN`, as chaves de `localStorage`
  (`beezap_sound`, que zerariam a preferência de som de quem já usa) e o repositório
  no GitHub. **Atenção ao webhook:** POST não segue redirect, então a URL de webhook
  de cada cliente tem que ser **re-cadastrada no painel da W-API** com o prefixo novo
  — enquanto não for, mensagem recebida não chega. As rotas `beezap/webhook/wapi/…`
  seguem registradas no Django justamente como rede de segurança até isso acontecer.
- **MULTIEMPRESA (SaaS)**: a mesma instalação atende **várias empresas clientes**;
  cada uma tem os seus setores, atendentes, contatos, conversas e as suas próprias
  credenciais de W-API/GPT. Existe um **gestor master** que cadastra e administra os
  clientes na tela **Clientes**. **Ver a seção 16** — ela é a referência do assunto.
- **Stack**: Django 5.2, Python 3.12, gunicorn, Nginx, SQLite (padrão) ou
  PostgreSQL (via `DATABASE_URL`).
- **Hospedagem**: VPS Linux, servido sob o prefixo de caminho **`/beeonboard/`**
  em `https://fabianopolone.com.br/beeonboard/`.
- **Idioma/UX**: interface em português, simples e didática; notificações via
  **toast** e **pop-up do desktop + som** nas Conversas; CSS por página; sem cursor
  piscando em elementos não editáveis.

## 2. Estrutura do código

```
config/            settings.py (env-driven), urls.py, wsgi.py
accounts/          app principal: models, views, urls, forms, admin, middleware,
                   backends, permissions.py, tenancy.py (multiempresa),
                   export.py (ZIP de portabilidade do cliente),
                   context_processors.py (marca do cliente),
                   management/commands/, templates de accounts
wapi/              MÓDULO (não é app instalado): client.py, parser.py, services.py, formatting.py
gpt/               MÓDULO (não é app): client.py, attendant.py (atendente virtual IA)
chatbot/           MÓDULO (não é app): handler.py (chatbot de menu, sem IA)
static/css/        CSS por página (dashboard.css, conversations.css, clients.css, ...)
static/js/         conversations.js (o comportamento da tela Conversas)
templates/         base.html + accounts/*.html
                   (accounts/_sidebar.html = barra lateral única, incluída por todas)
docs/              documentação (este arquivo, DEPLOY.md, etc.)
deploy/            deploy.sh, diag_static.sh, patch_nginx_beezap.sh, exemplos nginx/systemd
```

> `wapi/` é um módulo Python comum (importa `accounts.models`); **não** está em
> `INSTALLED_APPS`, por isso os models ficam em `accounts/models.py`.

## 3. Modelos (`accounts/models.py`) — migração atual: `0038`

> **Índices (migração `0036`)**: até ela, o único `db_index` do projeto era
> `Conversation.external_id`. As FKs ganham índice sozinhas, mas as consultas reais
> são **compostas** — `Conversation(company, status)`, `(company, last_message_at)`,
> `(company, chat_type)`, `(company, created_at)`; `Message(conversation, created_at)`,
> `(conversation, message_type, created_at)`, `(created_at)`, `(external_message_id)`;
> `WapiWebhookEvent(company, received_at)`, `(instance_id)`; `Contact(company, name)`.
> Ao criar consulta nova de tela ou métrica, conferir se ela cai num desses.


> **MULTIEMPRESA:** quase todo model abaixo tem um campo **`company`** (a empresa
> cliente dona do registro) e as unicidades passaram a ser **por empresa**. Os
> detalhes estão na **seção 16** — leia-a junto com esta.

- **Company**: a **EMPRESA CLIENTE** (uma "instância" do sistema): dados cadastrais,
  CNPJ, logo, cor de destaque e `is_active`. `get_default()` devolve a **empresa
  padrão**, que não pode ser excluída nem desativada. Ver seção 16.
- **User** (AbstractUser, login por e-mail; `role`:
  `master`/`adm`/`usuario`/`leitor`). **`company`** = empresa da pessoa; **nulo =
  gestor master** (dono da plataforma, fica acima das empresas e não lê conversas).
  Dois campos existem **para quem não tem `Attendant`** (na prática, o master):
  **`recovery_phone`** (WhatsApp da recuperação de senha) e
  **`must_change_password`** (troca obrigatória no primeiro acesso; o
  `InitialPasswordChangeMiddleware` olha **os dois lugares**, aqui e no atendente).
- **Attendant** (perfil de atendente, vínculo com User, troca de senha inicial).
  **Admin como atendente:** todo usuário `adm` ganha **automaticamente** um
  `Attendant` (via sinal em `accounts/signals.py` + backfill na migração `0025`) e é
  incluído em **todos os setores**, para poder **Assumir** atendimentos de qualquer
  fila sem criar/logar outra conta. Mantido em sincronia: ao salvar um usuário adm e
  ao criar/salvar um setor; a organização por arrastar-e-soltar dos setores re-inclui
  os admins. `conversation_take_view` também provisiona na hora (rede de segurança) e
  a edição de atendente **não rebaixa** um adm.
- **RoleMenuPermission** / **UserMenuPermission**: permissões de menu (quais botões
  cada perfil vê/acessa, e personalização por usuário). Ver seção 15. *(O antigo
  campo `full_history` saiu na migração `0038`: o controle de "ver conversa inteira"
  migrou para `Sector.view_full_history` / `UserConversationView.view_full_history`.)*
- **GroupAccess**: quem pode ver um grupo do WhatsApp (M2M com setores e usuários).
  Sem regra, o grupo só aparece para o admin. Ver seção 15.
- **Sector** (setores; M2M com Attendant; usado em transferência/roteamento manual).
  Na tela Setores, um atendente pode ficar em **vários setores** (fica sempre na
  coluna "disponíveis"; arrastar/"+ Adicionar" inclui, ✕ remove; cada card mostra
  "em N setores"). O selo **Admin/Administrador** identifica o admin em Setores e
  Atendentes. **Setor "Geral" PADRÃO** (`Sector.GENERAL_SECTOR_NAME='Geral'`,
  `Sector.ensure_general()`, prop `is_general`): **sempre existe** (criado na migração
  `0028`), **não pode ser excluído nem renomeado** (bloqueado no backend + sem botão de
  excluir e com selo "padrão" na tela + nome travado na edição), e **todos os
  atendentes fazem parte dele por padrão** (backfill na `0028` + sinal em
  `signals.py` que adiciona todo atendente novo ao criar). É o destino garantido do
  handoff da IA/chatbot (seções 13/14). Roteamento por atendente citado prefere um
  setor **específico** (não o Geral) quando o atendente tem outro. Campos de
  **visualização** (aba Visualização de conversas, seção 15): `view_scope`
  (`ConversationViewScope`: `own`/`sector_open`/`sector_all`/`all`, padrão
  `sector_open`) e `view_full_history` (bool) — padrão do setor para o alcance de
  conversas e "ver conversa inteira".
- **UserConversationView** (OneToOne com User): **exceção por usuário** da
  visualização de conversas, sobrepõe o setor. `view_scope` (nulo = herdar) e
  `view_full_history` (nulo = herdar). Ver seção 15.
- **PasswordResetCode** (recuperação de senha por código no WhatsApp). O telefone
  vem do `Attendant` de quem pede; o **gestor master** não tem atendente, então usa
  `User.recovery_phone` (tela Gestores) e o código sai pela instância da **empresa
  padrão** — ver `create_and_send_password_recovery_code` e a seção 16.
- **WapiConfiguration** (**uma por empresa**, `for_company(company)` — **sem empresa
  devolve instância vazia e não salva**, em vez de estourar `IntegrityError`: o campo
  é obrigatório, e quem pergunta "tem credencial?" deve receber "não", não um erro de
  banco. Idem `MenuBotConfiguration.for_company`, onde config vazia = modo `off`): `instance_id`,
  `token`, `webhook_token`. Credenciais reais ficam **aqui (no banco)**, editadas na
  tela Configurações → WhatsApp/W-API. `resolved_*()` cai para as variáveis de
  ambiente **apenas na empresa padrão** (`usa_credencial_do_ambiente`) — ver a nota
  na seção 16.
  `get_solo()` sobrevive só como compatibilidade (empresa padrão). Ver seção 16.
- **WapiWebhookEvent**: todo evento recebido do webhook (com `raw_payload`). É a
  tabela que mais cresce do sistema — a **retenção** fica no comando
  `prune_wapi_events` (seção 9). *(Os campos `processed`/`processing_error` saíram na
  migração `0038`: nunca foram escritos, e por isso o `status_label` respondia sempre
  "Recebido".)*
- **OpenAiConfiguration** (**UMA para toda a PLATAFORMA**, `get_solo()` — **não é
  por empresa**): `api_key`, `model` (padrão `gpt-4.1-nano`). Guarda a
  **API Key do GPT no banco** (editada na tela **Inteligência (IA)**, exclusiva do
  gestor master; nunca no código e não reexibida após salva).
  `resolved_api_key()`/`resolved_model()` caem para env
  (`OPENAI_API_KEY`/`OPENAI_MODEL`) se vazios. **Atendente virtual**:
  `instructions` (prompt/persona editável) e `max_turns` (limite de respostas,
  padrão 3). **Não tem `fallback_sector`**: setor pertence a uma empresa, então o
  destino do encaminhamento é o `fallback_sector` **do chatbot daquela empresa** (e,
  na falta dele, o setor Geral dela). **Contador de tokens**: `total_requests`, `total_prompt_tokens`,
  `total_completion_tokens`, `total_tokens`, `usage_since`, `last_used_at` —
  somados de forma atômica por `record_usage()` a cada chamada; `reset_usage()`
  zera. Ver seções 13 e 16. **Não existe mais um campo `enabled`**: a ativação da IA
  vem do **modo mestre** `MenuBotConfiguration.mode == 'ai'` de **cada empresa** (ver
  seção 14), que é a fonte única da verdade. O `enabled` sobrevivia só sendo
  **escrito** — nenhum código o lia — e uma flag de plataforma não poderia mesmo
  decidir por cada cliente.
- **CompanyAiUsage** (**uma linha por empresa e por MÊS**): consumo de IA daquela
  empresa — `total_requests`, `total_prompt_tokens`, `total_completion_tokens`,
  `total_tokens`, `first_used_at`, `last_used_at`, único por
  (`company`, `year`, `month`). Existe porque a **API Key do GPT é uma só, da
  plataforma**: o contador de `OpenAiConfiguration` diz *quanto* foi gasto, mas não
  **quem** gastou. `record(company, prompt, completion, total)` soma uma chamada no
  mês atual (atômico com `F()`, `get_or_create` para a linha do mês; **sem empresa
  não grava nada**); `month_totals(company[, ano, mês])` devolve o mês **sempre
  zerado quando não houve uso** (a tela não trata ausência), `previous_reference()`
  dá o mês anterior (virando o ano) e `all_time_totals(company)` soma todos os meses.
  Uma linha por mês (não uma por chamada) mantém o histórico sem a tabela crescer
  com o volume de mensagens, e o "mês atual" reinicia sozinho na virada. **É medição:
  não existe limite nem bloqueio por empresa.** Ver seções 5.0.2 e 13.
- **MenuBotConfiguration** (**uma por empresa**, `for_company(company)`): config do **chatbot de menu**
  (atendimento automático **sem IA**) **e** o **MODO MESTRE** de primeiro atendimento
  `mode` (`off`/`menu`/`ai`) — fonte única da verdade de qual motor atua. Campos de
  texto editáveis (`greeting`, `menu_intro`, `confirmation_message`,
  `invalid_message`, `handoff_message` — **todos** aceitam os placeholders
  `{saudacao}`, `{empresa}` e `{setor}`, resolvidos por `render_placeholders`),
  `max_attempts` (tentativas inválidas antes do handoff) e `fallback_sector`.
  Ver seção 14.
- **MenuOption**: uma opção do menu (`config` FK, `order` = número que o cliente
  digita, `label`, `sector` FK). `key` = `order`.
- **Contact**: `name`, `phone` (único, guardado **só em dígitos**), `display_name`,
  `initials`. É a base da tela **Contatos** e da resolução de nomes: criado
  **automaticamente** na 1ª mensagem de uma conversa **direta**, mas **SEM nome**
  (`name=''`) — o nome **nunca** vem do WhatsApp (pushName). Enquanto ninguém
  cadastrar, `display_name` cai para o **número**, então Conversas mostra o número; o
  nome só aparece quando alguém **clica no número e cadastra** (endpoint
  `conversation-name-contact`, que grava o Contact e por isso já aparece em
  **Contatos**) ou cadastra manualmente na tela Contatos. **Nome cadastrado a mão
  nunca é sobrescrito** por nada automático. O `phone` (dígitos) é a chave usada para
  trocar número→nome nas mensagens de grupo (remetente e menções `@`).
  **Conversa `@lid` também tem contato:** a W-API Lite entrega a conversa direta
  chaveada por um id interno (`@lid`), mas manda o **telefone real** no remetente
  (`sender.id`); `attach_contact_from_sender` resolve o Contact por esse telefone (ver
  seção 4), então essas conversas também aparecem pelo **número** e a pessoa fica
  **unificada** com os grupos e a tela Contatos (mesmo telefone = mesmo Contato:
  cadastrar o nome uma vez vale em todo lugar). Contatos criados **antes** desta regra
  continuam com o nome antigo (`cleanup_pushname_contacts`) e conversas `@lid` antigas
  ficaram sem contato (`link_lid_contacts`) — ver seção 9.
- **Conversation**: **um único chat por pessoa/grupo** (padrão WhatsApp — não dá mais
  fork por atendimento). `contact` (**opcional** — grupo não tem contato individual),
  `chat_type` (`private`/`group`), `external_id` (JID do grupo `@g.us`, telefone
  ou LID da direta), `name` (título/nome do grupo), `status`
  (`open`/`pending`/`closed`), `assigned_attendant`, `sector`,
  `last_message_text`, `last_message_at`, `unread_count`, `ai_turns` (respostas
  da IA no atendimento atual; zera ao transferir/encerrar/reabrir),
  `auto_reply_lock_at` (**trava do atendimento automático** — "estou processando
  desde"; fica no banco porque precisa valer entre os workers do gunicorn, ver
  `wapi/autoreply_lock.py` e a seção 14). Propriedades:
  `is_group`, `display_title`, `display_initials`, `recipient` (destino de envio).
- **Message**: `conversation`, `sector` (FK, **setor da conversa NO MOMENTO** em que a
  mensagem foi criada — carimbado em todos os pontos de criação; nulo enquanto sem setor,
  ex.: triagem da IA; usado para separar os atendimentos por setor na aba "Conversa do
  setor"), `direction` (`in`/`out`), `message_type`
  (`text/image/audio/video/document/sticker/gif/reaction/location/contact/unknown/system`;
  `system` = **divisória** de atendimento no meio do chat),
  `text`, `sender_name`, `sender_id`/`participant_id` (quem enviou; em grupo é o
  participante; **não existe mais um campo `phone`** — era gravado em todos os pontos
  de criação e nunca lido, numa tabela que cresce com o volume de mensagens), `is_group`, `from_me`, `is_ai` (marca falas do atendente
  virtual, para detectar quando um humano assume), `external_message_id` (id real da W-API,
  serve de `wapi_message_id`), `media_file`, `media_url`, `media_mimetype`,
  `media_status` (`none/pending/ok/unavailable`), `raw_payload`.

## 4. Integração W-API

### Cliente centralizado (`wapi/client.py`)
- Base: `https://api.w-api.app` + `/v1/message/<ação>?instanceId=<id>`,
  header `Authorization: Bearer <token>`. Tudo passa por `_wapi_post()`
  (credenciais, erros amigáveis, **log seguro sem token** no logger
  `beezap.wapi.send`). Sucesso é 2xx **e** sem `error` no corpo.
- Funções: `send_text_message`, `send_image_message`, `send_audio_message`,
  `send_video_message`, `send_document_message` (exige `extension`, ex.: `pdf`),
  `download_media`.

### Plano LITE vs PRO (instância atual é **LITE**)
- **Envio LITE (implementado):** texto (com emoji), imagem, áudio, vídeo, documento.
- **Recebimento (todos):** texto, imagem, áudio, vídeo, documento, **sticker, gif,
  reação** — recebidos, baixados e exibidos.
- **PRO (envio NÃO implementado / bloqueado):** enviar reação, sticker, GIF nativo,
  botões, listas, enquetes.

### Parser (`wapi/parser.py`)
- `parse_wapi_webhook_payload(payload)` → campos do `WapiWebhookEvent`
  (event_type, phone, contact_name, message_id, message_text, from_me, ...).
- `parse_wapi_media(payload)` → `message_type` normalizado + metadados de mídia
  (`media_key`, `direct_path`, `media_mimetype`, `media_url`, `caption`, `reaction`).
- `parse_wapi_media` também expõe `filename` (nome real do documento, separado da
  legenda) — usado para baixar com o nome/extensão corretos.
- `normalize_phone(value)` → só dígitos; remove `@s.whatsapp.net`/`@c.us`/`:device`;
  **rejeita `@g.us`/`@lid`/`@newsletter`/`@broadcast`** e números com **> 15 dígitos**
  (E.164 máx.; IDs internos "120363…" não são telefone).
- `is_group_jid(value)` → **coletivo/não-pessoal**: `@g.us` (grupo), `@newsletter`
  (canal), `@broadcast` (transmissão) ou número "pelado" longo demais para telefone.
- `is_ignorable_jid(value)` → conversas que **não são atendimento** e são ignoradas:
  o id literal `status`, `@newsletter` e `@broadcast`.
- `is_status_or_broadcast(payload)` → detecta **Status/stories** do WhatsApp mesmo
  quando o W-API Lite manda `chat.id == "status"` (sem `@broadcast`), ou pelo
  marcador `posterStatusID` (id do post de status), ou `status@broadcast` em
  qualquer campo. **Não usa `statusSourceType`**: o WhatsApp coloca esse campo em
  foto/vídeo/GIF **comuns** (`"IMAGE"`/`"VIDEO"`/`"GIF"`) só indicando que a mídia
  pode ser repostada como status — usá-lo fazia lotes de fotos/vídeos/gifs sumirem
  do chat. Status **não** vira conversa.
- `normalize_wapi_message_context(payload)` → **função central de GRUPO vs DIRETA**.
  Usa `is_group_jid` para decidir: JID coletivo ⇒ **grupo** (chat_id = JID, remetente
  separado em `sender_id`/`participant_id`); número puro / `@s.whatsapp.net` / `@lid`
  ⇒ **direta**. Retorna `chat_id`, `chat_type`, `is_group`, `sender_id`,
  `participant_id`, `sender_phone`, `connected_phone`, `sender_name`, `from_me`,
  `display_name`, `source`. O JID de grupo tem **prioridade** sobre telefone/remetente
  em qualquer campo. `_valid_name` exige ≥1 caractere alfanumérico (rejeita nomes só de
  pontuação, ex.: ".").
  - **`sender_phone`** = telefone REAL de quem enviou (`normalize_phone` do
    participante/remetente; **vazio** quando o remetente é só um id interno). É o que
    permite achar o número de uma conversa `@lid`.
  - **`connected_phone`** = o NOSSO número (`connectedPhone`), usado como **guarda**:
    nunca pode virar o contato da conversa.
- `normalize_recipient(value)` → destino de **envio**: mantém `@g.us`/`@lid`
  intactos (a W-API precisa do JID); telefone comum vira só dígitos.
- **Formato real do payload (W-API Lite):** o número do remetente vem em
  `sender.id`; o nome em `sender.pushName`; o conteúdo em `msgContent`
  (`conversation` / `extendedTextMessage.text` / `imageMessage` / `audioMessage` /
  `videoMessage` (+`gifPlayback`→gif) / `stickerMessage` / `documentMessage` /
  `reactionMessage`). Menções vêm como `@<número/LID>` no texto. **`connectedPhone`
  é o NOSSO número — nunca usar como remetente.** **Status:** `chat.id == "status"`
  com o autor em `sender` e `statusSourceType` no `contextInfo`.

### Serviços (`wapi/services.py`)
- `ingest_wapi_payload(payload)` é o **ponto único** de entrada de mensagem recebida
  (usado pelo webhook e pelo comando `sync_wapi_events_to_conversations`): normaliza
  o contexto, **resolve o conteúdo (tipo/texto/mídia) e só então** resolve a conversa e
  cria a mensagem; deduplica pelo id externo.
  **Ignora (não cria NADA — nem conversa):** canal/transmissão (`is_ignorable_jid`),
  Status (`is_status_or_broadcast`), mensagens de **sistema/tipo `unknown`**
  (`senderKeyDistributionMessage`/`protocolMessage`/`action`+`participants` em grupos,
  `templateMessage` de empresa/propaganda) e **texto vazio**.
  > **Ordem importa (bug já corrigido):** a conversa era resolvida/criada **antes**
  > desses descartes, então todo payload descartado deixava uma **conversa vazia** (sem
  > nenhuma mensagem, aparecendo na lista com o JID/`@lid` cru no título) e, em grupo
  > novo, ainda gastava uma chamada à W-API em `resolve_group_name`. Hoje a conversa só
  > é criada quando há conteúdo de verdade — e uma conversa **encerrada não reabre**
  > (`_reopen_for_new_service`) por causa de um evento de sistema.
- `resolve_conversation_for_context(ctx)` acha/cria a conversa certa: **grupo** →
  keyed pelo JID (`external_id`, `chat_type='group'`, sem contato); **direta com
  telefone** → contato + conversa aberta (comportamento antigo); **direta com id
  interno (`@lid`)** → keyed pelo próprio chat_id **e com o contato resolvido pelo
  telefone real** (`attach_contact_from_sender`). **Nunca cria contato privado para
  quem escreve no grupo.**
- `attach_contact_from_sender(conversation, ctx)` — **o caso normal da W-API Lite**: o
  chat da conversa direta vem como id interno (`53094503153686@lid`), mas o **telefone
  de verdade** vem no remetente (`sender.id`, ex.: `5519971548270`). A função anexa o
  `Contact` desse telefone à conversa, então ela aparece pelo **número** (clicável para
  cadastrar) em vez do pushName, e a pessoa fica **unificada** com grupos/Contatos. A
  conversa **continua chaveada pelo `@lid`** (`external_id`), que é o destino que a
  W-API exige no envio — nada é dividido nem unido. **Guardas:** não faz nada se a
  conversa já tem contato, se é grupo, se a mensagem é **nossa** (`from_me` — aí
  `sender.id` é o número da instância), se o remetente não é telefone válido, ou se o
  número é o `connected_phone`. Chamada em toda mensagem recebida, então conversas
  antigas sem contato **se resolvem sozinhas** na próxima mensagem (para as paradas,
  ver `link_lid_contacts` na seção 9).
- `save_incoming_message(conversation, ctx, ...)` cria a mensagem por tipo;
  para mídia, chama `download-media` e **salva o arquivo localmente** em
  `MEDIA/whatsapp/` (o `fileLink` da W-API expira). Estados `pending/ok/unavailable`.
  > **O download roda em BACKGROUND** (`download_incoming_media_async`, padrão
  > `download_async=True`). **Não voltar a fazê-lo dentro da requisição:**
  > `_download_to_media_file` faz `urlopen(timeout=60)` com **duas** tentativas, e
  > antes dele ainda há a chamada `download-media` à W-API — uma única foto de link
  > lento prendia um worker por mais de dois minutos. Com `--workers 2 --timeout 60`,
  > duas mídias lentas ao mesmo tempo travavam **o sistema inteiro** (todas as
  > empresas, todas as telas) e o gunicorn matava o worker no meio do download. A
  > mensagem já nasce `media_status='pending'`, o resumo da conversa é atualizado
  > **antes** de sair (a lista mostra "📷 Imagem" na hora) e o front já faz a mídia
  > aparecer sozinha no poll seguinte. Quem quer o arquivo pronto ao retornar passa
  > `download_async=False`: é o caso do comando
  > `sync_wapi_events_to_conversations`, onde bloquear é o certo (o processo tem que
  > terminar com o trabalho feito, não com threads que morrem ao sair). Testes em
  > `WebhookDoesNotWaitForMediaDownloadTests`.
  A extensão do arquivo salvo vem de `_ext_for_media` (nome original do documento →
  mapa de mimetype → `mimetypes` do Python → `bin`), evitando baixar como `.bin`.
- `document_filename(message)` → nome original do documento (do `raw_payload`),
  usado no download e na serialização.
- `download_incoming_media_async(message, media)` → baixa em **background** (thread
  daemon + lock por mensagem + `connection.close()`) a mídia que acabou de chegar. É
  o caminho normal do webhook.
- `retry_conversation_media_async(conversation_id)` → tenta rebaixar em **background**
  (thread) as mídias que falharam na chegada; disparado pelo botão **Atualizar**.
- `save_outgoing_media_message(...)` salva arquivo enviado em
  `MEDIA/whatsapp/outgoing/` (nome único uuid). Para **documento**, guarda o nome
  ORIGINAL em `raw_payload={'beezap_filename': ...}` — assim o chat mostra/baixa com
  o nome real (`document_filename()` lê isso; documento recebido lê do payload do webhook).
- Envio de mídia (`conversation_send_media_view`): a W-API baixa a mídia pela URL
  pública. Se o host for público (produção) usa a URL; se for **localhost/IP
  privado/.local** (ambiente local, onde a W-API na nuvem não alcança a URL) envia
  a mídia em **base64** (`_media_file_to_data_uri`) — decisão via
  `_host_reachable_by_wapi`. Sem isso, o envio local de imagem/áudio/vídeo/documento
  falhava com "verifique a conexão do WhatsApp".
- `convert_audio_to_ogg(uploaded)` converte áudio (webm/opus do Chrome) → **ogg**
  via **ffmpeg** (a W-API só aceita `.mp3`/`.ogg`).
- `ensure_wapi_image(uploaded, mimetype)` garante que a imagem enviada resulte numa
  URL terminada em `.png`/`.jpeg`/`.jpg` (a W-API **recusa** o resto com HTTP 500
  "A URL da imagem deve ser nos formatos ..."). PNG/JPEG → só normaliza a extensão
  do nome; webp/gif/bmp/heic/... → converte para **JPEG** via `_convert_image_to_jpeg`
  (ffmpeg). Chamada em `conversation_send_media_view` antes de salvar.
- Rótulos de "última mensagem": 📷 Imagem, 🎧 Áudio, 🎥 Vídeo, 🎞️ GIF, 💟 Figurinha,
  👍 Reação, 📄 Documento.

### Arquivos de mídia: acesso controlado (NÃO servir pelo Nginx)

Foto, áudio, vídeo e documento das conversas são **conteúdo do cliente**. Eles ficam
em `media/whatsapp/`, mas **essa pasta não é publicada pelo Nginx** — o arquivo só
sai por duas rotas do Django:

| Rota | Quem passa | Para quê |
|---|---|---|
| `midia/<id>/` (`message-media`) | **login** + `can_see_conversation` | é o que o chat usa |
| `midia-publica/<token>/` (`media-public`) | qualquer um **com o token assinado**, por 15 min | só para a **W-API** baixar a mídia que enviamos |

- `Message.resolved_media_url` devolve a rota **autenticada** (não mais
  `media_file.url`), então o serializer do chat nunca entrega o caminho cru.
- O **gestor master é barrado** aqui como qualquer um de fora, porque
  `can_see_conversation` é `False` para ele — inclusive no modo suporte.
- O link público é gerado por `_media_link_token(message)`
  (`django.core.signing`, salt `beezap.midia.publica`, `MEDIA_LINK_MAX_AGE` = 15 min)
  e usado em `conversation_send_media_view`. Ele existe porque a W-API roda na
  **nuvem** e baixa a mídia pela URL; o envio local continua caindo em **base64**
  (`_host_reachable_by_wapi`).
- Os arquivos recebidos passaram a ser salvos com **nome aleatório (uuid)**. Antes
  eram `wapi_<id_da_mensagem>.<ext>` — **sequencial**, então quem descobrisse um
  caminho descobria todos. Arquivos antigos continuam no disco com o nome velho; o
  acesso a eles agora também passa pela view, então não há o que migrar.
- `media/empresas/` (logos das empresas) **continua público**: aparece na barra
  lateral e não tem nada de conversa.

> **No deploy isso exige mudar o Nginx** (trocar `location /beeonboard/media/` por
> `location /beeonboard/media/empresas/`). Ver `DEPLOY.md` — sem isso, a mídia continua
> aberta pelo caminho antigo mesmo com o código novo.

### Webhook (POR CLIENTE — ver seção 16)
- View `wapi_webhook_view` (`@csrf_exempt`). Rotas: **`/webhook/wapi/<empresa>/`**
  (recomendada) e `/webhook/wapi/` (antiga, mantida) — cada uma também sob
  `/beeonboard/`. A **empresa** é resolvida por `resolve_webhook_company`: identificador
  da URL → `instanceId` do payload → empresa padrão. **Empresa inativa ou
  identificador desconhecido devolve 404 e nada é criado.**
- Aceita a chamada externa **sem token quando nenhum webhook_token está configurado**
  naquela empresa (senão exige `?token=`/header) — o token é **por empresa** e a
  validação acontece **depois** de identificar o cliente. A URL exibida na tela é a
  **do cliente** (`build_wapi_webhook_url(request, company)`).
- O cliente centralizado (`wapi/client.py`) exige **`company`** em toda função (ver
  seção 16): sem empresa não há envio, para nunca sair pela instância errada.
- **Painel "Últimas mensagens que chegaram"** (tela WhatsApp, só master): mostra
  **horário, direção (enviada/recebida) e tipo do conteúdo** — e nada mais.
  `serialize_wapi_event` **não** expõe `message_text`, `phone` nem `contact_name`: a
  tela é do gestor master, que administra sem ler o atendimento (seção 16). O que ele
  precisa dali é só confirmar que o canal está recebendo. O endpoint do poll
  (`wapi-webhook-events`) usa `require_master_in_company_json`, a **mesma** guarda da
  tela — antes exigia `role == 'adm'` e por isso devolvia 403 justamente para a única
  pessoa que abre a tela, e o JavaScript engolia o erro a cada 5 segundos.

## 5. Tela Conversas (`templates/accounts/conversations.html` + `conversations.css`)

- **Abas de tipo**: Todas / Diretas / Grupos (param `tipo` no endpoint da lista),
  com contagens. **Selo "Grupo"** na lista e no cabeçalho; em grupo, o **nome do
  participante** aparece acima de cada mensagem recebida.
- **Carrega em JANELA, não a base inteira** (`CONVERSATION_PAGE_SIZE` e
  `MESSAGE_PAGE_SIZE`, ambos 60). A lista abre com a primeira página e ganha
  **"Carregar mais conversas"** no fim; o chat abre com as **últimas 60** mensagens e
  ganha **"Carregar mensagens anteriores"** no topo. O `?limite=` tem teto
  (`MAX_PAGE_SIZE = 500`), para ninguém pedir a base por URL.
  > **Por que:** antes `conversations_view` serializava **todas** as conversas
  > visíveis dentro do HTML, `conversation_list_view` repetia a lista completa a cada
  > **12 s** e `conversation_messages_view` fazia `list()` da conversa **inteira** a
  > cada **6 s** — por aba aberta. Um grupo com anos de histórico era lido por
  > completo dez vezes por minuto. O poll pede a **mesma janela** que está na tela,
  > então o custo passou a ser o do que a pessoa está olhando.
  > **A janela nunca corta um atendimento:** ela é estendida para trás até a divisória
  > mais próxima. Sem isso, as abas "Conversa privada"/"Conversa do setor"
  > classificariam errado um segmento partido — elas dependem de ver o segmento
  > completo para saber de quem ele é e em que setor terminou (teste
  > `test_janela_nao_corta_um_atendimento_no_meio`).
  > Os **contadores dos chips continuam mostrando o total real**, não o da janela.
- **Contadores numa consulta só**: os 8 números dos chips (5 por status + 3 por tipo)
  saem de **duas** agregações (`_count_by_q` com `Count('id', filter=Q(...),
  distinct=True)`), não de 8 `.count()` — antes cada um refazia o join de
  visibilidade, a cada poll de 12 s por aba aberta. **O `distinct=True` é
  obrigatório**: um grupo liberado por setor **e** por usuário duplica linha no join e
  o número sairia inflado (há teste exatamente para esse caso). As condições ficam em
  `CONVERSATION_COUNT_Q` / `CONVERSATION_TYPE_COUNT_Q`, as **mesmas** usadas para
  filtrar a listagem — contador e lista não podem divergir.
- **Lista real** (server-rendered) + **filtros** (chips) com contagens reais: Todas,
  Não lidas (`unread_count>0`), **Conversando** (tem atendente e não fechada — status
  `open` assumido), Finalizadas (`closed`). **Busca** por nome/telefone/última mensagem,
  combinada com o filtro e a aba de tipo.
- **Aguardando (fila do setor)**: NÃO é um chip — é um **badge amarelo pulsante**
  (`.conv-waiting-badge`, `data-waiting-badge`) ao lado dos botões do topo
  (Som/Notificações/Atualizar), mostrando a **contagem** de conversas aguardando
  (`counts['aguardando']`, já escopado pelo setor do usuário via visibilidade).
  Fica pulsando (some quando zero) para todos os atendentes do setor; **clicar** filtra
  a lista só nos aguardando; clicar de novo volta para Todas.
- **"Em conversa comigo"**: conversas atribuídas ao usuário logado **E ainda ativas**
  (não fechadas) ganham destaque na lista (`.conv-item-mine`, borda azul + fundo; label
  "Em conversa com você") — flag `mine` no serializer. **Finalizado NÃO fica azul**
  (label "Finalizado"). As de outros mostram "Com &lt;atendente&gt;". Regra: **uma
  conversa = um atendente** (para trocar, transfere de setor/atendente).
- **Finalizados são só os MEUS**: um chat fechado só aparece para o atendente que o
  atendeu (por atribuição), não para o setor inteiro — ver a regra de visibilidade na
  seção 15. (O admin vê todos.)
- **Botões do painel** (`updateServiceButtons`, lê `contact.status` + `contact.mine`;
  chamado ao abrir E após assumir/encerrar/transferir, então o painel **atualiza na
  hora**): **Finalizado** (`closed`) → só leitura (esconde Assumir, Encerrar e transferir);
  **já é minha** (sou o atendente) → esconde **Assumir** (mostra só **Encerrar**);
  aguardando / de outro → mostra **Assumir** + **Encerrar**.
- **Chat via AJAX**: abrir zera não lidas; render por tipo; **composer fixo no
  rodapé** (corrigido com `min-height:0` na cadeia flex/grid e `[hidden]{display:none!important}`).
  Cada mensagem mostra **data e hora** discretas no rodapé do balão (`.conv-msg-time`,
  ex.: "14/07/2026 · 18:37 ✓"); o serializer (`_serialize_message`) expõe `date`
  (`%d/%m/%Y`) e `time` (`%H:%M`).
- **Barra de filtro do chat** (`.conv-filter-bar`, só em conversa **direta**): ao abrir
  um contato, filtra os **atendimentos** (segmentos entre as divisórias "Novo atendimento
  iniciado") por **dono** e por **setor**. Aparece quando há mais de um dono **ou** mais
  de um setor no histórico visível.
  - **Abas por dono** (`.conv-owner-tabs`, mostradas quando `owner_tabs` = **há mais de
    um atendimento** no histórico visível, para o filtro ficar descobrível): **Conversa
    do setor** (padrão, tudo o que a pessoa pode ver) × **Conversa privada** (só os
    atendimentos que ela mesma atendeu). "Meu" = o segmento tem resposta minha
    (`sender_name` == meu nome de atendente) ou a conversa está atribuída a mim no
    segmento atual. *(Depende de a pessoa ter "ver conversa inteira" — senão ela só
    enxerga o atendimento atual, um único segmento, e não há o que separar.)*
  - **Seletor de setor** (`.conv-sector-chips`, só na aba "Conversa do setor", quando há
    ≥2 setores): **Todos os setores** + um chip por setor presente no histórico. O
    **setor de cada atendimento** é resolvido no endpoint como o **último setor não-nulo
    do segmento** (com fallback para `Conversation.sector` no segmento atual) — então o
    atendimento **inteiro** (inclusive a triagem sem setor) entra no setor onde terminou.
  - Backend (`conversation_messages_view`): marca cada mensagem com `seg` (índice),
    `seg_mine` e `seg_sector` (id); retorna `owner_tabs` e `conv_sectors` (setores
    presentes). Front (`buildMessageEl` grava `data-seg-mine`/`data-seg-sector`;
    `applyFilters()` combina dono + setor com a classe `.conv-msg-hidden`), reaplicado
    no poll e resetado ("Conversa do setor" + "Todos") ao trocar de conversa.
  - **Dado por trás:** `Message.sector` é carimbado na criação com o setor da conversa
    naquele momento (migração `0030` faz backfill do atendimento **atual**; atendimentos
    antigos fechados ficam sem setor — não há como saber o setor histórico deles).
- **Poll incremental** (`syncMessages`): a atualização periódica só mexe no DOM
  quando chega mensagem nova ou muda o conteúdo (ex.: mídia baixada); **nunca**
  recria uma mídia que esteja tocando (não corta o play). Poll: mensagens 6s,
  lista 12s (só re-renderiza se a assinatura mudar), notificações 6s.
- **Mídia**: foto/vídeo aparecem como **miniatura leve** (vídeo com poster lazy via
  `IntersectionObserver`); clicar abre em **tela grande (lightbox)** com play. Áudio
  toca inline; GIF em loop silencioso. **Documento** baixa com nome/extensão reais
  (atributo `download`).
- **Menções em grupo**: `@<número>` no texto é resolvido para `@<nome>` (Contato
  salvo ou pushName de quem já enviou no grupo).
- **`_build_name_map(conversation, mensagens)`** monta o mapa `{número: nome}` a
  partir das mensagens **já carregadas**. Antes fazia uma varredura própria de todas
  as mensagens da conversa — uma **segunda** leitura completa do grupo no mesmo
  request que já havia feito a primeira, a cada poll de 6 s.
- **Só nome CADASTRADO aparece como nome** (em qualquer tela): o pushName do WhatsApp
  não é usado nem na conversa direta (ver `Contact` na seção 3) nem no grupo
  (`_build_name_map` resolve **apenas** por Contato cadastrado). Sem cadastro, aparece
  o **número**, e clicar nele abre o modal que cadastra o contato. No **cabeçalho** da
  conversa direta sem nome, o número ganha destaque pontilhado visível
  (`.conv-chat-name-unnamed`) e a linha de baixo mostra "Sem nome cadastrado — clique
  no número" (em vez de repetir o número); depois de salvar, o cabeçalho troca para o
  nome na hora. `_serialize_contact_info` expõe `contact_name` (nome realmente
  cadastrado, vazio = falta cadastrar) além de `name` (que cai para o número) — é o que
  evita o modal abrir com o telefone escrito no campo de nome.
- **Nome do remetente (grupo)**: **recebida** → mostra o nome (se não houver Contato
  cadastrado, o **número clicável** → modal "Nomear contato"); **enviada** → mostra o
  **nome do atendente que mandou** (como é um número só, o time sabe quem respondeu). O envio
  grava `Message.sender_name` = nome do atendente (`_current_attendant_name`); o front
  mostra acima do balão (`.conv-msg-sender-me`). **No corpo enviado ao WhatsApp** (grupo),
  o texto vai prefixado com `*<atendente>*\n...` — assim os **participantes do grupo**
  (fora do sistema) também veem quem falou. O texto **salvo no nosso chat fica sem o
  prefixo** (o nome já aparece acima do balão, para não duplicar). Em conversa **direta**
  não há prefixo; o **nome no cabeçalho** é clicável para renomear o contato.
- **Grupo NÃO é atendimento**: ao abrir um grupo, o painel **esconde Assumir/Encerrar/
  Transferir** (só nome/status/setor/atendente). E grupos **não entram em "Aguardando"**
  (o filtro/badge é só de conversas diretas).
- **Notificações (estilo WhatsApp Web)**: pop-up do desktop (Web Notifications) +
  **aviso sonoro** (beep via Web Audio, sem arquivo) quando a janela **não** está em
  foco (`document.hasFocus()`); toast interno quando em foco. No topo da lista:
  o **sino de Notificações** é um **indicador SOMENTE informativo** (não clicável,
  `<span>` com `.conv-notif-indicator`): apenas reflete o estado da permissão do
  navegador — verde = ativas, âmbar = desativadas, vermelho = bloqueadas — e se
  atualiza sozinho via `navigator.permissions` quando muda (ex.: liberar no cadeado);
  a permissão é concedida pelo próprio navegador (não pelo app). Ao lado, o botão
  **Som** (liga/desliga, salvo em `localStorage`). Título da aba mostra o total de
  não lidas.
- **Botão Atualizar** (ícone de refresh, ao lado de Som/Notificações; substituiu o
  "Sincronizar grupos"): sincroniza os nomes dos grupos **e** retenta as mídias que
  falharam na conversa aberta.
- **Composer**: 📎 anexo (imagem/áudio/vídeo/documento), 🎤 microfone (grava com
  `MediaRecorder`, converte p/ ogg no backend), campo de texto, enviar.
- **Campo de texto = `<textarea>`** (não `<input>`, que perdia quebras de linha):
  cresce sozinho até ~140px, **Enter envia / Shift+Enter quebra linha** (estilo
  WhatsApp Web). Uma trava `sendingMessage` impede **reenvio duplicado** ao apertar
  Enter várias vezes seguidas (o campo só limpa quando o envio termina). Enquanto
  envia, o **botão de enviar** troca o ícone do avião por um **spinner girando**
  (classe `.is-sending`), voltando ao normal ao terminar. Ao enviar,
  `conversation_send_view` passa o texto por
  `markdown_to_whatsapp()` (`wapi/formatting.py`): converte Markdown → formatação
  nativa do WhatsApp (`**negrito**`→`*negrito*`, títulos `#`→linha em negrito,
  listas `*/-/+`→`•`, `[texto](url)`→`texto (url)`; citação `>` e lista numerada
  mantidas) preservando as quebras. O histórico guarda a **mesma** versão enviada.
- **Transferência** (setor/atendente) por selects na coluna de info.
- **O JavaScript fica em `static/js/conversations.js`**, não inline no template. Eram
  **1.593 linhas** dentro do `conversations.html` (que tinha 1.773 no total): o JS não
  tinha cache-busting próprio (dependia do restart do gunicorn para publicar) e achar
  o HTML no meio do script exigia rolar quase 1.800 linhas. Carregado com
  `{% asset 'js/conversations.js' %}`.
  > **O arquivo não passa por template**, então não pode conter `{% %}` nem `{{ }}`.
  > Tudo o que vem do servidor entra por `data-*` em `.conv-body`: `data-conv-base`,
  > `data-csrf`, `data-read-only`, `data-page-size`, `data-logo`.
- **URLs AJAX** montadas a partir de `window.location.pathname` (até `/conversas/`)
  para respeitar o prefixo `/beeonboard/` mesmo se o `{% url %}` vier sem prefixo.
- Endpoints: `conversation-list` (`/conversas/lista/`, aceita `?limite=`),
  `conversation-messages` (aceita `?limite=` e `?retry=1` para rebaixar mídias falhas;
  responde `has_older`), `conversation-send`,
  `conversation-send-media`, `conversation-transfer`, `conversation-take`
  (`/conversas/<id>/assumir/`), `conversation-close` (`/conversas/<id>/encerrar/`),
  `conversation-sync-groups`, `conversation-name-contact` (`/conversas/nomear-contato/`),
  `wapi-webhook-events`.

## 5.0. Tela Clientes (`templates/accounts/clients.html` + `clients.css`)

- Rota `clientes/` (`clients_view`, nome de rota `clients`), **exclusiva do gestor
  master**: cadastra e administra as **empresas clientes** (dados, CNPJ, logo, cor de
  destaque, ativar/desativar, excluir). Detalhes completos na **seção 16**.
- Cada cartão tem o botão **Métricas**, que leva à tela da seção 5.3.

## 5.0.1. Tela Métricas do cliente (`templates/accounts/client_metrics.html` + `client_metrics.css`)

- Rota `clientes/<id>/metricas/` (`client_metrics_view`, nome `client-metrics`),
  **exclusiva do gestor master** (`require_master`; o ADM do cliente recebe **403**).
- **Só números, datas e estado do canal** — é a tela que responde "qual o tamanho
  deste cliente e ele está usando o sistema?", sem violar a regra de que o master não
  lê o atendimento. Dados de `build_company_metrics(company)` (em `views.py`):
  - **Canal**: credenciais configuradas (sim/não — **nunca** o Instance ID ou o
    token), modo de primeiro atendimento, último evento de webhook e total de eventos.
  - **Mensagens**: enviadas e recebidas (total, 7 e 30 dias), respostas automáticas
    (IA/chatbot), mensagens com arquivo e **data/hora da última enviada e recebida**.
  - **Conversas**: ativas, aguardando, finalizadas, grupos e novas em 7 dias.
  - **Equipe**: atendentes, administradores, usuários ativos, setores e contatos.
  - **Inteligência (IA)**: tokens do **mês atual**, do **mês anterior** e o
    **acumulado**, com o número de chamadas, se a IA está ligada no primeiro
    atendimento e a última resposta da IA. Vem de `CompanyAiUsage` (seção 3) — a
    chave e o modelo do GPT continuam sendo da plataforma; aqui é só o **consumo**.
  - As divisórias (`message_type='system'`) **não** entram na contagem de mensagens.
- **Saúde da conexão**: botão **Testar conexão** (`client-connection-check`, POST,
  só master) → `wapi.client.check_connection(company=...)`, que consulta
  `status-instance` e, se essa rota não existir no plano (404/405), sonda com
  `get-all-groups`. Devolve `WapiHealth` (configurado / conectado / rótulo /
  explicação). Fica **atrás de um botão** de propósito: verificar na abertura faria
  uma chamada externa por empresa a cada visita.
- Há teste garantindo que a tela **não** exibe texto de mensagem, nome de contato,
  nome de grupo, telefone nem credencial.

## 5.0.2. Tela Métricas dos clientes (`templates/accounts/platform_metrics.html` + `platform_metrics.css`)

- Rota `metricas/` (`platform_metrics_view`, nome `platform-metrics`), **exclusiva do
  gestor master** (`require_master`), item **Métricas** do menu dele
  (`PLATFORM_METRICS_ITEM`, entre Clientes e Inteligência (IA)).
- É o **um lugar só** para acompanhar a carteira: **todos os clientes lado a lado**
  em vez de abrir empresa por empresa. Dados de `build_platform_metrics()` (em
  `views.py`):
  - **Resumo da plataforma**: clientes ativos, canais configurados, conversas ativas
    e aguardando, mensagens em 30 dias, respostas automáticas, **tokens de IA no mês**
    e o acumulado por cliente.
  - **Conta da Inteligência (IA)**: se a API Key está cadastrada, o modelo em uso, o
    total da **plataforma** (`OpenAiConfiguration`) e a última chamada. O total da
    plataforma pode ser **maior que a soma por empresa** — inclui os **testes de
    conexão** (que não têm empresa) e tudo o que foi gasto **antes de existir a
    medição por cliente**; a tela explica isso num cartão, em vez de somar coisas
    diferentes.
  - **Cliente por cliente** (uma linha por empresa): canal (credencial cadastrada +
    último evento de webhook), modo de primeiro atendimento, conversas ativas e
    aguardando, mensagens em 30 dias e no total, respostas automáticas, **tokens no
    mês** (com chamadas e acumulado), última mensagem e o botão **Detalhes** (leva à
    tela da seção 5.0.1). Ordem: **empresa ativa primeiro**, depois **maior consumo
    de IA no mês** e maior movimento — quem está pesando na conta aparece em cima.
- **Consultas agregadas por empresa** (`values('company').annotate(...)`, uma consulta
  por assunto): a tela não fica mais lenta conforme a carteira cresce. **A tela de um
  cliente (5.0.1) segue o mesmo padrão**: `build_company_metrics` faz 3 agregações
  (`Count('id', filter=Q(...))`) em vez dos ~25 `.count()` que tinha.
- **Saúde do canal aqui é só "credencial cadastrada" + "último evento"**: consultar a
  W-API de verdade é uma chamada externa **por empresa** e continua atrás do botão
  **Testar conexão** da tela de cada cliente (seção 5.0.1).
- **Privacidade igual à da seção 5.0.1**: só números e datas — sem texto de mensagem,
  contato, grupo, arquivo ou credencial (há teste garantindo). A tabela rola **dentro
  do próprio painel** (`.table-wrap`), então a página não ganha rolagem horizontal.

## 5.1. Tela Contatos (`templates/accounts/contacts.html` + `contacts.css`)

- Rota `contatos/` (`contacts_view`, nome de rota `contacts`; item da barra lateral).
- Lista os `Contact` (avatar com iniciais, nome, telefone), **busca** por nome/telefone
  (GET `q`), contador e CRUD: adicionar/editar por **modal** e excluir (com confirmação).
- Telefone é **normalizado para dígitos** ao salvar (mesma chave da resolução de nomes),
  então o que se cadastra aqui aparece no lugar do número nas conversas de grupo.
- Disponível para qualquer usuário logado. Reaproveita `dashboard.css`/`attendants.css`.

## 5.2. Tela Dashboard (`templates/accounts/dashboard.html` + `dashboard.css`)

- Rota `dashboard/` (`dashboard_view`). **Só quem tem o botão Dashboard** (por padrão,
  só ADM — ver seção 15); quem não tem cai na 1ª tela disponível (`first_landing_url_name`).
- **Dados 100% reais** do banco, calculados em `build_dashboard_context()` (views):
  - **Cards**: Conversas ativas (não fechadas), Novas conversas (criadas nos últimos
    7 dias), Atendimentos finalizados (fechadas), Tempo médio de resposta (1ª resposta
    do atendente após a 1ª mensagem do cliente, média dos últimos 30 dias — calculado
    com **duas agregações `Min(created_at)`**, não carregando as mensagens: antes um
    `prefetch_related('messages')` trazia 30 dias de mensagens do cliente inteiro para
    produzir um único número).
  - **Atendimentos por dia**: últimos 7 dias (pela data da última mensagem). É um
    **gráfico de linha em SVG SEM texto** (só linha/área/grade, coords em % com
    `preserveAspectRatio=none` + `vector-effect=non-scaling-stroke`); os **números,
    datas e pontos são HTML posicionados por %** (`.daychart .dc-val/.dc-date/.dc-dot`).
    O CSS do gráfico está **embutido num `<style>`** no próprio `dashboard.html` (à
    prova de cache) e todo o bloco vem dentro de `{% localize off %}` (pt-BR, ver seção 6).
  - **Atendimentos por setor**: donut (conic-gradient inline) + legenda, com a
    distribuição real por setor.
  - **Atendimentos em andamento**: conversas `open` (cliente, setor, atendente, última
    atividade, última mensagem).
- **NÃO tem atalhos** no topo (Nova conversa/Fila/Relatórios/Configurações foram
  removidos — a pedido; o painel é só indicadores).
- Popular dados de demonstração: comando **`seed_demo_data`** (ver seção 9).

## 6. Deploy no VPS (LEIA — tem armadilhas específicas)

- App em `/var/www/beezap/`, serviço systemd **`beezap`**, gunicorn em
  **`127.0.0.1:8103`** (os exemplos em `deploy/` citam 8006, mas o serviço real
  roda em 8103; o Nginx `/beeonboard/` faz proxy para 8103).
- **Nginx**: config do domínio em `/etc/nginx/sites-available/site_idiomas`.
  Blocos do BEEonBOARD (proxy com `/` final **remove** o prefixo antes do Django):
  ```nginx
  location /beeonboard/static/admin/ { alias /var/www/beezap/staticfiles/admin/; }
  location /beeonboard/static/       { alias /var/www/beezap/static/; }   # serve a FONTE
  location /beeonboard/media/empresas/ { alias /var/www/beezap/media/empresas/; }  # SO logos
  location /beeonboard/              { proxy_pass http://127.0.0.1:8103/; ... }
  ```
  > **Não servir `/beeonboard/media/` inteiro.** A pasta `media/whatsapp/` guarda os
  > arquivos das conversas dos clientes; publicada pelo Nginx, ela fica acessível
  > sem login e sem checagem de empresa. A mídia sai pela view autenticada do Django
  > (ver seção 4, "Arquivos de mídia"). Se o servidor ainda tiver o bloco antigo,
  > **trocar no deploy** — o código novo sozinho não fecha esse caminho.
- **Prefixo `/beeonboard/`**: resolvido no Django via **`FORCE_SCRIPT_NAME=/beeonboard`**
  (`.env`), que prefixa todos os `{% url %}`/redirects. `LOGIN_URL`/
  `LOGIN_REDIRECT_URL`/`LOGOUT_REDIRECT_URL` são **nomes de rota** (herdam o prefixo).
- **Estáticos**: como o Nginx serve `static/` (a fonte) direto, **um `git pull`
  já publica CSS/JS** — sem `collectstatic`/`cp`. O admin do Django vem de
  `staticfiles/admin/` (rodar `collectstatic` uma vez).
- **Cache-busting é automático — não existe mais `?v=N` na mão.** Todo link de CSS usa
  a tag `{% asset 'css/x.css' %}` (`accounts/templatetags/beeonboard_assets.py`), que
  deriva a versão da **data de modificação do próprio arquivo**. Editar o CSS e
  publicar a versão nova passaram a ser a mesma ação.
  > **Por que mudou:** o `?v=N` manual nunca ficava certo, porque o mesmo arquivo é
  > carregado por vários templates. `dashboard.css` estava com `?v=6` em **8**
  > templates e **sem versão nenhuma** em outros **7**; `attendants.css` tinha versão
  > em 1 e faltava em 5; `login.css`, `password_recovery.css` e `wapi_settings.css`
  > não tinham versão. Ou seja: bumpar limpava o cache de metade das telas e deixava a
  > outra metade com o arquivo antigo no navegador — exatamente o sintoma "mudei o CSS
  > e não aparece". Um teste (`AssetVersioningTests`) reprova se `?v=` manual voltar a
  > aparecer num link de CSS.
- **Histórico do bug de estáticos**: o `settings.py` do servidor já foi editado à
  mão com `STATICFILES_DIRS=[]`, o que impedia o `collectstatic` de publicar o
  CSS. Corrigido de forma versionada (ver `DEPLOY.md`). Não esvaziar `STATICFILES_DIRS`.
- **ffmpeg**: dependência de **sistema** (não pip), **obrigatória** para envio de
  mídia — converte áudio gravado (`.webm`→`.ogg`) e imagens não suportadas pela
  W-API (webp/gif/bmp/heic→`.jpg`). `sudo apt install -y ffmpeg`. O `manage.py check`
  avisa se faltar (**`beezap.W001`**). Ver `requirements.txt` e `DEPLOY.md`.
- **Cookies com nome próprio** (`beeonboard_sessionid` / `beeonboard_csrftoken`,
  path = `FORCE_SCRIPT_NAME`). **Não voltar ao padrão do Django.** O domínio serve
  vários sistemas Django sob prefixos diferentes (`/beeonboard/`, `/trade/`,
  `/tradeanalise/`, `/italiano-ti/`, …); com o nome padrão `sessionid` no path `/`,
  eles **sobrescrevem a sessão um do outro** — entrar num derruba o login do outro.
  Os vizinhos já resolvem assim (`italianoti_sessionid`, `formdesenv_sessionid`).
  Efeito colateral do deploy que introduziu isso: as sessões abertas caíram **uma
  vez**.
- **`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`** ligam sozinhos quando
  `DEBUG=False` (o domínio é HTTPS). **HSTS vem desligado de propósito**: vale para o
  **domínio inteiro**, não só para `/beeonboard/`, e um `max-age` alto é difícil de
  desfazer — ligar via `SECURE_HSTS_SECONDS` no `.env`, com decisão consciente.
- **`SECRET_KEY` sem valor derruba a subida quando `DEBUG=False`.** Antes a aplicação
  subia com a chave de desenvolvimento (que é versionada, pública) se o
  `EnvironmentFile` do systemd falhasse em carregar — e com ela sessão e token
  assinado ficam forjáveis, sem nada na tela indicando o problema.
- **SQLite com `WAL` + `timeout=20`** (`SQLITE_OPTIONS` no `settings.py`): são 2
  workers do gunicorn **mais** threads de background (mídia, IA, chatbot) gravando na
  mesma base. No modo padrão, `database is locked` é questão de volume. Com
  PostgreSQL (`DATABASE_URL`) nada disso é necessário.
- **`DEBUG=False` no servidor** (já ativo — bom para segurança). **ARMADILHA CRÍTICA
  que já custou horas:** com `DEBUG=False`, o Django usa o `cached.Loader` e
  **guarda os templates compilados na memória de cada worker do gunicorn**. Um
  `git pull` atualiza o disco, mas o gunicorn continua servindo o **template ANTIGO**
  até os workers serem **realmente reiniciados**. Sintoma: mudança de template "não
  aparece" no navegador (nem anônima, nem no celular no 4G), enquanto o disco e os
  estáticos já estão novos. **Todo deploy TEM que reiniciar o gunicorn** e **confirmar
  que os PIDs foram reciclados**:
  ```bash
  sudo systemctl restart beezap
  ps -eo pid,etimes,cmd | grep "[b]eezap/venv/bin/gunicorn"   # etimes deve ser pequeno (segundos)
  # se não reciclou: sudo systemctl stop beezap; sudo pkill -f "beezap/venv/bin/gunicorn"; sudo systemctl start beezap
  ```
  Não há CDN/`proxy_cache` no Nginx (checado); quando template novo não aparece, o
  culpado quase sempre é este (gunicorn com template em cache), não o navegador.
- **Localização pt-BR em templates (`LANGUAGE_CODE='pt-br'`):** o Django imprime
  **float com vírgula** (`{{ 6.0 }}` → `6,0`). Se esse número vai para **CSS/atributo**
  (`style="left: {{ x }}%"`, atributo SVG), a vírgula gera valor **inválido** e o
  navegador ignora. Foi o bug do gráfico do dashboard. **Regra:** número que entra em
  CSS/atributo dentro de template → envolver com `{% load l10n %}{% localize off %}…{% endlocalize %}`
  ou montar a string em Python na view (strings não são localizadas).

### Fluxo de deploy

`bash deploy/deploy.sh` faz, em ordem: `git pull` → dependências → **backup do
`db.sqlite3`** (com `sqlite3 .backup`, consistente com o app no ar; mantém as 10
cópias mais recentes em `backup/`) → `migrate` → `collectstatic` →
**`check --deploy`** (informativo, não aborta) → restart do gunicorn **confirmando que
os PIDs reciclaram**. O backup existe porque **com SQLite não há rollback de
migration**: a única volta é o arquivo de antes.
```bash
cd /var/www/beezap
bash deploy/deploy.sh      # git pull + pip install + migrate + collectstatic + restart (RECOMENDADO)
# manual: git pull && venv/bin/python manage.py migrate && sudo systemctl restart beezap
#         e SEMPRE confirmar o restart: ps -eo pid,etimes,cmd | grep "[b]eezap/venv/bin/gunicorn"
```

## 7. Variáveis de ambiente (`.env`) — ver `.env.example`

Obrigatórias/relevantes em produção:
```
SECRET_KEY=...
DEBUG=False                       # já ativo no servidor (cacheia templates: reiniciar gunicorn no deploy)
ALLOWED_HOSTS=fabianopolone.com.br,www.fabianopolone.com.br
CSRF_TRUSTED_ORIGINS=https://fabianopolone.com.br
DATABASE_URL=sqlite:////var/www/beezap/db.sqlite3
FORCE_SCRIPT_NAME=/beeonboard
STATIC_URL=/beeonboard/static/
MEDIA_URL=/beeonboard/media/          # sem isto, envio de mídia falha (W-API não baixa a URL)
WAPI_BASE_URL=https://api.w-api.app
WAPI_MEDIA_MAX_MB=16
# Instance ID / Token da W-API ficam no BANCO (tela de config), não precisam no .env
# GPT (OpenAI): a API Key normalmente fica no BANCO (tela Inteligência (IA)); as
# variáveis abaixo são fallback opcional.
OPENAI_BASE_URL=https://api.openai.com
OPENAI_API_KEY=                   # opcional (fallback; o normal é cadastrar na tela)
OPENAI_MODEL=gpt-4.1-nano         # modelo padrão (o mais barato)
OPENAI_TIMEOUT=30
```

## 8. Fluxo de trabalho obrigatório (ver `CODEX_PADROES.md` e `GIT.md`)

> A mesma regra está no **`CLAUDE.md` na raiz** do projeto (carregado automaticamente
> pelo agente em toda sessão) e no topo do **`README.md`** — de propósito, para ela não
> depender de alguém abrir a documentação.

> **REGRA FIXA — NENHUMA ALTERAÇÃO FICA SEM `commit` + `push`.** Toda mudança
> (código, CSS, template ou documentação) é fechada no mesmo passo: atualizar a
> documentação, commitar e **enviar ao GitHub na hora**. Não acumular alterações
> locais para "commitar depois" — o repositório remoto é sempre o estado real do
> projeto. Se o `check` falhar, corrigir antes: não se commita com o check quebrado.

1. Fazer a alteração.
2. `python manage.py check` (e `makemigrations`/`migrate` se mexer em model).
3. Atualizar **apenas o final** de `docs/HISTORICO.md` **e** a documentação afetada
   (este `CONTEXTO.md` e os demais) — a documentação reflete sempre o estado atual.
4. Commit atômico (`feat:`/`fix:`/`docs:`/`style:`/`chore:`, mensagem em PT-BR) →
   **`git push` imediato** (código + docs no mesmo commit).
5. Não commitar `.env`, `db.sqlite3`, `venv/`, tokens.

### Rodar os testes

```bash
python manage.py test          # 371 testes, ~9 segundos
```

O projeto usa um runner proprio, `accounts/test_runner.py`
(`FastPasswordHasherRunner`), registrado em `settings.TEST_RUNNER`: ele troca o
`PASSWORD_HASHERS` por MD5 **somente durante os testes**. Sem isso a suite levava
**mais de 10 minutos** (quase todo teste cria usuario, e o PBKDF2 dominava o tempo),
e uma suite lenta assim simplesmente deixa de ser rodada antes do commit. O hash de
**producao continua o padrao do Django** — nada muda para senha real.

## 9. Comandos de diagnóstico úteis (no VPS)

```bash
# Publicação de estáticos:
bash deploy/diag_static.sh
# Motivo real de falha no envio W-API (mostra status + corpo, sem token):
sudo journalctl -u beezap -n 80 --no-pager | grep -iE "W-API|falhou"
# Testar uma view direto contra o banco (isola front-end de backend):
venv/bin/python manage.py shell -c "from django.test import Client; from accounts.models import User, Conversation; c=Client(); c.force_login(User.objects.filter(role='adm').first()); conv=Conversation.objects.first(); r=c.get('/conversas/%s/mensagens/'%conv.id, HTTP_HOST='localhost'); print(r.status_code)"
# Sincronizar eventos antigos em conversas:
venv/bin/python manage.py sync_wapi_events_to_conversations
```
> Obs.: `manage.py shell` no terminal **não carrega o `.env`** (quem carrega é o
> systemd para o gunicorn) — use `HTTP_HOST='localhost'` em testes de Client.

### Comandos de management (todos em `accounts/management/commands/`)
```bash
sync_wapi_events_to_conversations   # transforma eventos W-API antigos em conversas
sync_wapi_group_names               # atualiza os nomes dos grupos pela W-API
retry_wapi_media                    # rebaixa TODAS as mídias recebidas sem arquivo local
inspect_wapi_messages --name X --full   # DIAGNÓSTICO: payload cru + veredito do parser (Messages criadas)
inspect_wapi_events --hours 6 --full    # DIAGNÓSTICO: eventos BRUTOS do webhook, INCLUSIVE os descartados
inspect_wapi_groups [--full]            # DIAGNÓSTICO: resposta de get-all-groups + nome extraído por grupo
cleanup_status_messages [--delete]      # remove mensagens de Status que viraram conversa
cleanup_unknown_messages [--delete]     # remove mensagens de tipo 'unknown' (sistema)
cleanup_nonpersonal_conversations [--delete]  # remove conversas de canal/transmissão/"status"
cleanup_pushname_contacts [--apply]     # limpa nome herdado do pushName (contato volta a aparecer pelo NÚMERO)
link_lid_contacts [--apply]             # conversas diretas @lid antigas: acha o telefone real no histórico e anexa o Contato
merge_contact_conversations [--apply]   # unifica conversas picotadas em 1 chat por pessoa/grupo (dry-run)
seed_demo_data [--no-clear]             # popula DEMO: 5 setores/atendentes + conversas 7 dias (preserva admin/config)
prune_wapi_events [--dias 90] [--dias-apagar 365] [--empresa X] [--apply]
                                        # RETENCAO: esvazia o payload bruto de eventos antigos e apaga os muito velhos
auditar_empresas [--detalhe]            # AUDITORIA (só leitura): acha registro apontando para a empresa errada
```
> **`prune_wapi_events`** existe porque `WapiWebhookEvent` guardava o payload bruto de
> **todo** evento recebido e nada nunca apagava nada — é a tabela que mais cresce, e o
> mesmo JSON ainda fica duplicado em `Message.raw_payload` (que é o usado de verdade
> pelo retry de mídia e pelo nome do documento). Trabalha em dois níveis: **esvaziar o
> payload** dos mais velhos que `--dias` (mantendo a linha e as colunas já extraídas,
> então as Métricas não mudam) e **apagar a linha** dos mais velhos que
> `--dias-apagar`. Dry-run por padrão.
>
> **`auditar_empresas`** confere se o vínculo de empresa está coerente em todo o banco
> (atendente com empresa diferente da do usuário, conversa com setor/contato/atendente
> de outra empresa, mensagem com setor de fora, opção de menu apontando para setor de
> outro cliente, grupo liberado para setor/usuário de fora, usuário operacional sem
> empresa…). As views já filtram por empresa em cada ponto, mas **nada provava isso**.
> É **somente leitura** de propósito: mover dado de cliente é decisão consciente, não
> correção automática. Vale rodar depois de cada migração grande.

> Os `cleanup_*` e o `inspect_*` são **dry-run por padrão** (só listam); `--delete`
> (ou `--apply`, no `cleanup_pushname_contacts`) aplica. Úteis para limpar lixo antigo
> (status/canal/sistema) após um deploy do fix.
> O `cleanup_pushname_contacts` só limpa o nome quando ele é **idêntico** (ignorando
> caixa/espaços) ao pushName registrado em alguma mensagem recebida daquele número —
> nome digitado por uma pessoa não bate com pushName nenhum e é **preservado**.
> O `link_lid_contacts` resolve o telefone pelo remetente **mais frequente** das
> mensagens **recebidas** da conversa (nunca o `connectedPhone`) e **não altera** nome
> de contato já cadastrado; conversa sem mensagem recebida fica como está (resolve
> sozinha quando chegar uma).

## 10. Pendências / próximas etapas

- ~~**MULTIEMPRESA — Parte 3 (portabilidade)**~~ — **CONCLUÍDA**: aba **Meus dados**
  (o **cliente** baixa o ZIP com contatos/conversas/mensagens/mídias) e encerramento
  seguro do cliente. Ver seção 16.
- ~~**MULTIEMPRESA — Parte 4 (medição por cliente)**~~ — **CONCLUÍDA**: consumo de
  **tokens por empresa e por mês** (`CompanyAiUsage`, seção 3), o bloco
  **Inteligência (IA)** na tela de Métricas do cliente (seção 5.0.1) e a tela
  **Métricas dos clientes** com a carteira inteira num lugar só (seção 5.0.2).
- **Planos/limites por cliente**: **decidido não fazer por enquanto** (a pedido) — a
  plataforma **mede** o consumo e **não trava** nada. Se um dia existir teto por
  cliente, é `CompanyAiUsage` que já tem o consumo do ciclo mensal para ler.
- **Nova conversa**: botão para iniciar um chat digitando número + mensagem (e abrir
  em Conversas). Combinado como próxima etapa.
- **Fila de atendimento**: tela/fluxo da fila por setor. Próxima etapa.
- **Relatórios**: tela ainda não criada (item foi removido do menu por enquanto).
- Legenda (caption) ao enviar imagem/vídeo/documento pelo composer.
- Recursos **PRO** (reação/sticker/GIF nativo/botões/listas) quando a instância for PRO.
- Upload múltiplo, arrastar-e-soltar.
- (Opcional) Tornar as **menções `@` clicáveis** dentro do texto para nomear ali
  mesmo; hoje o clique-para-nomear está no remetente e no cabeçalho da direta.
- (Opcional) Retry de mídias falhas em **todas** as conversas (hoje o botão
  Atualizar age só na conversa aberta; existe o comando `retry_wapi_media` global).
- (Opcional) Decidir se o perfil `leitor` continua ou é **removido** no futuro. Hoje
  ele já é **somente-leitura de verdade** (bloqueio no backend + UI escondida; ver
  seção 15).

### Já concluído nesta fase (não são mais pendências)
- Download de **documento** corrigido (nome/extensão reais, qualquer tipo; não mais `.bin`).
- **Lightbox** de foto/vídeo (abre grande) — cobre o "preview em tela cheia".
- **Notificações** (pop-up + som + botões de estado) e **poll incremental** (não corta play).
- **Grupo vs direta/canal/status** robustos; Status/canal/sistema ignorados.
- **Menções** e **nomes de participantes** resolvidos; tela **Contatos** e nomear pelo chat.
- **Ciclo de atendimento**: ações **Assumir** e **Encerrar** na tela Conversas (ver seção 12).
- **Um único chat por pessoa/grupo** (padrão WhatsApp) com **divisórias** de atendimento
  (ver seção 12); comando `merge_contact_conversations` unifica chats antigos picotados.
- **Atendente virtual (IA) removido** por completo (módulo `ai_engine`, telas, models,
  Ollama) — ver nota na seção 12. **IA reconstruída via OpenAI/GPT** (seção 13) e
  **Chatbot de menu** (seção 14) como opções de primeiro atendimento.
- **Permissões de menu** por perfil/usuário + **acesso a grupos** por setor/usuário
  (seção 15); **admin vira atendente** de todos os setores automaticamente (seção 3).
- **Separação das conversas** por setor/grupo e **escopo de histórico** (seção 15).
- **Dashboard com dados reais** + comando `seed_demo_data` (seção 5.2).

## 11. Segurança

- Servidor: usar **chave SSH** e usuário não-root; **rotacionar** qualquer
  credencial que tenha sido exposta. Nunca colar senha/token em chat ou commit.
- Nunca expor token/payload/traceback ao usuário final (padrão já seguido).
- **Arquivo de conversa nunca é servido pelo Nginx** — só pela view autenticada
  (seção 4). Ao mexer em mídia, manter esse caminho: publicar a pasta de novo
  reabre o vazamento entre empresas.
- **Dado de cliente sempre filtrado por `company`**; o master não lê atendimento
  (seção 16, "O que o master NÃO alcança"). Ao criar tela/endpoint novo, começar
  pelo escopo de empresa, não deixá-lo para depois.
- **`logout` só por POST** (`@require_POST`; o botão "Sair" é o include
  `templates/accounts/_logout_form.html`, com CSRF). Por GET, qualquer página de
  terceiros derrubava a sessão de quem a abrisse com um `<img src=".../logout/">`.
- **Id de formulário passa por `views.id_valido()`**: `filter(pk='abc')` levanta
  `ValueError` no Django, ou seja, um valor forjado virava **500** em vez de "não
  encontrado".
- **`User.save()` normaliza o e-mail para minúsculo.** `email` é único no banco de
  forma sensível à caixa, mas o login busca com `email__iexact`: sem normalizar,
  `Joao@x.com` e `joao@x.com` coexistiam (conta criada pelo shell ou pelo admin) e o
  login estourava `MultipleObjectsReturned` — 500 na tela de entrada. O
  `EmailBackend` também ficou tolerante ao que já estiver gravado.
- **`Conversation` e `Message` NÃO ficam no admin do Django** (`accounts/admin.py`).
  Estavam registradas sem filtro de empresa e com `search_fields = ('text', …)`:
  qualquer conta `is_staff` lia e **pesquisava** o texto das conversas de todos os
  clientes por `/beeonboard/admin/`. Para inspecionar conversa existem os comandos
  `inspect_wapi_messages` / `inspect_wapi_events`, que rodam no servidor e não abrem
  uma busca por texto na web. **Não registrar de volta.**

## 12. Ciclo de atendimento (assumir / encerrar)

> O **atendente virtual (IA)** foi **removido** do sistema (módulo `ai_engine`, telas de
> Automação/Atendente Virtual, models `AiAttendantConfig`/`AutomationRule`, campos
> `Conversation.ai_state/ai_turns` e `Message.is_ai`, integração Ollama). Migração `0018`.
> O recebimento/webhook, Conversas, Contatos, Setores e envio seguem intactos.
> **A IA foi reconstruída do zero depois usando a API do OpenAI (GPT) — ver seção 13.**

- **Um único chat por pessoa/grupo** (padrão WhatsApp): `resolve_conversation_for_context`
  **sempre reusa a mesma** `Conversation` do contato/grupo (não exclui mais `closed`, não dá
  fork). Todo o histórico fica num só chat.
- Na tela Conversas há ações de atendimento: **Assumir** (usuário com perfil de atendente)
  e **Encerrar**. O admin pode transferir setor/atendente pelos selects da coluna de info.
- **Transferir** para um setor sem atendente deixa a conversa `pending` (Aguardando <Setor>);
  atribuir atendente deixa `open`.
- **Encerrar** (`conversation_close_view`): insere a **divisória** "Atendimento encerrado"
  (`message_type='system'`), marca `status='closed'`, limpa o `sector` mas **MANTÉM o
  `assigned_attendant`** que fechou — assim ele continua vendo a conversa em **Finalizados**
  (a visibilidade exige atribuição ou setor; sem o atendente, sumiria da vista dele). O
  chat e o histórico **permanecem**.
- A **próxima mensagem** do mesmo contato reusa o mesmo chat: `_reopen_for_new_service`
  insere "Novo atendimento iniciado", volta `status='open'` e **zera `assigned_attendant`
  e `sector`** (a nova conversa volta para a recepção/fila, sem dono).
- **Escopo do histórico** (não-admin, sem "conversa inteira"): mostra a partir da última
  divisória **"Novo atendimento iniciado"** (NÃO a de "encerrado") — assim um chat
  finalizado, ou recém-encaminhado pela IA, mostra **todo o atendimento** (cliente + IA/menu),
  não só a divisória. Ver seção 15.
- **Front**: `buildMessageEl` renderiza `kind='system'` como uma **pílula centralizada**
  (`.conv-divider`); a pílula mostra o texto + **data e hora** (ex.: "Atendimento
  encerrado · 14/07/2026 18:44"). CSS em `conversations.css?v=27`.
- **Chats já picotados** (do comportamento antigo de fork) são unificados pelo comando
  `merge_contact_conversations` (ver seção 9 / comandos de management).

## 13. Inteligência (IA) / GPT — integração com o OpenAI

> A IA foi recomeçada **do zero** usando a **API do OpenAI (GPT)** — nada de
> Ollama local (o antigo `ai_engine` foi removido; ver seção 12). Esta é a **base**:
> cadastro/validação da API Key. **A IA vem DESLIGADA** e ainda **não está ligada
> a nenhum fluxo** (recepção/resposta automática) — o comportamento vem depois.

- **Credencial no banco**: model `OpenAiConfiguration` (singleton, seção 3). A
  **API Key** é cadastrada na tela e salva no banco; nunca fica no código nem é
  reexibida após salva (mesmo padrão do token da W-API).
- **Cliente** (`gpt/client.py`): módulo comum (como `wapi/`, **não** é app), usa só
  `urllib` (sem pacote pip novo). Chama `POST https://api.openai.com/v1/chat/completions`
  com header `Authorization: Bearer <api_key>`. Funções: `chat_completion(messages,
  model=, temperature=, max_tokens=, timeout=)` → `GptResult(success, text, model,
  status_code, error)`; e `test_connection()` (chamada mínima que valida
  chave/modelo/créditos gastando pouquíssimo). Erros já vêm amigáveis (401 → chave
  recusada, 429/quota → sem créditos, modelo indisponível, etc.); log seguro no
  logger `beezap.gpt` (nunca expõe API Key/corpo/traceback). Nunca levanta exceção.
- **Tela "Inteligência (IA)"** (`templates/accounts/openai_settings.html` +
  `openai_settings.css`, escopo `.openai-settings-page`): agora é a **sub-aba IA da
  área Atendimento** (não é mais item solto na barra lateral), rota `configuracoes/ia/`
  (`openai-settings`), **só ADM** (`openai_settings_view`). Campos (form
  `OpenAiConfigurationForm`): **API Key** (oculta) e **Modelo** (select: `gpt-4.1-nano`
  [padrão, mais barato] / `gpt-4o-mini` / `gpt-4.1-mini` / `gpt-4o`). **A ativação
  (ligar a IA) NÃO é mais um checkbox aqui** — vem do **seletor de modo** no topo da
  área Atendimento (ver seção 14). Card de **status** (API Key / modelo / ativa) +
  botão **Testar conexão** (`form_type=test` → `gpt.client.test_connection`).
- **Consumo por empresa (medição)**: `chat_completion(..., company=<empresa>)` — o
  parâmetro é **opcional** e serve só para **medir**: quando vem, o mesmo consumo
  também é somado em `CompanyAiUsage` (por empresa e por mês, seção 3), que é o que
  responde "qual cliente está usando IA e quanto". Não muda nada no envio (chave e
  modelo continuam da plataforma) e **não existe limite nem bloqueio**. Quem passa a
  empresa é `gpt/attendant.py` (`company=conversation.company`); chamadas da
  plataforma, como o `test_connection()` da tela, ficam **sem empresa** e contam só no
  total geral. Se o contador da empresa falhar, a resposta do GPT **não** é derrubada
  (mesmo padrão do contador da plataforma). Ver a tela da seção 5.0.2.
- **Contador de consumo**: o OpenAI devolve `usage` (prompt/completion/total tokens)
  em cada resposta; `chat_completion` extrai e chama `OpenAiConfiguration.record_usage`
  (soma atômica com `F()`, segura para chamadas concorrentes). A tela mostra um card
  **"Consumo de tokens"** (total, entrada, saída, nº de chamadas, "contando desde" /
  "último uso") com botão **"Zerar contador"** (`form_type=reset-usage`). O teste de
  conexão também conta (gasto mínimo). CSS `openai_settings.css?v=7`.

### Atendente virtual (recepção/triagem) — `gpt/attendant.py`

A IA faz o **primeiro atendimento** de conversas **diretas** que ainda **não têm
setor nem atendente**: cumprimenta conforme o horário, entende o pedido e
**encaminha** para o setor certo (ou para o atendente citado). Ao encaminhar, sai
de cena e a conversa fica em aberto para o setor pegar. **Só atua com `enabled`
ligado.** Roda **sempre em background** (thread), nunca trava o webhook.

- **Disparo**: `save_incoming_message`/`ingest_wapi_payload` chamam
  `handle_incoming_for_ai_async(conversation_id)` para cada mensagem **recebida**
  de conversa direta. `ingest_wapi_payload(payload, trigger_ai=...)`: o **webhook ao
  vivo** usa `True`; o comando `sync_wapi_events_to_conversations` usa **`False`**
  (não responde mensagens históricas).
- **Contexto montado** (`build_system_prompt`): o **prompt/persona + regras de
  comportamento** ficam no campo **editável** (`instructions`, com `DEFAULT_INSTRUCTIONS`
  completo — brevidade, saudação do horário só na 1ª msg, não inventar, encaminhar ao
  setor geral quando nada específico se encaixa). O código **anexa automaticamente só
  os dados dinâmicos**: **data/hora** (saudação certa) + **tempo desde a mensagem
  anterior** (`_time_since_previous_text`: "primeira mensagem" / "há poucos minutos" /
  "há X hora(s)" / "há X dia(s) — nova conversa") + **setores** (nome + descrição) +
  **atendentes** (nome + setor) + **qual é o setor geral/curinga** (fallback) + a
  **regra de formato JSON** (obrigatória para o parsing) + o **nome da EMPRESA em
  nome de quem ela atende** + o **histórico** do
  **atendimento atual** (`build_history` pega só as mensagens **após a última
  divisória**, até `CONTEXT_MESSAGES=10`) em turnos `user`/`assistant`, terminando
  na mensagem atual — ao Encerrar/reabrir, o contexto começa limpo. A tela tem botão
  **"Restaurar prompt padrão"**.
- **A IA atende em nome da EMPRESA, não da plataforma**: como a configuração do GPT
  (e portanto o prompt editável) é **uma só** para todos os clientes, o nome de quem
  está atendendo entra como **dado dinâmico** em `build_system_prompt` — uma linha
  "Você está atendendo em nome da empresa X. Use esse nome ao se apresentar; nunca
  mencione o nome do sistema". Sem isso, o atendente virtual de **todos** os clientes
  se apresentava igual, e o prompt padrão ainda trazia o nome do sistema fixo no
  texto. Sem empresa, a linha simplesmente não entra (nunca um nome errado).
- **Decisão via JSON** (`response_format={'type':'json_object'}`): o modelo devolve
  `{"mensagem", "setor", "atendente"}`. Em ambos os casos o encaminhamento vai para
  um **SETOR** e a conversa fica **AGUARDANDO** (`pending`, **sem atribuir a ninguém**):
  `setor` casado → `_route_to_sector`; `atendente` casado → `_route_to_attendant`
  (vai para o **setor do atendente citado**, também sem atribuir a pessoa). Assim o
  time inteiro do setor é notificado e **alguém clica em Assumir** (aí vira `open`,
  "em atendimento"). Nenhum casado → envia a fala e incrementa `ai_turns`.
  **NÃO insere divisória**: o encaminhamento é parte do MESMO atendimento, então quem
  assumir vê **todo o histórico** (inclusive a conversa com a IA). O escopo de
  histórico (seção 15) só é cortado por Encerrar/reabrir, não pelo encaminhamento.
- **Limite/fallback**: ao atingir `max_turns` sem decidir, `_handoff_to_fallback`
  **sempre avisa o cliente** com uma mensagem clara de handoff (`HANDOFF_NOTICE`:
  "não consegui entender… vou pedir para um atendente…") — nunca transfere em
  silêncio nem repete a pergunta de esclarecimento — e **sempre encaminha para um
  SETOR real**: o `fallback_sector` configurado, um setor "Geral" existente ou, em
  último caso, o setor "Geral" padrão (`Sector.ensure_general()`). Assim a
  conversa **nunca fica órfã** (`pending` sem setor ficava invisível para os
  atendentes e fora de qualquer fila — parecia que "a IA não transferiu para
  ninguém"; era exatamente esse o bug). Criar o "Geral" dispara o sinal que inclui os
  admins nele, então o admin já vê a conversa em "Aguardando Geral".
- **Guardas** (`_should_handle` + `_human_replied_in_segment`): pula se desligada,
  sem API Key, grupo, `closed`, já tem setor/atendente, ou se um **humano já
  respondeu** no atendimento atual (mensagem `out` com `is_ai=False` após a última
  divisória). Lock por conversa evita processar rajadas em paralelo.
- **Tela**: além da conexão, tem o **prompt** editável, **limite de respostas**,
  **setor de fallback**, um painel **"O que é enviado para a IA"** (mostra o
  prompt + setores + atendentes + nota do histórico) e um painel **"Última chamada
  à IA (diagnóstico)"** que mostra o **request e o response completos** da última
  chamada real ao GPT (`OpenAiConfiguration.last_request/last_response/last_exchange_at`,
  gravados por `record_last_exchange` dentro de `chat_completion`; nunca contém a
  API Key). Para transparência total do que é (e do que não é) enviado.
- **Variáveis** (`.env`, seção 7): `OPENAI_BASE_URL`, `OPENAI_API_KEY` (fallback
  opcional), `OPENAI_MODEL`, `OPENAI_TIMEOUT`. O normal é cadastrar a chave pela tela.

## 14. Atendimento automático: modo mestre + Chatbot de menu (`chatbot/handler.py`)

O **primeiro atendimento** de conversas **diretas** sem setor/atendente é feito por
**um** de dois motores, escolhido pelo **modo mestre** `MenuBotConfiguration.mode`:

- `off` — nenhum atendimento automático;
- `menu` — **chatbot de menu** (fixo, sem IA, sem custo) — esta seção;
- `ai` — **atendente virtual GPT** (seção 13).

**Fonte única da verdade:** o webhook chama `_maybe_trigger_reception()`
(`wapi/services.py`) que, conforme o `mode`, dispara `handle_incoming_for_ai_async`
(IA), `handle_incoming_for_menu_async` (chatbot) ou nada. A guarda da IA
(`gpt/attendant._should_handle`) lê o modo (não mais `OpenAiConfiguration.enabled`).

**Trava por conversa (`wapi/autoreply_lock.py`) — vale ENTRE OS WORKERS.** A trava
que evita processar a mesma conversa duas vezes fica **no banco**
(`Conversation.auto_reply_lock_at`), não num `set()` em memória. Motivo: com
`--workers 2` cada worker tinha o seu set, então uma rajada de mensagens caindo em
processos diferentes passava pelas duas travas e **o cliente recebia o menu (ou a
resposta da IA) duas vezes**. Tomar a trava é um `UPDATE` condicional — atômico por
definição, sem Redis nem tabela nova. O TTL de 2 minutos existe para um worker morto
(o gunicorn mata worker no timeout) não deixar a conversa presa para sempre.
E **reprocessa**: se chegou mensagem nova durante o processamento, roda de novo (até
3 vezes) — antes a mensagem rejeitada pela trava era descartada, então o cliente
podia digitar a escolha e a conversa ficar parada, fora de qualquer fila.

**Chatbot de menu** (`chatbot/handler.py`, espelha o `gpt/attendant.py` — thread em
background, trava por conversa no banco, nunca levanta exceção):
- 1º contato do atendimento → envia **saudação + menu** (`build_menu_text`: `{saudacao}`
  vira Bom dia/tarde/noite, **`{empresa}` vira o nome da empresa cliente**; opções
  numeradas "1 - Financeiro").
  > **Quem se apresenta ao cliente final é a EMPRESA, não a plataforma.** O texto
  > padrão trazia o nome do sistema fixo ("Seja bem-vindo(a) a BEEZAP"), o que estava
  > errado duas vezes: era o nome **antigo** do produto depois da troca de marca, e
  > era o nome de um produto que o cliente final não conhece. Hoje o padrão usa
  > `{empresa}`, e a migração `0035` trocou o nome do sistema por `{empresa}` nos
  > textos **já salvos** no banco de cada cliente (só onde o nome aparecia — saudação
  > escrita pelo ADM fica intacta). `render_placeholders` é o único lugar que resolve
  > os três placeholders, então **todos** os textos da tela aceitam **todos** eles.
- Mensagens seguintes → `_match_option` interpreta o **número** digitado (ou o nome
  exato do rótulo/setor): opção válida → envia a **confirmação** (`{setor}`) e
  **encaminha para o setor** (`pending`/**aguardando**, sem atribuir a ninguém, **sem
  divisória** — quem assumir vê o histórico do menu); opção inválida → reexibe o menu
  e **conta a tentativa** (`Conversation.ai_turns`).
- Ao atingir `max_attempts` tentativas inválidas → **avisa** (`handoff_message`) e
  encaminha para o `fallback_sector` ou, em último caso, um setor "Geral" **criado na
  padrão** (`Sector.ensure_general()`) — igual à IA, a conversa **nunca fica órfã** sem
  setor.
- **Guardas** (`_should_handle` + `_human_replied_in_segment`): pula se o modo não é
  `menu`, se é grupo, `closed`, já tem setor/atendente, ou se um **humano já respondeu**
  no atendimento atual. Estado por segmento (após a última divisória): `_menu_already_presented`
  detecta se o menu já foi enviado (mensagem `out` automática `is_ai=True`).

**Telas (área Configurações → abas):** a barra `_settings_tabs.html` (+ `settings_tabs.css`)
dá as abas **[WhatsApp] [Atendimento]**; a aba Atendimento tem o **seletor de modo**
no topo (endpoint `atendimento-mode`, POST) e as sub-abas **[Chatbot de menu]
[Inteligência (IA)]**. A tela do chatbot (`atendimento_view`, `configuracoes/atendimento/`,
`chatbot_settings.html` + `chatbot_settings.css`, escopo `.chatbot-settings-page`, **só
ADM**) edita saudação/intro/opções (editor de linhas rótulo+setor com add/remove/renumerar
por JS)/mensagens/tentativas/fallback e mostra a **prévia do menu**. Tem o botão
**"Preencher automaticamente"** (JS): cria uma opção por **setor cadastrado** (rótulo =
nome do setor) e preenche todos os textos com o padrão (dados via `json_script`
`sectors-data`/`defaults-data`), para o ADM só ajustar e salvar. As opções são
reconstruídas no save a partir dos arrays `option_label[]`/`option_sector[]`
(`_save_menu_options`, linhas vazias ignoradas, renumeradas por ordem). O chatbot vem
**desligado** por padrão.

## 15. Permissões de menu (`accounts/permissions.py`)

Controla **quais botões da barra lateral cada perfil vê e acessa** — não é só
visual: as views são gateadas (`require_feature` / `user_can_access`), então
esconder o botão também bloqueia a URL.

> **Vale também para os endpoints AJAX** (`require_feature_json`): a tela e a URL de
> dados que a alimenta passam pela **mesma** guarda. Isso já foi um buraco real —
> `conversation-list` não tinha gate nenhum, então quem tinha o botão Conversas
> removido levava 403 na tela e continuava recebendo por aquela URL a lista completa,
> **com a prévia da última mensagem** de cada conversa. Ao criar endpoint novo,
> começar pela guarda, não deixá-la para depois (testes em
> `AjaxEndpointsRespectMenuPermissionsTests`).

- **Features** (botões reais, com ícone) em `MENU_FEATURES`: `dashboard`,
  `conversations`, `contacts`, `attendants`, `sectors`, `settings`. O botão
  **Permissões** (`permissions`) é exclusivo do ADM e o botão **Clientes**
  (`clients`) é exclusivo do **gestor master**; os dois ficam **fora** da matriz de
  toggles. Os placeholders antigos (Atendimentos/Campanhas/Relatórios) foram
  **removidos** do menu.
- **Gestor master**: vê **somente** o botão Clientes; nenhuma feature de atendimento
  fica liberada para ele e ele não enxerga conversa nenhuma. Ver seção 16.
- **Administrador**: sempre **acesso total** — **dentro da empresa dele** (não
  editável, para nunca se trancar fora).
- **Padrão** dos demais (`DEFAULT_ROLE_KEYS`): `usuario`/`leitor` = `conversations` +
  `contacts` (sem Dashboard). Ajustável na tela.
- **Aba Grupos**: ao ler as liberações de um grupo, usar `access.sectors.all()` — e
  **não** `.values_list()`, que ignora o `prefetch_related` e emite consulta nova
  (duas por grupo), anulando o prefetch.
- **Efetivo por usuário** (`allowed_keys_for`): adm → tudo; senão a personalização do
  usuário (`UserMenuPermission`, se houver) **sobrepõe** o padrão do perfil
  (`RoleMenuPermission` ou o padrão do código).
- **Landing pós-login**: quem não tem Dashboard cai na 1ª tela disponível
  (`first_landing_url_name`; `dashboard_view` redireciona).
- **Tela Permissões** (`permissions_view`, rota `permissoes/`, `permissions.html` +
  `permissions.css`, **só ADM**) — em **abas** (`Perfis` / `Botões do perfil` /
  `Visualização de conversas` / `Grupos`; aba padrão = Perfis; chaves internas
  `people`/`botoes`/`visualizacao`/`grupos` no `?tab=`):
  - **Perfis**: define o **papel de cada pessoa** (`adm`/`usuario`/`leitor`). Lista
    todos os usuários ativos (avatar + nome + e-mail) com um **seletor visual de 3
    pílulas** (👑 Administrador · 🎧 Usuário · 👁️ Leitor), a ativa colorida por perfil.
    Clicar salva na hora (AJAX `form_type=profile-role`, otimista com reversão em erro
    + toast). **Guardas:** o admin **não pode alterar o próprio perfil** (pílulas
    desabilitadas + selo "você") e há a rede de segurança "deve existir ≥1 admin".
    Promover a `adm` provisiona o atendente/setores via sinal (ver seção 3). É o
    **único lugar** onde se troca o papel pela interface. **Nota:** a edição de
    atendente (tela Atendentes) **não mexe mais no `role`** — o papel é definido só
    aqui (antes o edit forçava `usuario` e apagaria a escolha).
  - **Botões do perfil**: toggles por perfil (Administrador travado como "acesso
    total") + seção "Personalizar um usuário" (select → toggles). O select de
    usuário (form GET) e os redirects de salvar/resetar a personalização **preservam
    a aba** (`?tab=botoes&user=<id>`) — selecionar um usuário não joga mais de volta
    para a aba Perfis. *(O "Ver conversa inteira" saiu daqui — virou a aba
    Visualização de conversas.)*
  - **Visualização de conversas**: controla, **por setor** e com **exceção por
    usuário**, DUAS coisas (ver subseção "Separação das conversas" abaixo): (1) o
    **Alcance** — quais conversas a pessoa enxerga, num **slider de 4 níveis** (barra
    "menos → mais visualização": `own` / `sector_open` / `sector_all` / `all`, em
    `ConversationViewScope`; o slider grava o valor num `<input hidden>` e o rótulo do
    nível aparece embaixo — ver `scope_levels` via `json_script` + JS `[data-scope-slider]`);
    (2) **Ver conversa inteira** (`view_full_history`) — todo o histórico do chat ou só o
    atendimento atual. **Bloco "Por setor"** (`form_type=view-sectors`): um cartão por
    setor com o slider de Alcance + toggle "Ver conversa inteira", gravados em
    `Sector.view_scope`/`Sector.view_full_history`. **Bloco "Personalizar um usuário"**
    (`form_type=view-user`, com `view-user-reset`): select da pessoa (preserva a aba,
    `?tab=visualizacao&user=<id>`) → Alcance (checkbox **"Herdar do setor"** + o slider) e
    Ver conversa inteira (**Herdar** / Sim / Não), gravados em
    `UserConversationView` (campos nulos = herdar; sem nenhuma personalização a linha
    é removida). Salva automático (o autosave do JS agora dispara em `<select>` também).
  - **Grupos**: lista os grupos detectados (Conversation `chat_type='group'`) e libera
    cada um por **setor** e/ou **usuário** (grava em `GroupAccess`); botão **"Atualizar
    nomes"** chama `conversation-sync-groups` (nome real do grupo via W-API). O **nome
    do grupo é editável inline** (campo por grupo, `form_type=group-name` → `Conversation.name`;
    o JID vem como subtítulo) para corrigir quando a W-API não traz o nome. Botão
    **"Remover"** (X) por grupo apaga a conversa do grupo (`form_type=group-remove`).
    A lista de grupos é **dirigida por mensagem recebida** (um grupo aparece quando
    chega mensagem dele; não vem do `get-all-groups`), então grupos onde o número saiu
    podem ser removidos daqui; se chegar nova mensagem, o grupo reaparece.
  **Sem botão "Salvar"**: as alterações (perfis, botões, visualização e grupos) são
  **salvas automaticamente** ao clicar/alterar (fetch AJAX → `permissions_view`
  responde JSON quando `X-Requested-With`; toast de confirmação).
  `build_nav_items(user, active_label)` monta o menu a partir dessas regras.

### Perfil SOMENTE LEITURA (`leitor`)
- `is_read_only(user)` (`accounts/permissions.py`) = `role == 'leitor'`. O leitor
  **enxerga** as telas liberadas em "Botões do perfil", mas **não executa nenhuma
  ação que altere dados**. Enforçado no **backend** (autoritativo) e escondido no
  **frontend** (UX).
- **Backend:** `deny_readonly_json(request)` (endpoints AJAX) e `block_readonly(request)`
  (telas de formulário) retornam **403** para leitor em: enviar texto/mídia, assumir,
  encerrar, transferir, nomear contato, sincronizar grupos, salvar organização de
  setores, CRUD de contatos/atendentes/setores e salvar Configurações (W-API/IA/
  chatbot/modo). O que é **só leitura (GET)** — abrir Conversas, mensagens, listas —
  continua liberado.
- **Frontend:** `conversations_view`/`contacts_view` passam `read_only` ao template.
  Em Conversas, `.conv-body.is-readonly` esconde o **composer**, a caixa de
  **transferência** e os botões **Assumir/Encerrar**, e mostra uma barra
  "👁️ Perfil somente leitura" (`conversations.css?v=27`). Em Contatos, somem
  **Novo contato** e as ações **Editar/Excluir**.
- Quais **botões** o leitor vê continua vindo de "Botões do perfil" (o admin habilita).
  Ou seja: o admin escolhe **onde** o leitor entra; o perfil garante que ali ele
  **só visualiza**.

### Separação das conversas (quem vê quais chats) — configurável
- `visible_conversations(user, qs)` / `can_see_conversation(user, conv)` /
  `visible_conversations_q(user)` em `accounts/permissions.py`. **Primeiro filtra pela
  EMPRESA do usuário** (multiempresa, seção 16): o **gestor master** e quem está sem
  empresa não veem **nada**; o **admin vê tudo da empresa dele**. Para não-admin, as
  **diretas** dependem do **Alcance efetivo** (`effective_view_scope`, configurado na
  aba Visualização de conversas):
  - `own` → só as diretas **atribuídas a ele** (qualquer status);
  - `sector_open` → atribuídas a ele **OU** do(s) setor(es) dele **E não fechada**
    (**padrão de fábrica** = comportamento histórico: cada um só vê os PRÓPRIOS
    finalizados);
  - `sector_all` → atribuídas a ele **OU** do(s) setor(es) dele (**inclui finalizadas
    de outros** do setor);
  - `all` → **todas** as conversas diretas, de qualquer setor.
  Os **grupos** **independem do Alcance**: seguem sempre a liberação individual da aba
  Grupos (`GroupAccess`: por setor OU por usuário). Um usuário novo/sem setor e escopo
  padrão **não vê nada** ("zerado").
- **Alcance efetivo** (`effective_view_scope`): admin → `all`; senão a personalização
  do usuário (`UserConversationView.view_scope`, se definida) > o **mais permissivo**
  entre os setores dele (`Sector.view_scope`) > padrão de fábrica (`sector_open`).
  Ordem de permissividade em `VIEW_SCOPE_RANK`.
- Aplicado na lista (`conversations_view`, `conversation_list_view` — inclusive os
  contadores) e nas ações (`conversation-messages/send/take/transfer/close/send-media`
  retornam 403 se o usuário não pode ver a conversa).
- **Escopo do histórico** (`history_full_for`): ao abrir uma conversa, quem não tem
  "Ver conversa inteira" vê só o **atendimento atual** (mensagens a partir da última
  divisória); admin vê tudo. Fonte: exceção do usuário
  (`UserConversationView.view_full_history`, se definida) > algum setor dele com
  `Sector.view_full_history=True` > padrão `False`. *(Antes vinha de
  `RoleMenuPermission`/`UserMenuPermission.full_history`; essas colunas ficaram
  removidas na migração `0038` — o controle migrou para a aba Visualização de
  conversas.)*

## 16. MULTIEMPRESA (SaaS): empresas clientes + gestor master

O BEEonBOARD atende **várias empresas na mesma instalação**. Cada empresa cliente
(`Company`) é uma "instância" do sistema: tem os **seus** setores, atendentes,
contatos, conversas, mensagens e as **suas próprias** credenciais de W-API e GPT.

**Decisões de arquitetura (fechadas com o usuário):**

| Decisão | Escolha |
|---|---|
| Isolamento | **Banco único com vínculo de empresa** (todo registro aponta para a `Company` dona e as consultas filtram por ela). Funciona com o SQLite atual, um só deploy/migração |
| Acesso | **Mesma URL** (`/beeonboard/`): o login já define a empresa. Sem DNS/subdomínio por cliente |
| Gestor master | **Perfil novo `master`** (acima do Administrador), com a tela **Clientes** |
| Privacidade | O master **administra e exporta, mas NÃO lê as conversas** dos clientes (LGPD) |

### Model `Company` (empresa cliente)

Campos: `name` (nome fantasia), `legal_name` (razão social), `document` (CNPJ, guardado
**só em dígitos**), `slug` (identificador curto único — será a base da URL própria de
webhook na Parte 2), `email`, `phone`, `address`, `city`, `state`, `logo`
(**`FileField`**, não `ImageField`: evita depender do pacote Pillow — a validação de
extensão/tamanho fica no `CompanyForm`), `accent_color`, `notes`, `is_active`,
`is_default`.

Propriedades: `display_name`, `initials`, `status_label`, `formatted_document`,
`formatted_phone`, `location`, `logo_url`. `Company.get_default()` devolve a
**empresa padrão** (a que recebeu tudo o que existia antes do multiempresa); ela
**não pode ser excluída nem desativada**.

### O que ganhou vínculo de empresa

FK/OneToOne `company` **obrigatório** em: `Attendant`, `Sector`, `Contact`,
`Conversation`, `WapiWebhookEvent`, `RoleMenuPermission`, `WapiConfiguration` e
`MenuBotConfiguration`.

> **`OpenAiConfiguration` NÃO tem empresa**: o GPT é **uma configuração da
> plataforma** (a API Key é do master, que paga a conta da OpenAI). Ela nasceu por
> empresa na Parte 1 e voltou a ser única na migração `0032` — ver "O que é técnico
> não fica com o cliente", mais abaixo.

`User.company` é **opcional de propósito**: **nulo = gestor master** (fica acima das
empresas). Todo usuário operacional (`adm`/`usuario`/`leitor`) tem empresa.

**Não** ganharam campo próprio (a empresa é derivada, para não duplicar dado nem ter
que sincronizar em cada criação): `Message` (via `conversation`), `MenuOption` (via
`config`), `GroupAccess` (via `conversation`), `UserMenuPermission`,
`UserConversationView` e `PasswordResetCode` (via `user`).

### Unicidades que mudaram de global para POR EMPRESA

- `Sector.name` → `UniqueConstraint(company, name)` — duas empresas podem ter cada
  uma o seu setor "Financeiro".
- `Contact.phone` → `UniqueConstraint(company, phone)` — o mesmo cliente final pode
  falar com duas empresas, e cada uma tem o seu próprio cadastro/nome.
- `RoleMenuPermission.role` → `UniqueConstraint(company, role)` — cada cliente define
  os próprios botões por perfil.

> `User.email` continua **único global** (é a chave de login).

### Configurações: o que é por empresa e o que é da plataforma

| Configuração | Escopo | Como buscar |
|---|---|---|
| **WapiConfiguration** (instância + token do WhatsApp) | **por empresa** | `WapiConfiguration.for_company(company)` |
| **MenuBotConfiguration** (chatbot de menu + `mode`) | **por empresa** | `MenuBotConfiguration.for_company(company)` |
| **OpenAiConfiguration** (API Key do GPT, modelo, prompt, consumo) | **UMA da plataforma** | `OpenAiConfiguration.get_solo()` |

`WapiConfiguration.get_solo()` sobrevive só como compatibilidade (devolve a da
empresa padrão); código novo usa `for_company`. Já `OpenAiConfiguration.get_solo()`
**é a forma correta** — é a configuração única (busca a primeira linha por id, não
`pk=1` fixo, porque a migração `0032` pode ter mantido outro id).

`OpenAiConfiguration.record_usage()` / `record_last_exchange()` deixaram de ser
`classmethod` (que gravavam fixo em `pk=1`) e passaram a ser **métodos de instância**.

### `accounts/tenancy.py` (novo)

Concentra as regras de "quem é o master" e "qual é a empresa da requisição":
`is_master(user)`, `user_company(user)`, `current_company(request)`,
`set_active_company(request, company)` (empresa em que o master entra para dar
suporte, guardada na sessão — uso efetivo na Parte 2), `scoped(queryset, company)`
(**sem empresa não devolve nada** — a falta de empresa nunca pode virar "ver tudo"),
`require_master(request)` e `deny_master_json(request)`.

Em `views.py`, `request_company(request)` é o atalho usado em todo ponto de criação
(retaguarda para a empresa padrão, para nunca gravar registro sem empresa).

### Perfil `master` (`accounts/permissions.py`)

- `CLIENTS_ITEM` = botão **Clientes**, **fora** da matriz de toggles (nenhum perfil de
  cliente pode receber esse botão).
- `nav_items_for(master)` devolve as telas da plataforma ("Clientes", "Métricas",
  "Inteligência (IA)" e "Gestores") e, no modo suporte, acrescenta **"WhatsApp"**
  (`WHATSAPP_ITEM`);
  `allowed_keys_for(master)` é **vazio** e `user_can_access(master, <qualquer feature
  da empresa>)` = **False**, também dentro do painel do cliente.
- `first_landing_url_name(master)` = `clients` (e `dashboard_view` já redireciona
  quem não tem Dashboard).
- `visible_conversations(master, ...)` = **vazio** e `can_see_conversation(master, ...)`
  = **False** — o master não lê conversa nenhuma.
- `visible_conversations` passou a **filtrar pela empresa do usuário** antes de aplicar
  o Alcance (inclusive para o admin, que agora vê tudo **da empresa dele**, não do
  sistema). Usuário sem empresa não vê nada.
- `role_allowed_keys(role, company)` recebe a empresa (a linha de permissão é por
  empresa).

### Tela **Clientes** (`clients_view`, rota `clientes/`, só master)

`templates/accounts/clients.html` + `static/css/clients.css` (escopo
`.clients-page`/`.clients-modal`; reaproveita `dashboard.css` e os botões de
`attendants.css`).

- **Resumo**: empresas cadastradas / ativas.
- **Busca** por nome, razão social, CNPJ ou cidade.
- **Lista em cartões** com logo (ou iniciais na cor de destaque), nome, razão social,
  selos **Ativa/Inativa** e **Padrão**, dados cadastrais e os contadores reais de
  **usuários** e **conversas** (o master vê o tamanho do cliente, não o conteúdo).
  Os dois contadores vêm de **`Subquery`**, não de dois `Count` no mesmo `annotate`:
  `Count` sobre relações diferentes na mesma consulta faz o banco cruzar usuários ×
  conversas de cada empresa (o `distinct=True` corrige o número, não o custo).
- **Ações**: Editar, Desativar/Reativar e Excluir. A **empresa padrão** não tem
  Desativar nem Excluir (bloqueado também no backend). A exclusão avisa que apaga
  todos os dados e sugere exportar antes (Parte 3).
- **Modal** com o `CompanyForm` renderizado pelo servidor (tem upload de logo e erros
  por campo). "Editar" recarrega com `?editar=<id>`, o que mantém o modal preenchido
  mesmo quando a validação falha. Logo aceita **PNG/JPG/WEBP/SVG até 2 MB**; CNPJ
  exige 14 dígitos; telefone exige DDD; UF vira maiúscula; `slug` é **gerado pelo
  nome** quando fica em branco.
- Layout responsivo: cartão vira coluna única abaixo de 900px e o formulário vira
  uma coluna abaixo de 620px — sem rolagem horizontal.

### Marca do cliente na barra lateral

- `accounts/context_processors.py` (**novo**, registrado em `settings.TEMPLATES`)
  fornece `brand` em **todas** as telas: logo e nome **da empresa** de quem está
  logado; sem logo, as **iniciais** da empresa na **cor de destaque** dela. O master
  vê a marca do BEEonBOARD com o rótulo "Gestão de clientes".
- A barra lateral estava **copiada em 8 templates**; virou o include
  **`templates/accounts/_sidebar.html`** (única fonte). `.sidebar-initials` em
  `dashboard.css?v=6`.
- **Contraste das iniciais**: a cor do texto **não** é fixa no CSS — vem de
  `Company.accent_text_color`, que usa `readable_text_color()` (em
  `accounts/models.py`) para escolher entre texto claro e escuro pelo **maior
  contraste real** (razão WCAG) contra a cor de destaque cadastrada. Motivo: o master
  escolhe a cor livremente e um cliente cadastrado com **`#000000`** ficava com as
  iniciais **pretas sobre preto** (invisíveis) na barra lateral. Um limiar fixo de
  luminância não resolvia: verde claro recebia texto branco, que lê pior que o
  escuro. A mesma cor calculada é aplicada nas iniciais do **cartão da tela
  Clientes** e no **logo da tela de Métricas do cliente**, e `.sidebar-initials`
  ganhou um **anel interno claro** para o chip não desaparecer no azul-escuro da
  barra quando o destaque é muito escuro.
  *(As iniciais são a retaguarda de quem não tem logo. O logo é cadastrado pelo
  **próprio ADM da empresa** na aba **Marca** de Configurações — ver a subseção
  "Aba Marca" adiante — e o master também pode fazê-lo em Clientes → Editar.)*

### Migração `0031_multiempresa`

Roda em 4 fases para **não perder nada em produção**: (1) cria `Company`; (2) adiciona
`company` **nulo** em todos os models; (3) **cria a "Empresa padrão" e aponta TODOS os
registros existentes para ela** (usuários, atendentes, setores, contatos, conversas,
eventos de webhook, permissões de perfil e as três configurações); (4) torna o campo
**obrigatório** e troca as unicidades globais pelas unicidades por empresa.

Depois dela o sistema funciona **exatamente como antes**: existe uma única empresa e
todo mundo está nela.

### Como criar o gestor master

**O normal é pela tela Gestores** (seção abaixo): um master cadastra outro. O shell
serve só para o **primeiro** master de uma instalação nova (ou para destravar quem
perdeu o acesso sem WhatsApp cadastrado):

```bash
venv/bin/python manage.py shell -c "
from accounts.models import User
User.objects.create_user(email='SEU-EMAIL', password='SUA-SENHA', role=User.Role.MASTER,
                         recovery_phone='5511999999999')
"
```
> Use o **seu** e-mail e uma senha forte de verdade — o exemplo acima já foi copiado
> literalmente uma vez, criando um master `master@seudominio.com` com senha pública em
> produção. O master fica **sem empresa** (`company=None`), que é o que o coloca acima
> das empresas; depois de logar, cai direto na tela **Clientes**.

### Tela Gestores (`templates/accounts/masters.html` + `masters.css`)

`gestores/` (`masters_view`, nome `masters`), **exclusiva do master** (`require_master`
→ 403 para qualquer perfil de cliente, inclusive por POST forjado). É o menu
`MASTERS_ITEM`, ao lado de Clientes e Inteligência (IA).

- **Novo gestor** (`action=create`, `MasterUserForm`): nome, e-mail, **senha inicial
  (mínimo 8)** e **WhatsApp — obrigatório**. Cria `role=master`, `company=None`,
  `recovery_phone` preenchido e `must_change_password=True`.
- **Acesso** (por cartão): `save-phone` (troca o WhatsApp de recuperação) e
  `reset-password` (nova senha inicial; volta a marcar troca obrigatória — senha
  definida por outra pessoa é sempre provisória).
- **Travas no backend**, não só na tela: ninguém **se** desativa ou **se** exclui;
  **nunca sobra zero master ativo** (a plataforma ficaria sem dono); **excluir exige
  desativar antes**, a mesma ordem da tela Clientes.

> **Por que o WhatsApp é obrigatório aqui:** o master não tem empresa nem `Attendant`,
> que é de onde saía o telefone de recuperação de todo mundo. Sem `recovery_phone`,
> senha perdida só se resolve pelo shell do VPS.

### Isolamento completo (Parte 2 — CONCLUÍDA)

O sistema já pode atender **vários clientes de verdade ao mesmo tempo**: cada um com o
seu WhatsApp, a sua IA e os seus dados, sem se ver.

#### Webhook por cliente (`resolve_webhook_company`)

Rotas: **`webhook/wapi/<empresa>/`** (recomendada — `wapi-webhook-company`) e a antiga
`webhook/wapi/` (mantida). A empresa é identificada em **três degraus**:

1. **identificador na URL** (o `slug` da empresa) — cada cliente cadastra na W-API a
   URL própria dele, que a tela WhatsApp/W-API já exibe pronta;
2. **`instanceId` do payload**, casado com o `instance_id` cadastrado na tela da
   empresa — cobre quem ainda usa a URL antiga;
3. **empresa padrão**, última retaguarda, para a instalação de um único cliente
   continuar funcionando sem reconfigurar nada.

**Empresa inativa não recebe**: o webhook responde 404 e **nada** é criado (nem o
evento bruto). O **token de webhook é validado por empresa** (`WapiConfiguration`
daquele cliente), e a validação acontece **depois** de identificar a empresa.

#### O `.env` não empresta credencial para cliente nenhum

`WapiConfiguration.resolved_instance_id()` / `resolved_token()` /
`resolved_webhook_token()` caem para `WAPI_INSTANCE_ID` / `WAPI_TOKEN` /
`WAPI_WEBHOOK_TOKEN` **somente quando a empresa é a padrão**
(`usa_credencial_do_ambiente`).

Motivo: essas variáveis são herança da época de **um cliente único**, e a empresa
padrão é a dona de tudo o que existia antes do multiempresa — para ela o fallback é o
comportamento certo e mantém uma instalação antiga funcionando sem reconfigurar nada.
Para **qualquer outra** empresa o fallback era perigoso: um cliente novo, ainda sem
credencial cadastrada, mandaria mensagem pela instância do `.env` — **pelo WhatsApp de
outro cliente**. Isso anulava justamente a garantia construída ao tornar `company`
obrigatório em `wapi/client.py`. Sem credencial própria o certo é não enviar nada, e a
tela Atendimento já mostra "WhatsApp ainda não configurado".

O `manage.py check` avisa (**`beezap.W002`**) quando o `.env` tem essas variáveis
preenchidas e existe mais de uma empresa cadastrada.

#### `company` obrigatório no cliente da W-API

Em `wapi/client.py` todas as funções públicas (`send_text_message`,
`send_image_message`, `send_audio_message`, `send_video_message`,
`send_document_message`, `download_media`, `get_all_groups`) recebem **`company`
obrigatório e somente-nomeado** (`*, company`). `_company_config()` **levanta erro**
se vier `None`. É uma escolha deliberada: deixar a empresa implícita mandaria a
mensagem pelo WhatsApp de outro cliente, então o código não permite mais isso —
qualquer chamada esquecida falha na hora em vez de errar em silêncio.

O envio sempre usa `conversation.company`; o download de mídia usa
`message.conversation.company`.

#### IA e chatbot

- `gpt/client.chat_completion(...)` e `test_connection()`: **sem empresa** — a API
  Key, o modelo e o contador de tokens são da **plataforma** (`get_solo()`).
- `gpt/attendant.py` é escopado por empresa no que é **dado do cliente**:
  `available_sectors(company)`, `available_attendants(company)`,
  `build_system_prompt(config, company, ...)`, `_match_sector(name, company)`,
  `_match_attendant(name, company)` e `_resolve_fallback_sector(company)`.
  **Isto era um vazamento real:** sem o escopo, a IA listaria no prompt (e ofereceria
  ao cliente final) os setores e atendentes de outra empresa.
- `_resolve_fallback_sector(company)` usa o **`fallback_sector` do chatbot daquela
  empresa** e, na falta dele, o setor **Geral** dela — a conversa nunca fica sem fila.
- `chatbot/handler.py`: textos, opções, modo e fallback vêm de
  `MenuBotConfiguration.for_company(conversation.company)`.
- `wapi.services._maybe_trigger_reception` lê o **modo** da empresa da conversa: um
  cliente pode usar IA enquanto outro usa o chatbot de menu ou nada.

#### O que é TÉCNICO não fica com o cliente

Regra de produto: **credencial e custo são do gestor master**; o cliente configura
apenas o que é conteúdo do negócio dele.

| Configuração | Escopo | Quem edita | Onde |
|---|---|---|---|
| **WhatsApp (W-API)** — instância + token | **por empresa** | **só o master**, dentro do painel do cliente | aba WhatsApp (`wapi-settings`) |
| **Inteligência (IA)** — API Key, modelo, prompt, limite, consumo | **UMA da plataforma** | **só o master** | menu do master → Inteligência (IA) (`openai-settings`) |
| **Chatbot de menu** — saudação, opções → setor, mensagens, tentativas, fallback | por empresa | **o cliente** (ADM) | Configurações (`atendimento`) |
| **Modo de primeiro atendimento** — desligado / chatbot / IA | por empresa | **o cliente** (ADM) | seletor no topo de Configurações |

Por que assim: o token da W-API e a API Key do GPT são credenciais (e a do GPT gera
**custo na conta do master**), então não ficam ao alcance do cliente. Já a saudação e
as opções do menu são texto do negócio — se dependessem do master, cada troca de
palavra viraria pedido de suporte.

**Uma API Key para todos os clientes.** A configuração do GPT é única: o master
cadastra a chave uma vez e ela atende todas as empresas. Cada cliente só decide **se**
o primeiro atendimento dele usa a IA. A tela do master mostra quantas empresas ativas
estão com a IA ligada. O que continua sendo **por empresa** na IA são os dados do
atendimento: os **setores e atendentes** que entram no prompt e o **setor de
fallback** — sempre os da empresa daquela conversa.

**Como isso aparece no menu:**

- **Master, fora do painel de cliente**: `Clientes` + `Métricas` +
  `Inteligência (IA)` + `Gestores` (`MASTER_ONLY_ITEMS` em
  `accounts/permissions.py`).
- **Master, dentro do painel de um cliente** (modo suporte): o acima + **`WhatsApp`**
  (`WHATSAPP_ITEM`, aponta direto para `wapi-settings`) — **e nada mais**. Setores,
  Atendentes, Permissões e Atendimento são do negócio do cliente e ficam com o **ADM
  dele**.
- **Cliente (ADM)**: `Configurações` aponta para **`atendimento`** — não existe mais
  aba WhatsApp nem sub-aba IA para ele. A barra de abas (`_settings_tabs.html`) só
  renderiza a aba WhatsApp quando `brand.is_master`.

**Enforçado no backend, não só escondido:** `wapi_settings_view` usa
`require_master_in_company(request)` (403 para qualquer perfil de cliente; e um
redirect amigável para *Clientes* quando o master ainda não escolheu a empresa) e
`openai_settings_view` usa `require_master(request)`. Um POST forjado pelo cliente
não grava credencial nenhuma — há teste para os dois casos.

**Status para o cliente (sem credencial).** `build_service_status(company)` monta dois
avisos na tela Atendimento: **"WhatsApp conectado"** / *"ainda não configurado"* e
**"Inteligência (IA) disponível"** / *"indisponível"*, com a orientação de falar com o
administrador da plataforma. Verde = pronto, âmbar = pendente
(`.service-status` em `settings_tabs.css?v=2`). Nenhum Instance ID, token ou API Key
aparece ali — há teste garantindo isso.

#### Telas escopadas por empresa

Todas as consultas passam pela empresa de quem está logado (`request_company(request)`):

| Tela | O que é escopado |
|---|---|
| Contatos | lista, busca, contagem, edição e exclusão |
| Setores | lista, edição, exclusão e o arrastar-e-soltar (com a re-inclusão dos admins) |
| Atendentes | lista e edição (id de outro cliente dá 404) |
| Permissões | pessoas, padrões por perfil, personalização por usuário, setores da aba Visualização e grupos |
| Dashboard | todos os indicadores |
| Conversas | já vinha de `visible_conversations`, que agora filtra por empresa antes do Alcance |
| Transferência | selects **e** o POST só aceitam setor/atendente da mesma empresa |
| Configurações | credenciais (só master), textos do chatbot, modo, URL de webhook e eventos exibidos |
| Meus dados (exportação) | o ZIP sai da empresa **de quem está logado** — o endpoint não aceita id de empresa |
| Marca (logo e cor) | a empresa vem **de quem está logado**; o endpoint não aceita id de empresa |

Detalhes que também mudaram: os **selects de setor de fallback** (IA e chatbot) só
listam setores da empresa (`CompanyForm`-style `company=` no `__init__` dos forms); a
**validação de nome de setor repetido** (`SectorForm`) e a de **telefone duplicado**
valem dentro da empresa; a resolução de nomes nas **mensagens de grupo**
(`_build_name_map`) usa apenas contatos da própria empresa; e a regra **"deve existir
ao menos um administrador"** passou a valer **por empresa**.

#### Acesso do cliente (criado pelo master)

Na tela **Clientes**, cada empresa sem acesso mostra **"Criar o acesso do cliente"**
(`action=create-admin`, `CompanyAdminForm`): nome, e-mail, senha inicial e WhatsApp.
Cria o **Administrador** da empresa (`role=adm`, `company=<empresa>`), provisiona o
`Attendant` dele com **`must_change_password=True`** (o `InitialPasswordChangeMiddleware`
força a troca no primeiro acesso) e garante o setor **Geral** da empresa nova. O
**e-mail é único no sistema inteiro** (é a chave de login), não por empresa. Empresas
que já têm acesso mostram a lista de administradores no cartão.

#### Modo suporte ("Entrar no painel do cliente")

`action=enter` grava a empresa na sessão (`tenancy.set_active_company`) e leva o master
para a tela WhatsApp/W-API daquele cliente. Nesse modo ele alcança **somente essa
tela** (`WHATSAPP_ITEM` em `accounts/permissions.py`) — a instância e o token da W-API,
a única parte **técnica**, com credencial, que não pode ficar na mão do cliente.

**Nenhuma feature da empresa fica liberada para o master**: `user_can_access` devolve
`False` para toda `key` de `MENU_FEATURES` quando o perfil é `master` — Setores,
Atendentes, Permissões e Atendimento (chatbot + modo de primeiro atendimento) são o
**negócio do cliente** e ficam com o **ADM dele**. Quem protege a tela do WhatsApp não é
a matriz de features e sim `views.require_master_in_company` (ser master **e** estar
dentro do painel).

**Conversas, Contatos e Dashboard** nunca estiveram abertos: são os dados pessoais dos
clientes finais da empresa, e a regra do projeto é que o master administra sem ler o
atendimento de ninguém. `visible_conversations` continua devolvendo **vazio** para o
master, inclusive no modo suporte.

A barra lateral e a tela Clientes mostram um **aviso âmbar** ("Modo suporte — você está
no painel de X") com o botão **Sair do painel** (`action=leave`), para o master nunca
confundir de quem é o painel que está vendo. `nav_items_for(user, label, in_company=)`
recebe esse estado (é ele que acrescenta o botão WhatsApp), e
`build_nav_items(user, label, request)` o calcula por `master_in_company(request)`.
`user_can_access(user, key)` **não** recebe mais `in_company`: o estado deixou de mudar
o que o master pode.

#### O que o master NÃO alcança (privacidade) — e o que ele vê no lugar

Regra: **o master administra os clientes e não lê o atendimento deles** — nem o de
uma empresa, nem o de outra, em nenhuma tela e por nenhuma URL.

| O que | Como está fechado |
|---|---|
| Conversas e mensagens | `visible_conversations`/`can_see_conversation` devolvem vazio/False para o master (inclusive no modo suporte) **e** os endpoints AJAX chamam `deny_master_json` + `require_feature_json` — duas trancas independentes |
| **Nomear contato pelo chat** | `conversation-name-contact` passa por `deny_master_json` + `require_feature_json('contacts')`. Antes não tinha guarda nenhuma: o master gravava contato dentro da empresa do cliente por POST, enquanto a tela Contatos já dava 403 para ele |
| **Texto das mensagens na tela WhatsApp** | o painel "Últimas mensagens que chegaram" mostra só **horário, direção e tipo** — nunca o texto, o telefone ou o nome do contato. Antes mostrava os três, na única tela que só o master abre (ver seção 4) |
| **Arquivos** (foto/áudio/vídeo/documento) | só saem por `message-media`, que usa `can_see_conversation` → **403 para o master**. O Nginx não publica mais `media/whatsapp/` (seção 4) |
| Contatos, Dashboard, Setores, Atendentes, Permissões e Atendimento | `user_can_access` devolve `False` para o master em **toda** feature da empresa — **403** inclusive no modo suporte e por POST forjado |
| **Nomes dos grupos** de WhatsApp | ficam na tela Permissões, que é do ADM da empresa: o master leva **403** na tela inteira e nos POSTs `groups`/`group-name`/`group-remove` |
| Uma empresa ver a outra | toda consulta passa por `company`; `scoped()` sem empresa não devolve nada |
| **Exportação (ZIP)** | é do **cliente** (aba Meus dados). `_deny_master_export` dá **403** para o master, inclusive no modo suporte |

**No lugar disso, ele tem as telas de Métricas**: a de **um cliente** (seção 5.0.1)
— mensagens enviadas/recebidas, atendentes, conversas por estado, saúde da conexão da
W-API, consumo de IA e quando foi a última mensagem — e a de **todos os clientes**
(seção 5.0.2), com a carteira inteira lado a lado. Números e datas, nunca conteúdo.

> **A API Key do GPT continua sendo UMA, da plataforma** (`OpenAiConfiguration`, tela
> Inteligência (IA)) — o master paga a conta. O que passou a existir é a **medição por
> empresa**: `CompanyAiUsage` guarda tokens e chamadas de cada cliente **por mês**
> (seção 3), então o master vê quem consome IA sem deixar de ter o total da conta.
> **Sem limite e sem bloqueio**: medir não trava o atendimento de ninguém.

#### Comandos de management por empresa

```bash
sync_wapi_group_names                  # todas as empresas ativas (uma chamada por empresa)
sync_wapi_group_names --empresa acme   # apenas uma
inspect_wapi_groups --empresa acme     # diagnostico da instancia daquele cliente
sync_wapi_events_to_conversations      # usa a empresa gravada em cada evento
link_lid_contacts --apply              # resolve o contato dentro da empresa da conversa
```

#### Como colocar um cliente novo no ar

1. **Clientes → Nova empresa**: dados, logo e cor.
2. No cartão da empresa, **Criar o acesso do cliente** (o Administrador dele).
3. **Entrar no painel** → aba **WhatsApp**: colar o Instance ID e o Token da W-API
   **daquele** cliente e copiar a **URL de webhook exibida** (já vem com o
   identificador da empresa) para cadastrar no painel da W-API.
4. **Sair do painel** — o trabalho do master acaba aqui. O Administrador do cliente
   entra com a senha inicial, troca a senha e monta a operação dele: **setores**,
   **atendentes**, **permissões** e o **chatbot/modo de atendimento**.

> O master **não** cria setor nem atendente para o cliente: essas telas dão **403**
> para ele, inclusive dentro do painel (ver "Modo suporte"). Se o cliente precisar de
> ajuda, o caminho é orientá-lo — o que o master resolve sozinho é o WhatsApp.

> A **API Key do GPT** não entra nesse passo-a-passo: é cadastrada **uma vez** no menu
> do master (**Inteligência (IA)**) e vale para todos os clientes.

> A **empresa padrão** continua sendo a dona de tudo o que existia antes do
> multiempresa e o destino do webhook sem identificador. Não pode ser excluída nem
> desativada.

### Aba Marca: o cliente cadastra o próprio logo

`configuracoes/marca/` (`company_brand_view`, nome `company-brand`;
`templates/accounts/company_brand.html` + `company_brand.css`), aba **Marca** em
Configurações, do **ADM da empresa**.

- Edita **só identidade visual**: `logo` e `accent_color` (form `CompanyBrandForm`).
  Nome, CNPJ e identificador continuam com o master, na tela Clientes.
- **Por que é do cliente:** logo e cor são a marca do negócio dele, não credencial.
  Antes só o master alcançava isso, então **trocar de logo virava pedido de suporte**
  — a mesma razão que já tinha deixado os textos do chatbot com o cliente.
- A tela mostra uma **prévia com o fundo real da barra lateral** (o menu é escuro, e é
  ali que o logo aparece), o botão **Remover logo** (volta para as iniciais) e as
  recomendações de formato: PNG/SVG **transparente**, **quadrado**, até **2 MB**.
- **Trocar ou remover apaga o arquivo antigo do disco** (`_remove_company_logo_file`):
  o Django só troca o valor no banco, então sem isso cada troca deixaria um arquivo
  órfão no servidor, sem ninguém conseguir alcançá-lo pela interface.
- **Bloqueios (backend, não só a aba escondida):** `require_feature('settings')` — que
  dá **403 para o gestor master**, inclusive por POST forjado — e `block_readonly`
  (o perfil `leitor` vê e não salva). A empresa vem **de quem está logado**, então não
  existe "trocar o logo do vizinho" por URL forjada.
- A validação de logo/cor é compartilhada com a tela Clientes pelo
  **`CompanyBrandFieldsMixin`** (`accounts/forms.py`) — uma regra só para os dois
  formulários (extensões `png/jpg/jpeg/webp/svg`, 2 MB, cor `#rrggbb`).

### Parte 3 — portabilidade (CONCLUÍDA)

**Quem exporta é o CLIENTE, não o master.** A documentação original previa o master
baixando o ZIP; isso foi revisto porque contraria a regra de que ele não acessa o
atendimento — um ZIP com todas as conversas seria ler tudo de uma vez. O master
apenas encerra o cliente com segurança.

**Aba "Meus dados"** (`configuracoes/dados/`, `company_data_view`, nome `company-data`;
+ `company-export` para o download). Fica em **Configurações**, ao lado de Atendimento,
para o **ADM da empresa**. O download é POST em `company_export_view`.

- **Bloqueios (backend, não só a aba escondida)**: `require_feature('settings')`,
  `_deny_master_export` (**403 para o master, inclusive no modo suporte**) e
  `block_readonly` (o perfil `leitor` não exporta). A empresa vem **de quem está
  logado** — o endpoint não aceita id de empresa, então não existe "exportar a do
  vizinho" por URL forjada.
- **`accounts/export.py`** monta o ZIP: `LEIA-ME.txt`, `empresa.json`, `setores.csv`,
  `atendentes.csv`, `usuarios.csv` (**sem senha nem hash**), `contatos.csv`,
  `conversas.csv`, `mensagens.csv` e a pasta `midias/`. CSV em **UTF-8 com BOM e
  separador `;`** (abre direto no Excel pt-BR). `mensagens.csv` liga-se a
  `conversas.csv` pela coluna `conversa_id`, e a coluna `arquivo` aponta para o
  arquivo dentro de `midias/` (renomeado para `<id_da_mensagem>-<nome_original>`,
  já que em disco o nome é um uuid).
- **Escala**: o ZIP é escrito num **arquivo temporário em disco** (não em memória) e
  as mensagens são percorridas com `iterator(chunk_size=500)` — anos de histórico não
  podem derrubar o gunicorn. Mídia que sumiu do disco é **pulada**, nunca derruba a
  exportação inteira.

**Encerrar cliente com segurança** (tela Clientes): a exclusão agora exige, no
backend, que a empresa esteja **desativada** ("desative antes, assim o atendimento
para primeiro e o cliente tem tempo de exportar") **e** que o master **digite o nome
exato** da empresa (`confirm_name`, comparado sem diferenciar maiúsculas). A empresa
padrão continua inexcluível. Ordem recomendada: **cliente exporta → desativar →
excluir**.

A exclusão também **apaga os arquivos de mídia do disco**
(`_delete_company_media_files`): o `delete()` em cascata limpa o banco, mas o Django
**não** remove o arquivo — sem isso, as fotos e documentos do cliente ficariam órfãos
no servidor para sempre, sem ninguém conseguir ver nem apagar pela interface. A
mensagem de sucesso informa quantos arquivos saíram. **O logo da empresa entra nessa
limpeza** (antes ficava para trás: a função só percorria `Message.media_file`), e
**trocar o logo por Clientes → Editar também apaga o antigo** — a aba Marca do
cliente já fazia isso, a tela do master não.
