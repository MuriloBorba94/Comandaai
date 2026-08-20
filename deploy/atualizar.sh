#!/usr/bin/env bash
# Publica uma versão nova do Comanda ai.
#
#   sudo /opt/comandaai/deploy/atualizar.sh
#
# Roda como ROOT, e desce para o usuário `comandaai` nas partes que mexem no
# código. O contrário não funciona: `comandaai` é um usuário de sistema, sem
# direito a sudo, e não consegue reiniciar o serviço.
#
# A ordem importa: backup antes de tudo, migration antes de reiniciar. Se a
# migration falhar, o `set -e` aborta aqui e o serviço continua rodando na
# versão ANTIGA — melhor do que subir código novo contra um banco velho.
set -euo pipefail

RAIZ=/opt/comandaai
APP=comandaai

if [ "$(id -u)" -ne 0 ]; then
	echo "Rode como root:  sudo $0" >&2
	exit 1
fi

cd "$RAIZ"

echo "==> Backup antes de mexer"
# Como o usuário da aplicação, para os arquivos não ficarem do root e o backup
# agendado (que roda como comandaai) continuar conseguindo escrever na pasta.
sudo -u "$APP" "$RAIZ/deploy/backup.sh"

echo "==> Versão atual"
antes=$(sudo -u "$APP" git -C "$RAIZ" rev-parse --short HEAD)
echo "    $antes"

echo "==> Baixando o código"
sudo -u "$APP" git -C "$RAIZ" pull --ff-only

depois=$(sudo -u "$APP" git -C "$RAIZ" rev-parse --short HEAD)
if [ "$antes" = "$depois" ]; then
	echo "==> Nada novo ($depois). Saindo sem reiniciar."
	exit 0
fi
echo "    $antes -> $depois"

echo "==> Dependências"
sudo -u "$APP" "$RAIZ/.venv/bin/pip" install --quiet --upgrade -r "$RAIZ/requirements.txt"

echo "==> Migrations"
sudo -u "$APP" env FLASK_APP=run.py "$RAIZ/.venv/bin/python" -m flask db upgrade

echo "==> Reiniciando"
systemctl restart "$APP"
sleep 2

if systemctl is-active --quiet "$APP"; then
	echo "OK: serviço ativo na versão $depois"
else
	echo "FALHOU: o serviço não subiu." >&2
	echo "Veja o motivo:  journalctl -u $APP -n 50 --no-pager" >&2
	echo "Para voltar:    sudo -u $APP git -C $RAIZ reset --hard $antes && systemctl restart $APP" >&2
	exit 1
fi
