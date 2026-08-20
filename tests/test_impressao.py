"""Fase 8 — impressão na cozinha pelo agente do restaurante.

Os testes que mais importam aqui são três, e todos tratam de coisa que já
aconteceu na vida real de quem opera restaurante:

1. **Comanda não sai duas vezes.** Nem quando o atendente clica Confirmar e
   depois Em preparo, nem quando a internet cai entre imprimir e confirmar.
2. **Comanda não atravessa a fronteira do tenant.** O código de ativação de um
   restaurante não pode buscar trabalho no endereço de outro.
3. **Impressão nunca derruba a venda.** Se o enfileiramento falhar, o pedido
   continua gravado — comanda se reimprime, pedido perdido no checkout não.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.impressao import (
    MAX_TENTATIVAS,
    STATUS_CANCELADO,
    STATUS_ERRO,
    STATUS_IMPRESSO,
    STATUS_IMPRIMINDO,
    STATUS_PENDENTE,
    TIPO_ADICAO,
    TIPO_COMANDA,
    TIPO_FECHAMENTO,
    AgenteImpressao,
    ImpressaoJob,
)
from app.models.pedido import (
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    TIPO_ENTREGA,
    TIPO_MESA,
    TIPO_RETIRADA,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services import impressao as servico
from app.services.pedidos import adicionar_itens_comanda, criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


@pytest.fixture()
def loja(app, two_tenants):
    """Um produto em cada tenant, e o salão do A com mesas."""
    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant_a.qtd_mesas = 4

    xtudo = Produto(tenant_id=tenant_a.id, nome="X-Tudo", preco=30.0)
    pizza = Produto(tenant_id=tenant_b.id, nome="Pizza", preco=40.0)
    db.session.add_all([xtudo, pizza])
    db.session.commit()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "xtudo": xtudo.id,
        "pizza": pizza.id,
    }


def _pedido(tenant, produto_id, tipo=TIPO_RETIRADA, **extra):
    payload = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": tipo,
        "pagamento": "Dinheiro",
        "carrinho": [{"produto_id": produto_id, "quantidade": 2}],
    }
    payload.update(extra)
    return criar_pedido(tenant, payload)


def _cabecalhos(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Pareamento
# --------------------------------------------------------------------------- #


def test_parear_guarda_so_o_hash_do_codigo(loja):
    token = servico.parear(loja["tenant_a"])

    agente = servico.agente_do_tenant(loja["tenant_a"].id)
    assert agente is not None
    assert agente.token_hash != token
    assert len(agente.token_hash) == 64
    assert servico.autenticar(loja["tenant_a"], token) is agente


def test_gerar_codigo_novo_invalida_o_anterior(loja):
    antigo = servico.parear(loja["tenant_a"])
    novo = servico.parear(loja["tenant_a"])

    assert servico.autenticar(loja["tenant_a"], antigo) is None
    assert servico.autenticar(loja["tenant_a"], novo) is not None
    # Continua sendo UM agente por restaurante, não dois.
    assert AgenteImpressao.query.filter_by(tenant_id=loja["tenant_a"].id).count() == 1


def test_codigo_de_um_restaurante_nao_vale_no_outro(loja):
    token_a = servico.parear(loja["tenant_a"])
    servico.parear(loja["tenant_b"])

    assert servico.autenticar(loja["tenant_b"], token_a) is None


def test_desparear_solta_o_que_estava_reservado(loja):
    token = servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)
    servico.reservar_proximo(servico.autenticar(loja["tenant_a"], token))

    assert ImpressaoJob.query.first().status == STATUS_IMPRIMINDO
    servico.desparear(loja["tenant_a"])

    assert servico.agente_do_tenant(loja["tenant_a"].id) is None
    assert ImpressaoJob.query.first().status == STATUS_PENDENTE


# --------------------------------------------------------------------------- #
# Quando a comanda entra na fila
# --------------------------------------------------------------------------- #


def test_pedido_do_site_so_imprime_quando_alguem_confirma(loja):
    servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    # Chegou o pedido: ninguém aceitou ainda, nada de papel.
    assert ImpressaoJob.query.count() == 0

    transicionar(pedido, STATUS_CONFIRMADO)
    assert ImpressaoJob.query.count() == 1


def test_comanda_de_mesa_imprime_na_abertura(loja):
    servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"], tipo=TIPO_MESA, mesa=2)

    job = ImpressaoJob.query.one()
    assert job.tipo == TIPO_COMANDA
    assert job.pedido_id == pedido.id
    assert "MESA: 02" in job.conteudo


def test_avancar_duas_vezes_nao_imprime_a_comanda_duas_vezes(loja):
    """Novo -> Confirmado -> Em preparo é um caminho comum no painel."""
    servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)

    assert ImpressaoJob.query.filter_by(tipo=TIPO_COMANDA).count() == 1


def test_sem_agente_pareado_a_fila_nao_cresce(loja):
    """Restaurante que nunca instalou o agente não acumula comanda velha."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert ImpressaoJob.query.count() == 0


