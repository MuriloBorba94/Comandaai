#!/usr/bin/env bash
# Backup do Comanda ai: o banco e as fotos enviadas pelos restaurantes.
#
# Roda diariamente pelo cron (ver deploy/README.md) e antes de cada publicação.
# Guarda 14 dias e apaga o resto — num disco pequeno, backup infinito acaba
# enchendo o disco e derrubando o sistema que ele devia proteger.
#
# A parte delicada (copiar o banco e CONFERIR a cópia) mora dentro da aplicação,
# em `flask fazer-backup`. Duas razões: o caminho do banco vem da configuração
# em vez de escrito aqui, e aquilo é testado pela suíte. Este script cuida do
# resto — fotos, cópia para fora da máquina e limpeza.
set -euo pipefail

RAIZ=/opt/comandaai
DESTINO="$RAIZ/backups"
QUANDO=$(date +%Y%m%d-%H%M%S)
mkdir -p "$DESTINO"

# O banco, com conferência. Se a cópia não prestar, o comando sai com erro e o
# `set -e` interrompe tudo aqui — de propósito: backup ruim tratado como bom é
# pior do que backup nenhum, porque cria confiança onde não há.
cd "$RAIZ"
FLASK_APP=run.py "$RAIZ/.venv/bin/python" -m flask fazer-backup --destino "$DESTINO"

# As fotos não estão no banco: sem elas, o cardápio volta sem imagem nenhuma.
if [ -d "$RAIZ/app/static/uploads" ]; then
	tar -czf "$DESTINO/uploads-$QUANDO.tar.gz" -C "$RAIZ/app/static" uploads
fi

# ---------------------------------------------------------------------------
# Cópia para fora desta máquina
# ---------------------------------------------------------------------------
#
# Sem isto, tudo acima é só uma cópia no MESMO disco que deveria ser protegido.
# Disco com problema, servidor apagado por engano ou conta suspensa levam o
# sistema e o backup juntos.
#
# Configure `BACKUP_REMOTO` no .env com um destino do rclone, por exemplo:
#
#     BACKUP_REMOTO=gdrive:comandaai-backups
#
# Ver deploy/BACKUP-FORA-DO-DISCO.md.
REMOTO=$(grep -E '^BACKUP_REMOTO=' "$RAIZ/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs || true)

if [ -n "${REMOTO:-}" ]; then
	if ! command -v rclone >/dev/null 2>&1; then
		# Falha barulhenta, e não aviso discreto: quem configurou um destino
		# remoto acredita que o backup está saindo da máquina. Descobrir que
		# não estava é justamente o que este bloco existe para evitar.
		echo "ERRO: BACKUP_REMOTO está configurado mas o rclone não está instalado." >&2
		echo "      Instale com: sudo apt install -y rclone" >&2
		exit 1
	fi
	echo "==> Enviando para $REMOTO"
	rclone copy "$DESTINO" "$REMOTO" --include "saas-*" --include "uploads-*" --max-age 25h
	echo "    enviado"
else
	echo "AVISO: sem BACKUP_REMOTO no .env — o backup está no MESMO disco do sistema." >&2
fi

find "$DESTINO" -type f -mtime +14 -delete
echo "Backup em $DESTINO ($QUANDO)"
