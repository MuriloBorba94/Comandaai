from __future__ import annotations

import click
from flask import Flask, current_app

from .extensions import db
from .models.platform_admin import PlatformAdmin
from .models.tenant import Tenant
from .models.usuario import Usuario

SENHA_CURTA = 8


def _avisar_se_senha_fraca(senha: str) -> None:
    """Avisa, mas não impede. A escolha da senha é de quem administra."""
    if len(senha) < SENHA_CURTA:
        click.echo(
            click.style(
                f"Aviso: senha com menos de {SENHA_CURTA} caracteres.", fg="yellow"
            )
        )
    if senha.isdigit():
        click.echo(
            click.style(
                "Aviso: senha só com números é fácil de adivinhar por tentativa.",
                fg="yellow",
            )
        )


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
        _avisar_se_senha_fraca(password)
        click.echo("Concluído.")

    @app.cli.command("listar-tenants")
    def listar_tenants() -> None:
        """Mostra os tenants, seus usuários e por qual endereço acessar cada um.

        Serve para descobrir qual usuário pertence a qual restaurante — senha
        nenhuma é exibida, porque só existe o hash.
        """
        tenants = Tenant.query.order_by(Tenant.slug).all()
        if not tenants:
            click.echo("Nenhum tenant cadastrado.")
            return

        porta = current_app.config.get("PORT", "5000")
        base = (current_app.config.get("TENANT_BASE_DOMAINS") or ["localhost"])[0]

        for tenant in tenants:
            estado = tenant.status if tenant.ativo else f"{tenant.status} (desativado)"
            click.echo("")
            click.echo(click.style(f"{tenant.nome_fantasia}", bold=True) + f"  [{estado}]")
            click.echo(f"  acesso:  http://{tenant.slug}.{base}:{porta}/login")
            usuarios = sorted(tenant.usuarios, key=lambda u: u.username)
            if not usuarios:
                click.echo("  usuários: nenhum")
                continue
            for usuario in usuarios:
                marca = "" if usuario.ativo else "  (inativo)"
                click.echo(f"  usuário: {usuario.username}  ({usuario.role}){marca}")

        click.echo("")
        click.echo(
            f"Área da plataforma: http://{current_app.config['PLATFORM_HOSTNAME']}:{porta}/plataforma/login"
        )
        click.echo("Para redefinir uma senha: flask definir-senha --tenant SLUG --usuario USUARIO")

    @app.cli.command("definir-senha")
    @click.option("--tenant", "slug", required=True, help="Slug do tenant (o subdomínio).")
    @click.option("--usuario", "username", required=True, help="username do usuário do tenant.")
    def definir_senha(slug: str, username: str) -> None:
        """Redefine a senha de um usuário de tenant.

        A senha é pedida no terminal, com entrada oculta e confirmação. Não é
        aceita como argumento de linha de comando de propósito: assim ela não
        fica no histórico do shell nem na lista de processos.
        """
        slug = (slug or "").strip().lower()
        username = (username or "").strip()

        tenant = Tenant.query.filter_by(slug=slug).first()
        if tenant is None:
            disponiveis = ", ".join(t.slug for t in Tenant.query.order_by(Tenant.slug))
            click.echo(f"Tenant '{slug}' não existe. Tenants: {disponiveis or 'nenhum'}")
            raise SystemExit(1)

        # O filtro inclui tenant_id: dois tenants podem ter o mesmo username, e
        # trocar a senha do usuário errado seria um estrago silencioso.
        usuario = Usuario.query.filter_by(tenant_id=tenant.id, username=username).first()
        if usuario is None:
            disponiveis = ", ".join(sorted(u.username for u in tenant.usuarios))
            click.echo(
                f"O tenant '{slug}' não tem usuário '{username}'. "
                f"Usuários deste tenant: {disponiveis or 'nenhum'}"
            )
            raise SystemExit(1)

        senha = click.prompt(
            f"Nova senha para '{username}' em '{tenant.nome_fantasia}'",
            hide_input=True,
            confirmation_prompt="Repita a senha",
        )
        if not senha.strip():
            click.echo("Senha vazia; nada foi alterado.")
            raise SystemExit(1)

        usuario.set_password(senha)
        db.session.commit()
        _avisar_se_senha_fraca(senha)
        click.echo(f"Senha de '{username}' atualizada em '{tenant.nome_fantasia}'.")
