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

## Pendente para próxima etapa

- **UI de anexo no composer** para enviar imagem/áudio/vídeo/documento (LITE): os
  métodos do client (`send_image_message`, `send_audio_message`,
  `send_video_message`, `send_document_message`) já estão prontos; falta o botão
  de anexar + endpoint interno que salva o arquivo em `MEDIA`, gera URL pública e
  chama a W-API.
- **Recursos PRO** (enviar reação/sticker/GIF nativo, botões, listas, enquetes):
  manter bloqueados com aviso enquanto a instância for LITE.
- Miniatura/preview clicável em tela cheia para imagens.
