# Regras de Git do BEEZAP

## 1. Regra principal

- Cada etapa concluída deve gerar um commit próprio.
- Cada commit deve ser pequeno e relacionado somente à etapa atual.
- Depois do commit, deve ser feito `git push`.
- Se o check falhar, não pode fazer commit nem push.

## 2. Fluxo obrigatório ao final de cada etapa

Sempre rodar:

```bash
python manage.py check
```

Se passar, rodar:

```bash
git status
```

Depois adicionar somente os arquivos relacionados à etapa:

```bash
git add caminho/do/arquivo1 caminho/do/arquivo2
```

Depois fazer commit:

```bash
git commit -m "tipo: descrição curta"
```

Depois fazer push:

```bash
git push
```

Se for branch nova e o push pedir upstream:

```bash
git push -u origin NOME_DA_BRANCH
```

## 3. Arquivos que nunca devem ser enviados para o Git

Não adicionar:

- `.env`
- `.venv/`
- `venv/`
- `db.sqlite3`, se existir
- `__pycache__/`
- arquivos `.pyc`
- arquivos temporários
- logs sensíveis
- tokens
- senhas
- credenciais da W-API
- arquivos de cache

## 4. Antes de commitar

Verificar:

- se `python manage.py check` passou
- se não entrou `.env`
- se não entrou `db.sqlite3`
- se não entrou `.venv`
- se o commit tem somente arquivos da etapa atual
- se `docs/HISTORICO.md` foi atualizado apenas no final, sem apagar histórico antigo

## 5. Mensagens de commit recomendadas

Usar padrões:

- `docs:` para documentação
- `feat:` para nova funcionalidade
- `fix:` para correção
- `chore:` para configuração/manutenção
- `style:` para HTML/CSS/visual
- `test:` para testes
- `refactor:` apenas quando houver refatoração autorizada

Exemplos:

- `docs: criar plano inicial do BEEZAP`
- `chore: configurar variaveis de ambiente`
- `feat: criar modelos principais de atendimento`
- `feat: criar endpoint webhook da W-API`
- `fix: corrigir parser do webhook`
- `style: ajustar visual da tela de conversas`
- `test: adicionar testes do fluxo principal`

## 6. Regras para branch

- Trabalhar em branch de feature quando possível.
- Exemplo:

```bash
git checkout -b feature/wapi-mvp
```

- Se já estiver em uma branch correta, continuar nela.
- Não trocar de branch sem necessidade.
- Não fazer merge sem autorização.

## 7. Se der erro no Git

Se `git push` falhar por autenticação, permissão ou remote não configurado:

- não tentar inventar solução
- mostrar o erro exato
- explicar qual comando o usuário precisa rodar manualmente

## 8. Resumo obrigatório no final de cada etapa

O Codex deve informar:

- arquivos alterados
- resultado do `python manage.py check`
- arquivos adicionados ao commit
- mensagem do commit
- resultado do `git push`
- próximo passo recomendado
