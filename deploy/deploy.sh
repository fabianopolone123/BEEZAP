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

echo ">> reiniciando servico (com verificacao de restart)..."
# ARMADILHA (ver docs/DEPLOY.md): com DEBUG=False o Django guarda os templates
# compilados na MEMORIA de cada worker do gunicorn. Um `git pull` atualiza o disco,
# mas o gunicorn continua servindo o template ANTIGO ate os workers serem REALMENTE
# reciclados. Ja houve caso de `systemctl restart` NAO reciclar. Por isso o deploy
# confirma que os PIDs mudaram e, se nao mudaram, forca o reinicio de verdade.
GUNICORN_PATTERN="beezap/venv/bin/gunicorn"

pids_before="$(pgrep -f "$GUNICORN_PATTERN" | sort | tr '\n' ' ')"
echo "   PIDs antes:  ${pids_before:-nenhum}"

sudo systemctl restart beezap
sleep 2
pids_after="$(pgrep -f "$GUNICORN_PATTERN" | sort | tr '\n' ' ')"
echo "   PIDs depois: ${pids_after:-nenhum}"

# Sem processo novo OU PIDs identicos aos antigos => nao reciclou. Forca o reinicio.
if [ -z "$pids_after" ] || [ "$pids_before" = "$pids_after" ]; then
    echo "   AVISO: os workers NAO reciclaram. Forcando o reinicio de verdade..."
    sudo systemctl stop beezap || true
    sudo pkill -f "$GUNICORN_PATTERN" 2>/dev/null || true
    sleep 1
    sudo systemctl start beezap
    sleep 2
    pids_after="$(pgrep -f "$GUNICORN_PATTERN" | sort | tr '\n' ' ')"
    echo "   PIDs depois (forcado): ${pids_after:-nenhum}"
fi

# Verificacao final: precisa haver gunicorn rodando; senao aborta com erro.
if [ -z "$pids_after" ]; then
    echo "   ERRO: o gunicorn do beezap NAO esta rodando apos o restart!" >&2
    sudo systemctl status beezap --no-pager -l 2>&1 | tail -20 || true
    exit 1
fi
echo "   OK: gunicorn reiniciado. Idade dos processos (etimes = poucos segundos):"
ps -eo pid,etimes,cmd | grep "[b]eezap/venv/bin/gunicorn" || true

echo ">> OK. Deploy concluido."
echo "   Dica: valide com  bash deploy/diag_static.sh  e recarregue com Ctrl+F5."
