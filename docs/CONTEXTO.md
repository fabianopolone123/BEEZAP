# Contexto do Projeto BEEZAP (handoff)

Documento único para retomar o projeto do zero (ex.: nova sessão do Claude/Codex).
Leia também: `CODEX_PADROES.md`, `GIT.md`, `HISTORICO.md`, `DEPLOY.md`,
`WAPI_MEDIA_INTEGRATION.md`.

---

## 1. Visão geral

- **BEEZAP**: sistema Django de atendimento/automação de WhatsApp via **W-API**.
- **Stack**: Django 5.2, Python 3.12, gunicorn, Nginx, SQLite (padrão) ou
  PostgreSQL (via `DATABASE_URL`).
- **Hospedagem**: VPS Linux, servido sob o prefixo de caminho **`/beezap/`**
  em `https://fabianopolone.com.br/beezap/`.
- **Idioma/UX**: interface em português, simples e didática; notificações via
  **toast** e **pop-up do desktop + som** nas Conversas; CSS por página; sem cursor
  piscando em elementos não editáveis.

## 2. Estrutura do código

```
config/            settings.py (env-driven), urls.py, wsgi.py
accounts/          app principal: models, views, urls, forms, admin, middleware,
                   backends, management/commands/, templates de accounts
wapi/              MÓDULO (não é app instalado): client.py, parser.py, services.py, formatting.py
gpt/               MÓDULO (não é app): client.py, attendant.py (atendente virtual IA)
chatbot/           MÓDULO (não é app): handler.py (chatbot de menu, sem IA)
static/css/        CSS por página (dashboard.css, conversations.css, wapi_settings.css, ...)
templates/         base.html + accounts/*.html
docs/              documentação (este arquivo, DEPLOY.md, etc.)
deploy/            deploy.sh, diag_static.sh, patch_nginx_beezap.sh, exemplos nginx/systemd
```

> `wapi/` é um módulo Python comum (importa `accounts.models`); **não** está em
> `INSTALLED_APPS`, por isso os models ficam em `accounts/models.py`.

## 3. Modelos (`accounts/models.py`) — migração atual: `0027`

- **User** (AbstractUser, login por e-mail; `role`: `leitor`/`usuario`/`adm`).
- **Attendant** (perfil de atendente, vínculo com User, troca de senha inicial).
  **Admin como atendente:** todo usuário `adm` ganha **automaticamente** um
  `Attendant` (via sinal em `accounts/signals.py` + backfill na migração `0025`) e é
  incluído em **todos os setores**, para poder **Assumir** atendimentos de qualquer
  fila sem criar/logar outra conta. Mantido em sincronia: ao salvar um usuário adm e
  ao criar/salvar um setor; a organização por arrastar-e-soltar dos setores re-inclui
  os admins. `conversation_take_view` também provisiona na hora (rede de segurança) e
  a edição de atendente **não rebaixa** um adm.
- **RoleMenuPermission** / **UserMenuPermission**: permissões de menu (quais botões
  cada perfil vê/acessa, e personalização por usuário) + `full_history` (ver a
  conversa inteira ou só o atendimento atual). Ver seção 15.
- **GroupAccess**: quem pode ver um grupo do WhatsApp (M2M com setores e usuários).
  Sem regra, o grupo só aparece para o admin. Ver seção 15.
- **Sector** (setores; M2M com Attendant; usado em transferência/roteamento manual).
  Na tela Setores, um atendente pode ficar em **vários setores** (fica sempre na
  coluna "disponíveis"; arrastar/"+ Adicionar" inclui, ✕ remove; cada card mostra
  "em N setores"). O selo **Admin/Administrador** identifica o admin em Setores e
  Atendentes.
- **PasswordResetCode** (recuperação de senha por código no WhatsApp).
- **WapiConfiguration** (singleton `get_solo()`): `instance_id`, `token`,
  `webhook_token`. Credenciais reais ficam **aqui (no banco)**, editadas na tela
  Configurações → WhatsApp/W-API. `resolved_*()` cai para env se vazio.
