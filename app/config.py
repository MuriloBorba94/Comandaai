from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class ConfiguracaoInvalida(RuntimeError):
    """Erro de configuração que impede o sistema de iniciar com segurança."""


INSECURE_SECRET_KEYS = {
    "",
    "dev-change-me-now",
    "change-me",
    "changeme",
    "secret",
}


def _split_list(value: str) -> list[str]:
    return [chunk.strip().lower() for chunk in (value or "").split(",") if chunk.strip()]


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me-now")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or f"sqlite:///{(BASE_DIR / 'instance' / 'saas.db').as_posix()}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "connect_args": {"timeout": 30} if SQLALCHEMY_DATABASE_URI.startswith("sqlite") else {},
    }

    WTF_CSRF_TIME_LIMIT = None
    RATELIMIT_STORAGE_URI = "memory://"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    # Deliberadamente SEM SESSION_COOKIE_DOMAIN: cookies ficam "host-only",
    # então o cookie de um tenant nunca é enviado para outro subdomínio.
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12

    LOG_FOLDER = str(BASE_DIR / "logs")

    # Hostname reservado para a área da plataforma (super-admin); nunca é
    # tratado como tenant. Ex.: "app.localhost" em dev, "app.suaapp.com.br" em produção.
    PLATFORM_HOSTNAME = os.getenv("PLATFORM_HOSTNAME", "app.localhost").strip().lower()

    # Domínios base para reconhecer o subdomínio do tenant (ex.: "localhost"
    # em dev; "suaapp.com.br" em produção). Aceita vários, separados por vírgula.
    TENANT_BASE_DOMAINS = _split_list(os.getenv("TENANT_BASE_DOMAINS", "localhost"))

    PLATFORM_ADMIN_USERNAME = os.getenv("PLATFORM_ADMIN_USERNAME", "admin")
    PLATFORM_ADMIN_PASSWORD = os.getenv("PLATFORM_ADMIN_PASSWORD", "")

    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = os.getenv("PORT", "5000")
