# BEEZAP — instruções para o agente

Sistema Django de atendimento/automação de WhatsApp via **W-API**, **multiempresa
(SaaS)**: a mesma instalação atende várias empresas clientes, cada uma com os seus
setores, atendentes, contatos e conversas, e existe um **gestor master** acima delas.

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
- `docs/DEPLOY.md` — deploy no VPS sob o prefixo `/beezap/`.
- `docs/WAPI_MEDIA_INTEGRATION.md` — mídia e LITE vs PRO da W-API.

## Como este projeto trabalha

- **Só implementar com pedido explícito** do usuário. O repositório **não** tem
  arquivo de plano/roadmap; pendências conhecidas ficam no fim de `docs/CONTEXTO.md`.
- Evoluir **por partes**, preservando uma base funcional; interface em **português**,
  simples e didática, pensada para quem tem pouca experiência técnica.
- **CSS por página** com classe raiz própria (`dashboard.css` é só a base do painel);
  incrementar o `?v=N` do link ao editar CSS. Layout responsivo, sem rolagem
  horizontal na página.
- Mensagens ao usuário via **toast**, curtas, sem dado técnico (token, payload,
  traceback, ID interno).
- **Multiempresa**: todo dado de cliente é filtrado por `company`; o **master
  administra e mede, mas não lê o atendimento** de ninguém. Ao criar tela ou
  endpoint, começar pelo escopo de empresa.

## Ambiente

- Local: venv em `./venv` — usar `.\venv\Scripts\python.exe manage.py ...`. Sem
  `.env` local (SQLite + `DEBUG=True` por padrão). **ffmpeg não instalado** aqui, então
  `manage.py check` sempre emite o aviso `beezap.W001` — é esperado, não é regressão.
- Produção: VPS Linux em `https://fabianopolone.com.br/beezap/`, app em
  `/var/www/beezap`, deploy com `bash deploy/deploy.sh`. **Todo deploy reinicia o
  gunicorn e confirma que os PIDs reciclaram** (com `DEBUG=False` o template fica em
  cache na memória dos workers). Ver `docs/DEPLOY.md`.
