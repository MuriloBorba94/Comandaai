# Subir o Comanda ai na VPS

Escrito para o servidor contratado: **Ubuntu 24.04 LTS, 2 GB de RAM, acesso por
SSH**, domínio **comandaai.app.br**.

> **Nunca usou SSH nem terminal Linux?** Comece pelo
> [PRIMEIROS-PASSOS.md](PRIMEIROS-PASSOS.md): ele cobre conectar na VPS, o
> básico de sobrevivência no terminal e a configuração do DNS. Depois volte para
> cá no passo 1.

O desenho é este:

    internet ──HTTPS──> Caddy (80/443) ──HTTP──> waitress (127.0.0.1:5000) ──> SQLite
                          │
                          └── certificado curinga *.comandaai.app.br via DNS do Cloudflare

- `comandaai.app.br` → página inicial do produto + área da plataforma
- `loja.comandaai.app.br` → o painel e o cardápio de cada restaurante
- `www.comandaai.app.br` → redireciona para o apex

---

## 0. Antes de tocar no servidor: o DNS

Este é o item que mais atrasa, e não depende da VPS.

O sistema é multi-tenant por subdomínio, então precisa de certificado **curinga**
(`*.comandaai.app.br`). Certificado curinga só sai por validação DNS, e o
provedor precisa ter API que o Caddy conheça. Por isso:

1. No registro.br, aponte os nameservers de `comandaai.app.br` para o
   **Cloudflare** (plano gratuito serve).

2. No Cloudflare, crie os registros — todos com a **nuvem cinza (DNS only)**:

   | Tipo | Nome | Conteúdo |
   |---|---|---|
   | A | `@` | IP da VPS |
   | A | `*` | IP da VPS |
   | A | `www` | IP da VPS |

   A nuvem tem que ficar **cinza**: o plano gratuito do Cloudflare não faz proxy
   de subdomínio curinga, e com a nuvem laranja o `*` simplesmente não funciona.

3. Crie um token em *My Profile → API Tokens → Create Token*, com
   `Zone → DNS → Edit` e `Zone → Zone → Read`, restrito à zona
   `comandaai.app.br`. Guarde: esse token é a senha do seu DNS.

Confira a propagação antes de seguir — os dois têm que devolver o IP da VPS:

    dig +short comandaai.app.br
    dig +short qualquercoisa.comandaai.app.br

---

## 1. Preparar o servidor

    ssh root@SEU_IP

    apt update && apt upgrade -y
    apt install -y python3-venv python3-pip git sqlite3 ufw

Usuário próprio para a aplicação — nada roda como root:

    adduser --system --group --home /opt/comandaai --shell /bin/bash comandaai

Firewall. A porta 5000 fica **fechada** para a internet de propósito: quem
atende é o Caddy.

    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable
    ufw status

---

## 2. Levar o código

O código vive num repositório **privado** no GitHub:
`https://github.com/MuriloBorba94/Comandaai`. Na sua máquina, o `git push` já
funciona (o Git para Windows guarda a credencial). No servidor não — ele é uma
máquina nova, que o GitHub nunca viu.

Um servidor não digita senha. Ele se identifica com uma **chave de deploy**: uma
chave só de leitura, válida para este único repositório. Não expira, e se um dia
o servidor for comprometido, ela não dá acesso a mais nada seu.

### 2.1 Gerar a chave no servidor

    install -d -m 700 -o comandaai -g comandaai /opt/comandaai/.ssh
    sudo -u comandaai ssh-keygen -t ed25519 -C "vps-comandaai" -f /opt/comandaai/.ssh/id_ed25519 -N ""
    cat /opt/comandaai/.ssh/id_ed25519.pub

O `-N ""` cria a chave **sem senha**, de propósito: o servidor precisa usá-la
sozinho, sem ninguém para digitar nada.

O `cat` imprime uma linha começando com `ssh-ed25519 AAAA...`. **Copie essa linha
inteira** — é a chave pública, pode ser mostrada sem risco. A privada
(`id_ed25519`, sem `.pub`) nunca sai do servidor.

### 2.2 Autorizar no GitHub

1. Abra <https://github.com/MuriloBorba94/Comandaai/settings/keys>
2. **Add deploy key**
3. **Title**: `vps-locaweb`
4. **Key**: cole a linha do passo anterior
5. **Não marque** *Allow write access* — o servidor só precisa ler
6. **Add key**

### 2.3 Registrar a identidade do GitHub e testar

