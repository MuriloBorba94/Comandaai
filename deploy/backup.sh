#!/usr/bin/env bash
# Backup do Comanda ai: o banco e as fotos enviadas pelos restaurantes.
#
# Roda diariamente pelo cron (ver deploy/README.md) e antes de cada publicação.
# Guarda 14 dias e apaga o resto — num disco pequeno, backup infinito acaba
# enchendo o disco e derrubando o sistema que ele devia proteger.
set -euo pipefail

RAIZ=/opt/comandaai
DESTINO="$RAIZ/backups"
QUANDO=$(date +%Y%m%d-%H%M%S)
mkdir -p "$DESTINO"

# `sqlite3 .backup` em vez de copiar o arquivo: cópia crua de um SQLite com
# escrita em andamento sai corrompida.
if [ -f "$RAIZ/instance/saas.db" ]; then
	sqlite3 "$RAIZ/instance/saas.db" ".backup '$DESTINO/saas-$QUANDO.db'"
	gzip -f "$DESTINO/saas-$QUANDO.db"
fi

# As fotos não estão no banco: sem elas, o cardápio volta sem imagem nenhuma.
if [ -d "$RAIZ/app/static/uploads" ]; then
	tar -czf "$DESTINO/uploads-$QUANDO.tar.gz" -C "$RAIZ/app/static" uploads
fi

find "$DESTINO" -type f -mtime +14 -delete
echo "Backup em $DESTINO ($QUANDO)"
