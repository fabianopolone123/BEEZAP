# Contexto do Projeto BEEZAP (handoff)

Documento único para retomar o projeto do zero (ex.: nova sessão do Claude/Codex).
Leia também: `CODEX_PADROES.md`, `GIT.md`, `HISTORICO.md`, `DEPLOY.md`,
`WAPI_MEDIA_INTEGRATION.md`.

---

## 1. Visão geral

- **BEEZAP**: sistema Django de atendimento/automação de WhatsApp via **W-API**.
- **Stack**: Django 5.2, Python 3.12, gunicorn, Nginx, SQLite (padrão) ou
  PostgreSQL (via `DATABASE_URL`). IA local opcional via Ollama (`ai_engine`).
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
ai_engine/         integração Ollama (tela de teste de IA)
wapi/              MÓDULO (não é app instalado): client.py, parser.py, services.py, formatting.py
static/css/        CSS por página (dashboard.css, conversations.css, wapi_settings.css, ...)
templates/         base.html + accounts/*.html
docs/              documentação (este arquivo, DEPLOY.md, etc.)
deploy/            deploy.sh, diag_static.sh, patch_nginx_beezap.sh, exemplos nginx/systemd
```

> `wapi/` é um módulo Python comum (importa `accounts.models`); **não** está em
> `INSTALLED_APPS`, por isso os models ficam em `accounts/models.py`.

## 3. Modelos (`accounts/models.py`) — migração atual: `0013`

- **User** (AbstractUser, login por e-mail; `role`: `leitor`/`usuario`/`adm`).
- **Attendant** (perfil de atendente, vínculo com User, troca de senha inicial).
- **Sector** (setores; M2M com Attendant).
- **AutomationRule** (regras para orientar a IA).
- **PasswordResetCode** (recuperação de senha por código no WhatsApp).
- **WapiConfiguration** (singleton `get_solo()`): `instance_id`, `token`,
  `webhook_token`. Credenciais reais ficam **aqui (no banco)**, editadas na tela
  Configurações → WhatsApp/W-API. `resolved_*()` cai para env se vazio.
- **WapiWebhookEvent**: todo evento recebido do webhook (com `raw_payload`).
- **Contact**: `name`, `phone` (único, guardado **só em dígitos**), `display_name`,
  `initials`. É a base da tela **Contatos** e da resolução de nomes: criado
  **automaticamente** na 1ª mensagem de uma conversa **direta** (nome = pushName),
  e também ao **nomear** um participante de grupo (clique no número) ou cadastrar
  manualmente. O `phone` (dígitos) é a chave usada para trocar número→nome nas
  mensagens de grupo (remetente e menções `@`).
- **Conversation**: `contact` (**opcional** — grupo não tem contato individual),
  `chat_type` (`private`/`group`), `external_id` (JID do grupo `@g.us`, telefone
  ou LID da direta), `name` (título/nome do grupo), `status`
  (`open`/`pending`/`closed`), `assigned_attendant`, `sector`,
  `last_message_text`, `last_message_at`, `unread_count`. Propriedades:
  `is_group`, `display_title`, `display_initials`, `recipient` (destino de envio).
- **Message**: `conversation`, `direction` (`in`/`out`), `message_type`
  (`text/image/audio/video/document/sticker/gif/reaction/location/contact/unknown`),
  `text`, `sender_name`, `sender_id`/`participant_id` (quem enviou; em grupo é o
  participante), `is_group`, `from_me`, `external_message_id` (id real da W-API,
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
  quando o W-API Lite manda `chat.id == "status"` (sem `@broadcast`) ou pelos
  marcadores `statusSourceType`/`posterStatusID`, ou `status@broadcast` em qualquer
  campo. Status **não** vira conversa.
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
  Enter várias vezes seguidas (o campo só limpa quando o envio termina). Ao enviar,
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
  `conversation-send-media`, `conversation-transfer`, `conversation-sync-groups`,
  `conversation-name-contact` (`/conversas/nomear-contato/`), `wapi-webhook-events`.

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
  links de CSS em `conversations.html` (hoje `conversations.css?v=14`) — **incrementar
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
inspect_wapi_messages --name X --full   # DIAGNÓSTICO: payload cru + veredito do parser
cleanup_status_messages [--delete]      # remove mensagens de Status que viraram conversa
cleanup_unknown_messages [--delete]     # remove mensagens de tipo 'unknown' (sistema)
cleanup_nonpersonal_conversations [--delete]  # remove conversas de canal/transmissão/"status"
```
> Os `cleanup_*` e o `inspect_*` são **dry-run por padrão** (só listam); `--delete`
> aplica. Úteis para limpar lixo antigo (status/canal/sistema) após um deploy do fix.

## 10. Pendências / próximas etapas

- Legenda (caption) ao enviar imagem/vídeo/documento pelo composer.
- Ações de **assumir atendimento** / **encerrar conversa** (para os filtros
  "Em atendimento"/"Finalizadas" ganharem vida completa).
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

## 11. Segurança

- Servidor: usar **chave SSH** e usuário não-root; **rotacionar** qualquer
  credencial que tenha sido exposta. Nunca colar senha/token em chat ou commit.
- Nunca expor token/payload/traceback ao usuário final (padrão já seguido).
