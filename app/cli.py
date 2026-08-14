from __future__ import annotations

from datetime import date

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

    @app.cli.command("seed-planos")
    def seed_planos() -> None:
        """Cria três planos sugeridos, se o catálogo estiver vazio.

        Os preços são um ponto de partida para você ajustar na tela de Planos —
        nenhum tenant é cobrado enquanto o plano dele custar zero.
        """
        from .models.assinatura import Plano

        if Plano.query.count():
            click.echo("O catálogo já tem planos; nada foi alterado.")
            return

        sugestoes = [
            ("trial", "Teste", 0.0, "Período de avaliação, sem cobrança.", 0),
            ("starter", "Starter", 99.90, "Cardápio, pedidos e cozinha.", 1),
            ("pro", "Pro", 199.90, "Tudo do Starter, com salão e relatórios.", 2),
        ]
        for slug, nome, preco, descricao, ordem in sugestoes:
            db.session.add(
                Plano(slug=slug, nome=nome, preco_mensal=preco, descricao=descricao, ordem=ordem)
            )
        db.session.commit()
        click.echo("Planos criados: " + ", ".join(s[0] for s in sugestoes))
        # Só caracteres que sobrevivem a console Windows legado (cp1252): uma
        # seta "→" faz o comando quebrar com UnicodeEncodeError depois de já ter
        # gravado no banco.
        click.echo("Ajuste os precos em Plataforma > Planos.")

    @app.cli.command("ciclo-cobranca")
    @click.option(
        "--simular",
        is_flag=True,
        help="Mostra o que aconteceria sem gravar nada.",
    )
    def ciclo_cobranca(simular: bool) -> None:
        """Emite as mensalidades do mês e reavalia o acesso de cada tenant.

        Feito para rodar uma vez por dia (Agendador de Tarefas no Windows, cron no
        Linux). É idempotente: rodar de novo no mesmo dia não duplica cobrança.
        """
        from .models.assinatura import Cobranca
        from .models.tenant import Tenant
        from .services.faturamento_saas import deve_cobrar, executar_ciclo

        if simular:
            hoje = date.today()
            click.echo(f"Simulação para {hoje.strftime('%d/%m/%Y')} — nada será gravado.\n")
            for tenant in Tenant.query.order_by(Tenant.slug).all():
                em_aberto = [c for c in tenant.cobrancas if c.status == "pendente"]
                atraso = max((c.dias_de_atraso(hoje) for c in em_aberto), default=0)
                # Cobrança cancelada não conta como já emitida.
                ja_emitida = any(
                    c.competencia.replace(day=1) == hoje.replace(day=1) and c.status != "cancelada"
                    for c in tenant.cobrancas
                )
                acao = (
                    "emitiria cobrança"
                    if deve_cobrar(tenant, hoje) and not ja_emitida
                    else "nada a emitir"
                )
                click.echo(
                    f"  {tenant.slug:20s} status={tenant.status:10s} "
                    f"em aberto={len(em_aberto)} atraso={atraso}d  -> {acao}"
                )
            return

        resumo = executar_ciclo()
        click.echo(
            f"Emitidas: {resumo['emitidas']} · avaliados: {resumo['avaliados']} · "
            f"suspensos: {resumo['suspensos']} · em atraso: {resumo['atrasados']}"
        )
        pendentes = Cobranca.query.filter_by(status="pendente").count()
        click.echo(f"Cobranças pendentes no total: {pendentes}")

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
