# Deploy VPS BEEZAP

Guia para colocar o BEEZAP em uma VPS Linux em modo homologacao/teste.

Esta etapa nao e producao definitiva. O objetivo e ter uma URL publica segura para continuar o desenvolvimento e testar o webhook da W-API.

## Premissas

- Projeto Django: `config.wsgi:application`.
- Pasta sugerida: `/var/www/beezap`.
- Servico sugerido: `beezap`.
- Porta interna sugerida: `127.0.0.1:8006`.
- Dominio sugerido: `beezap.seudominio.com`.
- Webhook W-API: `https://beezap.seudominio.com/webhook/wapi/`.

Antes de usar a porta `8006`, confirme se ela esta livre.

```bash
sudo ss -ltnp | grep 8006
```

## Cuidados com VPS que ja possui outros projetos em producao

- Nao alterar `nginx.conf` global sem necessidade.
- Nao apagar arquivos existentes em `sites-enabled` ou `sites-available`.
- Nao reiniciar Nginx antes de rodar `sudo nginx -t`.
- Preferir `reload` em vez de `restart` quando apropriado.
- Nao mexer em certificados de outros dominios.
- Nao alterar firewall sem entender impacto nos outros projetos.
- Usar pasta separada para o BEEZAP.
- Usar venv separado.
- Usar banco separado.
- Usar usuario de banco separado.
- Usar systemd service separado.
- Fazer backup das configuracoes antes de mexer em Nginx.
- Nao reutilizar banco, usuario, socket ou porta de outros projetos.

## 1. Pre-requisitos

Na VPS, validar Python e ferramentas basicas:

```bash
python3 --version
sudo apt update
sudo apt install python3-venv python3-pip git nginx ffmpeg
```

> **ffmpeg e OBRIGATORIO** para o envio de midia: converte o audio gravado no
> navegador (.webm -> .ogg) e imagens nao suportadas pela W-API (webp/gif/bmp/heic
> -> .jpg). Sem ele o envio desses formatos falha (o `manage.py check` avisa —
> `beezap.W001`). Ver `requirements.txt` e `docs/DEPLOY.md`.

Se for usar PostgreSQL:

```bash
sudo apt install postgresql postgresql-contrib libpq-dev
```

Nao instale ou reinicie servicos sem validar que isso nao afeta os outros projetos.

## 2. Clonar repositorio

Exemplo:

```bash
sudo mkdir -p /var/www/beezap
sudo chown -R $USER:$USER /var/www/beezap
git clone https://github.com/fabianopolone123/BEEZAP.git /var/www/beezap
cd /var/www/beezap
git checkout feature/wapi-mvp
```

## 3. Criar ambiente virtual

```bash
cd /var/www/beezap
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Criar `.env`

Copie o exemplo:

```bash
cp .env.example .env
nano .env
```

Defina valores reais apenas na VPS. Nao commitar `.env`.

Exemplo de homologacao:

```env
SECRET_KEY=troque-por-uma-chave-forte
DEBUG=False
ALLOWED_HOSTS=beezap.seudominio.com
CSRF_TRUSTED_ORIGINS=https://beezap.seudominio.com
DATABASE_URL=sqlite:////var/www/beezap/db.sqlite3
WAPI_WEBHOOK_TOKEN=troque-este-token
```

## 5. Banco de dados

### Opcao A: SQLite temporario

Aceitavel apenas para teste simples/homologacao inicial.

```env
DATABASE_URL=sqlite:////var/www/beezap/db.sqlite3
```

### Opcao B: PostgreSQL recomendado

Criar banco separado. Nao reutilizar banco de outros projetos.

Exemplo:

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE beezap_db;
CREATE USER beezap_user WITH PASSWORD 'troque-a-senha';
GRANT ALL PRIVILEGES ON DATABASE beezap_db TO beezap_user;
\q
```

No `.env`:

```env
DATABASE_URL=postgresql://beezap_user:troque-a-senha@127.0.0.1:5432/beezap_db
```

## 6. Migrar banco

Rodar somente no banco do BEEZAP:

```bash
source /var/www/beezap/.venv/bin/activate
cd /var/www/beezap
python manage.py migrate
```

