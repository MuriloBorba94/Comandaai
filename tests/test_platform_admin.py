from __future__ import annotations

from app.extensions import db
from app.models.tenant import Tenant
from app.models.usuario import Usuario


def _login_platform_admin(client):
    return client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
        follow_redirects=False,
    )


def test_super_admin_precisa_logar_para_ver_tenants(client):
    resposta = client.get("/plataforma/tenants", base_url="http://app.localhost", follow_redirects=False)
    assert resposta.status_code in (302, 303)
    assert "/plataforma/login" in resposta.headers["Location"]


def test_super_admin_cria_tenant_com_primeiro_usuario_admin(app, client, platform_admin):
    login = _login_platform_admin(client)
    assert login.status_code in (302, 303)

    resposta = client.post(
        "/plataforma/tenants/novo",
        data={
            "slug": "pizzaria-joao",
            "nome_fantasia": "Pizzaria do João",
            "email_contato": "joao@example.com",
            "plano": "trial",
            "admin_username": "joao",
            "admin_password": "senha-joao-123",
        },
        base_url="http://app.localhost",
        follow_redirects=False,
    )
    assert resposta.status_code in (302, 303)

    with app.app_context():
        tenant = Tenant.query.filter_by(slug="pizzaria-joao").first()
        assert tenant is not None
        usuario = Usuario.query.filter_by(tenant_id=tenant.id, username="joao").first()
        assert usuario is not None
        assert usuario.role == "admin"
        assert usuario.check_password("senha-joao-123")
