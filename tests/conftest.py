from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.platform_admin import PlatformAdmin
from app.models.tenant import Tenant
from app.models.usuario import Usuario


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    # Desligado por padrão para que os testes de isolamento possam fazer quantas
    # tentativas de login precisarem. Quem testa o limiter é
    # tests/test_login_ratelimit.py, que o religa explicitamente.
    RATELIMIT_ENABLED = False
    # Fora de logs/, para os testes não sujarem o log real da aplicação com
    # tentativas de login falhas.
    LOG_FOLDER = str(Path(tempfile.gettempdir()) / "comanda-ai-testes" / "logs")
    # Idem para uploads: nenhum teste deve gravar em app/static/uploads. Quem
    # testa upload de verdade (test_cardapio.py) aponta para um tmp_path próprio.
    UPLOAD_FOLDER = str(Path(tempfile.gettempdir()) / "comanda-ai-testes" / "uploads")
    PLATFORM_HOSTNAME = "app.localhost"
    TENANT_BASE_DOMAINS = ["localhost"]
    PLATFORM_ADMIN_USERNAME = "admin"
    PLATFORM_ADMIN_PASSWORD = "senha-super-admin-123"


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def two_tenants(app):
    """Cria dois tenants, cada um com um Usuario admin de mesmo username e um
    Produto próprio — a base para provar isolamento entre tenants."""
    with app.app_context():
        tenant_a = Tenant(slug="tenant-a", nome_fantasia="Restaurante A", email_contato="a@example.com", status="active")
        tenant_b = Tenant(slug="tenant-b", nome_fantasia="Restaurante B", email_contato="b@example.com", status="active")
        db.session.add_all([tenant_a, tenant_b])
        db.session.flush()

        user_a = Usuario(tenant_id=tenant_a.id, nome="Admin A", username="admin", role="admin")
        user_a.set_password("senha-a-123")
        user_b = Usuario(tenant_id=tenant_b.id, nome="Admin B", username="admin", role="admin")
        user_b.set_password("senha-b-123")
        db.session.add_all([user_a, user_b])
        db.session.commit()

        return {"tenant_a": tenant_a.id, "tenant_b": tenant_b.id}


@pytest.fixture()
def platform_admin(app):
    with app.app_context():
        admin = PlatformAdmin(nome="Super Admin", username="admin")
        admin.set_password("senha-super-admin-123")
        db.session.add(admin)
        db.session.commit()
        return admin.id


def login_tenant(client, slug: str, username: str, password: str):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        base_url=f"http://{slug}.localhost",
        follow_redirects=False,
    )
