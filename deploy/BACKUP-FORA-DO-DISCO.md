# Mandar o backup para fora do servidor

Hoje o `backup.sh` guarda banco e fotos em `/opt/comandaai/backups`. Isso
protege contra **erro** — apagar algo sem querer, uma migration que deu errado,
um restaurante que quer voltar o cardápio de ontem.

Não protege contra o que mais assusta: disco com problema, servidor apagado por
engano, conta suspensa. Nesses casos o sistema e o backup vão juntos, porque
moram no mesmo lugar.

São **10 minutos** e resolve.

---

## O caminho mais simples: Google Drive

O `rclone` fala com Google Drive, OneDrive, Dropbox e mais uns quarenta. Você já
tem uma conta Google, então é o menor caminho.

### 1. Instalar

**No servidor:**

```bash
sudo apt update && sudo apt install -y rclone
```

### 2. Conectar à sua conta

```bash
sudo -u comandaai rclone config
```

Ele faz perguntas. As respostas:

| Pergunta | Resposta |
|---|---|
| `n/s/q` | `n` (novo remoto) |
| `name>` | `gdrive` |
| `Storage>` | procure `drive` na lista e digite o número |
| `client_id>` | deixe vazio, `Enter` |
| `client_secret>` | deixe vazio, `Enter` |
| `scope>` | `1` (acesso total) |
| `service_account_file>` | vazio, `Enter` |
| `Edit advanced config?` | `n` |
| `Use web browser to automatically authenticate?` | **`n`** |

> O **`n`** na última é o que trava a maioria das pessoas. O servidor não tem
> navegador. Respondendo `n`, o rclone imprime um comando para você rodar **na
> sua máquina** — ele abre o navegador, você autoriza com a sua conta Google,
> e ele devolve um código para colar de volta no servidor.

No fim: `y` para confirmar e `q` para sair.

### 3. Testar

```bash
sudo -u comandaai rclone mkdir gdrive:comandaai-backups
```

```bash
sudo -u comandaai rclone lsd gdrive:
```

Se `comandaai-backups` aparecer na lista, está conectado.

### 4. Ligar no backup

**No servidor**, acrescente a linha no `.env`:

```bash
sudo -u comandaai nano /opt/comandaai/.env
```

```
BACKUP_REMOTO=gdrive:comandaai-backups
```

Salve (`Ctrl+O`, `Enter`, `Ctrl+X`).

### 5. Rodar uma vez, na mão

```bash
sudo -u comandaai /opt/comandaai/deploy/backup.sh
```

Espere ver `==> Enviando para gdrive:comandaai-backups`. Confira no seu Google
Drive: a pasta deve ter um `saas-*.db.gz` e um `uploads-*.tar.gz`.

---

## O passo que quase todo mundo pula

Backup que nunca foi restaurado não é backup — é um arquivo que você espera que
funcione. A diferença aparece no pior dia possível.

```bash
cd /opt/comandaai && sudo -u comandaai env FLASK_APP=run.py .venv/bin/python -m flask verificar-backup
```

Ele abre o backup mais recente e confere três coisas, cada uma pegando o que as
outras não pegam:

- a **soma de verificação**, que denuncia byte trocado no disco ou transferência
  truncada;
- o **`integrity_check`** do SQLite, que denuncia estrutura quebrada;
- a **contagem de linhas**, que denuncia backup vazio — e backup vazio passa nos
  dois anteriores sem reclamar.

Também reclama se o backup mais recente estiver velho demais, porque cron que
parou de rodar não avisa ninguém.

Vale agendar junto com o backup:

```bash
crontab -u comandaai -e
```

```
45 4 * * * cd /opt/comandaai && FLASK_APP=run.py .venv/bin/python -m flask verificar-backup >> /opt/comandaai/logs/backup.log 2>&1
```

---

## Restaurar, quando precisar

**Pare o sistema antes.** Trocar o banco embaixo de um processo em execução dá
resultado imprevisível:

```bash
sudo systemctl stop comandaai
```

```bash
cd /opt/comandaai/backups && gunzip -k saas-AAAAMMDD-HHMMSS.db.gz
```

Guarde o banco atual em vez de sobrescrevê-lo — se a restauração for do arquivo
errado, é ele que salva:

```bash
sudo -u comandaai mv /opt/comandaai/instance/saas.db /opt/comandaai/instance/saas-antes-de-restaurar.db
sudo -u comandaai cp /opt/comandaai/backups/saas-AAAAMMDD-HHMMSS.db /opt/comandaai/instance/saas.db
```

As fotos, se precisarem voltar também:

```bash
sudo -u comandaai tar -xzf /opt/comandaai/backups/uploads-AAAAMMDD-HHMMSS.tar.gz -C /opt/comandaai/app/static
```

```bash
sudo systemctl start comandaai && systemctl is-active comandaai
```

---

## Se você preferir não usar o Google Drive

O `BACKUP_REMOTO` aceita qualquer destino do rclone. Uma alternativa sem conta
em nuvem nenhuma é puxar os arquivos para a sua própria máquina, de vez em
quando:

```powershell
scp -r root@SEU_IP:/opt/comandaai/backups "$HOME\comandaai-backups"
```

Funciona, mas depende de você lembrar. Um backup que depende de alguém lembrar
é um backup que vai falhar exatamente no mês em que fizer falta.
