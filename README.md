# BEEZAP

Projeto Django para a plataforma BEEZap, com tela inicial de login e base organizada para evoluir por módulos.

## Regra fixa de trabalho (vale para toda alteração)

> **Nenhuma alteração fica sem `commit` + `push` e sem documentação atualizada.**
> Toda mudança — código, CSS, template ou texto — se fecha no mesmo passo:

1. Fazer a alteração.
2. Rodar `python manage.py check` (e `makemigrations`/`migrate` se mexer em model);
   rodar os testes quando mexer no backend. **Check quebrado não vira commit.**
3. Atualizar a documentação afetada — **sempre** o final de
   [docs/HISTORICO.md](docs/HISTORICO.md) e, quando o estado do projeto mudar,
   [docs/CONTEXTO.md](docs/CONTEXTO.md) (arquitetura, telas, endpoints, comandos,
   pendências) e os demais arquivos de `docs/`. A documentação reflete sempre o
   estado atual.
4. Commit atômico com mensagem em **PT-BR** (`feat:`/`fix:`/`docs:`/`style:`/`chore:`),
   **código e documentação no mesmo commit**.
5. `git push` na hora — nada de acumular mudança local para "enviar depois". O
   repositório remoto é o estado real do projeto.

Nunca commitar `.env`, `db.sqlite3`, `venv/` ou credenciais. Detalhes em
[docs/CODEX_PADROES.md](docs/CODEX_PADROES.md) e [docs/GIT.md](docs/GIT.md); a mesma
regra fica em [CLAUDE.md](CLAUDE.md), que o agente carrega em toda sessão.

## Documentação

- [docs/CONTEXTO.md](docs/CONTEXTO.md) — **comece por aqui**: visão geral, arquitetura,
  multiempresa, W-API, telas e armadilhas do VPS (documento de handoff).
- [docs/INDEX.md](docs/INDEX.md) — índice de toda a documentação.
- [docs/CODEX_PADROES.md](docs/CODEX_PADROES.md) — padrões de UI/CSS/commit.
- [docs/GIT.md](docs/GIT.md) — regras de Git, commit e push.
- [docs/HISTORICO.md](docs/HISTORICO.md) — o que já foi feito e as decisões tomadas.
- [docs/DEPLOY.md](docs/DEPLOY.md) — deploy no VPS sob o prefixo `/beezap/`.
- [docs/WAPI_MEDIA_INTEGRATION.md](docs/WAPI_MEDIA_INTEGRATION.md) — emoji, mídia e
  recursos LITE vs PRO da W-API.
