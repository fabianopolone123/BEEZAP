# Documentação BEEZAP

Arquivos oficiais:

- `CONTEXTO.md`: **comece por aqui** — visão geral, arquitetura, W-API, telas
  (Conversas, Contatos), notificações, comandos de management, deploy e armadilhas
  do VPS. Documento de handoff para retomar o projeto do zero.
- `CODEX_PADROES.md`: regras de trabalho (padrões de UI/CSS/commit).
- `GIT.md`: regras de Git, commit e push.
- `HISTORICO.md`: registro do que já foi feito e decisões já tomadas.
- `DEPLOY.md`: deploy no VPS, dependências de sistema (ffmpeg), variáveis `.env`
  do prefixo `/beezap/` e como publicar/testar estáticos.
- `DEPLOY_VPS.md`: guia inicial de homologação em VPS (histórico; ver `DEPLOY.md`
  para a realidade atual sob `/beezap/`).
- `WAPI_MEDIA_INTEGRATION.md`: emoji, mídia (imagem/áudio/vídeo/documento),
  sticker/gif/reação recebidos, download de mídia, LITE vs PRO.
- `../CLAUDE.md` (raiz do projeto): instruções carregadas automaticamente pelo agente
  em toda sessão — a regra fixa de commit/push/documentação, os padrões do projeto e
  as armadilhas do ambiente. É um resumo operacional, não substitui o `CONTEXTO.md`.

Regra fixa (vale para toda alteração):

**Nenhuma alteração fica sem `commit` + `push` e sem documentação atualizada.** Toda
mudança de código, CSS, template ou texto é fechada no mesmo passo: validar
(`python manage.py check` e os testes quando mexer no backend), registrar no final de
`HISTORICO.md`, atualizar a documentação afetada (principalmente `CONTEXTO.md`),
commitar código + documentação juntos com mensagem em PT-BR e dar `push` na hora. O
detalhamento está em `CODEX_PADROES.md` e `GIT.md`.

Regra importante:

O projeto não possui arquivo de plano, roadmap ou próximas etapas dentro do repositório. O Codex só deve implementar algo quando receber um prompt explícito do usuário. Pendências conhecidas ficam listadas ao final de `CONTEXTO.md`.
