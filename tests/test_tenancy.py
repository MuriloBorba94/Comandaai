from __future__ import annotations

from app.extensions import db
from app.models.produto import Produto

from .conftest import login_tenant


def test_produto_de_um_tenant_nao_aparece_no_outro(app, client, two_tenants):
    with app.app_context():
        db.session.add(Produto(tenant_id=two_tenants["tenant_a"], nome="X-Burguer", preco=25.0))
        db.session.add(Produto(tenant_id=two_tenants["tenant_b"], nome="Pizza Marguerita", preco=40.0))
        db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta_a = client.get("/admin/produtos", base_url="http://tenant-a.localhost")
    assert b"X-Burguer" in resposta_a.data
    assert b"Pizza Marguerita" not in resposta_a.data

    client.get("/logout", base_url="http://tenant-a.localhost")

    login_tenant(client, "tenant-b", "admin", "senha-b-123")
    resposta_b = client.get("/admin/produtos", base_url="http://tenant-b.localhost")
    assert b"Pizza Marguerita" in resposta_b.data
    assert b"X-Burguer" not in resposta_b.data


def test_sessao_de_um_tenant_e_rejeitada_no_outro(client, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/admin/produtos", base_url="http://tenant-b.localhost", follow_redirects=False)

    # A sessão pertence ao tenant A; acessando pelo subdomínio do tenant B
    # deve ser tratado como deslogado (redireciona para o login), nunca 200.
    assert resposta.status_code in (302, 303)
    assert "/login" in resposta.headers["Location"]


def test_slug_desconhecido_retorna_404(client):
    resposta = client.get("/", base_url="http://nao-existe.localhost")
    assert resposta.status_code == 404


def test_tenant_suspenso_bloqueia_acesso(app, client, two_tenants):
    from app.models.tenant import Tenant

    with app.app_context():
        tenant = db.session.get(Tenant, two_tenants["tenant_a"])
        tenant.status = "suspended"
        db.session.commit()

    resposta = client.get("/", base_url="http://tenant-a.localhost")
    assert resposta.status_code == 402