- **WapiWebhookEvent**: todo evento recebido do webhook (com `raw_payload`).
- **OpenAiConfiguration** (singleton `get_solo()`): `api_key`, `model`
  (padrão `gpt-4.1-nano`), `enabled`. Guarda a **API Key do GPT no banco**
  (editada na tela **Inteligência (IA)**; nunca no código e não reexibida após
  salva). `resolved_api_key()`/`resolved_model()` caem para env
  (`OPENAI_API_KEY`/`OPENAI_MODEL`) se vazios. **Atendente virtual**:
  `instructions` (prompt/persona editável), `max_turns` (limite de respostas,
  padrão 3), `fallback_sector` (FK Sector, para onde encaminhar quando não
  identificar). **Contador de tokens**: `total_requests`, `total_prompt_tokens`,
  `total_completion_tokens`, `total_tokens`, `usage_since`, `last_used_at` —
  somados de forma atômica por `record_usage()` a cada chamada; `reset_usage()`
  zera. Ver seção 13. **`enabled` ficou vestigial**: a ativação da IA agora vem do
  **modo mestre** `MenuBotConfiguration.mode == 'ai'` (ver seção 14); `enabled` é
  mantido em sincronia só por compatibilidade.
- **MenuBotConfiguration** (singleton `get_solo()`): config do **chatbot de menu**
  (atendimento automático **sem IA**) **e** o **MODO MESTRE** de primeiro atendimento
  `mode` (`off`/`menu`/`ai`) — fonte única da verdade de qual motor atua. Campos de
  texto editáveis (`greeting` com `{saudacao}`, `menu_intro`, `confirmation_message`
  com `{setor}`, `invalid_message`, `handoff_message`), `max_attempts` (tentativas
  inválidas antes do handoff) e `fallback_sector`. Ver seção 14.
- **MenuOption**: uma opção do menu (`config` FK, `order` = número que o cliente
  digita, `label`, `sector` FK). `key` = `order`.
- **Contact**: `name`, `phone` (único, guardado **só em dígitos**), `display_name`,
  `initials`. É a base da tela **Contatos** e da resolução de nomes: criado
  **automaticamente** na 1ª mensagem de uma conversa **direta** (nome = pushName),
  e também ao **nomear** um participante de grupo (clique no número) ou cadastrar
  manualmente. O `phone` (dígitos) é a chave usada para trocar número→nome nas
  mensagens de grupo (remetente e menções `@`).
- **Conversation**: **um único chat por pessoa/grupo** (padrão WhatsApp — não dá mais
  fork por atendimento). `contact` (**opcional** — grupo não tem contato individual),
  `chat_type` (`private`/`group`), `external_id` (JID do grupo `@g.us`, telefone
  ou LID da direta), `name` (título/nome do grupo), `status`
  (`open`/`pending`/`closed`), `assigned_attendant`, `sector`,
  `last_message_text`, `last_message_at`, `unread_count`, `ai_turns` (respostas
  da IA no atendimento atual; zera ao transferir/encerrar/reabrir). Propriedades:
  `is_group`, `display_title`, `display_initials`, `recipient` (destino de envio).
