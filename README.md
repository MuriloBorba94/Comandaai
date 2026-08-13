# Comanda ai — núcleo multi-tenant

Este projeto é o **esqueleto** do sistema multi-tenant que vai substituir, aos
poucos, o sistema single-tenant de `C:\borbas_burguer_v17`. Ele não toca em
nada daquele repositório — é uma base nova, isolada, para construir o produto
revendável por assinatura mensal.

O que já existe aqui:
- Modelo de `Tenant` (cada restaurante cliente) identificado por subdomínio.
- Autenticação de `Usuario` escopada por tenant (dois tenants podem ter
  usuários com o mesmo `username`, sem conflito).
- Um `PlatformAdmin` separado — o super-admin (você, o revendedor) que cria e
  gerencia os tenants, sem se misturar com os admins de cada loja.
- Rate limit por IP nas rotas de login, contando somente tentativas que falham.
- **Cardápio completo por tenant** (Fase 1): `Categoria` ordenável, `Adicional`
  vinculado por produto, `Produto` com foto, e vitrine pública agrupada na
  ordem que cada tenant define.
- **Pedidos, cozinha e mesa** (Fase 2): carrinho e checkout na vitrine, página
  pública de acompanhamento por token, painel `/cozinha` com máquina de estados,
  e comanda de mesa com mapa do salão. O painel se atualiza sozinho: consulta
  `/cozinha/eventos` a cada 8 s e só recarrega a tela quando a fila muda, com
  aviso sonoro opcional para pedido novo.
- **Cupons e taxa por bairro** (Fase 3): cupom com reserva de uso (não vende
  além do limite em checkouts simultâneos), e taxa/prazo de entrega por bairro.
- Migrations versionadas com Flask-Migrate/Alembic desde o início.

O que **não** está aqui ainda (ver [`ROADMAP.md`](ROADMAP.md) para as fases):
pedidos, cozinha, cupons, PIX, WhatsApp, impressão, estoque/financeiro,
cobrança recorrente real, deploy de produção. A lógica de negócio é portada de
`C:\borbas_burguer_v17` fase por fase.

### Fotos de produto

As imagens ficam em `app/static/uploads/<slug-do-tenant>/`, fora do git. O
upload valida o **conteúdo** do arquivo com Pillow (não a extensão, que é dado
do cliente), converte para WebP e redimensiona. O limite é 5 MB por imagem
(`MAX_CONTENT_LENGTH`).

## Como rodar localmente

```bash
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Edite o `.env`: gere uma `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`)
e defina `PLATFORM_ADMIN_PASSWORD`.

```bash
.venv\Scripts\flask db init
.venv\Scripts\flask db migrate -m "schema inicial"
.venv\Scripts\flask db upgrade
.venv\Scripts\flask seed-platform-admin
.venv\Scripts\python run.py
```

**Atenção:** o `.env` é lido **apenas** pelo comando `seed-platform-admin`, nunca
pelo login. O login confere a senha contra o hash gravado no banco. Portanto, se
você mudar `PLATFORM_ADMIN_PASSWORD` depois de já ter semeado, o login continuará
exigindo a senha antiga — rode `flask seed-platform-admin` de novo para
regravar o hash (o comando detecta o admin existente e só atualiza a senha).

O servidor sobe em `http://0.0.0.0:5000`. Como o subdomínio identifica o
tenant, acesse por `http://app.localhost:5000` (área da plataforma) ou
`http://<slug-do-tenant>.localhost:5000` (área de um tenant) — navegadores
modernos resolvem qualquer `*.localhost` para `127.0.0.1` automaticamente,
sem precisar editar o arquivo `hosts`.

## Descobrir usuários e redefinir senha

Senha de usuário existe no banco **somente como hash** — não há como recuperar,
nem para você. Quando alguém esquece, o caminho é redefinir.

Pela interface, o super-admin faz isso em **Plataforma → Tenants → Editar**: a
tela lista os usuários do restaurante e define nova senha sem pedir a antiga.
Também é lá que se ajusta plano, status da assinatura e o bloqueio manual.

Pelo terminal, quando não há navegador à mão:

Para ver quais tenants existem, o usuário de cada um e o endereço de acesso:

```bash
.venv\Scripts\flask listar-tenants
```

Para redefinir a senha de um usuário de tenant:

```bash
.venv\Scripts\flask definir-senha --tenant pizzaria-joao --usuario joao
```

A senha é pedida no terminal, com entrada oculta e confirmação. Ela **não** é
aceita como argumento de linha de comando de propósito: assim não fica no
histórico do shell nem visível na lista de processos. O filtro inclui o tenant,
então dois restaurantes podem ter um usuário `admin` sem risco de trocar a senha
do errado.

Fluxo para testar manualmente:
1. Login do super-admin em `http://app.localhost:5000/plataforma/login`.
2. Criar um tenant (isso já cria o primeiro usuário admin dele).
3. Repita para um segundo tenant.
4. Logar em `http://<slug1>.localhost:5000/login` e `http://<slug2>.localhost:5000/login`
   e confirmar que os produtos de um nunca aparecem no outro.

## Testes

```bash
.venv\Scripts\pytest
```

`tests/test_tenancy.py` é o teste mais importante: prova que não há
vazamento de dados entre tenants.