## 7. Arquivos estaticos

```bash
python manage.py collectstatic
```

O projeto usa:

```text
STATIC_ROOT=/var/www/beezap/staticfiles
```

## 8. Testar Django

```bash
python manage.py check
```

## 9. Testar Gunicorn manualmente

Antes de criar systemd, validar a porta:

```bash
sudo ss -ltnp | grep 8006
```

Se estiver livre:

```bash
source /var/www/beezap/.venv/bin/activate
cd /var/www/beezap
gunicorn config.wsgi:application --bind 127.0.0.1:8006
```

Em outro terminal:

```bash
curl -I http://127.0.0.1:8006/
```

## 10. Configurar systemd

Use o arquivo de exemplo:

```text
deploy/beezap.service.example
```

Copiar manualmente, revisando caminhos, usuario e porta:

```bash
sudo cp /var/www/beezap/deploy/beezap.service.example /etc/systemd/system/beezap.service
sudo nano /etc/systemd/system/beezap.service
```

Depois:

```bash
sudo systemctl daemon-reload
sudo systemctl enable beezap
sudo systemctl start beezap
sudo systemctl status beezap
```

Nao sobrescrever servicos existentes.

## 11. Configurar Nginx

Use o arquivo de exemplo:

```text
deploy/nginx_beezap.conf.example
```

Copiar manualmente:

```bash
sudo cp /var/www/beezap/deploy/nginx_beezap.conf.example /etc/nginx/sites-available/beezap
sudo nano /etc/nginx/sites-available/beezap
```

Ativar sem apagar outros sites:

```bash
sudo ln -s /etc/nginx/sites-available/beezap /etc/nginx/sites-enabled/beezap
sudo nginx -t
```

Somente se `nginx -t` passar:

```bash
sudo systemctl reload nginx
```

## 12. Dominio e HTTPS

Apontar o DNS do subdominio para a VPS.

Exemplo:

```text
beezap.seudominio.com -> IP_DA_VPS
```

Para HTTPS, usar Certbot ou ferramenta ja padronizada na VPS. Nao mexer nos certificados de outros dominios sem backup.

## 13. Webhook W-API

Na W-API, configurar no campo:

```text
Ao receber uma mensagem
```

URL:

```text
https://beezap.seudominio.com/webhook/wapi/
```

Se `WAPI_WEBHOOK_TOKEN` estiver configurado, usar uma das opcoes:

Query string:

```text
https://beezap.seudominio.com/webhook/wapi/?token=TOKEN_DO_WEBHOOK
```

Header:

```text
X-BEEZAP-WEBHOOK-TOKEN: TOKEN_DO_WEBHOOK
```

Nao usar o token da W-API como token do webhook.

## 14. Ollama/IA

A VPS informada tem 4 GB e ja roda outros projetos. Nao instalar Ollama nesta etapa sem medir RAM/CPU.

Configuracoes disponiveis por `.env`:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_TIMEOUT=30
OLLAMA_TEMPERATURE=0.2
OLLAMA_NUM_PREDICT=180
OLLAMA_NUM_GPU=0
```

Ativar IA local no VPS apenas depois de avaliar consumo.

## 15. Checklist final

- Porta `8006` livre.
- Pasta `/var/www/beezap` separada.
- **ffmpeg instalado** (`ffmpeg -version`) — envio de audio/imagem depende dele.
- `.env` criado e nao versionado.
- `DEBUG=False` no VPS.
- `ALLOWED_HOSTS` com dominio correto.
- `CSRF_TRUSTED_ORIGINS` com `https://dominio`.
- Banco separado.
- `python manage.py migrate` rodado no banco correto.
- `python manage.py collectstatic` executado.
- Gunicorn testado manualmente.
- Service systemd separado.
- Nginx server block separado.
- `sudo nginx -t` passou.
- Nginx recarregado somente apos teste.
- HTTPS configurado sem mexer em outros certificados.
- Webhook `/webhook/wapi/` acessivel publicamente.
- Token do webhook configurado.
- W-API configurada no campo "Ao receber uma mensagem".

## 16. Comandos uteis de verificacao

