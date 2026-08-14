"""Aviso de mensalidade dentro do painel do restaurante.

Existe porque o cliente não é notificado por e-mail nem WhatsApp: sem o aviso,
ele descobre que devia no dia em que a loja para de funcionar.

O teste mais importante é o do vazamento: quem está pedindo um lanche não pode
ver a cobrança que o restaurante paga à plataforma.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.extensions import db
from app.models.assinatura import Plano
from app.models.tenant import Tenant
from app.services.faturamento_saas import (
    avaliar_status,
    cancelar_cobranca,
    gerar_cobranca,
    registrar_pagamento,
)
from tests.conftest import login_tenant

BASE = "http://loja.localhost"
HOJE = date(2026, 8, 20)


def _cenario(preco=99.90, contato="WhatsApp (81) 99999-0000"):
    db.session.add(Plano(slug="starter", nome="Starter", preco_mensal=preco))
    tenant = Tenant(
        slug="loja",
        nome_fantasia="Loja Teste",
        email_contato="loja@example.com",
        plano="starter",
        status="active",
        trial_termina_em=datetime(2026, 8, 1),
    )
    db.session.add(tenant)
    db.session.flush()
    from app.models.usuario import Usuario

    usuario = Usuario(tenant_id=tenant.id, nome="Dono", username="dono", role="admin")
    usuario.set_password("senha-dono-123")
    db.session.add(usuario)
    db.session.commit()
    return tenant


def _painel(client):
    return client.get("/admin/", base_url=BASE).get_data(as_text=True)


def _texto(resposta) -> str:
    """Colapsa espaços: o HTML quebra linha no meio das frases."""
    return " ".join(resposta.get_data(as_text=True).split())


# --------------------------------------------------------------------------- #
# O que o dono do restaurante vê
# --------------------------------------------------------------------------- #


def test_sem_cobranca_aberta_nao_mostra_aviso(app, client):
    _cenario()
    login_tenant(client, "loja", "dono", "senha-dono-123")
    assert "Mensalidade" not in _painel(client)


def test_cobranca_em_dia_mostra_aviso_informativo(app, client):
    tenant = _cenario()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    login_tenant(client, "loja", "dono", "senha-dono-123")

    corpo = _painel(client)
    assert "aviso-informativo" in corpo
    assert "R$ 99,90" in corpo
    assert cobranca.vencimento.strftime("%d/%m/%Y") in corpo
    assert "atraso" not in corpo


def test_cobranca_vencida_avisa_quantos_dias_faltam_para_bloquear(app, client):
    """É esse prazo que dá ao cliente a chance de agir antes de perder a loja."""
    tenant = _cenario()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    cobranca.vencimento = date.today() - timedelta(days=2)
    db.session.commit()
    avaliar_status(tenant)

    login_tenant(client, "loja", "dono", "senha-dono-123")
    corpo = _painel(client)
    assert "aviso-urgente" in corpo
    assert "atraso há 2 dia(s)" in corpo
    assert "bloqueado em 4 dia(s)" in corpo  # carência de 5


def test_aviso_mostra_o_contato_configurado(app, client):
    tenant = _cenario()
    app.config["PLATFORM_CONTATO"] = "WhatsApp (81) 99999-0000"
    gerar_cobranca(tenant, hoje=HOJE)
    login_tenant(client, "loja", "dono", "senha-dono-123")

    assert "WhatsApp (81) 99999-0000" in _painel(client)


def test_sem_contato_configurado_o_aviso_ainda_orienta(app, client):
    tenant = _cenario()
    app.config["PLATFORM_CONTATO"] = ""
    gerar_cobranca(tenant, hoje=HOJE)
    login_tenant(client, "loja", "dono", "senha-dono-123")

    assert "Fale com o suporte" in _painel(client)


def test_pagar_remove_o_aviso(app, client):
    tenant = _cenario()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    login_tenant(client, "loja", "dono", "senha-dono-123")
    assert "Mensalidade" in _painel(client)

    registrar_pagamento(cobranca)
    assert "Mensalidade" not in _painel(client)


def test_cancelar_remove_o_aviso(app, client):
    tenant = _cenario()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    login_tenant(client, "loja", "dono", "senha-dono-123")

    cancelar_cobranca(cobranca)
    assert "Mensalidade" not in _painel(client)


def test_varias_mensalidades_em_aberto_somam(app, client):
    tenant = _cenario(preco=100.0)
    # O teste grátis tem de acabar antes de julho, senão a cobrança de julho
    # simplesmente não é gerada e o teste mediria outra coisa.
    tenant.trial_termina_em = datetime(2026, 6, 1)
    db.session.commit()
    gerar_cobranca(tenant, competencia=date(2026, 7, 1), hoje=date(2026, 7, 2))
    gerar_cobranca(tenant, competencia=date(2026, 8, 1), hoje=HOJE)

    login_tenant(client, "loja", "dono", "senha-dono-123")
    corpo = _painel(client)
    assert "R$ 200,00" in corpo
    assert "2 mensalidades em aberto" in corpo


def test_aviso_aparece_nas_outras_telas_do_admin(app, client):
    """Não serve estar só no painel: o dono pode entrar direto em Produtos."""
    tenant = _cenario()
    gerar_cobranca(tenant, hoje=HOJE)
    login_tenant(client, "loja", "dono", "senha-dono-123")

    for url in ("/admin/produtos", "/admin/categorias", "/admin/configuracoes"):
        assert "Mensalidade" in client.get(url, base_url=BASE).get_data(as_text=True), url


# --------------------------------------------------------------------------- #
# O que o cliente final NÃO pode ver
# --------------------------------------------------------------------------- #


def test_cliente_final_nunca_ve_a_cobranca_do_restaurante(app, client):
    """Quem está pedindo um lanche não tem nada a ver com a assinatura da loja."""
    tenant = _cenario()
    gerar_cobranca(tenant, hoje=HOJE)

    for url in ("/", "/carrinho"):
        corpo = client.get(url, base_url=BASE).get_data(as_text=True)
        assert "Mensalidade" not in corpo, url
        assert "aviso-assinatura" not in corpo, url


def test_sessao_de_outro_tenant_nao_ve_o_aviso(app, client, two_tenants):
    """Estar logado noutro restaurante não pode mostrar a cobrança desta loja."""
    tenant = _cenario()
    gerar_cobranca(tenant, hoje=HOJE)

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/", base_url=BASE).get_data(as_text=True)
    assert "Mensalidade" not in corpo


# --------------------------------------------------------------------------- #
# Tela de bloqueio
# --------------------------------------------------------------------------- #


def test_tela_de_bloqueio_explica_a_inadimplencia(app, client):
    """Antes dizia apenas "acesso suspenso", e o dono achava que era defeito."""
    tenant = _cenario()
    app.config["PLATFORM_CONTATO"] = "WhatsApp (81) 99999-0000"
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    cobranca.vencimento = date.today() - timedelta(days=10)
    db.session.commit()
    avaliar_status(tenant)

    resposta = client.get("/", base_url=BASE)
    corpo = _texto(resposta)
    assert resposta.status_code == 402
    assert "mensalidade em atraso" in corpo
    assert "R$ 99,90" in corpo
    assert "vencida há 10 dia(s)" in corpo
    assert "WhatsApp (81) 99999-0000" in corpo


def test_bloqueio_manual_nao_acusa_inadimplencia(app, client):
    """Suspender por abuso não pode dizer ao cliente que ele está devendo."""
    tenant = _cenario()
    tenant.ativo = False
    db.session.commit()

    resposta = client.get("/", base_url=BASE)
    corpo = _texto(resposta)
    assert resposta.status_code == 402
    assert "acesso suspenso" in corpo
    assert "atraso" not in corpo
    assert "R$" not in corpo


def test_tenant_cancelado_sem_divida_nao_acusa_inadimplencia(app, client):
    tenant = _cenario()
    tenant.status = "canceled"
    db.session.commit()

    corpo = client.get("/", base_url=BASE).get_data(as_text=True)
    assert "atraso" not in corpo
