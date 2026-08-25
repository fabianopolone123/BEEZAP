# Deploy e arquivos estáticos (CSS/JS) do BEEonBOARD

Este documento explica como publicar o BEEonBOARD no VPS e, principalmente, como
garantir que alterações de **CSS/JS apareçam** em produção após o deploy.

## Ambiente atual (produção)

- Projeto em: `/var/www/beezap/`
- Servido pelo **gunicorn** (systemd, serviço `beezap`) atrás do **Nginx**.
- URL pública sob o prefixo **`/beeonboard/`** (ex.: `https://fabianopolone.com.br/beeonboard/`).
- Config do Nginx do domínio: `/etc/nginx/sites-available/site_idiomas`.
- Pastas de estáticos:
  - Fonte (no Git): `/var/www/beezap/static/` (ex.: `static/css/conversations.css`)
  - Coletada (servida pelo Nginx): `/var/www/beezap/staticfiles/`

## Dependências do sistema

Além do Python **3.12+** e dos pacotes pip (`requirements.txt`: Django, gunicorn,
psycopg e **pywebpush**, este último para o aviso de nova mensagem), o servidor precisa
destas dependências de **sistema** (não vêm pelo pip):

- **ffmpeg** — **OBRIGATÓRIO** para o envio de mídia. Converte (1) o áudio gravado
  no navegador (`.webm` do Chrome) para `.ogg` e (2) imagens não suportadas pela
  W-API (**webp/gif/bmp/heic...**) para `.jpg` (a W-API exige URL terminando em
  `.png`/`.jpeg`/`.jpg`). Sem ele, o **envio de áudio gravado e de imagens
  webp/gif/etc. falha** (JPG/PNG, vídeo, documento e texto continuam funcionando).
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```
  > O `python manage.py check` avisa quando o ffmpeg está ausente
  > (**`beezap.W001`**) — assim o problema aparece no deploy, não só em produção.
- **nginx** — proxy reverso; serve `/beeonboard/static/` e **apenas**
  `/beeonboard/media/empresas/` (logos). A mídia das conversas **não** é servida pelo
  Nginx — ver a seção de mídia mais abaixo.
- **git** — deploy via `git pull`.

Verificação rápida depois de instalar:
```bash
ffmpeg -version            # deve imprimir a versão
cd /var/www/beezap && venv/bin/python manage.py check   # não deve listar beezap.W001
```

## Variáveis de ambiente obrigatórias (`.env`)

Para o app funcionar sob o prefixo `/beeonboard/` e para a mídia funcionar:

```
FORCE_SCRIPT_NAME=/beezap      # Django gera todas as URLs com o prefixo
STATIC_URL=/beeonboard/static/     # CSS/JS servidos pelo Nginx sob /beeonboard/static/
MEDIA_URL=/beeonboard/media/       # caminho dos arquivos salvos (logos das empresas)
```

As credenciais da W-API (Instance ID e Token) ficam salvas no banco pela tela de
Configurações — não precisam estar no `.env`.

### Aviso de nova mensagem (Web Push): chaves VAPID

**Sem estas chaves o pop-up de nova mensagem NÃO chega com a aba em segundo plano** —
e é justamente aí que ele importa. O aviso antigo dependia de um timer de 6s na tela, e
o Chrome estrangula timer de aba oculta para 1x/minuto (ver seção 5.4 do `CONTEXTO.md`).

Gere o par **uma vez, no servidor** (a chave privada não deve trafegar por chat/e-mail
nem ir para o Git):

```bash
cd /var/www/beezap && venv/bin/python manage.py gerar_chaves_vapid
```

Cole as três linhas no `.env` e reinicie o serviço:

```
WEBPUSH_VAPID_PUBLIC_KEY=...
WEBPUSH_VAPID_PRIVATE_KEY=...      # SEGREDO
WEBPUSH_VAPID_SUBJECT=mailto:contato@fabianopolone.com.br
```

> O `manage.py check` avisa quando faltam (**`beezap.W003`**), como faz com o ffmpeg.
> **Trocar o par depois obriga todos os navegadores a se inscreverem de novo** (as
> inscrições antigas param de ser aceitas) — então gere uma vez e guarde.

Cada pessoa ainda precisa **clicar no sino** da tela Conversas uma vez, para conceder a
permissão do navegador e inscrever aquele aparelho. Um aparelho por inscrição: celular e
desktop são duas.

## ⚠️ Mídia das conversas NÃO pode ser servida pelo Nginx

As fotos, áudios, vídeos e documentos das conversas (`media/whatsapp/`) são
**conteúdo dos clientes**. Servindo essa pasta direto pelo Nginx, o arquivo fica
acessível a **qualquer um que descubra o caminho** — sem login, sem checagem de
empresa. Era assim até aqui, e os arquivos recebidos ainda usavam nome sequencial
(`wapi_<id>.jpg`), o que tornava a enumeração trivial.

Hoje a mídia sai por duas rotas do próprio Django:

- **`/beeonboard/midia/<id>/`** — exige login e aplica as regras da conversa (empresa +
  alcance). É o que o chat usa. O **gestor master também é barrado** aqui.
- **`/beeonboard/midia-publica/<token>/`** — link **assinado** e de **curta duração**
  (15 min), usado só para a W-API (que roda na nuvem) baixar a mídia que enviamos.

**No Nginx, o bloco `location /beeonboard/media/` deve ser trocado** por um que libere
apenas os logos das empresas:

```nginx
# REMOVER:  location /beeonboard/media/ { alias /var/www/beezap/media/; }
location /beeonboard/media/empresas/ {
    alias /var/www/beezap/media/empresas/;
    access_log off;
}
```

Confira depois do deploy (o primeiro tem que dar **404**, o segundo **302/200**):

```bash
curl -o /dev/null -s -w "%{http_code}\n" https://fabianopolone.com.br/beeonboard/media/whatsapp/wapi_1.jpg
curl -o /dev/null -s -w "%{http_code}\n" https://fabianopolone.com.br/beeonboard/midia/1/
```

> `client_max_body_size` continua valendo para o **upload**; o download agora passa
> pelo gunicorn (`FileResponse`), então nada precisa mudar de tamanho.

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
  então o prefixo `/beeonboard/static/` fica no `.env` e **ninguém precisa editar o
  `settings.py` no servidor**.
- Mantém `STATICFILES_DIRS = [BASE_DIR / 'static']` com aviso para nunca esvaziar.

No `.env` de produção:
```
STATIC_URL=/beeonboard/static/
```

### Opção A (RECOMENDADA): Nginx serve a pasta-fonte `static/`

Assim, **todo `git pull` já publica o CSS/JS** — sem `collectstatic`, sem `cp`.
No `server { }` do domínio (`/etc/nginx/sites-available/site_idiomas`), deixar:

```nginx
# admin do Django vem do collectstatic (mais especifico, vem antes):
location /beeonboard/static/admin/ { alias /var/www/beezap/staticfiles/admin/; }
# CSS/JS/imagens do BEEonBOARD servidos direto da fonte:
location /beeonboard/static/       { alias /var/www/beezap/static/; }
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
→ `restart` do serviço **com verificação automática do reinício**. Ele guarda os
PIDs do gunicorn antes e depois do `restart`; se os workers **não reciclaram** (PIDs
iguais ou nenhum processo), **força** o reinício de verdade (`stop` + `pkill` +
`start`) e, se mesmo assim o gunicorn não subir, **aborta com erro** (`exit 1`). Ao
final imprime o `etimes` dos processos (deve ser poucos segundos). Ou seja: a
armadilha do template em cache (abaixo) passou a ser tratada **automaticamente** pelo
`deploy.sh` — não precisa mais conferir na mão.

