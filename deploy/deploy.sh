#!/usr/bin/env bash
# Deploy padrao do BEEonBOARD no VPS.
# Uso: bash deploy/deploy.sh
#
# O que ele faz, em ordem:
#   git pull -> dependencias -> BACKUP DO BANCO -> migracoes -> collectstatic ->
#   check --deploy -> restart do gunicorn COM VERIFICACAO de que os PIDs reciclaram.
#
# Publica alteracoes de codigo E de arquivos estaticos (CSS/JS/imagens).
# Nao depende de `cp -r static/* staticfiles/` manual.
#
# Os identificadores tecnicos seguem `beezap` de proposito (servico systemd, pasta
# /var/www/beezap): renomea-los nao e necessario e arriscaria o ambiente no ar.
set -e

BASE="/var/www/beezap"
cd "$BASE"

echo ">> git pull..."
git pull

echo ">> dependencias..."
venv/bin/pip install -r requirements.txt

echo ">> backup do banco (antes de migrar)..."
# Com SQLite nao existe rollback de migration: se algo der errado no `migrate`, a
# unica volta e o arquivo de antes. Guardamos as 10 copias mais recentes.
if [ -f db.sqlite3 ]; then
    mkdir -p backup
    CARIMBO="$(date +%Y%m%d-%H%M%S)"
    # `.backup` do sqlite3 e consistente com o app no ar (um `cp` pode pegar a base
    # no meio de uma escrita). Se o binario nao existir, cai para o cp.
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 db.sqlite3 ".backup 'backup/db-${CARIMBO}.sqlite3'"
    else
        cp db.sqlite3 "backup/db-${CARIMBO}.sqlite3"
    fi
    echo "   backup/db-${CARIMBO}.sqlite3"
    # Mantem so as 10 copias mais novas (a pasta nao pode crescer sem fim).
    ls -1t backup/db-*.sqlite3 2>/dev/null | tail -n +11 | xargs -r rm -f
else
    echo "   (sem db.sqlite3 — provavelmente PostgreSQL; backup e do servidor de banco)"
fi

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

echo ">> conferindo a configuracao de producao (check --deploy)..."
# Nao aborta o deploy: alguns avisos sao decisoes conscientes (ex.: HSTS desligado
# porque vale para o dominio inteiro). Mas fica registrado na saida do deploy, em vez
# de so aparecer quando alguem lembra de rodar o comando a mao.
venv/bin/python manage.py check --deploy 2>&1 | sed 's/^/   /' || true

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
