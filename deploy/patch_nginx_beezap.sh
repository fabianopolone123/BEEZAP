#!/usr/bin/env bash
# Ajusta o Nginx do BEEZAP para servir os estaticos do APP direto da pasta-fonte
# (/var/www/beezap/static/), com o admin do Django vindo de staticfiles/admin/.
# Depois disso, um simples `git pull` ja publica CSS/JS (sem collectstatic/cp).
#
# Seguro: cria backup, aplica so no bloco /beezap/static/, valida com `nginx -t`
# e restaura o backup se algo der errado.
#
# Uso: sudo bash deploy/patch_nginx_beezap.sh
#      (opcional) sudo bash deploy/patch_nginx_beezap.sh /caminho/do/site
set -e

SITE="${1:-/etc/nginx/sites-available/site_idiomas}"
[ -f "$SITE" ] || { echo "Arquivo nao encontrado: $SITE"; exit 1; }

BACKUP="$SITE.bak.$(date +%Y%m%d%H%M%S)"
cp "$SITE" "$BACKUP"
echo "Backup criado: $BACKUP"

python3 - "$SITE" <<'PY'
import sys
path = sys.argv[1]
src = open(path, encoding='utf-8').read()

old = (
    "    location /beezap/static/ {\n"
    "        alias /var/www/beezap/staticfiles/;\n"
    "        access_log off;\n"
    "        expires 7d;\n"
    "    }\n"
)
new = (
    "    location /beezap/static/admin/ {\n"
    "        alias /var/www/beezap/staticfiles/admin/;\n"
    "        access_log off;\n"
    "        expires 7d;\n"
    "    }\n"
    "\n"
    "    location /beezap/static/ {\n"
    "        alias /var/www/beezap/static/;\n"
    "        access_log off;\n"
    "    }\n"
)

if "location /beezap/static/admin/" in src:
    print("Ja parece ajustado (bloco admin presente). Nada a fazer.")
    sys.exit(0)
if old not in src:
    print("ERRO: nao encontrei o bloco /beezap/static/ exatamente como esperado.")
    print("Ajuste manualmente seguindo deploy/nginx_beezap.conf.example.")
    sys.exit(2)

open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
print("Bloco /beezap/static/ ajustado: app servido de static/, admin de staticfiles/admin/.")
PY

echo ">> Validando configuracao do Nginx (nginx -t)..."
if nginx -t; then
    systemctl reload nginx
    echo ">> OK: Nginx recarregado. Agora 'git pull' ja publica CSS/JS do BEEZAP."
else
    echo ">> ERRO no nginx -t. Restaurando backup..."
    cp "$BACKUP" "$SITE"
    echo ">> Backup restaurado; nenhuma mudanca aplicada."
    exit 1
fi