```bash
sudo systemctl status beezap
sudo journalctl -u beezap -f
sudo nginx -t
curl -I https://beezap.seudominio.com/
curl -X POST https://beezap.seudominio.com/webhook/wapi/ -H "Content-Type: application/json" -d '{"event":"message.received"}'
```

## 17. Estado atual do VPS BEEZAP (ambiente real)

Atualizado em 2026-07-08.

- Host: `145.223.93.162`.
- Aplicacao: `/var/www/beezap/`.
- Branch de deploy: `main`.
- Deploy padrao: sempre via Git + `bash deploy/deploy.sh` dentro de `/var/www/beezap`.
- Servico Django/Gunicorn: `beezap`.
- Porta interna real: `127.0.0.1:8103`.
- URL publica: `https://fabianopolone.com.br/beezap/`.
- Nginx do dominio: `/etc/nginx/sites-available/site_idiomas`.
- Prefixo Django: `FORCE_SCRIPT_NAME=/beezap` no `.env`.
- Banco atual: SQLite em `/var/www/beezap/db.sqlite3`.
- Static/media esperados no `.env`: `STATIC_URL=/beezap/static/` e `MEDIA_URL=/beezap/media/`.
- `ffmpeg` instalado e `manage.py check` sem avisos no VPS.
- `DEBUG=True` ainda aparece como pendencia de seguranca se estiver assim no `.env`.

### Estado atual da IA/Ollama

- Ollama instalado no sistema por `curl -fsSL https://ollama.com/install.sh | sh`.
- Servico systemd: `ollama`, ativo.
- API local: `http://127.0.0.1:11434`.
- Modelo baixado: `qwen2.5:1.5b`.
- Modo: CPU-only (`OLLAMA_NUM_GPU=0`).
- Override systemd leve em `/etc/systemd/system/ollama.service.d/beezap-light.conf`:

```ini
[Service]
Environment=OLLAMA_KEEP_ALIVE=30s
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
```

- O modelo carrega para classificar e descarrega apos o keep-alive, liberando RAM.
- Atendente Virtual esta ativado no banco (`AiAttendantConfig.enabled=True`) com empresa `BEEZAP`, `max_turns=3` e fallback sem setor.
- Regras iniciais de IA aplicadas com:

```bash
cd /var/www/beezap
venv/bin/python manage.py seed_ai_sector_rules --overwrite
```

- Regras criadas/atualizadas: Compras/Vendas e Financeiro. As descricoes vazias desses setores tambem sao preenchidas pelo comando.

### Ciclo atual de atendimento

- Nova conversa direta sem setor/atendente cai na recepcao da IA.
- A IA envia boas-vindas, tenta classificar a intencao e, ao identificar setor, marca a conversa como `pending` no setor correto, sem escolher atendente.
- Enquanto a conversa estiver aberta/pendente com setor ou atendente, novas mensagens seguem no atendimento atual e nao passam pela IA.
- A tela Conversas tem acoes `Assumir` e `Encerrar`.
- Encerrar marca a conversa como `closed`; a proxima mensagem do mesmo contato cria nova conversa aberta sem setor/atendente e volta para a IA.

### Comandos de verificacao rapida

```bash
cd /var/www/beezap
git status --short
git rev-parse --short HEAD
venv/bin/python manage.py check
systemctl is-active beezap
systemctl is-active ollama
curl -s http://127.0.0.1:11434/api/tags | head
ollama ps
free -h
```

Teste de classificacao por regras/IA:

```bash
cd /var/www/beezap
venv/bin/python manage.py shell -c "from ai_engine.services import classify_intent; from accounts.models import Sector; r=classify_intent('preciso da segunda via do boleto', Sector.objects.all()); print(r.sector.name if r.sector else '-', r.source)"
```

Resultado esperado para a frase acima: `Financeiro keyword`.

### Regras operacionais

- Nao copiar arquivos locais direto para o VPS.
- Toda alteracao deve seguir: editar localmente, atualizar documentacao, `manage.py check`, testes quando aplicavel, commit pt-BR, `git push`, e no VPS `bash deploy/deploy.sh`.
- Nao commitar `.env`, banco, tokens, senhas ou payloads sensiveis.
- Rotacionar credenciais expostas em chat e preferir SSH key/usuario nao-root.