- **Message**: `conversation`, `direction` (`in`/`out`), `message_type`
  (`text/image/audio/video/document/sticker/gif/reaction/location/contact/unknown/system`;
  `system` = **divisória** de atendimento no meio do chat),
  `text`, `sender_name`, `sender_id`/`participant_id` (quem enviou; em grupo é o
  participante), `is_group`, `from_me`, `is_ai` (marca falas do atendente
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
  `participant_id`, `sender_name`, `from_me`, `display_name`, `source`. O JID de
  grupo tem **prioridade** sobre telefone/remetente em qualquer campo. `_valid_name`
  exige ≥1 caractere alfanumérico (rejeita nomes só de pontuação, ex.: ".").
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
  o contexto, resolve a conversa e cria a mensagem; deduplica pelo id externo.
  **Ignora (não cria nada):** canal/transmissão (`is_ignorable_jid`), Status
  (`is_status_or_broadcast`) e mensagens de **sistema/tipo `unknown`**
  (ex.: `senderKeyDistributionMessage`/`protocolMessage`, comuns em grupos).
- `resolve_conversation_for_context(ctx)` acha/cria a conversa certa: **grupo** →
  keyed pelo JID (`external_id`, `chat_type='group'`, sem contato); **direta com
  telefone** → contato + conversa aberta (comportamento antigo); **direta sem
  telefone (`@lid`)** → keyed pelo próprio chat_id, sem contato. **Nunca cria
  contato privado para quem escreve no grupo.**
- `save_incoming_message(conversation, ctx, ...)` cria a mensagem por tipo;
  para mídia, chama `download-media` e **salva o arquivo localmente** em
  `MEDIA/whatsapp/` (o `fileLink` da W-API expira). Estados `pending/ok/unavailable`.
  A extensão do arquivo salvo vem de `_ext_for_media` (nome original do documento →
  mapa de mimetype → `mimetypes` do Python → `bin`), evitando baixar como `.bin`.
- `document_filename(message)` → nome original do documento (do `raw_payload`),
  usado no download e na serialização.
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

### Webhook
- View `wapi_webhook_view` (`@csrf_exempt`). Rotas: `/webhook/wapi/` e
  `/beezap/webhook/wapi/`. Aceita a chamada externa **sem token quando nenhum
  webhook_token está configurado** (senão exige `?token=`/header). URL exibida na
  tela vem de `reverse('wapi-webhook')` → com prefixo vira `/beezap/webhook/wapi/`.

## 5. Tela Conversas (`templates/accounts/conversations.html` + `conversations.css`)

- **Abas de tipo**: Todas / Diretas / Grupos (param `tipo` no endpoint da lista),
  com contagens. **Selo "Grupo"** na lista e no cabeçalho; em grupo, o **nome do
  participante** aparece acima de cada mensagem recebida.
- **Lista real** (server-rendered) + **filtros** com contagens reais: Todas,
  Não lidas (`unread_count>0`), Em atendimento (tem atendente e não fechada),
  Aguardando (sem atendente e não fechada), Finalizadas (`closed`). **Busca** por
  nome/telefone/última mensagem, combinada com o filtro e a aba de tipo.
- **Chat via AJAX**: abrir zera não lidas; render por tipo; **composer fixo no
  rodapé** (corrigido com `min-height:0` na cadeia flex/grid e `[hidden]{display:none!important}`).
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
- **Nome do remetente (grupo)**: mostra o nome; se não houver, mostra o **número
  clicável** → modal "Nomear contato" (cria/atualiza `Contact`). Em conversa
  **direta**, o **nome no cabeçalho** é clicável para renomear o contato.
- **Notificações (estilo WhatsApp Web)**: pop-up do desktop (Web Notifications) +
  **aviso sonoro** (beep via Web Audio, sem arquivo) quando a janela **não** está em
  foco (`document.hasFocus()`); toast interno quando em foco. Dois botões-ícone no
  topo da lista: **Notificações** (mostra o estado da permissão — verde/âmbar/vermelho —
  e força o pedido ao clicar) e **Som** (liga/desliga, salvo em `localStorage`).
  Título da aba mostra o total de não lidas.
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
- **URLs AJAX** montadas a partir de `window.location.pathname` (até `/conversas/`)
  para respeitar o prefixo `/beezap/` mesmo se o `{% url %}` vier sem prefixo.
- Endpoints: `conversation-list` (`/conversas/lista/`), `conversation-messages`
  (aceita `?retry=1` para rebaixar mídias falhas), `conversation-send`,
  `conversation-send-media`, `conversation-transfer`, `conversation-take`
  (`/conversas/<id>/assumir/`), `conversation-close` (`/conversas/<id>/encerrar/`),
  `conversation-sync-groups`, `conversation-name-contact` (`/conversas/nomear-contato/`),
  `wapi-webhook-events`.

## 5.1. Tela Contatos (`templates/accounts/contacts.html` + `contacts.css`)

- Rota `contatos/` (`contacts_view`, nome de rota `contacts`; item da barra lateral).
- Lista os `Contact` (avatar com iniciais, nome, telefone), **busca** por nome/telefone
  (GET `q`), contador e CRUD: adicionar/editar por **modal** e excluir (com confirmação).
- Telefone é **normalizado para dígitos** ao salvar (mesma chave da resolução de nomes),
  então o que se cadastra aqui aparece no lugar do número nas conversas de grupo.
- Disponível para qualquer usuário logado. Reaproveita `dashboard.css`/`attendants.css`.

## 6. Deploy no VPS (LEIA — tem armadilhas específicas)

- App em `/var/www/beezap/`, serviço systemd **`beezap`**, gunicorn em
  **`127.0.0.1:8103`** (os exemplos em `deploy/` citam 8006, mas o serviço real
  roda em 8103; o Nginx `/beezap/` faz proxy para 8103).
- **Nginx**: config do domínio em `/etc/nginx/sites-available/site_idiomas`.
  Blocos do BEEZAP (proxy com `/` final **remove** o prefixo antes do Django):
  ```nginx
  location /beezap/static/admin/ { alias /var/www/beezap/staticfiles/admin/; }
  location /beezap/static/       { alias /var/www/beezap/static/; }   # serve a FONTE
  location /beezap/media/        { alias /var/www/beezap/media/; }
  location /beezap/              { proxy_pass http://127.0.0.1:8103/; ... }
  ```
- **Prefixo `/beezap/`**: resolvido no Django via **`FORCE_SCRIPT_NAME=/beezap`**
  (`.env`), que prefixa todos os `{% url %}`/redirects. `LOGIN_URL`/
  `LOGIN_REDIRECT_URL`/`LOGOUT_REDIRECT_URL` são **nomes de rota** (herdam o prefixo).
- **Estáticos**: como o Nginx serve `static/` (a fonte) direto, **um `git pull`
  já publica CSS/JS** — sem `collectstatic`/`cp`. O admin do Django vem de
  `staticfiles/admin/` (rodar `collectstatic` uma vez). Cache-busting: `?v=N` nos
  links de CSS em `conversations.html` (hoje `conversations.css?v=19`) — **incrementar
  ao editar o CSS**. O JS fica inline no template (publica com o `git pull`).
- **Histórico do bug de estáticos**: o `settings.py` do servidor já foi editado à
  mão com `STATICFILES_DIRS=[]`, o que impedia o `collectstatic` de publicar o
  CSS. Corrigido de forma versionada (ver `DEPLOY.md`). Não esvaziar `STATICFILES_DIRS`.
- **ffmpeg**: dependência de **sistema** (não pip), **obrigatória** para envio de
  mídia — converte áudio gravado (`.webm`→`.ogg`) e imagens não suportadas pela
  W-API (webp/gif/bmp/heic→`.jpg`). `sudo apt install -y ffmpeg`. O `manage.py check`
  avisa se faltar (**`beezap.W001`**). Ver `requirements.txt` e `DEPLOY.md`.
- **DEBUG=True em produção**: ainda ativo no servidor — **risco de segurança**
  (expõe traceback). Pendência: mover para `DEBUG=False` no `.env`.

### Fluxo de deploy
```bash
cd /var/www/beezap
bash deploy/deploy.sh      # git pull + pip install + migrate + collectstatic + restart
# (ou manual: git pull && venv/bin/python manage.py migrate && sudo systemctl restart beezap)
```

## 7. Variáveis de ambiente (`.env`) — ver `.env.example`

Obrigatórias/relevantes em produção:
```
SECRET_KEY=...
DEBUG=False                       # (hoje True no servidor — corrigir)
ALLOWED_HOSTS=fabianopolone.com.br,www.fabianopolone.com.br
CSRF_TRUSTED_ORIGINS=https://fabianopolone.com.br
DATABASE_URL=sqlite:////var/www/beezap/db.sqlite3
FORCE_SCRIPT_NAME=/beezap
STATIC_URL=/beezap/static/
MEDIA_URL=/beezap/media/          # sem isto, envio de mídia falha (W-API não baixa a URL)
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

1. Fazer a alteração.
2. `python manage.py check` (e `makemigrations`/`migrate` se mexer em model).
3. Atualizar **apenas o final** de `docs/HISTORICO.md`.
4. Commit atômico (`feat:`/`fix:`/`docs:`/`style:`/`chore:`) → `git push`.
5. Não commitar `.env`, `db.sqlite3`, `venv/`, tokens.

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
merge_contact_conversations [--apply]   # unifica conversas picotadas em 1 chat por pessoa/grupo (dry-run)
```
> Os `cleanup_*` e o `inspect_*` são **dry-run por padrão** (só listam); `--delete`
> aplica. Úteis para limpar lixo antigo (status/canal/sistema) após um deploy do fix.

## 10. Pendências / próximas etapas

- Legenda (caption) ao enviar imagem/vídeo/documento pelo composer.
- Recursos **PRO** (reação/sticker/GIF nativo/botões/listas) quando a instância for PRO.
- `DEBUG=False` em produção (segurança).
- Upload múltiplo, arrastar-e-soltar.
- (Opcional) Tornar as **menções `@` clicáveis** dentro do texto para nomear ali
  mesmo; hoje o clique-para-nomear está no remetente e no cabeçalho da direta.
- (Opcional) Retry de mídias falhas em **todas** as conversas (hoje o botão
  Atualizar age só na conversa aberta; existe o comando `retry_wapi_media` global).

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
  Ollama) — ver nota na seção 12.

