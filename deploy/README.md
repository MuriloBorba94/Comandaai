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

O jeito que facilita as atualizações é um repositório privado no GitHub (de
graça). Este projeto ainda não tem remoto configurado, então na **sua máquina**:

    git remote add origin git@github.com:SEU_USUARIO/comandaai.git
    git push -u origin main

E no servidor:

    cd /opt
    git clone git@github.com:SEU_USUARIO/comandaai.git comandaai
    chown -R comandaai:comandaai /opt/comandaai

Sem GitHub, dá para mandar um `.tar.gz` por `scp` — mas aí cada atualização é
manual e o `deploy/atualizar.sh` não serve.

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

    apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    apt update && apt install -y caddy

O Caddy padrão **não** traz os módulos de DNS. Este comando troca o binário por
um com o módulo do Cloudflare:

    caddy add-package github.com/caddy-dns/cloudflare

O token vai no ambiente do serviço, não no `Caddyfile` (que vai para o git).
Crie `/etc/systemd/system/caddy.service.d/cloudflare.conf` com:

    [Service]
    Environment=CLOUDFLARE_API_TOKEN=cole_o_token_aqui

Depois:

    chmod 600 /etc/systemd/system/caddy.service.d/cloudflare.conf
    systemctl daemon-reload

Configuração:

    cp /opt/comandaai/deploy/Caddyfile /etc/caddy/Caddyfile
    nano /etc/caddy/Caddyfile
    mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
    caddy validate --config /etc/caddy/Caddyfile
    systemctl restart caddy
    journalctl -u caddy -f

Troque `SEU_EMAIL@exemplo.com` no `Caddyfile`. O primeiro certificado curinga
leva de 30 s a 2 min — o Caddy cria um registro `_acme-challenge` no Cloudflare,
espera a propagação e apaga depois.

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
