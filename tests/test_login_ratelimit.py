"""Prova que as rotas de login travam força bruta por IP, sem punir quem acerta.

O limiter fica desligado no TestConfig (ver conftest.py); aqui ele é religado com
um limite baixo para o teste ser rápido e determinístico.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db, limiter
from app.models.platform_admin import PlatformAdmin
from app.models.tenant import Tenant
from app.models.usuario import Usuario
from tests.conftest import TestConfig

LIMITE = 3


class RateLimitConfig(TestConfig):
    RATELIMIT_ENABLED = True
    LOGIN_RATELIMIT = f"{LIMITE} per minute"


@pytest.fixture()
def app():
    application = create_app(RateLimitConfig)
    with application.app_context():
        db.create_all()
        # O limiter é um singleton de módulo: sem reset, a cota consumida por um
        # teste vazaria para o próximo.
        limiter.reset()

        tenant = Tenant(
            slug="tenant-a", nome_fantasia="Restaurante A", email_contato="a@example.com", status="active"
        )
        db.session.add(tenant)
        db.session.flush()
        usuario = Usuario(tenant_id=tenant.id, nome="Admin A", username="admin", role="admin")
        usuario.set_password("senha-a-123")
        admin = PlatformAdmin(nome="Super Admin", username="admin")
        admin.set_password("senha-super-admin-123")
        db.session.add_all([usuario, admin])
        db.session.commit()

        yield application

        limiter.reset()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def _post_login(client, senha, ip, url="/login", base_url="http://tenant-a.localhost"):
    return client.post(
        url,
        data={"username": "admin", "password": senha},
        base_url=base_url,
        environ_base={"REMOTE_ADDR": ip},
    )


def test_forca_bruta_no_login_do_tenant_e_bloqueada(client):
    for tentativa in range(LIMITE):
        resposta = _post_login(client, "senha-errada", "10.0.0.1")
        assert resposta.status_code == 200, f"tentativa {tentativa + 1} deveria só falhar, não bloquear"

    bloqueada = _post_login(client, "senha-errada", "10.0.0.1")
    assert bloqueada.status_code == 429

    # E continua bloqueado mesmo com a senha CORRETA — é isso que impede o
    # atacante de simplesmente acertar na tentativa seguinte.
    assert _post_login(client, "senha-a-123", "10.0.0.1").status_code == 429


def test_forca_bruta_no_login_do_super_admin_e_bloqueada(client):
    url, base = "/plataforma/login", "http://app.localhost"
    for _ in range(LIMITE):
        assert _post_login(client, "errada", "10.0.0.2", url=url, base_url=base).status_code == 200
    assert _post_login(client, "errada", "10.0.0.2", url=url, base_url=base).status_code == 429


def test_bloqueio_e_por_ip_e_nao_afeta_outros_usuarios(client):
    for _ in range(LIMITE + 1):
        _post_login(client, "senha-errada", "10.0.0.3")
    assert _post_login(client, "senha-errada", "10.0.0.3").status_code == 429

    # Outro IP não herda o bloqueio, e ainda consegue logar normalmente.
    assert _post_login(client, "senha-a-123", "10.0.0.4").status_code == 302


def test_login_bem_sucedido_nao_consome_cota(client):
    """Um usuário legítimo pode logar quantas vezes quiser sem se trancar fora."""
    for tentativa in range(LIMITE * 3):
        resposta = _post_login(client, "senha-a-123", "10.0.0.5")
        assert resposta.status_code == 302, f"login correto nº {tentativa + 1} foi bloqueado"


def test_abrir_a_tela_de_login_nao_consome_cota(client):
    """GET no formulário não pode gastar cota, senão um F5 tranca o usuário."""
    for _ in range(LIMITE * 3):
        assert client.get("/login", base_url="http://tenant-a.localhost", environ_base={"REMOTE_ADDR": "10.0.0.6"}).status_code == 200

    # A cota ainda está inteira: a primeira falha apenas falha.
    assert _post_login(client, "senha-errada", "10.0.0.6").status_code == 200


def test_pagina_de_bloqueio_explica_o_motivo(client):
    for _ in range(LIMITE + 1):
        _post_login(client, "senha-errada", "10.0.0.7")
    resposta = _post_login(client, "senha-errada", "10.0.0.7")
    assert resposta.status_code == 429
    assert "Muitas tentativas" in resposta.get_data(as_text=True)