Sem isto, o clone para no meio perguntando se você confia no github.com — e como
ele roda por outro usuário, você não vê a pergunta.

    ssh-keyscan github.com 2>/dev/null | sudo -u comandaai tee -a /opt/comandaai/.ssh/known_hosts >/dev/null
    sudo -u comandaai ssh -T git@github.com

A resposta esperada é:

    Hi MuriloBorba94/Comandaai! You've successfully authenticated,
    but GitHub does not provide shell access.

Isso é **sucesso**, mesmo parecendo aviso: o GitHub não dá terminal a ninguém.

### 2.4 Clonar

A pasta `/opt/comandaai` já existe (é a casa do usuário, e agora tem o `.ssh`
dentro), e o `git clone` se recusa a escrever em pasta que não está vazia. Então
clona fora e copia para dentro:

    sudo -u comandaai git clone git@github.com:MuriloBorba94/Comandaai.git /tmp/codigo
    sudo -u comandaai cp -a /tmp/codigo/. /opt/comandaai/
    rm -rf /tmp/codigo

O `/.` no fim de `/tmp/codigo/.` é o que faz copiar **o conteúdo** da pasta,
inclusive o `.git` — sem ele, você copiaria a pasta para dentro dela mesma.

### 2.5 Amarrar a chave ao repositório

    sudo -u comandaai git -C /opt/comandaai config core.sshCommand "ssh -i /opt/comandaai/.ssh/id_ed25519 -o IdentitiesOnly=yes"

Isso grava no repositório qual chave usar. Sem essa linha, o `git pull` do
`deploy/atualizar.sh` pode falhar depois — ele roda por outro usuário, e nem
sempre o `ssh` acha a chave sozinho. É uma linha agora para não virar um deploy
travado no futuro.

Confirme:

    sudo -u comandaai git -C /opt/comandaai pull --ff-only

Tem que responder `Already up to date.`

> **Alternativa sem chave:** dá para clonar por HTTPS usando um token de acesso
> pessoal do GitHub. Funciona, mas o token vai escrito no `.git/config` e expira
> — aí o deploy quebra num dia qualquer, sem aviso. A chave de deploy não tem
> esse problema.

---

## 3. Ambiente Python

O Ubuntu 24.04 traz Python 3.12, que atende: o projeto não usa nenhuma sintaxe
mais nova que isso.

    cd /opt/comandaai
    sudo -u comandaai python3 -m venv .venv
    sudo -u comandaai .venv/bin/pip install --upgrade pip
    sudo -u comandaai .venv/bin/pip install -r requirements.txt

---

## 4. Configuração

    cp deploy/env.producao.example .env
    nano .env
    chown comandaai:comandaai .env
    chmod 600 .env

A `SECRET_KEY` sai daqui — a aplicação se recusa a subir com chave fraca:

    python3 -c "import secrets; print(secrets.token_hex(32))"

Duas linhas que costumam passar batidas, e as duas têm consequência real:

- `HOST=127.0.0.1` — com `0.0.0.0` a aplicação fica acessível em
  `http://SEU_IP:5000`, sem HTTPS e sem o proxy.
- `TRUSTED_PROXIES=1` — sem isso o rate limit de login conta todos os visitantes
  como o IP do Caddy, e o primeiro que errar a senha bloqueia os outros.

---

## 5. Banco e primeiro acesso

    cd /opt/comandaai
    sudo -u comandaai FLASK_APP=run.py .venv/bin/python -m flask db upgrade
    sudo -u comandaai FLASK_APP=run.py .venv/bin/python -m flask seed-platform-admin
    sudo -u comandaai FLASK_APP=run.py .venv/bin/python -m flask seed-planos

O `seed-platform-admin` lê usuário e senha do `.env` **uma única vez**. Mudar o
`.env` depois não muda a senha — para trocar, use `flask definir-senha`.

---

## 6. Serviço

    cp deploy/comandaai.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now comandaai
    systemctl status comandaai

Teste local, antes de existir HTTPS. Tem que devolver o HTML da página inicial:

    curl -s -H "Host: comandaai.app.br" http://127.0.0.1:5000/ | head -5

---

## 7. Caddy com o certificado curinga

O passo mais difícil do guia. São sete etapas, cada uma com um jeito de conferir
se deu certo antes de seguir. **Não pule as conferências** — errar aqui e só
descobrir três etapas depois é o que faz perder a tarde.

