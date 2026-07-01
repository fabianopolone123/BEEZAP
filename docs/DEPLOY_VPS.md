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
sudo apt install python3-venv python3-pip git nginx
```

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