## 11. Segurança

- Servidor: usar **chave SSH** e usuário não-root; **rotacionar** qualquer
  credencial que tenha sido exposta. Nunca colar senha/token em chat ou commit.
- Nunca expor token/payload/traceback ao usuário final (padrão já seguido).

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
- **Encerrar** (`conversation_close_view`): insere uma **divisória** no chat
  (`save_system_message`, `Message.message_type='system'` → "Atendimento encerrado"), marca
  `status='closed'` e limpa `assigned_attendant`/`sector`. A conversa e o histórico **permanecem**.
- A **próxima mensagem** do mesmo contato reusa o mesmo chat: `_reopen_for_new_service` insere
  a divisória "Novo atendimento iniciado" e volta `status='open'`. As divisórias marcam os
  limites de cada atendimento dentro do chat único.
- **Front**: `buildMessageEl` renderiza `kind='system'` como uma **pílula centralizada**
  (`.conv-divider`, sem balão/horário); CSS em `conversations.css?v=19`.
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
- **Contador de consumo**: o OpenAI devolve `usage` (prompt/completion/total tokens)
  em cada resposta; `chat_completion` extrai e chama `OpenAiConfiguration.record_usage`
  (soma atômica com `F()`, segura para chamadas concorrentes). A tela mostra um card
  **"Consumo de tokens"** (total, entrada, saída, nº de chamadas, "contando desde" /
  "último uso") com botão **"Zerar contador"** (`form_type=reset-usage`). O teste de
  conexão também conta (gasto mínimo). CSS `openai_settings.css?v=3`.

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
  **regra de formato JSON** (obrigatória para o parsing) + o **histórico** do
  **atendimento atual** (`build_history` pega só as mensagens **após a última
  divisória**, até `CONTEXT_MESSAGES=10`) em turnos `user`/`assistant`, terminando
  na mensagem atual — ao Encerrar/reabrir, o contexto começa limpo. A tela tem botão
  **"Restaurar prompt padrão"**.
