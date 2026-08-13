from __future__ import annotations

from .conftest import login_tenant


def test_mesmo_username_em_dois_tenants_nao_colide(client, two_tenants):
    login_a = login_tenant(client, "tenant-a", "admin", "senha-a-123")
    assert login_a.status_code in (302, 303)
    client.get("/logout", base_url="http://tenant-a.localhost")

    login_b = login_tenant(client, "tenant-b", "admin", "senha-b-123")
    assert login_b.status_code in (302, 303)


def test_senha_errada_nao_loga(client, two_tenants):
    resposta = login_tenant(client, "tenant-a", "admin", "senha-errada")
    assert resposta.status_code == 200  # renderiza o login de novo, com flash de erro
    assert "Usuário ou senha inválidos".encode("utf-8") in resposta.data


def test_login_de_usuario_de_outro_tenant_nao_funciona(client, two_tenants):
    # username "admin" existe nos dois tenants, mas com senhas diferentes;
    # a senha do tenant B não deve autenticar no tenant A.
    resposta = login_tenant(client, "tenant-a", "admin", "senha-b-123")
    assert resposta.status_code == 200
    assert "Usuário ou senha inválidos".encode("utf-8") in resposta.data