## ⚠️ Mudança de TEMPLATE não aparece? Reinicie o gunicorn de verdade

Como o servidor roda com **`DEBUG=False`**, o Django **cacheia os templates
compilados na memória de cada worker do gunicorn** (`cached.Loader`). Um `git pull`
atualiza o disco, mas o gunicorn **continua servindo o template ANTIGO** até os
workers serem **reiniciados de fato**. Sintoma: a alteração de HTML "não aparece"
no navegador (nem em aba anônima, nem no celular no 4G), enquanto o disco
(`manage.py shell` lendo o template) e o CSS (`curl`) já estão novos. **Não é cache
de navegador nesses casos** (o Nginx não tem `proxy_cache` nem há CDN).

Sempre reinicie **e confirme que os PIDs foram reciclados**:
```bash
sudo systemctl restart beezap
ps -eo pid,etimes,cmd | grep "[b]eezap/venv/bin/gunicorn"   # etimes deve ser pequeno (segundos)
```
Se o `etimes` continuar grande (o processo não recriou), force:
```bash
sudo systemctl stop beezap
sudo pkill -f "beezap/venv/bin/gunicorn"
sudo systemctl start beezap
```
> Por isso, prefira sempre o `deploy/deploy.sh` (que já reinicia).

## ⚠️ Número quebrando CSS/atributo em template (locale pt-BR)

`LANGUAGE_CODE='pt-br'` faz o Django imprimir **float com vírgula** no template
(`{{ 6.0 }}` → `6,0`). Se esse número entra em **CSS/atributo** (`style="left: {{ x }}%"`,
atributo SVG), a vírgula gera valor **inválido** e o navegador ignora — foi o bug do
gráfico do dashboard. **Regra:** número que vai para CSS/atributo dentro de template →
`{% load l10n %}{% localize off %}…{% endlocalize %}` ou montar a string no Python
(strings não são localizadas).

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
ficar em cache do navegador). O cache-busting é **automático**: todo link de CSS/JS
usa a tag `{% asset 'css/arquivo.css' %}`, que deriva a versão da **data de
modificação do arquivo** — não existe mais `?v=N` na mão (ver `CONTEXTO.md`, seção 6).

## Reconciliar o settings.py editado à mão (uma vez)

Se o `settings.py` do servidor ainda estiver com edições manuais:
```bash
cd /var/www/beezap
git diff config/settings.py                         # ver o que foi editado
grep -q '^STATIC_URL=' .env || echo 'STATIC_URL=/beeonboard/static/' >> .env
git checkout -- config/settings.py                  # descartar edicao manual
git pull                                            # pega a versao versionada
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart beezap
```

## Observação de segurança

Em produção o ideal é `DEBUG=False` no `.env` (com `DEBUG=True` o Django expõe
traceback técnico ao usuário final). Ajustar quando possível.

