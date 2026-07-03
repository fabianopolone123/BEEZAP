#!/usr/bin/env bash
# Diagnostico de entrega de arquivos estaticos do BEEZAP.
# Uso: bash deploy/diag_static.sh
set -u
BASE=/var/www/beezap

echo "===== 1) o arquivo-fonte tem a correcao? (esperado: >=1) ====="
grep -cF '[hidden]' "$BASE/static/css/conversations.css" 2>&1 || echo "sem arquivo fonte"

echo
echo "===== 2) conteudo de staticfiles/ ====="
ls -la "$BASE/staticfiles/" 2>&1 | head -20

echo
echo "===== 3) conteudo de staticfiles/css/ ====="
ls -la "$BASE/staticfiles/css/" 2>&1 | head -30

echo
echo "===== 4) onde existe conversations.css no projeto inteiro ====="
find "$BASE" -name 'conversations*.css' 2>/dev/null

echo
echo "===== 5) o que o Django acha (DEBUG / STATIC) ====="
"$BASE/venv/bin/python" "$BASE/manage.py" shell -c "from django.conf import settings as s; print('DEBUG =', s.DEBUG); print('STATIC_URL =', s.STATIC_URL); print('STATIC_ROOT =', s.STATIC_ROOT); print('STATICFILES_DIRS =', list(s.STATICFILES_DIRS))" 2>&1

echo
echo "===== 6) config do nginx (dominio / static / proxy) ====="
grep -rniE 'server_name|static|alias|proxy_pass' /etc/nginx 2>/dev/null | grep -v '#' | head -40
echo "(se vazio, o servidor web pode nao ser o nginx)"

echo
echo "===== 7) quem escuta nas portas 80/443 ====="
ss -ltnp 2>/dev/null | grep -E ':80|:443' | head

echo
echo "===== FIM ====="