def test_item_novo_na_comanda_imprime_so_o_que_entrou(loja):
    servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"], tipo=TIPO_MESA, mesa=1)

    outro = Produto(tenant_id=loja["tenant_a"].id, nome="Refrigerante", preco=6.0)
    db.session.add(outro)
    db.session.commit()
    adicionar_itens_comanda(pedido, [{"produto_id": outro.id, "quantidade": 1}])

    adicao = ImpressaoJob.query.filter_by(tipo=TIPO_ADICAO).one()
    assert "Refrigerante" in adicao.conteudo
    # O que o cozinheiro já fez não pode voltar no papel.
    assert "X-Tudo" not in adicao.conteudo
    # Nem o valor: papel com preço, na cozinha, vira conta na mão do cliente.
    assert "TOTAL" not in adicao.conteudo


def test_cancelar_pedido_tira_da_fila_o_que_ainda_nao_saiu(loja):
    from app.models.pedido import STATUS_CANCELADO as PEDIDO_CANCELADO

    servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    assert ImpressaoJob.query.one().status == STATUS_PENDENTE

    transicionar(pedido, PEDIDO_CANCELADO)
    assert ImpressaoJob.query.one().status == STATUS_CANCELADO


def test_cancelar_pedido_nao_apaga_o_que_ja_foi_impresso(loja):
    from app.models.pedido import STATUS_CANCELADO as PEDIDO_CANCELADO

    token = servico.parear(loja["tenant_a"])
    agente = servico.autenticar(loja["tenant_a"], token)
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    trabalho = servico.reservar_proximo(agente)
    servico.concluir(agente, trabalho["job_id"], trabalho["claim_token"], True)

    transicionar(pedido, PEDIDO_CANCELADO)
    # O papel está na cozinha; o registro precisa continuar dizendo isso.
    assert ImpressaoJob.query.one().status == STATUS_IMPRESSO


def test_falha_ao_enfileirar_nao_derruba_o_pedido(loja, monkeypatch):
    servico.parear(loja["tenant_a"])
    monkeypatch.setattr(
        servico, "montar_comanda", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("banco fora"))
    )

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert pedido.status == STATUS_CONFIRMADO
    assert ImpressaoJob.query.count() == 0


# --------------------------------------------------------------------------- #
# O que sai no papel
# --------------------------------------------------------------------------- #


def test_comanda_traz_o_que_a_cozinha_precisa(loja):
    pedido = _pedido(
        loja["tenant_a"],
        loja["xtudo"],
        tipo=TIPO_ENTREGA,
        endereco="Rua das Flores, 123, apto 4",
        observacao="tocar a campainha",
    )
    texto = servico.montar_comanda(pedido)

    assert "RESTAURANTE A" in texto
    assert "COMANDA DE PRODUCAO" in texto
    assert f"PEDIDO #{pedido.numero}" in texto
    assert "2x X-Tudo" in texto
    assert "Rua das Flores" in texto
    assert "tocar a campainha" in texto
    assert "R$ 60,00" in texto