O que vai acontecer: o Caddy vai pedir à Let's Encrypt um certificado válido para
`*.comandaai.app.br`. Para provar que o domínio é seu, ele cria um registro
temporário no seu DNS do Cloudflare, espera propagar e apaga. É por isso que ele
precisa do token.

### 7.1 Conferir o DNS antes de tudo

    dig +short comandaai.app.br
    dig +short qualquercoisa.comandaai.app.br

Os dois têm que devolver **o IP da sua VPS**. Se devolverem `1.1.1.1` (o valor
temporário que você pode ter deixado) ou nada, pare aqui e corrija os registros
no Cloudflare — o certificado vai até sair, mas o site não abre.

Se o comando `dig` não existir:

    apt install -y dnsutils

### 7.2 Conferir o token do Cloudflare

Vale gastar dez segundos aqui: token errado é a causa mais comum de o certificado
não sair, e o erro do Caddy não diz isso com clareza.

    curl -s -H "Authorization: Bearer COLE_SEU_TOKEN_AQUI" https://api.cloudflare.com/client/v4/user/tokens/verify

Resposta boa contém `"status":"active"` e `"success":true`. Se vier
`"success":false`, o token está errado ou expirado — gere outro
(PRIMEIROS-PASSOS.md, item 1.5).

### 7.3 Instalar o Caddy

Quatro comandos. Cole **um por vez** e espere cada um terminar.

    apt install -y debian-keyring debian-archive-keyring apt-transport-https curl

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list

    apt update && apt install -y caddy

Confira:

    caddy version

Tem que imprimir algo como `v2.8.4`. Se disser `command not found`, algum dos
quatro comandos falhou — role a tela para achar a mensagem de erro.

### 7.4 Adicionar o módulo de DNS do Cloudflare

O Caddy que vem do apt **não** sabe falar com o Cloudflare. Este comando baixa um
binário novo, já com o módulo:

    caddy add-package github.com/caddy-dns/cloudflare

Demora de 30 s a 2 min e não imprime quase nada enquanto trabalha.

> **`Error: package is already added`** não é erro: o módulo já está instalado,
> provavelmente porque o comando foi rodado duas vezes. Siga para a conferência.

Confira:

    caddy list-modules | grep cloudflare

Tem que aparecer `dns.providers.cloudflare`. **Se não aparecer, não siga** — o
certificado não vai sair de jeito nenhum, e o erro no log vai parecer ser outra
coisa.

Se o `add-package` disser que já está adicionado **mas** o `list-modules` não
mostrar nada, o binário em uso ficou velho. Este comando o reconstrói com a lista
atual de módulos:

    caddy upgrade
    systemctl restart caddy
    caddy list-modules | grep cloudflare

### 7.5 Dar o token ao serviço do Caddy

O token não pode ir no `Caddyfile`, porque esse arquivo está no Git. Ele vai numa
configuração extra do serviço, que só o root lê.

Primeiro crie a pasta (ela ainda não existe):

    mkdir -p /etc/systemd/system/caddy.service.d

Agora abra o editor:

    nano /etc/systemd/system/caddy.service.d/cloudflare.conf

O nano abre uma tela vazia. Digite exatamente estas duas linhas, trocando pelo seu
token:

    [Service]
    Environment=CLOUDFLARE_API_TOKEN=cole_seu_token_aqui

Para salvar e sair: **Ctrl+O**, **Enter**, **Ctrl+X**.

Proteja o arquivo e recarregue o systemd:

    chmod 600 /etc/systemd/system/caddy.service.d/cloudflare.conf
    systemctl daemon-reload

Confira que o Caddy vai receber o token (mostra a variável, sem o valor):

    systemctl show caddy -p Environment | grep -o CLOUDFLARE_API_TOKEN

Tem que imprimir `CLOUDFLARE_API_TOKEN`. Se não imprimir nada, o arquivo está no
lugar errado ou o `daemon-reload` não rodou.

### 7.6 Colocar a configuração

    cp /opt/comandaai/deploy/Caddyfile /etc/caddy/Caddyfile

Prepare a pasta **e o arquivo** de log, já com o dono certo:

    mkdir -p /var/log/caddy
    touch /var/log/caddy/comandaai.log
    chown -R caddy:caddy /var/log/caddy

