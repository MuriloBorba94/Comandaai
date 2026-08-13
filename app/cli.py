from __future__ import annotations

import click
from flask import Flask, current_app

from .extensions import db
from .models.platform_admin import PlatformAdmin


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-platform-admin")
    def seed_platform_admin() -> None:
        """Cria (ou atualiza a senha do) primeiro super-admin da plataforma."""
        username = current_app.config["PLATFORM_ADMIN_USERNAME"]
        password = current_app.config["PLATFORM_ADMIN_PASSWORD"]
        if not password:
            click.echo("Defina PLATFORM_ADMIN_PASSWORD no .env antes de rodar este comando.")
            raise SystemExit(1)

        admin = PlatformAdmin.query.filter_by(username=username).first()
        if admin is None:
            admin = PlatformAdmin(nome=username, username=username)
            db.session.add(admin)
            click.echo(f"Criando super-admin '{username}'.")
        else:
            click.echo(f"Atualizando senha do super-admin '{username}'.")

        admin.set_password(password)
        db.session.commit()
        click.echo("Concluído.")