def test_nenhuma_linha_estoura_a_largura_do_papel(loja):
    """Impressora térmica quebra sozinha, no meio da palavra, o que não couber."""
    pedido = _pedido(
        loja["tenant_a"],
        loja["xtudo"],
        tipo=TIPO_ENTREGA,
        endereco="Avenida Governador Agamenon Magalhaes, 1234, bloco C, apartamento 1502",
        observacao="sem cebola, sem tomate, sem picles, capricha no molho e manda talher extra",
    )
    for linha in servico.montar_comanda(pedido).split("\n"):
        assert len(linha) <= servico.LARGURA, linha


def test_conferencia_de_consumo_avisa_que_nao_e_fiscal(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"], tipo=TIPO_MESA, mesa=3)
    texto = servico.montar_comanda(pedido, tipo=TIPO_FECHAMENTO)

    assert "CONFERENCIA DE CONSUMO" in texto
    assert "NAO E DOCUMENTO FISCAL" in texto
    # "Comanda Aberta" é o valor que o sistema guarda enquanto a mesa não
    # escolheu — não é forma de pagamento, e este papel vai para o cliente.
    assert "PAGAMENTO" not in texto


def test_comanda_fechada_mostra_a_forma_de_pagamento(loja):
    from app.services.pedidos import fechar_comanda

    pedido = _pedido(loja["tenant_a"], loja["xtudo"], tipo=TIPO_MESA, mesa=3)
    fechar_comanda(pedido, "Cartão na entrega")

    assert "PAGAMENTO: Cartão na entrega" in servico.montar_comanda(pedido)


# --------------------------------------------------------------------------- #
# Reserva e confirmação
# --------------------------------------------------------------------------- #


def test_reserva_impede_dois_agentes_de_pegar_a_mesma_comanda(loja):
    token = servico.parear(loja["tenant_a"])
    agente = servico.autenticar(loja["tenant_a"], token)
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)

    primeiro = servico.reservar_proximo(agente)
    segundo = servico.reservar_proximo(agente)

    assert primeiro is not None
    assert segundo is None


def test_reserva_abandonada_volta_para_a_fila(loja):
    """O computador do balcão foi desligado no meio da impressão."""
    from datetime import datetime, timedelta

    token = servico.parear(loja["tenant_a"])
    agente = servico.autenticar(loja["tenant_a"], token)
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)
    servico.reservar_proximo(agente)

    job = ImpressaoJob.query.one()
    job.reservado_em = datetime.now() - timedelta(seconds=999)
    db.session.commit()

    assert servico.reservar_proximo(agente) is not None


def test_comanda_para_de_ser_oferecida_depois_de_muitas_falhas(loja):
    """Impressora sem papel não pode travar a fila inteira num pedido só."""
    token = servico.parear(loja["tenant_a"])
    agente = servico.autenticar(loja["tenant_a"], token)
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)

    for _ in range(MAX_TENTATIVAS):
        trabalho = servico.reservar_proximo(agente)
        assert trabalho is not None
        servico.concluir(agente, trabalho["job_id"], trabalho["claim_token"], False, "sem papel")

    assert servico.reservar_proximo(agente) is None
    assert ImpressaoJob.query.one().status == STATUS_ERRO


def test_confirmacao_com_reserva_errada_e_recusada(loja):
    token = servico.parear(loja["tenant_a"])
    agente = servico.autenticar(loja["tenant_a"], token)
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)
    trabalho = servico.reservar_proximo(agente)

    with pytest.raises(ValueError):
        servico.concluir(agente, trabalho["job_id"], "reserva-de-outro", True)


def test_agente_nao_confirma_trabalho_de_outro_restaurante(loja):
    token_a = servico.parear(loja["tenant_a"])
    token_b = servico.parear(loja["tenant_b"])
    agente_a = servico.autenticar(loja["tenant_a"], token_a)
    agente_b = servico.autenticar(loja["tenant_b"], token_b)

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)
    trabalho = servico.reservar_proximo(agente_a)

    with pytest.raises(ValueError):
        servico.concluir(agente_b, trabalho["job_id"], trabalho["claim_token"], True)