O `touch` parece desnecessário e não é. O `caddy validate` do fim desta etapa
provisiona os módulos de verdade e cria o arquivo de log — e como você roda o
`validate` como **root**, o arquivo nasce sendo do root. Depois o serviço, que
roda como usuário `caddy`, não consegue escrever nele e morre com
`permission denied`. Criando antes com o dono certo, o problema não existe.

    nano /etc/caddy/Caddyfile

No nano, troque `SEU_EMAIL@exemplo.com` pelo seu e-mail de verdade (é onde a
Let's Encrypt avisa se um certificado estiver perto de vencer). Salve com
**Ctrl+O**, **Enter**, **Ctrl+X**.

Confira a sintaxe:

    caddy validate --config /etc/caddy/Caddyfile

Duas mensagens aqui são **esperadas** e não indicam problema:

- `API token '' appears invalid` — o `validate` roda fora do serviço, então ele
  não vê o token. Quem precisa dele é o serviço, e o 7.5 já entregou.
- `Caddyfile input is not formatted` — só estética. Se incomodar:
  `caddy fmt --overwrite /etc/caddy/Caddyfile`.

O que importa é não haver erro de **sintaxe**.

### 7.7 Subir e acompanhar

    systemctl restart caddy
    journalctl -u caddy -f

O `-f` deixa o log rolando ao vivo. Agora **espere de 30 s a 2 min**. Você quer
ver linhas como:

    obtaining certificate
    ...
    certificate obtained successfully

Quando aparecer `certificate obtained successfully`, deu certo. Aperte **Ctrl+C**
para sair do log.

Se aparecer erro, os três mais comuns:

| No log aparece | O que é |
|---|---|
| `no such module`, `dns.providers.cloudflare` | O 7.4 não funcionou. Refaça e confirme com `caddy list-modules` |
| `Invalid request headers`, `Authentication error` | Token errado ou sem as duas permissões. Volte ao 7.2 |
| `timed out waiting for record to fully propagate` | Normal na primeira vez. O Caddy tenta de novo sozinho; espere mais 2 min |
| `open /var/log/caddy/comandaai.log: permission denied` | O arquivo de log ficou do root. Conserto: `touch /var/log/caddy/comandaai.log && chown -R caddy:caddy /var/log/caddy && systemctl restart caddy` |

E confirme que o serviço ficou de pé:

    systemctl is-active caddy

Tem que responder `active`.

### 7.8 Tirar o token do log do sistema

O serviço do Caddy que vem no pacote sobe com `--environ`, que despeja **todas**
as variáveis de ambiente no log — incluindo o token do Cloudflare, em texto puro,
a cada reinício. Qualquer um que leia o log do sistema vê o token.

Faça isto depois que o certificado tiver saído. Abra o editor:

    nano /etc/systemd/system/caddy.service.d/sem-environ.conf

Digite exatamente estas três linhas:

    [Service]
    ExecStart=
    ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile

Salve com **Ctrl+O**, **Enter**, **Ctrl+X**. Depois:

    systemctl daemon-reload
    systemctl restart caddy

A primeira linha `ExecStart=` vazia é obrigatória: ela apaga o comando original
antes de definir o novo. Sem ela, o systemd tenta rodar os dois.

E limpe o log antigo, que já guardou o token:

    journalctl --rotate
    journalctl --vacuum-time=1s

Confirme que o token não aparece mais:

    journalctl -u caddy | grep -c CLOUDFLARE_API_TOKEN

Tem que responder `0`.

---

## 8. Conferir

    curl -sI https://comandaai.app.br | head -3                        # 200
    curl -sI https://www.comandaai.app.br | head -3                    # 301 para o apex
    curl -sI https://qualquer.comandaai.app.br | head -3               # 404, subdomínio sem tenant
    curl -sI https://comandaai.app.br/static/css/landing.css | head -3 # 200

O último confirma que o Caddy está servindo os arquivos estáticos do disco. Se
der 404, o `root` do bloco `handle_path` aponta para o lugar errado.

No navegador: `https://comandaai.app.br` mostra a página inicial, e
`/plataforma/` pede o login do super-admin. Crie o primeiro restaurante por lá —
o subdomínio dele passa a funcionar na hora, sem mexer em DNS nem certificado.

---

## 9. Backup automático

Sem isso, um disco com problema leva os pedidos e as fotos de todos os clientes.

    crontab -u comandaai -e

Adicione:

    30 4 * * * /opt/comandaai/deploy/backup.sh >> /opt/comandaai/logs/backup.log 2>&1

O script guarda 14 dias em `/opt/comandaai/backups`. **Isso ainda não é backup de
verdade**, porque mora no mesmo disco: mande a pasta para fora (rclone para um
Google Drive, ou `scp` para a sua máquina) assim que possível.

---

## 10. Atualizar depois

Na sua máquina, `git push`. No servidor:

    sudo -u comandaai /opt/comandaai/deploy/atualizar.sh

Faz backup, baixa o código, instala dependências, roda as migrations e reinicia —
nessa ordem. Se a migration falhar, o serviço continua na versão antiga em vez de
subir código novo contra banco velho.

---

## Opcional: Redis para o rate limit

Com `memory://`, a contagem de tentativas de login zera a cada restart do
serviço. Com 2 GB de RAM cabe folgado (~10 MB):

    apt install -y redis-server
    systemctl enable --now redis-server
    sudo -u comandaai /opt/comandaai/.venv/bin/pip install redis

No `.env`, troque para `RATELIMIT_STORAGE_URI=redis://localhost:6379/0` e
reinicie o serviço.

---

## Quando trocar para Postgres

Não agora. O SQLite dá conta dos primeiros restaurantes e economiza a memória de
um serviço a mais. O sinal para trocar é `database is locked` no log — significa
que duas escritas estão se atropelando. A troca é o `DATABASE_URL` no `.env` mais
um `flask db upgrade` no banco novo; o código já suporta os dois.

---

## Quando der problema

Sintomas que você provavelmente vai encontrar, e o que cada um significa.

**"Sua página ficou desatualizada" ao fazer login, sempre.**
O Flask-WTF exige o header `Referer` em requisições HTTPS (`WTF_CSRF_SSL_STRICT`).
Navegador normal manda; extensão de privacidade agressiva ou proxy corporativo
podem remover. Confirme no log do Caddy se o `Referer` está chegando. Se for esse
o caso e você precisar aceitar esses clientes, dá para desligar a checagem — mas
ela é uma defesa real, então só faça isso com o problema confirmado.

**`git@github.com: Permission denied (publickey)` ao clonar.**
O servidor não tem chave autorizada no repositório — é o passo 2 deste guia. Não
significa que o repositório não existe: o GitHub responde essa mesma mensagem
para repositório inexistente e para chave não autorizada, porque não conta a
estranhos o que existe lá dentro. Refaça o 2.1 ao 2.5.

Se o `ssh -T git@github.com` responder `Hi MuriloBorba94/Comandaai!`, a chave
está certa e o problema é outro — provavelmente a URL do clone (confira as
maiúsculas: `Comandaai`, não `comandaai`).

**Certificado não emite; log do Caddy fala de DNS.**
Na ordem: o token do Cloudflare tem `Zone → DNS → Edit` **e** `Zone → Zone →
Read`? O `caddy add-package` rodou (o Caddy padrão não tem o módulo de DNS)? O
registro `*` está com a **nuvem cinza**? Teste o token:

    curl -s -H "Authorization: Bearer SEU_TOKEN" https://api.cloudflare.com/client/v4/user/tokens/verify

**CSS e imagens dando 404, o resto funcionando.**
O `root` do bloco `handle_path /static/*` no Caddyfile aponta para o lugar
errado. Tem que ser exatamente `/opt/comandaai/app/static`. Confira com
`ls /opt/comandaai/app/static/css/comanda.css`.

**502 Bad Gateway.**
O Caddy está de pé e a aplicação não. `systemctl status comandaai` e
`journalctl -u comandaai -n 50`. As causas mais comuns: `SECRET_KEY` vazia ou
fraca (a aplicação se recusa a subir de propósito), migration pendente, ou
permissão — o usuário `comandaai` precisa poder escrever em `instance/`, `logs/`
e `app/static/uploads/`.

**Subdomínio de um restaurante dá 404.**
O restaurante existe com aquele slug exato? Confira com
`sudo -u comandaai FLASK_APP=run.py .venv/bin/python -m flask listar-tenants`.
Slug errado, tenant `suspended` ou `ativo=false` também mudam a resposta — nesse
caso vem 402 com a tela de acesso suspenso, não 404.

**`database is locked` no log.**
Duas escritas se atropelando no SQLite. É o sinal de trocar para Postgres.

**A loja aparece bloqueada sem motivo.**
O ciclo de cobrança suspende quem passou da carência. Veja em
`/plataforma/cobrancas`. Para liberar, registre o pagamento; para desbloquear
sem pagamento, mude o status do tenant na tela de edição.
