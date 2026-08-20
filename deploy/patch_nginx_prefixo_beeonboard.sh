#!/usr/bin/env bash
# Troca o PREFIXO DE URL de /beezap/ para /beeonboard/ no Nginx (renome da marca:
# o sistema passou a se chamar BEEonBOARD).
#
# O que muda: apenas os blocos `location` do prefixo e o header X-Script-Name.
# O que NAO muda: a pasta da aplicacao (/var/www/beezap), o servico systemd
# (beezap), a porta do gunicorn (8103) e o banco. Renomear isso nao e necessario
# para trocar o endereco e so aumentaria o risco.
#
# O endereco antigo passa a REDIRECIONAR para o novo (301), para bookmark e link
# antigo continuarem funcionando.
#
# >>> ATENCAO — WEBHOOK DA W-API <<<
# Redirect NAO resolve o webhook: a W-API envia POST e POST nao segue 301. Depois
# de rodar este script, entre em cada cliente (tela WhatsApp) e RE-CADASTRE no
# painel da W-API a URL exibida (agora com /beeonboard/). Enquanto isso nao for
# feito, mensagem recebida NAO chega.
#
# Seguro: cria backup, valida com `nginx -t` e restaura o backup se algo falhar.
#
# Uso: sudo bash deploy/patch_nginx_prefixo_beeonboard.sh
#      (opcional) sudo bash deploy/patch_nginx_prefixo_beeonboard.sh /caminho/do/site
set -e

SITE="${1:-/etc/nginx/sites-available/site_idiomas}"
[ -f "$SITE" ] || { echo "Arquivo nao encontrado: $SITE"; exit 1; }

BACKUP="$SITE.bak.$(date +%Y%m%d%H%M%S)"
cp "$SITE" "$BACKUP"
echo "Backup criado: $BACKUP"

python3 - "$SITE" <<'PY'
import io
import sys

arquivo = sys.argv[1]
texto = io.open(arquivo, encoding='utf-8').read()
original = texto

if 'location /beeonboard/ {' in texto:
    print('Nada a fazer: o prefixo /beeonboard/ ja esta configurado.')
    raise SystemExit(0)

# 1) O endereco antigo passa a redirecionar para o novo.
velho_exato = """    location = /beezap {
        return 301 /beezap/;
    }
"""
novo_exato = """    # ENDERECO ANTIGO (/beezap/): redireciona para o novo. Serve para bookmark e
    # link antigo; NAO serve para o webhook da W-API, porque POST nao segue
    # redirect — a URL do webhook precisa ser re-cadastrada no painel da W-API.
    location = /beezap {
        return 301 /beeonboard/;
    }

    location /beezap/ {
        rewrite ^/beezap/(.*)$ /beeonboard/$1 permanent;
    }

    location = /beeonboard {
        return 301 /beeonboard/;
    }
"""
if texto.count(velho_exato) != 1:
    print('ERRO: bloco "location = /beezap" nao encontrado como esperado.')
    raise SystemExit(1)
texto = texto.replace(velho_exato, novo_exato)

# 2) Os blocos que servem o app respondem no prefixo novo. Os `alias` continuam
#    apontando para /var/www/beezap — a pasta da aplicacao nao muda.
trocas = [
    ('location /beezap/static/admin/ {', 'location /beeonboard/static/admin/ {'),
    ('location /beezap/static/ {', 'location /beeonboard/static/ {'),
    ('location /beezap/media/empresas/ {', 'location /beeonboard/media/empresas/ {'),
    ('location /beezap/ {\n        proxy_pass', 'location /beeonboard/ {\n        proxy_pass'),
    ('proxy_set_header X-Script-Name /beezap;', 'proxy_set_header X-Script-Name /beeonboard;'),
]
for velho, novo in trocas:
    if texto.count(velho) != 1:
        print('ERRO: trecho nao encontrado (ou repetido): %s' % velho)
        raise SystemExit(1)
    texto = texto.replace(velho, novo)

if '/var/www/beezap' not in texto:
    print('ERRO: a pasta da aplicacao (/var/www/beezap) desapareceu do arquivo.')
    raise SystemExit(1)

io.open(arquivo, 'w', encoding='utf-8', newline='').write(texto)
print('Nginx editado: /beeonboard/ agora serve o app, /beezap/ redireciona.')
PY

echo ">> validando a configuracao (nginx -t)..."
if nginx -t; then
    systemctl reload nginx
    echo ">> OK. Nginx recarregado."
    echo ">> FALTA: ajustar o .env (FORCE_SCRIPT_NAME, STATIC_URL, MEDIA_URL) e"
    echo "          reiniciar o servico: sudo systemctl restart beezap"
    echo ">> FALTA: re-cadastrar a URL do webhook no painel da W-API de cada cliente."
else
    cp "$BACKUP" "$SITE"
    echo ">> FALHOU no nginx -t. Backup restaurado: $BACKUP"
    exit 1
fi
