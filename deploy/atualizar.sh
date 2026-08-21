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

# O backup roda como o usuário da aplicação e é o primeiro passo. Se ele não
# puder ser executado, o set -e aborta aqui — e é o que se quer: publicar versão
# nova sem ter conseguido salvar a anterior é o pior dos dois mundos.
if [ ! -x "$RAIZ/deploy/backup.sh" ]; then
	echo "O backup.sh existe mas não tem permissão de execução." >&2
	echo "Rode uma vez:  sudo chmod +x $RAIZ/deploy/*.sh" >&2
	exit 1
fi

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
	echo "    sem commit novo ($depois)"
else
	echo "    $antes -> $depois"
fi

# Qual versão está DE FATO no ar, e não qual está no disco.
#
# Esta marca existe por causa de um estado que já aconteceu de verdade: o `git
# pull` foi feito na mão, fora deste script, e na vez seguinte o script comparou
# o commit antes e depois do PRÓPRIO pull, viu que não mudou nada e saiu sem
# instalar dependência, sem migrar e sem reiniciar. Resultado: código novo no
# disco, código velho rodando e banco sem as tabelas novas — que estoura no
# primeiro reboot, quando ninguém está olhando.
#
# Comparar com o commit anterior responde "o pull trouxe algo?". O que importa
# é outra pergunta: "o que está rodando é o que está no disco?".
MARCA="$RAIZ/.versao-publicada"
publicada=$(cat "$MARCA" 2>/dev/null || true)

precisa_reiniciar=0
if [ "$publicada" != "$depois" ]; then
	precisa_reiniciar=1
	if [ -n "$publicada" ]; then
		echo "    no ar está $publicada; no disco está $depois"
	fi
fi
if ! systemctl is-active --quiet "$APP"; then
	echo "    o serviço não está ativo"
	precisa_reiniciar=1
fi

# Dependências e migrations rodam SEMPRE. As duas não fazem nada quando já estão
# em dia, e custam segundos; pulá-las com base num palpite é o que criou o
# problema descrito acima.
echo "==> Dependências"
sudo -u "$APP" "$RAIZ/.venv/bin/pip" install --quiet --upgrade -r "$RAIZ/requirements.txt"

echo "==> Migrations"
# A saída é capturada para se saber se ALGO foi aplicado (decide o restart), mas
# a captura não pode esconder um erro: com `set -e`, um comando que falha dentro
# de $(...) abortaria o script antes de imprimir o motivo. Daí o `if !`.
if ! saida_migration=$(sudo -u "$APP" env FLASK_APP=run.py "$RAIZ/.venv/bin/python" -m flask db upgrade 2>&1); then
	echo "$saida_migration" | sed 's/^/    /' >&2
	echo "FALHOU: a migration não passou." >&2
	echo "O serviço NÃO foi reiniciado e continua rodando a versão anterior." >&2
	exit 1
fi
echo "$saida_migration" | sed 's/^/    /'
if echo "$saida_migration" | grep -q "Running upgrade"; then
	# Banco mudou: o processo no ar precisa ver o schema novo.
	precisa_reiniciar=1
fi

if [ "$precisa_reiniciar" -eq 0 ]; then
	echo "==> Já estava tudo publicado na versão $depois. Nada a reiniciar."
	exit 0
fi

echo "==> Reiniciando"
systemctl restart "$APP"
sleep 2

if systemctl is-active --quiet "$APP"; then
	# Só depois de o serviço subir de verdade. Gravar antes faria a próxima
	# execução acreditar que uma publicação quebrada tinha dado certo.
	echo "$depois" > "$MARCA"
	chown "$APP":"$APP" "$MARCA"
	echo "OK: serviço ativo na versão $depois"
else
	echo "FALHOU: o serviço não subiu." >&2
	echo "Veja o motivo:  journalctl -u $APP -n 50 --no-pager" >&2
	echo "Para voltar:    sudo -u $APP git -C $RAIZ reset --hard $antes && systemctl restart $APP" >&2
	exit 1
fi
