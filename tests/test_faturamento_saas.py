"""Fase 4 — cobrança da própria plataforma (a mensalidade que o tenant paga).

Não é porte: o sistema single-tenant não tem nada equivalente. O provedor é
"manual" — você recebe o PIX e registra o pagamento.

Os testes mais importantes: o dinheiro (valor vem do plano e fica congelado), a
consequência (atraso além da carência bloqueia a loja de fato) e a proteção dos
tenants que já existiam (sem prazo de trial, ninguém é suspenso de surpresa).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models.assinatura import (
    COBRANCA_CANCELADA,
    COBRANCA_PAGA,
    COBRANCA_PENDENTE,
    Cobranca,
    Plano,
    primeiro_dia,
    somar_um_mes,
)
from app.models.tenant import Tenant
from app.services.faturamento_saas import (
    PRAZO_MINIMO_DIAS,
    avaliar_status,
    cancelar_cobranca,
    deve_cobrar,
    executar_ciclo,
    gerar_cobranca,
    registrar_pagamento,
    resumo_do_tenant,
)
from tests.conftest import login_tenant

BASE_PLATAFORMA = "http://app.localhost"
HOJE = date(2026, 8, 20)


def _login_plataforma(client):
    return client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=False,
    )


def _plano(slug="starter", preco=99.90, **campos):
    plano = Plano(slug=slug, nome=slug.title(), preco_mensal=preco, **campos)
    db.session.add(plano)
    db.session.commit()
    return plano


def _tenant(slug="loja", plano="starter", fim_trial=date(2026, 8, 1), status="active"):
    tenant = Tenant(
        slug=slug,
        nome_fantasia=f"Loja {slug}",
        email_contato=f"{slug}@example.com",
        plano=plano,
        status=status,
        trial_termina_em=(
            datetime.combine(fim_trial, datetime.min.time()) if fim_trial else None
        ),
    )
    db.session.add(tenant)
    db.session.commit()
    return tenant


# --------------------------------------------------------------------------- #
# Quando cobrar
# --------------------------------------------------------------------------- #


def test_plano_sem_preco_nao_gera_cobranca(client):
    _plano("cortesia", preco=0.0)
    tenant = _tenant(plano="cortesia")

    assert deve_cobrar(tenant, HOJE) is False
    assert gerar_cobranca(tenant, hoje=HOJE) is None


def test_plano_fora_do_catalogo_nao_gera_cobranca(client):
    """Tenant apontando para um slug que não existe não pode virar cobrança de R$ 0."""
    tenant = _tenant(plano="inexistente")
    assert gerar_cobranca(tenant, hoje=HOJE) is None


def test_tenant_sem_prazo_de_trial_nunca_e_cobrado(client):
    """Protege os tenants criados antes desta fase de serem cobrados de surpresa."""
    _plano()
    tenant = _tenant(fim_trial=None)

    assert deve_cobrar(tenant, HOJE) is False
    assert gerar_cobranca(tenant, hoje=HOJE) is None
    assert avaliar_status(tenant, hoje=HOJE) == "active"


def test_dentro_do_trial_nao_cobra_e_status_e_trial(client):
    _plano()
    tenant = _tenant(fim_trial=HOJE + timedelta(days=5))

    assert deve_cobrar(tenant, HOJE) is False
    assert gerar_cobranca(tenant, hoje=HOJE) is None
    assert avaliar_status(tenant, hoje=HOJE) == "trial"


def test_tenant_desativado_nao_e_cobrado(client):
    _plano()
    tenant = _tenant()
    tenant.ativo = False
    db.session.commit()

    assert deve_cobrar(tenant, HOJE) is False


def test_tenant_cancelado_nao_e_cobrado_nem_reavaliado(client):
    _plano()
    tenant = _tenant(status="canceled")

    assert deve_cobrar(tenant, HOJE) is False
    assert avaliar_status(tenant, hoje=HOJE) == "canceled", "cancelado é terminal"


# --------------------------------------------------------------------------- #
# Emissão
# --------------------------------------------------------------------------- #


def test_cobranca_usa_o_preco_do_plano(client):
    _plano(preco=149.90)
    tenant = _tenant()

    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    assert cobranca is not None
    assert cobranca.valor == 149.90
    assert cobranca.plano_slug == "starter"
    assert cobranca.status == COBRANCA_PENDENTE
    assert cobranca.provedor == "manual"
    assert cobranca.competencia == primeiro_dia(HOJE)


def test_cobranca_nao_duplica_na_mesma_competencia(client):
    _plano()
    tenant = _tenant()

    primeira = gerar_cobranca(tenant, hoje=HOJE)
    segunda = gerar_cobranca(tenant, hoje=HOJE)

    assert primeira.id == segunda.id
    assert Cobranca.query.filter_by(tenant_id=tenant.id).count() == 1


def test_cobranca_nunca_nasce_vencida(client):
    """O ciclo rodando depois do dia de vencimento não pode bloquear o cliente
    por um atraso que ele nunca teve chance de evitar."""
    _plano()
    tenant = _tenant()
    # Dia 20, com vencimento configurado no dia 10: o dia 10 já passou.
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    assert cobranca.vencimento >= HOJE + timedelta(days=PRAZO_MINIMO_DIAS)
    assert cobranca.dias_de_atraso(HOJE) == 0


def test_valor_fica_congelado_quando_o_preco_do_plano_muda(client):
    plano = _plano(preco=99.90)
    tenant = _tenant()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    plano.preco_mensal = 299.90
    db.session.commit()

    db.session.refresh(cobranca)
    assert cobranca.valor == 99.90, "cobrança emitida não pode ser reescrita"


def test_competencias_diferentes_geram_cobrancas_diferentes(client):
    _plano()
    tenant = _tenant()

    agosto = gerar_cobranca(tenant, hoje=HOJE)
    setembro = gerar_cobranca(tenant, competencia=date(2026, 9, 1), hoje=date(2026, 9, 2))

    assert agosto.id != setembro.id
    assert Cobranca.query.filter_by(tenant_id=tenant.id).count() == 2


# --------------------------------------------------------------------------- #
# Consequência: atraso bloqueia a loja
# --------------------------------------------------------------------------- #


def test_atraso_dentro_da_carencia_deixa_a_loja_funcionando(client):
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    # Dois dias após o vencimento, com carência de cinco.
    depois = cobranca.vencimento + timedelta(days=2)
    assert avaliar_status(tenant, hoje=depois) == "past_due"
    assert client.get("/", base_url="http://loja.localhost").status_code == 200


def test_atraso_alem_da_carencia_bloqueia_a_loja(client):
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    depois = cobranca.vencimento + timedelta(days=6)
    assert avaliar_status(tenant, hoje=depois) == "suspended"
    resposta = client.get("/", base_url="http://loja.localhost")
    assert resposta.status_code == 402, "inadimplente além da carência precisa ser bloqueado"


def test_pagamento_libera_a_loja(client):
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    depois = cobranca.vencimento + timedelta(days=6)
    avaliar_status(tenant, hoje=depois)
    assert client.get("/", base_url="http://loja.localhost").status_code == 402

    registrar_pagamento(cobranca, hoje=depois)

    db.session.refresh(tenant)
    assert tenant.status == "active"
    assert client.get("/", base_url="http://loja.localhost").status_code == 200


def test_pagamento_registra_valor_metodo_e_data(client):
    _plano(preco=99.90)
    tenant = _tenant()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    registrar_pagamento(cobranca, valor=95.0, metodo="PIX Nubank", observacao="desconto acordado")

    assert cobranca.status == COBRANCA_PAGA
    assert cobranca.valor_pago == 95.0
    assert cobranca.valor == 99.90, "o valor cobrado não muda; o pago é registrado à parte"
    assert cobranca.metodo_pagamento == "PIX Nubank"
    assert cobranca.pago_em is not None
    assert cobranca.observacao == "desconto acordado"


def test_pagamento_avanca_a_proxima_cobranca(client):
    _plano()
    tenant = _tenant()
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    registrar_pagamento(cobranca)

    db.session.refresh(tenant)
    assert tenant.proxima_cobranca_em is not None
    assert tenant.proxima_cobranca_em.date() >= somar_um_mes(cobranca.competencia)


def test_nao_paga_duas_vezes(client):
    _plano()
    cobranca = gerar_cobranca(_tenant(), hoje=HOJE)
    registrar_pagamento(cobranca)

    with pytest.raises(ValueError, match="já está paga"):
        registrar_pagamento(cobranca)


def test_cancelar_cobranca_libera_o_tenant(client):
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    depois = cobranca.vencimento + timedelta(days=6)
    avaliar_status(tenant, hoje=depois)
    assert tenant.status == "suspended"

    cancelar_cobranca(cobranca, observacao="erro de emissão")

    assert cobranca.status == COBRANCA_CANCELADA
    db.session.refresh(tenant)
    assert tenant.status in ("active", "trial")


def test_cobranca_paga_nao_pode_ser_cancelada(client):
    _plano()
    cobranca = gerar_cobranca(_tenant(), hoje=HOJE)
    registrar_pagamento(cobranca)

    with pytest.raises(ValueError, match="não pode ser cancelada"):
        cancelar_cobranca(cobranca)


def test_bloqueio_manual_e_independente_do_pagamento(client):
    """Pagar em dia não desfaz o bloqueio manual do super-admin."""
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    tenant.ativo = False
    db.session.commit()

    registrar_pagamento(cobranca)

    db.session.refresh(tenant)
    assert tenant.status == "active", "o status reflete a cobrança"
    assert tenant.ativo is False, "mas o bloqueio manual continua"
    assert client.get("/", base_url="http://loja.localhost").status_code == 402


# --------------------------------------------------------------------------- #
# Isolamento entre tenants
# --------------------------------------------------------------------------- #


def test_pagar_cobranca_de_um_tenant_nao_afeta_outro(client):
    _plano()
    tenant_a = _tenant(slug="loja-a")
    tenant_b = _tenant(slug="loja-b")
    cobranca_a = gerar_cobranca(tenant_a, hoje=HOJE)
    cobranca_b = gerar_cobranca(tenant_b, hoje=HOJE)

    depois = cobranca_a.vencimento + timedelta(days=6)
    avaliar_status(tenant_a, hoje=depois)
    avaliar_status(tenant_b, hoje=depois)
    assert tenant_a.status == "suspended" and tenant_b.status == "suspended"

    registrar_pagamento(cobranca_a, hoje=depois)

    db.session.refresh(tenant_a)
    db.session.refresh(tenant_b)
    assert tenant_a.status == "active"
    assert tenant_b.status == "suspended", "o outro tenant continua bloqueado"
    assert cobranca_b.status == COBRANCA_PENDENTE


def test_atraso_de_um_tenant_nao_bloqueia_o_outro(client):
    _plano()
    tenant_a = _tenant(slug="loja-a")
    _tenant(slug="loja-b", fim_trial=HOJE + timedelta(days=10))
    cobranca = gerar_cobranca(tenant_a, hoje=HOJE)

    executar_ciclo(hoje=cobranca.vencimento + timedelta(days=6))

    assert client.get("/", base_url="http://loja-a.localhost").status_code == 402
    assert client.get("/", base_url="http://loja-b.localhost").status_code == 200


# --------------------------------------------------------------------------- #
# Ciclo automático
# --------------------------------------------------------------------------- #


def test_ciclo_emite_e_e_idempotente(client):
    _plano()
    _tenant(slug="loja-a")
    _tenant(slug="loja-b")

    primeiro = executar_ciclo(hoje=HOJE)
    assert primeiro["emitidas"] == 2

    segundo = executar_ciclo(hoje=HOJE)
    assert segundo["emitidas"] == 0, "rodar de novo no mesmo dia não pode duplicar"
    assert Cobranca.query.count() == 2


def test_ciclo_suspende_quem_passou_da_carencia(client):
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)

    resumo = executar_ciclo(hoje=cobranca.vencimento + timedelta(days=6))

    assert resumo["suspensos"] == 1
    db.session.refresh(tenant)
    assert tenant.status == "suspended"


def test_ciclo_ignora_tenant_cancelado(client):
    _plano()
    tenant = _tenant(slug="loja", status="canceled")

    executar_ciclo(hoje=HOJE)

    db.session.refresh(tenant)
    assert tenant.status == "canceled"
    assert Cobranca.query.filter_by(tenant_id=tenant.id).count() == 0


def test_resumo_do_tenant(client):
    _plano(preco=100.0)
    tenant = _tenant()
    paga = gerar_cobranca(tenant, hoje=HOJE)
    registrar_pagamento(paga, valor=100.0)
    gerar_cobranca(tenant, competencia=date(2026, 9, 1), hoje=date(2026, 9, 2))

    resumo = resumo_do_tenant(tenant, hoje=date(2026, 9, 2))
    assert resumo["valor_mensal"] == 100.0
    assert resumo["qtd_pagas"] == 1
    assert resumo["total_pago"] == 100.0
    assert len(resumo["em_aberto"]) == 1
    assert resumo["total_em_aberto"] == 100.0


# --------------------------------------------------------------------------- #
# Telas da plataforma
# --------------------------------------------------------------------------- #


def test_telas_de_cobranca_exigem_super_admin(client, platform_admin):
    for url in ("/plataforma/cobrancas", "/plataforma/planos"):
        resposta = client.get(url, base_url=BASE_PLATAFORMA, follow_redirects=False)
        assert resposta.status_code in (302, 303)
        assert "/plataforma/login" in resposta.headers["Location"]


def test_admin_de_tenant_nao_ve_cobrancas_da_plataforma(client, platform_admin, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.get("/plataforma/cobrancas", base_url=BASE_PLATAFORMA, follow_redirects=False)
    assert resposta.status_code in (302, 303)


def test_telas_de_cobranca_e_plano_renderizam(client, platform_admin):
    _plano(preco=99.90)
    tenant = _tenant(slug="loja")
    gerar_cobranca(tenant, hoje=HOJE)
    _login_plataforma(client)

    cobrancas = client.get("/plataforma/cobrancas", base_url=BASE_PLATAFORMA)
    assert cobrancas.status_code == 200
    assert "Loja loja" in cobrancas.get_data(as_text=True)

    planos = client.get("/plataforma/planos", base_url=BASE_PLATAFORMA)
    assert planos.status_code == 200
    assert "starter" in planos.get_data(as_text=True)

    editar = client.get(
        f"/plataforma/tenants/{tenant.id}/editar", base_url=BASE_PLATAFORMA
    ).get_data(as_text=True)
    assert "Assinatura" in editar
    assert "R$ 99,90" in editar


def test_marcar_paga_pela_tela(client, platform_admin):
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/cobrancas/{cobranca.id}/pagar",
        data={"metodo": "PIX"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "registrado" in resposta.get_data(as_text=True)
    db.session.refresh(cobranca)
    assert cobranca.status == COBRANCA_PAGA


def test_emitir_cobranca_pela_tela_do_tenant(client, platform_admin):
    _plano()
    tenant = _tenant(slug="loja")
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/cobrancas/gerar",
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "vencimento" in resposta.get_data(as_text=True)
    assert Cobranca.query.filter_by(tenant_id=tenant.id).count() == 1


def test_emitir_cobranca_sem_motivo_avisa(client, platform_admin):
    """Plano de graça: a tela precisa explicar por que nada foi emitido."""
    _plano("cortesia", preco=0.0)
    tenant = _tenant(slug="loja", plano="cortesia")
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/cobrancas/gerar",
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "Nada a cobrar" in resposta.get_data(as_text=True)


def test_plano_em_uso_nao_pode_ser_excluido(client, platform_admin):
    plano = _plano()
    _tenant(plano="starter")
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/planos/{plano.id}/excluir",
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "estão neste plano" in resposta.get_data(as_text=True)
    assert Plano.query.filter_by(slug="starter").first() is not None


def test_plano_do_catalogo_e_aceito_no_tenant(client, platform_admin):
    """Criar um plano novo no catálogo precisa habilitá-lo na edição do tenant."""
    _plano("premium", preco=399.0)
    tenant = _tenant(plano="starter", fim_trial=None)
    _plano("starter")
    _login_plataforma(client)

    client.post(
        f"/plataforma/tenants/{tenant.id}/editar",
        data={
            "slug": tenant.slug,
            "nome_fantasia": tenant.nome_fantasia,
            "email_contato": tenant.email_contato,
            "plano": "premium",
            "status": "active",
            "ativo": "on",
        },
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    db.session.refresh(tenant)
    assert tenant.plano == "premium"


def test_ciclo_pela_tela(client, platform_admin):
    _plano()
    _tenant(slug="loja")
    _login_plataforma(client)

    resposta = client.post(
        "/plataforma/cobrancas/ciclo", base_url=BASE_PLATAFORMA, follow_redirects=True
    )
    assert "Ciclo executado" in resposta.get_data(as_text=True)
    assert Cobranca.query.count() == 1


# --------------------------------------------------------------------------- #
# Reemissão depois de cancelar
# --------------------------------------------------------------------------- #


def test_reemitir_o_mes_depois_de_cancelar(client):
    """Cancelar por engano travava o mês para sempre.

    A busca não filtrava status (devolvia a cancelada como se valesse) e a unique
    constraint cheia impediria inserir outra. Agora o índice único é parcial.
    """
    _plano(preco=99.90)
    tenant = _tenant(slug="loja")

    primeira = gerar_cobranca(tenant, hoje=HOJE)
    cancelar_cobranca(primeira, observacao="emitida por engano")

    segunda = gerar_cobranca(tenant, hoje=HOJE)
    assert segunda is not None
    assert segunda.id != primeira.id, "precisa ser uma cobrança nova"
    assert segunda.status == COBRANCA_PENDENTE
    assert segunda.competencia == primeira.competencia


def test_cancelamento_fica_no_historico(client):
    """Reemitir não pode apagar o registro de que houve um cancelamento."""
    _plano()
    tenant = _tenant(slug="loja")
    primeira = gerar_cobranca(tenant, hoje=HOJE)
    cancelar_cobranca(primeira, observacao="acordo comercial")
    gerar_cobranca(tenant, hoje=HOJE)

    todas = Cobranca.query.filter_by(tenant_id=tenant.id).all()
    assert len(todas) == 2
    canceladas = [c for c in todas if c.status == COBRANCA_CANCELADA]
    assert len(canceladas) == 1
    assert canceladas[0].observacao == "acordo comercial"


def test_reemissao_usa_o_preco_atual_do_plano(client):
    plano = _plano(preco=99.90)
    tenant = _tenant(slug="loja")
    primeira = gerar_cobranca(tenant, hoje=HOJE)
    cancelar_cobranca(primeira)

    plano.preco_mensal = 149.90
    db.session.commit()

    segunda = gerar_cobranca(tenant, hoje=HOJE)
    assert segunda.valor == 149.90
    assert primeira.valor == 99.90, "a cancelada mantém o valor da época"


def test_ainda_nao_duplica_cobranca_viva(client):
    """A proteção contra duplicidade continua valendo para a pendente."""
    _plano()
    tenant = _tenant(slug="loja")

    primeira = gerar_cobranca(tenant, hoje=HOJE)
    segunda = gerar_cobranca(tenant, hoje=HOJE)

    assert primeira.id == segunda.id
    assert Cobranca.query.filter_by(tenant_id=tenant.id).count() == 1


def test_ciclo_reemite_mes_cancelado(client):
    """O ciclo do dia seguinte precisa reemitir o mês que foi cancelado."""
    _plano()
    tenant = _tenant(slug="loja")
    cancelar_cobranca(gerar_cobranca(tenant, hoje=HOJE))

    resumo = executar_ciclo(hoje=HOJE)

    assert resumo["emitidas"] == 1
    assert Cobranca.query.filter_by(tenant_id=tenant.id, status=COBRANCA_PENDENTE).count() == 1


def test_ciclo_nao_reemite_mes_ja_pago(client):
    _plano()
    tenant = _tenant(slug="loja")
    registrar_pagamento(gerar_cobranca(tenant, hoje=HOJE))

    resumo = executar_ciclo(hoje=HOJE)

    assert resumo["emitidas"] == 0
    assert Cobranca.query.filter_by(tenant_id=tenant.id).count() == 1


def test_cancelar_a_cobranca_libera_o_tenant_e_permite_recomecar(client):
    """Fluxo real: cancelou por engano, o cliente saiu do bloqueio, reemite."""
    _plano()
    tenant = _tenant(slug="loja")
    cobranca = gerar_cobranca(tenant, hoje=HOJE)
    atrasado = cobranca.vencimento + timedelta(days=6)
    avaliar_status(tenant, hoje=atrasado)
    assert client.get("/", base_url="http://loja.localhost").status_code == 402

    cancelar_cobranca(cobranca)
    assert client.get("/", base_url="http://loja.localhost").status_code == 200

    nova = gerar_cobranca(tenant, hoje=atrasado)
    assert nova.status == COBRANCA_PENDENTE
    assert nova.dias_de_atraso(atrasado) == 0, "a reemissão dá prazo novo"
