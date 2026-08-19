# Primeiros passos — do zero até conectar na VPS

Este guia assume que você **nunca usou SSH nem terminal Linux**. Ele não pula
nada. Quando você chegar ao fim, siga o [README.md](README.md), que é a parte
técnica da publicação.

VPS não tem janela, não tem mouse, não tem área de trabalho. Você digita um
comando, aperta Enter, e ele responde com texto. É estranho nas primeiras vezes
e depois fica natural.

---

# PARTE 1 — O que fazer AGORA (não depende da VPS)

Faça esta parte hoje, mesmo que a Locaweb ainda não tenha liberado nada. É o
item mais demorado, porque depende de propagação de DNS.

## 1.1 O domínio está registrado?

Entre em <https://registro.br> e confira se `comandaai.app.br` está no seu nome.

- **Não está?** Registre agora. Custa por volta de R$ 40/ano e a confirmação
  costuma sair no mesmo dia.
- **Está?** Siga.

## 1.2 Criar conta no Cloudflare (grátis)

Por que o Cloudflare, se você já tem a Locaweb: seu sistema precisa de um
certificado de segurança **curinga** (que vale para `qualquercoisa.comandaai.app.br`),
e esse tipo de certificado só é emitido automaticamente se o servidor conseguir
conversar com o painel de DNS por API. O Cloudflare tem essa API, é gratuito, e
é o caminho mais curto.

1. <https://dash.cloudflare.com/sign-up> — crie a conta.
2. **Add a site** → digite `comandaai.app.br` → escolha o plano **Free**.
3. O Cloudflare vai mostrar **dois endereços de nameserver**, algo como:

       marge.ns.cloudflare.com
       rex.ns.cloudflare.com

   Deixe essa tela aberta; você vai precisar deles no próximo passo.

## 1.3 Apontar o domínio para o Cloudflare

