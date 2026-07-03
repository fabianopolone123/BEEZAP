#!/usr/bin/env bash
# Deploy padrao do BEEZAP no VPS.
# Uso: bash deploy/deploy.sh
#
# Publica alteracoes de codigo E de arquivos estaticos (CSS/JS/imagens).
# Nao depende mais de `cp -r static/* staticfiles/` manual.
set -e

BASE="/var/www/beezap"
cd "$BASE"

echo ">> git pull..."
git pull

echo ">> dependencias..."
venv/bin/pip install -r requirements.txt

echo ">> migracoes..."
venv/bin/python manage.py migrate --noinput

echo ">> arquivos estaticos (collectstatic)..."
# Com STATICFILES_DIRS = [BASE_DIR/'static'] no settings.py, isto copia o CSS/JS
# do projeto para staticfiles/. Se estiver usando a OPCAO B do Nginx (servindo
# a pasta-fonte static/ direto), o collectstatic segue util para o admin.
venv/bin/python manage.py collectstatic --noinput

echo ">> verificando dependencias de sistema..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "   AVISO: ffmpeg nao encontrado. O envio de audio gravado no navegador"
    echo "          vai falhar. Instale com: sudo apt install -y ffmpeg"
fi

echo ">> reiniciando servico..."
sudo systemctl restart beezap

echo ">> OK. Deploy concluido."
echo "   Dica: valide com  bash deploy/diag_static.sh  e recarregue com Ctrl+F5."
