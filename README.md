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
- Uma entidade de exemplo (`Produto`) provando, de ponta a ponta, que os dados
  de um tenant nunca aparecem para outro.
- Migrations versionadas com Flask-Migrate/Alembic desde o início.

O que **não** está aqui ainda (ver [`ROADMAP.md`](ROADMAP.md) para as fases):
cardápio completo, pedidos, cozinha, cupons, PIX, WhatsApp, impressão,
estoque/financeiro, cobrança recorrente real, deploy de produção. Este
esqueleto só precisa provar que o núcleo multi-tenant funciona; a lógica de
negócio será portada de `C:\borbas_burguer_v17` fase por fase.

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
