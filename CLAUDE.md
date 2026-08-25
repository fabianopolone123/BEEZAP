# BEEonBOARD — instruções para o agente

Sistema Django de atendimento/automação de WhatsApp via **W-API**, **multiempresa
(SaaS)**: a mesma instalação atende várias empresas clientes, cada uma com os seus
setores, atendentes, contatos e conversas, e existe um **gestor master** acima delas.

**Marca:** o produto se chama **BEEonBOARD** (antes BEEZap). O nome exibido sai de
`PLATFORM_NAME` em `accounts/context_processors.py` e o logo é
`static/images/logo-beeonboard.png`. O endereço é **`/beeonboard/`** (o antigo
`/beezap/` redireciona). **Não renomear os identificadores técnicos**, que seguem
`beezap` porque o ambiente em produção depende deles: serviço systemd `beezap`, pasta
`/var/www/beezap`, loggers `beezap.*`, check `beezap.W001`, header
`X-BEEZAP-WEBHOOK-TOKEN` e as chaves de `localStorage`.

## REGRA FIXA — toda alteração fecha com commit + push e documentação atualizada

**Nenhuma alteração fica sem `commit` + `push`.** Vale para código, CSS, template e
texto. Não acumular mudança local para "enviar depois": o repositório remoto é o
estado real do projeto.

Ordem obrigatória ao terminar qualquer alteração:

1. Fazer a alteração.
2. Validar: `python manage.py check` (mais `makemigrations`/`migrate` se mexer em
   model) e os testes (`python manage.py test`) quando mexer no backend.
   **Check ou teste quebrado não vira commit** — corrigir antes.
3. **Atualizar a documentação no mesmo passo**: sempre acrescentar a entrada no
   **final** de `docs/HISTORICO.md` (sem apagar histórico antigo) e atualizar o que
   mudou de estado em `docs/CONTEXTO.md` (arquitetura, models, telas, endpoints,
   comandos, pendências) e nos demais arquivos de `docs/`. A documentação reflete
   **sempre** o estado atual do projeto.
4. Commit atômico, mensagem em **PT-BR** (`feat:`/`fix:`/`docs:`/`style:`/`chore:`),
   com **código e documentação juntos**.
5. `git push` na hora.

Nunca commitar `.env`, `db.sqlite3`, `venv/`, `__pycache__/` nem credencial (token da
W-API, API Key do GPT, senha).

## Antes de começar, leia a documentação

- `docs/CONTEXTO.md` — **comece por aqui**: handoff completo (arquitetura,
  multiempresa, W-API, IA/chatbot, telas, permissões, deploy e pendências).
- `docs/CODEX_PADROES.md` — padrões de UI/CSS/notificação/commit.
- `docs/GIT.md` — regras de Git.
- `docs/HISTORICO.md` — o que já foi feito e por quê.
- `docs/DEPLOY.md` — deploy no VPS sob o prefixo `/beeonboard/`.
- `docs/WAPI_MEDIA_INTEGRATION.md` — mídia e LITE vs PRO da W-API.

## Como este projeto trabalha

- **Só implementar com pedido explícito** do usuário. O repositório **não** tem
  arquivo de plano/roadmap; pendências conhecidas ficam no fim de `docs/CONTEXTO.md`.
- Evoluir **por partes**, preservando uma base funcional; interface em **português**,
  simples e didática, pensada para quem tem pouca experiência técnica.
- **CSS por página** com classe raiz própria (`dashboard.css` é só a base do painel).
  O cache-busting é **automático**: usar `{% asset 'css/arquivo.css' %}`
  (`{% load beeonboard_assets %}`) — **não** existe mais `?v=N` na mão. Layout
  responsivo, sem rolagem horizontal na página.
- Mensagens ao usuário via **toast**, curtas, sem dado técnico (token, payload,
  traceback, ID interno).
- **Multiempresa**: todo dado de cliente é filtrado por `company`; o **master
  administra e mede, mas não lê o atendimento** de ninguém. Ao criar tela ou
  endpoint, começar pelo escopo de empresa.

## Ambiente

- Local: venv na raiz do projeto — **confira o nome antes**, pode ser `venv\` ou
  `.venv\` (na máquina de desenvolvimento atual é **`.venv`**): usar
  `.\.venv\Scripts\python.exe manage.py ...`. Sem `.env` local (SQLite +
  `DEBUG=True` por padrão), e o `db.sqlite3` local pode estar **sem migrações
  aplicadas** — a suíte de testes usa banco próprio, então testar não exige `migrate`.
- **Dois avisos do `check` são ESPERADOS no local e não são regressão:**
  `beezap.W001` (ffmpeg fora do PATH — o envio de áudio gravado e de imagem
  webp/gif/bmp/heic falha) e `beezap.W003` (chaves VAPID ausentes — o aviso de nova
  mensagem por Web Push fica inerte). Em produção os dois estão resolvidos.
- Produção: VPS Linux em `https://fabianopolone.com.br/beeonboard/`, app em
  `/var/www/beezap`, deploy com `bash deploy/deploy.sh`. **Todo deploy reinicia o
  gunicorn e confirma que os PIDs reciclaram** (com `DEBUG=False` o template fica em
  cache na memória dos workers). Ver `docs/DEPLOY.md`.