- **Decisão via JSON** (`response_format={'type':'json_object'}`): o modelo devolve
  `{"mensagem", "setor", "atendente"}`. `atendente` casado → `_route_to_attendant`
  (assign + setor do atendente + `open`); `setor` casado → `_route_to_sector`
  (setor + `pending`, sem atendente); nenhum → envia a fala e incrementa `ai_turns`.
  Cada encaminhamento insere uma **divisória** "Encaminhado para … pela IA".
- **Limite/fallback**: ao atingir `max_turns` sem decidir, `_handoff_to_fallback`
  **sempre avisa o cliente** com uma mensagem clara de handoff (`HANDOFF_NOTICE`:
  "não consegui entender… vou pedir para um atendente…") — nunca transfere em
  silêncio nem repete a pergunta de esclarecimento — e então encaminha para
  `fallback_sector` (ou um setor "Geral" automático). Sem nenhum fallback, avisa e
  deixa `pending` sem setor, mantendo `ai_turns` no máximo para não repetir o aviso.
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

**Chatbot de menu** (`chatbot/handler.py`, espelha o `gpt/attendant.py` — thread em
background, lock por conversa, nunca levanta exceção):
- 1º contato do atendimento → envia **saudação + menu** (`build_menu_text`: `{saudacao}`
  vira Bom dia/tarde/noite; opções numeradas "1 - Financeiro").