def test_agente_de_um_restaurante_nao_recebe_comanda_do_outro(loja):
    token_b = servico.parear(loja["tenant_b"])
    servico.parear(loja["tenant_a"])

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)

    agente_b = servico.autenticar(loja["tenant_b"], token_b)
    assert servico.reservar_proximo(agente_b) is None


# --------------------------------------------------------------------------- #
# A API que o agente usa
# --------------------------------------------------------------------------- #


def test_api_recusa_codigo_invalido(client, loja):
    servico.parear(loja["tenant_a"])
    resposta = client.post(
        "/api/impressao/agente/proximo",
        json={},
        headers=_cabecalhos("codigo-de-mentira"),
        base_url=BASE_A,
    )
    assert resposta.status_code == 401
    assert resposta.get_json()["status"] == "erro"


def test_api_recusa_codigo_certo_no_endereco_errado(client, loja):
    """A trava dupla: o código é do A, o endereço chamado é o do B."""
    token_a = servico.parear(loja["tenant_a"])
    servico.parear(loja["tenant_b"])

    resposta = client.post(
        "/api/impressao/agente/proximo",
        json={},
        headers=_cabecalhos(token_a),
        base_url=BASE_B,
    )
    assert resposta.status_code == 401


def test_ciclo_completo_pela_api(client, loja):
    token = servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    proximo = client.post(
        "/api/impressao/agente/proximo",
        json={"agent_name": "PC-BALCAO", "printer_name": "Daruma DR800", "version": "1.0"},
        headers=_cabecalhos(token),
        base_url=BASE_A,
    )
    trabalho = proximo.get_json()["trabalho"]
    assert "2x X-Tudo" in trabalho["content"]
    assert trabalho["encoding"] == "cp850"

    resultado = client.post(
        "/api/impressao/agente/resultado",
        json={"job_id": trabalho["job_id"], "claim_token": trabalho["claim_token"], "ok": True},
        headers=_cabecalhos(token),
        base_url=BASE_A,
    )
    assert resultado.get_json()["situacao"] == STATUS_IMPRESSO

    # O que o agente informou aparece na tela do dono do restaurante.
    agente = servico.agente_do_tenant(loja["tenant_a"].id)
    assert agente.nome == "PC-BALCAO"
    assert agente.impressora == "Daruma DR800"
    assert agente.online is True


def test_api_do_agente_funciona_com_csrf_ligado():
    """O agente não é navegador: não tem sessão, cookie nem token de formulário.

    Este teste liga o CSRF de propósito — o resto da suíte roda com ele
    desligado, e sem isto uma isenção quebrada só apareceria em produção, com o
    agente já instalado no restaurante.
    """
    from app import create_app
    from app.models.tenant import Tenant
    from app.models.usuario import Usuario
    from tests.conftest import TestConfig

    class ComCSRF(TestConfig):
        WTF_CSRF_ENABLED = True

    aplicacao = create_app(ComCSRF)
    with aplicacao.app_context():
        db.create_all()
        tenant = Tenant(
            slug="tenant-csrf", nome_fantasia="CSRF", email_contato="c@example.com", status="active"
        )
        db.session.add(tenant)
        db.session.flush()
        usuario = Usuario(tenant_id=tenant.id, nome="A", username="admin", role="admin")
        usuario.set_password("senha-csrf-123")
        db.session.add(usuario)
        db.session.commit()
        token = servico.parear(tenant)

        resposta = aplicacao.test_client().post(
            "/api/impressao/agente/proximo",
            json={},
            headers=_cabecalhos(token),
            base_url="http://tenant-csrf.localhost",
        )
        assert resposta.status_code == 200
        db.drop_all()