1. Volte no <https://registro.br>, entre no domínio `comandaai.app.br`.
2. Procure **DNS** → **Alterar servidores DNS** (ou "Usar servidores DNS
   próprios" / "Configurar servidores DNS").
3. Apague o que estiver lá e coloque os dois endereços do Cloudflare.
4. Salve.

A partir daí a propagação leva de 15 minutos a algumas horas. O Cloudflare manda
um e-mail quando reconhece a mudança.

> Isso **não** derruba nada — você ainda não tem nada no ar nesse domínio.

## 1.4 Criar os registros de DNS

Ainda no Cloudflare, menu **DNS** → **Records** → **Add record**. Você vai criar
três, e ainda não tem o IP da VPS: use `1.1.1.1` como valor temporário e volte
para corrigir depois.

| Tipo | Name | IPv4 address | Proxy status |
|---|---|---|---|
| A | `@` | IP da VPS | **DNS only** (nuvem cinza) |
| A | `*` | IP da VPS | **DNS only** (nuvem cinza) |
| A | `www` | IP da VPS | **DNS only** (nuvem cinza) |

**A nuvem tem que ficar CINZA, não laranja.** Clique nela para alternar. Com a
nuvem laranja, o registro `*` não funciona no plano gratuito e nada dos seus
clientes vai abrir.

O `*` (asterisco) é o que faz `qualquerloja.comandaai.app.br` funcionar sem você
criar um registro para cada restaurante novo.

## 1.5 Criar o token de API do Cloudflare

É a senha que o servidor vai usar para provar que o domínio é seu, na hora de
emitir o certificado.

1. Clique no ícone do seu perfil (canto superior direito) → **My Profile**.
2. **API Tokens** → **Create Token**.
3. Escolha **Create Custom Token** (botão *Get started*).
4. Preencha:
   - **Token name**: `caddy-comandaai`
   - **Permissions** — adicione **duas** linhas:
     - `Zone` · `DNS` · `Edit`
     - `Zone` · `Zone` · `Read`
   - **Zone Resources**: `Include` · `Specific zone` · `comandaai.app.br`
5. **Continue to summary** → **Create Token**.
6. **Copie o token e guarde num lugar seguro.** Ele aparece **uma única vez**.

> Esse token dá controle do seu DNS. Não mande para ninguém, não cole em chat,
> não suba para o GitHub.

**Fim da Parte 1.** Se a VPS ainda não chegou, pare aqui.

---

# PARTE 2 — Conectar na VPS

## 2.1 Pegar os dados no painel da Locaweb

Entre no painel da Locaweb e anote:

- **Endereço IP** do servidor (quatro números, tipo `189.126.x.x`)
- **Usuário** de acesso (quase sempre `root`)
- **Senha** de root (ou a instrução de como definir a primeira senha)

Se o painel só mostrar a senha uma vez, copie antes de fechar.

## 2.2 Abrir o terminal no seu Windows

Você **não precisa instalar nada**. O Windows 10/11 já tem SSH.

1. Aperte a tecla **Windows**.
2. Digite `powershell`.
3. Clique em **Windows PowerShell**.

Vai abrir uma janela preta ou azul com algo assim:

    PS C:\Users\Murilo Borba>

Esse `PS C:\...` é o **seu computador**. Guarde essa informação: daqui a pouco o
prompt vai mudar, e é assim que você sabe se está digitando na sua máquina ou no
servidor.

## 2.3 Conectar

Digite, trocando pelo IP de verdade:

    ssh root@189.126.0.0

Aperte Enter. Na **primeira** conexão aparece isto:

    The authenticity of host '189.126.0.0' can't be established.
    ED25519 key fingerprint is SHA256:xxxxxxxxxxxxxxxx.
    Are you sure you want to continue connecting (yes/no/[fingerprint])?

Digite `yes` e Enter. (Só `y` não serve — tem que ser a palavra inteira.)

Depois ele pede a senha:

    root@189.126.0.0's password:

Digite a senha e Enter.

> **A senha não aparece na tela enquanto você digita.** Não aparece asterisco,
> não aparece bolinha, o cursor não mexe. Isso é normal no Linux. Digite e dê
> Enter. Se errar, ele pede de novo.

Deu certo quando o prompt virar algo assim:

    root@srv-locaweb:~#

O `#` no fim significa que você é root — e que qualquer comando ali mexe no
servidor de verdade. **Você está dentro.**

## 2.4 Sobreviver no terminal

Quatro coisas que resolvem 90% do sufoco:

**Colar comando.** Copie daqui (Ctrl+C) e no PowerShell clique com o **botão
direito** do mouse. Ctrl+V às vezes não funciona; botão direito funciona sempre.

**O comando "travou" e não volta o prompt.** Alguns ficam rodando de propósito
(mostrando log ao vivo). Aperte **Ctrl+C** para cortar e voltar ao prompt.

**Editar arquivo.** Vamos usar o `nano`. Ele abre o texto na tela e mostra a ajuda
embaixo. Para salvar e sair:

1. **Ctrl+O** (letra O, de *output*) — ele pergunta o nome do arquivo
2. **Enter** — confirma
3. **Ctrl+X** — sai

Se você se perder e quiser sair **sem** salvar: **Ctrl+X**, depois `n`.

**Sair do servidor.** Digite `exit` e Enter. O prompt volta para
`PS C:\Users\...`, que é a sua máquina.

## 2.5 Primeira coisa a fazer, antes de tudo

Um servidor novo com senha de root começa a receber tentativas de invasão em
poucas horas — não é exagero, é o normal da internet. Estes três comandos
resolvem o básico. Cole um por vez e espere terminar:

    apt update && apt upgrade -y

Esse é o mais demorado (pode levar 5 minutos) e vai imprimir muita coisa. Se em
algum momento aparecer uma tela azul perguntando sobre arquivos de configuração
ou reinício de serviços, aceite o padrão (Tab até `<Ok>` e Enter).

    apt install -y fail2ban
    systemctl enable --now fail2ban

O `fail2ban` bloqueia automaticamente quem erra a senha várias vezes. Confirme
que ficou de pé:

    systemctl is-active fail2ban

Tem que responder `active`.

## 2.6 Anote o essencial

Antes de seguir, tenha isto escrito num lugar seguro:

- [ ] IP da VPS
- [ ] Senha de root
- [ ] Token do Cloudflare
- [ ] Senha que você vai definir para o super-admin do Comanda ai

---

# PARTE 3 — Publicar o sistema

Agora vá para o [README.md](README.md) e comece no **passo 1**. O passo 0 (DNS)
você já fez na Parte 1 deste guia.

Cada comando de lá é para colar no terminal **conectado no servidor** — aquele
com o prompt `root@...#`. Quando o guia disser "na sua máquina", volte para a
janela do PowerShell com `PS C:\...`.

## Se algo der errado

Não tem problema, e quase nunca é irreversível. Faça isto:

1. **Copie a mensagem de erro inteira** (selecione com o mouse, Ctrl+C).
2. Anote **qual comando** você tinha acabado de rodar.
3. Me manda os dois.

Erro no terminal é texto — com a mensagem em mãos eu digo exatamente o que
aconteceu. O que **não** ajuda é "deu erro": aí eu só posso adivinhar.

Dois comandos que servem para quase todo diagnóstico:

    systemctl status comandaai
    journalctl -u comandaai -n 50 --no-pager

O primeiro diz se a aplicação está de pé. O segundo mostra as últimas 50 linhas
do que ela reclamou.