- Mensagens seguintes → `_match_option` interpreta o **número** digitado (ou o nome
  exato do rótulo/setor): opção válida → envia a **confirmação** (`{setor}`) e
  **encaminha para o setor** (`pending`, divisória "Encaminhado para o setor X pelo
  chatbot"); opção inválida → reexibe o menu e **conta a tentativa** (`Conversation.ai_turns`).
- Ao atingir `max_attempts` tentativas inválidas → **avisa** (`handoff_message`) e
  encaminha para o `fallback_sector` (ou deixa `pending` sem setor).
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

- **Features** (botões reais, com ícone) em `MENU_FEATURES`: `dashboard`,
  `conversations`, `contacts`, `attendants`, `sectors`, `settings`. O botão
  **Permissões** (`permissions`) é exclusivo do ADM e fica fora da matriz. Os
  placeholders antigos (Atendimentos/Campanhas/Relatórios) foram **removidos** do menu.
- **Administrador**: sempre **acesso total** (não editável — nunca se tranca fora).
- **Padrão** dos demais (`DEFAULT_ROLE_KEYS`): `usuario`/`leitor` = `conversations` +
  `contacts` (sem Dashboard). Ajustável na tela.
- **Efetivo por usuário** (`allowed_keys_for`): adm → tudo; senão a personalização do
  usuário (`UserMenuPermission`, se houver) **sobrepõe** o padrão do perfil
  (`RoleMenuPermission` ou o padrão do código).
- **Landing pós-login**: quem não tem Dashboard cai na 1ª tela disponível
  (`first_landing_url_name`; `dashboard_view` redireciona).
- **Tela Permissões** (`permissions_view`, rota `permissoes/`, `permissions.html` +
  `permissions.css`, **só ADM**) — em **abas**:
  - **Botões do perfil**: toggles por perfil (Administrador travado como "acesso
    total") + seção "Personalizar um usuário" (select → toggles). Cada perfil/usuário
    tem também o toggle **"Ver conversa inteira"** (`full_history`).
  - **Grupos**: lista os grupos detectados (Conversation `chat_type='group'`) e libera
    cada um por **setor** e/ou **usuário** (grava em `GroupAccess`); botão **"Atualizar
    nomes"** chama `conversation-sync-groups` (nome real do grupo via W-API). O **nome
    do grupo é editável inline** (campo por grupo, `form_type=group-name` → `Conversation.name`;
    o JID vem como subtítulo) para corrigir quando a W-API não traz o nome. Botão
    **"Remover"** (X) por grupo apaga a conversa do grupo (`form_type=group-remove`).
    A lista de grupos é **dirigida por mensagem recebida** (um grupo aparece quando
    chega mensagem dele; não vem do `get-all-groups`), então grupos onde o número saiu
    podem ser removidos daqui; se chegar nova mensagem, o grupo reaparece.
  **Sem botão "Salvar"**: as alterações (perfis, usuário e grupos) são **salvas
  automaticamente** ao clicar (fetch AJAX → `permissions_view` responde JSON quando
  `X-Requested-With`; toast de confirmação).
  `build_nav_items(user, active_label)` monta o menu a partir dessas regras.

### Separação das conversas (quem vê quais chats)
- `visible_conversations(user, qs)` / `can_see_conversation(user, conv)` em
  `accounts/permissions.py`. **Admin vê tudo.** Não-admin vê: **diretas** atribuídas a
  ele OU do(s) setor(es) dele; **grupos** liberados para o(s) setor(es) dele OU para
  ele (via `GroupAccess`). Um usuário novo/sem setor **não vê nada** ("zerado").
- Aplicado na lista (`conversations_view`, `conversation_list_view` — inclusive os
  contadores) e nas ações (`conversation-messages/send/take/transfer/close/send-media`
  retornam 403 se o usuário não pode ver a conversa).
- **Escopo do histórico** (`history_full_for`): ao abrir uma conversa, quem não tem
  "Ver conversa inteira" vê só o **atendimento atual** (mensagens a partir da última
  divisória); admin/`full_history` vê tudo.
