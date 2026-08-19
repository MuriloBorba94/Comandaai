#!/usr/bin/env bash
# Publica uma versão nova do Comanda ai.
#
#   sudo -u comandaai /opt/comandaai/deploy/atualizar.sh
#
# A ordem importa: backup antes de tudo, migration antes de reiniciar. Se a
# migration falhar, o serviço continua rodando na versão antiga em vez de subir
# com o código novo contra um banco velho.
set -euo pipefail

RAIZ=/opt/comandaai
cd "$RAIZ"

echo "==> Backup antes de mexer"
"$RAIZ/deploy/backup.sh"

echo "==> Baixando o código"
git pull --ff-only

echo "==> Dependências"
.venv/bin/pip install --quiet --upgrade -r requirements.txt

echo "==> Migrations"
FLASK_APP=run.py .venv/bin/python -m flask db upgrade

echo "==> Reiniciando"
sudo systemctl restart comandaai
sleep 2
systemctl is-active --quiet comandaai && echo "OK: serviço ativo" || {
	echo "FALHOU: veja  journalctl -u comandaai -n 50"
	exit 1
}
