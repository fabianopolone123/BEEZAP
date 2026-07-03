# Integração de mídia da W-API no BEEZAP

Documenta como o BEEZAP trata emoji, mídia (imagem/áudio/vídeo/documento),
figurinha/sticker, GIF e reação da W-API, e o que respeita o plano **LITE vs PRO**.

## Plano LITE vs PRO

A instância atual é **LITE**. A implementação respeita isso:

**Envio liberado (LITE):** texto (com emoji), imagem, áudio, vídeo, documento.
**Recebimento (LITE):** todos os tipos — inclusive sticker, GIF e reação — são
recebidos, salvos e exibidos no chat (com download da mídia).
**Envio bloqueado (PRO):** reação, remover reação, sticker, GIF nativo, botões,
listas, enquetes. Não há ação de envio para esses tipos; se o atendente tentar,
deve ver o aviso "Disponível apenas no plano PRO da W-API".

## Endpoints da W-API usados

Base: `https://api.w-api.app/v1/message/<ação>?instanceId=<INSTANCE_ID>`
Header: `Authorization: Bearer <TOKEN>` · `Content-Type: application/json`

| Ação | Path | Body principal | Plano |
|------|------|----------------|-------|
| Texto | `send-text` | `{phone, message}` | LITE |
| Imagem | `send-image` | `{phone, image, caption?}` | LITE |
| Áudio | `send-audio` | `{phone, audio}` | LITE |
| Vídeo | `send-video` | `{phone, video, caption?}` | LITE |
| Documento | `send-document` | `{phone, document, fileName?, caption?}` | LITE |
| Baixar mídia | `download-media` | `{mediaKey, directPath, type, mimetype}` → `{fileLink, expires, ...}` | LITE |
| Reação/Sticker/GIF | — | — | **PRO (não enviado)** |

Tudo centralizado em [wapi/client.py](../wapi/client.py) (`_wapi_post` cuida de
credenciais, headers, erros e logs seguros — nunca expõe token nem traceback).

## 1. Emojis

Emoji é apenas texto Unicode: vai/vem pelo `send-text` normalmente. O banco
(`TextField`), serializer e template preservam Unicode; nada é convertido.

## 2. ID real da mensagem (`external_message_id`)

Toda mensagem (recebida pelo webhook e enviada pelo sistema) guarda o
`external_message_id` retornado/recebido da W-API, além do id interno do BEEZAP.
Esse id é a base para reações no futuro (PRO).

## 3. Tipos de mensagem

`Message.message_type`: `text, image, audio, video, document, sticker, gif,
reaction, location, contact, unknown`. Mensagens antigas continuam como `text`
(default da migration — histórico preservado).

Campos de mídia no model: `media_file` (arquivo salvo localmente), `media_url`
(link remoto, pode expirar), `media_mimetype`, `media_status`
(`none/pending/ok/unavailable`).

## 4. Recebimento e download de mídia

Fluxo no webhook ([accounts/views.py](../accounts/views.py) →
[wapi/services.py](../wapi/services.py)):

1. `parse_wapi_media()` identifica o tipo pela chave de conteúdo
   (`imageMessage`, `audioMessage`, `videoMessage`, `stickerMessage`,
   `documentMessage`, `reactionMessage`, `conversation`/`extendedTextMessage`;
   `videoMessage.gifPlayback` → `gif`).
2. Cria a mensagem com `media_status='pending'`.
3. Chama `download-media` (mediaKey/directPath/type/mimetype).
4. Baixa o `fileLink` e **salva localmente** em `MEDIA_ROOT/whatsapp/`
   (o link da W-API é temporário — não dependemos dele depois).
5. `media_status='ok'` (arquivo salvo) ou `unavailable` (falha) — a mensagem é
   salva de qualquer forma; o recebimento de texto nunca quebra.

A "última mensagem" da conversa usa rótulos amigáveis: 📷 Imagem, 🎧 Áudio,
🎥 Vídeo, 🎞️ GIF, 💟 Figurinha, 👍 Reação, 📄 Documento.

## 5. Render no chat ([templates/accounts/conversations.html](../templates/accounts/conversations.html))

- text → balão normal (emoji ok)
- image/sticker → imagem (sticker menor, sem balão grande)
- audio → player `<audio>` · video/gif → `<video>` (gif com autoplay/loop/mudo)
- document → link para baixar
- reaction → "Reagiu: <emoji>"
- location/contact/unknown → aviso discreto
- mídia baixando → "Carregando mídia..." · falha/expirada → "Mídia indisponível"

CSS específico em `static/css/conversations.css` (classes `conv-media-*`). O
composer fixo no rodapé e os filtros não foram afetados.

## 6. URLs públicas de mídia

`MEDIA_URL` vem de variável de ambiente (`/beezap/media/` em produção), servido
pelo Nginx em `location /beezap/media/ { alias /var/www/beezap/media/; }`.

## 7. Envio de mídia LITE pela conversa (composer)

Na tela Conversas, o composer tem um botão de **anexo** (clipe) que abre um menu:
Imagem, Áudio, Vídeo, Documento.

Fluxo:
1. O atendente escolhe o tipo e seleciona o arquivo.
2. O frontend faz `POST` (multipart) para
   `conversas/<id>/enviar-midia/` com `file` e `media_type`.
3. O backend ([accounts/views.py](../accounts/views.py) `conversation_send_media_view`):
   - valida conversa/telefone/tipo/mimetype e **tamanho** (`WAPI_MEDIA_MAX_MB`, padrão 16 MB);
   - salva o arquivo em `MEDIA/whatsapp/outgoing/` com **nome único** (uuid — nunca
     usa o nome do usuário, evita traversal/sobrescrita);
   - monta a **URL pública** com `request.build_absolute_uri(media_file.url)`
     (respeita o prefixo `/beezap/media/` via `MEDIA_URL`);
   - chama o método correto do client: `send_image_message`, `send_audio_message`,
     `send_video_message` ou `send_document_message`;
   - salva a mensagem `out` com `media_file`, `media_mimetype`, `external_message_id`
     e `media_status` = `ok` (sucesso) ou `unavailable` (falha, sem quebrar a tela).

**Tipos e mimetypes aceitos:** `image/*`, `audio/*`, `video/*` e documentos
(pdf, doc/docx, xls/xlsx, ppt/pptx, txt, csv). Legenda (caption) ainda não é
enviada pelo composer (ver pendências).

A mensagem enviada é renderizada imediatamente no chat (reaproveitando o mesmo
render de mídia recebida) e a última mensagem da conversa vira o rótulo do tipo.

## Pendente para próxima etapa

- **Legenda (caption)** ao enviar imagem/vídeo/documento pelo composer.
- **Recursos PRO** (enviar reação/sticker/GIF nativo, botões, listas, enquetes):
  manter bloqueados com aviso enquanto a instância for LITE.
- **Gravação de áudio pelo navegador**, **upload múltiplo** e **arrastar-e-soltar**.
- Miniatura/preview clicável em tela cheia para imagens.
