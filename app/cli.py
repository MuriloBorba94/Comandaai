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

    @app.cli.command("enviar-avisos")
    @click.option("--limite", default=50, show_default=True, help="Quantos avisos por execução.")
    def enviar_avisos(limite: int) -> None:
        """Reenvia os avisos de WhatsApp que ainda não saíram.

        Feito para rodar de minuto em minuto no cron. O envio normal acontece na
        hora em que o pedido muda de status; este comando é a rede de proteção
        para quando a Meta estava fora do ar naquele instante.

        Só mexe no que é automático: aviso esperando alguém clicar não é
        problema a resolver sozinho.
        """
        from .services.notificacoes import despachar_pendentes

        resultado = despachar_pendentes(limite=limite)
        if not resultado["tentadas"]:
            click.echo("Nada pendente.")
            return
        click.echo(
            f"Tentadas: {resultado['tentadas']}  "
            f"enviadas: {resultado['enviadas']}  "
            f"falharam: {resultado['falharam']}"
        )

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

    # ----------------------------------------------------------------- #
    # Fase 11: trazer um restaurante do sistema single-tenant
    # ----------------------------------------------------------------- #

    @app.cli.command("importar-legado")
    @click.option("--banco", required=True, help="Caminho do .db do sistema antigo.")
    @click.option("--slug", required=True, help="Endereço do restaurante (vira o subdomínio).")
    @click.option("--email", required=True, help="E-mail de contato do restaurante.")
    @click.option("--nome", default=None, help="Nome fantasia. Padrão: o nome da loja no banco antigo.")
    @click.option("--plano", default="trial", show_default=True, help="Slug do plano.")
    @click.option("--mesas", default=0, show_default=True, help="Quantidade de mesas do salão.")
    @click.option("--fotos", default=None, help="Pasta de uploads do sistema antigo, para copiar as fotos.")
    @click.option("--simular", is_flag=True, help="Roda tudo e desfaz no fim, só para ver o relatório.")
    def importar_legado(banco, slug, email, nome, plano, mesas, fotos, simular) -> None:
        """Importa um restaurante do sistema single-tenant para dentro do Comanda ai.

        Traz configuração da loja, cardápio com fotos, adicionais, bairros,
        cupons, insumos, fichas técnicas e usuários (com a senha atual). O
        histórico de pedidos NÃO vem — o porquê está em app/services/importacao.py.

        Rode primeiro com --simular: ele executa a importação de verdade e
        desfaz no fim, então o relatório mostra o que aconteceria sem gravar nada.
        """
        from .models.assinatura import Plano
        from .services.importacao import ErroDeImportacao, importar

        if plano and not Plano.query.filter_by(slug=plano).first():
            click.echo(f"Aviso: nao existe plano '{plano}' no catalogo; o tenant libera tudo.")

        try:
            relatorio = importar(
                banco,
                slug=slug.strip().lower(),
                email_contato=email.strip(),
                nome_fantasia=nome,
                plano=plano,
                qtd_mesas=mesas,
                pasta_fotos=fotos,
                simular=simular,
            )
        except ErroDeImportacao as erro:
            click.echo(f"Importacao nao comecou: {erro}")
            raise SystemExit(1)

        for linha in relatorio.linhas():
            click.echo(linha)

        click.echo("")
        if simular:
            click.echo("Nada foi gravado. Rode de novo sem --simular para valer.")
        else:
            click.echo(f"Pronto. O restaurante ja responde em {slug}.<seu-dominio>.")

    @app.cli.command("remover-tenant")
    @click.option("--slug", required=True, help="Restaurante a remover.")
    @click.option("--apagar-fotos", is_flag=True, help="Apaga tambem a pasta de imagens dele.")
    @click.option("--sim", is_flag=True, help="Pula a confirmacao. Para script.")
    def remover_tenant(slug, apagar_fotos, sim) -> None:
        """Apaga um restaurante e TUDO que e dele. Nao tem desfazer.

        Existe para a migracao poder ser refeita: importou, olhou, nao gostou,
        remove e importa de novo. Fora disso, pense duas vezes — some pedido,
        produto, estoque e historico financeiro junto.
        """
        import shutil
        from pathlib import Path

        tenant = Tenant.query.filter_by(slug=slug).first()
        if tenant is None:
            disponiveis = ", ".join(t.slug for t in Tenant.query.order_by(Tenant.slug))
            click.echo(f"Nao existe restaurante '{slug}'. Existem: {disponiveis or 'nenhum'}")
            raise SystemExit(1)

        from .models.pedido import Pedido
        from .models.produto import Produto

        pedidos = Pedido.query.filter_by(tenant_id=tenant.id).count()
        produtos = Produto.query.filter_by(tenant_id=tenant.id).count()
        usuarios = len(tenant.usuarios)

        click.echo(f"Restaurante : {tenant.nome_fantasia} ({tenant.slug})")
        click.echo(f"Vao sumir   : {produtos} produto(s), {pedidos} pedido(s), {usuarios} usuario(s)")
        if pedidos:
            # Pedido e historico de venda: o financeiro e os relatorios saem
            # junto. Merece um aviso separado, nao uma linha no meio.
            click.echo("ATENCAO: este restaurante tem venda registrada. O historico vai junto.")

        if not sim:
            # Digitar o slug, e nao "s/n": confirmacao de uma tecla e o que faz
            # alguem apagar o tenant errado no piloto automatico.
            digitado = click.prompt(f"Digite '{slug}' para confirmar", default="", show_default=False)
            if digitado.strip() != slug:
                click.echo("Nao confere. Nada foi apagado.")
                raise SystemExit(1)

        pasta = Path(app.config["UPLOAD_FOLDER"]) / slug
        db.session.delete(tenant)
        db.session.commit()

        if apagar_fotos and pasta.is_dir():
            shutil.rmtree(pasta, ignore_errors=True)
            click.echo(f"Pasta de imagens removida: {pasta}")
        elif pasta.is_dir():
            click.echo(f"As imagens continuam em {pasta} (use --apagar-fotos para remover).")

        click.echo(f"Restaurante '{slug}' removido.")