def test_api_sem_trabalho_responde_vazio_sem_erro(client, loja):
    token = servico.parear(loja["tenant_a"])
    resposta = client.post(
        "/api/impressao/agente/proximo", json={}, headers=_cabecalhos(token), base_url=BASE_A
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["trabalho"] is None


def test_ping_identifica_o_restaurante(client, loja):
    token = servico.parear(loja["tenant_a"])
    resposta = client.post(
        "/api/impressao/agente/ping",
        json={"agent_name": "PC-BALCAO", "printer_name": "Epson", "version": "1.0"},
        headers=_cabecalhos(token),
        base_url=BASE_A,
    )
    assert resposta.get_json()["restaurante"] == "Restaurante A"


def test_reserva_expirada_responde_409_para_o_agente_seguir_em_frente(client, loja):
    token = servico.parear(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    servico.enfileirar(pedido, forcar=True)
    agente = servico.autenticar(loja["tenant_a"], token)
    trabalho = servico.reservar_proximo(agente)
    servico.concluir(agente, trabalho["job_id"], trabalho["claim_token"], True)

    resposta = client.post(
        "/api/impressao/agente/resultado",
        json={"job_id": trabalho["job_id"], "claim_token": trabalho["claim_token"], "ok": True},
        headers=_cabecalhos(token),
        base_url=BASE_A,
    )
    assert resposta.status_code == 409


# --------------------------------------------------------------------------- #
# Tela do painel
# --------------------------------------------------------------------------- #


def test_tela_mostra_o_codigo_uma_vez_so(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post("/admin/impressao/parear", base_url=BASE_A)
    primeira = client.get("/admin/impressao", base_url=BASE_A)
    segunda = client.get("/admin/impressao", base_url=BASE_A)

    assert "Código de ativação" in primeira.get_data(as_text=True)
    assert "token-gerado" in primeira.get_data(as_text=True)
    assert "token-gerado" not in segunda.get_data(as_text=True)


def test_download_do_agente_nao_leva_segredo_junto(client, loja):
    """O pacote vai para o computador do restaurante; nada de config nem log."""
    import io
    import zipfile

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.get("/admin/impressao/agente.zip", base_url=BASE_A)
    assert resposta.status_code == 200

    dentro = zipfile.ZipFile(io.BytesIO(resposta.data)).namelist()
    assert "agente/agente_impressao.py" in dentro
    assert "agente/instalar_agente.bat" in dentro
    assert "agente/LEIA-ME.md" in dentro
    assert not any("agente_config" in nome or ".log" in nome or ".venv" in nome for nome in dentro)


def test_reimprimir_avisa_quando_ninguem_vai_buscar(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    resposta = client.post(
        f"/cozinha/pedidos/{pedido.id}/imprimir", base_url=BASE_A, follow_redirects=True
    )
    texto = resposta.get_data(as_text=True)

    assert "nenhum computador está pareado" in texto
    # A comanda entra na fila mesmo assim: quem pediu foi uma pessoa.
    assert ImpressaoJob.query.count() == 1


def test_reimprimir_nao_alcanca_pedido_de_outro_restaurante(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    pedido_b = _pedido(loja["tenant_b"], loja["pizza"])

    client.post(f"/cozinha/pedidos/{pedido_b.id}/imprimir", base_url=BASE_A, follow_redirects=True)

    assert ImpressaoJob.query.count() == 0


def test_plano_sem_impressao_bloqueia_a_tela(client, loja):
    from app.models.assinatura import Plano

    db.session.add(Plano(slug="basico", nome="Básico", recursos="cozinha,mesas"))
    loja["tenant_a"].plano = "basico"
    db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.get("/admin/impressao", base_url=BASE_A, follow_redirects=True)

    assert "não está incluído no plano" in resposta.get_data(as_text=True)


def test_plano_sem_impressao_nao_enfileira(loja):
    from app.models.assinatura import Plano

    db.session.add(Plano(slug="basico", nome="Básico", recursos="cozinha"))
    loja["tenant_a"].plano = "basico"
    db.session.commit()
    servico.parear(loja["tenant_a"])

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert ImpressaoJob.query.count() == 0
