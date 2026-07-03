# Deploy e arquivos estáticos (CSS/JS) do BEEZAP

Este documento explica como publicar o BEEZAP no VPS e, principalmente, como
garantir que alterações de **CSS/JS apareçam** em produção após o deploy.

## Ambiente atual (produção)

- Projeto em: `/var/www/beezap/`
- Servido pelo **gunicorn** (systemd, serviço `beezap`) atrás do **Nginx**.
- URL pública sob o prefixo **`/beezap/`** (ex.: `https://fabianopolone.com.br/beezap/`).
- Config do Nginx do domínio: `/etc/nginx/sites-available/site_idiomas`.
- Pastas de estáticos:
  - Fonte (no Git): `/var/www/beezap/static/` (ex.: `static/css/conversations.css`)
  - Coletada (servida pelo Nginx): `/var/www/beezap/staticfiles/`

## O problema que já aconteceu

Alterações de CSS (ex.: `conversations.css`) **não apareciam** no sistema mesmo
após `git pull`. Não era bug de código nem cache do navegador.

**Causa:** o `settings.py` tinha sido editado à mão no servidor e ficou com
`STATICFILES_DIRS = []`. Com essa lista vazia, o `collectstatic` **não copiava**
a pasta `static/` do projeto para `staticfiles/` (só copiava o estático do admin).
Como o Nginx serve `staticfiles/`, o CSS novo nunca chegava ao navegador.

Correção emergencial usada na época (não usar mais como solução):
```bash
cp -r /var/www/beezap/static/* /var/www/beezap/staticfiles/
```

## Correção definitiva

O `settings.py` do repositório agora:
- Lê `STATIC_URL` de variável de ambiente (`os.getenv('STATIC_URL', '/static/')`),
  então o prefixo `/beezap/static/` fica no `.env` e **ninguém precisa editar o
  `settings.py` no servidor**.
- Mantém `STATICFILES_DIRS = [BASE_DIR / 'static']` com aviso para nunca esvaziar.

No `.env` de produção:
```
STATIC_URL=/beezap/static/
```

### Opção A (RECOMENDADA): Nginx serve a pasta-fonte `static/`

Assim, **todo `git pull` já publica o CSS/JS** — sem `collectstatic`, sem `cp`.
No `server { }` do domínio (`/etc/nginx/sites-available/site_idiomas`), deixar:

```nginx
# admin do Django vem do collectstatic (mais especifico, vem antes):
location /beezap/static/admin/ { alias /var/www/beezap/staticfiles/admin/; }
# CSS/JS/imagens do BEEZAP servidos direto da fonte:
location /beezap/static/       { alias /var/www/beezap/static/; }
```

Aplicar:
```bash
sudo nginx -t && sudo systemctl reload nginx
# uma unica vez, para o admin do Django:
cd /var/www/beezap && venv/bin/python manage.py collectstatic --noinput
```

Depois disso, o fluxo de deploy vira só: `git pull` + reiniciar serviço.

### Opção B (alternativa): manter `collectstatic`

Manter o Nginx servindo `staticfiles/` (`alias /var/www/beezap/staticfiles/;`) e
rodar `collectstatic` em todo deploy. Como o `STATICFILES_DIRS` do repositório está
correto, o `collectstatic` passa a copiar o `static/` do projeto normalmente.

## Fluxo de deploy padrão

```bash
cd /var/www/beezap
bash deploy/deploy.sh
```

O script faz: `git pull` → `pip install` → `migrate` → `collectstatic --noinput`
→ `restart` do serviço.

## Como testar se o CSS novo foi publicado

```bash
bash deploy/diag_static.sh
```
Ou manualmente (marcador único da regra de layout do chat):
```bash
grep -cF '[hidden]' /var/www/beezap/static/css/conversations.css       # fonte
# Opcao A: servido da fonte, entao o de cima ja e o que vai ao ar.
# Opcao B: precisa aparecer tambem em staticfiles/:
grep -cF '[hidden]' /var/www/beezap/staticfiles/css/conversations.css
```
No navegador, sempre validar em **aba anônima** ou com **Ctrl + F5** (o CSS pode
ficar em cache do navegador). Os links de CSS da tela Conversas usam `?v=` para
ajudar a furar cache quando o arquivo muda.

## Reconciliar o settings.py editado à mão (uma vez)

Se o `settings.py` do servidor ainda estiver com edições manuais:
```bash
cd /var/www/beezap
git diff config/settings.py                         # ver o que foi editado
grep -q '^STATIC_URL=' .env || echo 'STATIC_URL=/beezap/static/' >> .env
git checkout -- config/settings.py                  # descartar edicao manual
git pull                                            # pega a versao versionada
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart beezap
```

## Observação de segurança

Em produção o ideal é `DEBUG=False` no `.env` (com `DEBUG=True` o Django expõe
traceback técnico ao usuário final). Ajustar quando possível.
