"""Recursos liberados por plano (feature-gating).

A regra que mais importa aqui é a de compatibilidade: aplicar gating num sistema
em uso não pode tirar acesso de ninguém em silêncio. Plano não configurado — ou
plano fora do catálogo — libera tudo, e a restrição só passa a valer quando
alguém marca as caixas de propósito.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app import create_app
from app.extensions import db
from app.models.assinatura import RECURSOS_SLUGS, Plano
from app.models.cupom import BairroEntrega, Cupom
from app.models.pedido import TIPO_ENTREGA, TIPO_MESA, TIPO_RETIRADA, Pedido
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services.pedidos import criar_pedido
from app.services.recursos import recursos_do_tenant, tenant_libera
from tests.conftest import TestConfig, login_tenant

BASE_A = "http://tenant-a.localhost"


@pytest.fixture()
def app(tmp_path):
    class ComUploads(TestConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(ComUploads)
    with application.app_context():
        db.create_all()
        yield application
        db.drop_all()


@pytest.fixture()
def cenario(app, two_tenants):
    """Tenant A no plano 'starter', com cardápio, cupom e bairro prontos."""
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant.plano = "starter"
    tenant.qtd_mesas = 5
    produto = Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0)
    cupom = Cupom(tenant_id=tenant.id, codigo="DEZ", tipo="percentual", valor=10.0, limite_usos=9)
    bairro = BairroEntrega(tenant_id=tenant.id, nome="Centro", taxa=8.0)
    db.session.add_all([produto, cupom, bairro])
    db.session.commit()
    return {"tenant": tenant, "produto": produto.id}


def _plano(recursos=None, slug="starter"):
    """Cria o plano; recursos=None deixa "não configurado" (libera tudo)."""
    plano = Plano(slug=slug, nome=slug.title(), preco_mensal=99.0)
    if recursos is not None:
        plano.definir_recursos(recursos)
    db.session.add(plano)
    db.session.commit()
    return plano


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), (10, 120, 90)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# --------------------------------------------------------------------------- #
# Compatibilidade: ninguém perde acesso de surpresa
# --------------------------------------------------------------------------- #


def test_plano_nao_configurado_libera_tudo(cenario):
    _plano(recursos=None)
    assert recursos_do_tenant(cenario["tenant"]) == set(RECURSOS_SLUGS)


def test_plano_fora_do_catalogo_libera_tudo(cenario):
    """Tenant apontando para um slug sem plano cadastrado não pode ser bloqueado."""
    cenario["tenant"].plano = "plano-que-nao-existe"
    db.session.commit()
    assert recursos_do_tenant(cenario["tenant"]) == set(RECURSOS_SLUGS)


def test_plano_configurado_vazio_bloqueia_tudo(cenario):
    """Marcar nada é diferente de nunca ter configurado."""
    plano = _plano(recursos=[])
    assert plano.recursos_configurados is True
    assert recursos_do_tenant(cenario["tenant"]) == set()


def test_slug_invalido_e_ignorado_na_gravacao(cenario):
    plano = _plano(recursos=["cozinha", "voar", "mesas"])
    assert plano.recursos_liberados == {"cozinha", "mesas"}


# --------------------------------------------------------------------------- #
# Bloqueio nas telas de operação
# --------------------------------------------------------------------------- #


def test_cozinha_bloqueada_quando_fora_do_plano(cenario, client):
    _plano(recursos=["mesas"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/cozinha", base_url=BASE_A, follow_redirects=True)
    corpo = resposta.get_data(as_text=True)
    assert "não está incluído no plano" in corpo
    # Caiu no painel, não na cozinha: a fila de pedidos não aparece.
    assert 'class="colunas"' not in corpo
    # E o painel não oferece link para uma tela que ela não pode abrir.
    assert 'href="/cozinha"' not in corpo


def test_cozinha_liberada_quando_no_plano(cenario, client):
    """Contraprova: sem isto, o teste acima passaria mesmo com a tela quebrada."""
    _plano(recursos=["cozinha"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/cozinha", base_url=BASE_A).get_data(as_text=True)
    assert "Pedidos em andamento" in corpo


def test_eventos_da_cozinha_tambem_sao_bloqueados(cenario, client):
    """A rota de atualização automática não pode ser uma porta lateral."""
    _plano(recursos=[])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/cozinha/eventos", base_url=BASE_A, follow_redirects=False)
    assert resposta.status_code in (302, 303)


def test_mesas_bloqueadas_quando_fora_do_plano(cenario, client):
    _plano(recursos=["cozinha"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    for url in ("/mesas", "/mesas/1"):
        corpo = client.get(url, base_url=BASE_A, follow_redirects=True).get_data(as_text=True)
        assert "não está incluído no plano" in corpo


def test_cupons_e_bairros_bloqueados_no_admin(cenario, client):
    _plano(recursos=["cozinha", "mesas"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    for url in ("/admin/cupons", "/admin/bairros"):
        corpo = client.get(url, base_url=BASE_A, follow_redirects=True).get_data(as_text=True)
        assert "não está incluído no plano" in corpo


def test_menu_esconde_o_que_o_plano_nao_inclui(cenario, client):
    _plano(recursos=["cozinha"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)
    assert "/cozinha" in corpo
    assert "/admin/cupons" not in corpo
    assert "/admin/bairros" not in corpo
    assert "/mesas" not in corpo
    # O básico continua no menu.
    assert "/admin/produtos" in corpo


# --------------------------------------------------------------------------- #
# Bloqueio no que envolve dinheiro
# --------------------------------------------------------------------------- #


def test_cupom_nao_e_aplicado_quando_fora_do_plano(cenario, client):
    _plano(recursos=[])
    with pytest.raises(ValueError, match="não usa cupons"):
        criar_pedido(
            cenario["tenant"],
            {
                "cliente": "Maria",
                "telefone": "81999998888",
                "tipo": TIPO_RETIRADA,
                "pagamento": "Dinheiro",
                "cupom": "DEZ",
                "carrinho": [{"produto_id": cenario["produto"], "quantidade": 1}],
            },
        )


def test_taxa_de_bairro_nao_e_cobrada_quando_fora_do_plano(cenario):
    """Sem o recurso, a entrega sai com taxa zero em vez de dar erro."""
    _plano(recursos=[])
    pedido = criar_pedido(
        cenario["tenant"],
        {
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_ENTREGA,
            "endereco": "Rua das Flores, 100",
            "pagamento": "Dinheiro",
            "carrinho": [{"produto_id": cenario["produto"], "quantidade": 1}],
        },
    )
    assert pedido.taxa_entrega == 0.0
    assert pedido.bairro_id is None
    assert pedido.total == 30.0


def test_taxa_de_bairro_e_cobrada_quando_no_plano(cenario):
    """Contraprova do teste acima."""
    _plano(recursos=["bairros"])
    pedido = criar_pedido(
        cenario["tenant"],
        {
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_ENTREGA,
            "endereco": "Rua das Flores, 100",
            "pagamento": "Dinheiro",
            "carrinho": [{"produto_id": cenario["produto"], "quantidade": 1}],
        },
    )
    assert pedido.taxa_entrega == 8.0
    assert pedido.total == 38.0


def test_vitrine_esconde_cupom_e_bairro_fora_do_plano(cenario, client):
    _plano(recursos=[])
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": cenario["produto"], "quantidade": 1},
        base_url=BASE_A,
        follow_redirects=True,
    )

    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "Cupom de desconto" not in corpo
    assert "Bairro da entrega" not in corpo


def test_aplicar_cupom_pela_vitrine_e_recusado(cenario, client):
    _plano(recursos=[])
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": cenario["produto"], "quantidade": 1},
        base_url=BASE_A,
        follow_redirects=True,
    )

    resposta = client.post(
        "/carrinho/cupom", data={"cupom": "DEZ"}, base_url=BASE_A, follow_redirects=True
    )
    assert "não usa cupons" in resposta.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Fotos
# --------------------------------------------------------------------------- #


def test_foto_ignorada_quando_fora_do_plano(cenario, client):
    _plano(recursos=[])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/produtos",
        data={"nome": "Sem Foto", "preco": "10,00", "imagem": (_png(), "foto.png")},
        base_url=BASE_A,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "não inclui fotos" in resposta.get_data(as_text=True)

    produto = Produto.query.filter_by(nome="Sem Foto").one()
    assert produto.imagem is None, "o produto é criado, só sem a foto"


def test_foto_aceita_quando_no_plano(cenario, client):
    _plano(recursos=["fotos"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/produtos",
        data={"nome": "Com Foto", "preco": "10,00", "imagem": (_png(), "foto.png")},
        base_url=BASE_A,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert Produto.query.filter_by(nome="Com Foto").one().imagem is not None


# --------------------------------------------------------------------------- #
# O que nunca é bloqueado
# --------------------------------------------------------------------------- #


def test_vender_nunca_depende_de_plano(cenario, client):
    """Cardápio, carrinho, pedido e acompanhamento são a base do produto.

    Um plano que não tira pedido não faria sentido comercial, então nem existe a
    opção de desligar isso.
    """
    _plano(recursos=[])

    assert client.get("/", base_url=BASE_A).status_code == 200
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": cenario["produto"], "quantidade": 1},
        base_url=BASE_A,
        follow_redirects=True,
    )
    client.post(
        "/pedido",
        data={
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_RETIRADA,
            "pagamento": "Dinheiro",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )
    pedido = Pedido.query.one()
    assert pedido.total == 30.0
    assert client.get(f"/pedido/{pedido.public_token}", base_url=BASE_A).status_code == 200


def test_produtos_e_categorias_nunca_sao_bloqueados(cenario, client):
    _plano(recursos=[])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    for url in ("/admin/produtos", "/admin/categorias", "/admin/adicionais", "/admin/configuracoes"):
        assert client.get(url, base_url=BASE_A).status_code == 200, url


# --------------------------------------------------------------------------- #
# Visão do contratante e da plataforma
# --------------------------------------------------------------------------- #


def test_tenant_ve_o_que_o_plano_inclui(cenario, client):
    plano = _plano(recursos=["cozinha"])
    plano.descricao = "Ideal para quem só faz delivery."
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/configuracoes", base_url=BASE_A).get_data(as_text=True)
    assert "Seu plano" in corpo
    assert "Ideal para quem só faz delivery." in corpo
    assert "R$ 99,00" in corpo
    assert "Painel da cozinha" in corpo
    # O que não está incluído aparece marcado como fora, não escondido.
    assert "não incluído no seu plano" in corpo


def test_plataforma_marca_os_recursos_do_plano(client, platform_admin):
    plano = _plano(recursos=["cozinha", "fotos"])
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
    )

    corpo = client.get("/plataforma/planos", base_url="http://app.localhost").get_data(as_text=True)
    assert 'value="cozinha"' in corpo
    assert "Salão e comanda de mesa" in corpo
    assert plano.libera("cozinha") and not plano.libera("mesas")


def test_plataforma_grava_os_recursos_marcados(client, platform_admin):
    plano = _plano(recursos=None)
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
    )

    client.post(
        f"/plataforma/planos/{plano.id}/salvar",
        data={
            "nome": "Starter",
            "preco_mensal": "99,00",
            "ordem": "1",
            "ativo": "on",
            "recursos": ["cozinha", "bairros"],
        },
        base_url="http://app.localhost",
        follow_redirects=True,
    )

    db.session.refresh(plano)
    assert plano.recursos_liberados == {"cozinha", "bairros"}
    assert plano.recursos_configurados is True


def test_mesa_de_um_plano_nao_afeta_o_outro_tenant(cenario, client, two_tenants):
    """Tenants em planos diferentes têm recursos diferentes."""
    _plano(recursos=["mesas"], slug="starter")
    _plano(recursos=[], slug="basico")

    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant_b.plano = "basico"
    db.session.commit()

    assert tenant_libera(cenario["tenant"], "mesas") is True
    assert tenant_libera(tenant_b, "mesas") is False


# --------------------------------------------------------------------------- #
# Limites numéricos do plano
#
# O plano deixou de ser só "liga/desliga": agora tem teto. O que precisa ser
# provado é a regra de compatibilidade — plano sem limite não limita nada — e
# que o teto barra a CRIAÇÃO sem travar quem já passou dele.
# --------------------------------------------------------------------------- #


def _plano_com_limites(limites, slug="starter", recursos=None):
    plano = _plano(recursos=recursos, slug=slug)
    plano.definir_limites(limites)
    db.session.commit()
    return plano


def test_plano_sem_limites_nao_limita(cenario):
    from app.services.recursos import dentro_do_limite, limite_do_tenant

    _plano(recursos=None)
    assert limite_do_tenant(cenario["tenant"], "max_produtos") is None
    assert dentro_do_limite(cenario["tenant"], "max_produtos", 10_000) is True


@pytest.mark.parametrize("valor", [0, -3, "", None, "abc"])
def test_limite_zerado_ou_invalido_vira_sem_limite(cenario, valor):
    """Zero significa "sem teto"; texto inválido não pode virar teto acidental."""
    from app.services.recursos import limite_do_tenant

    _plano_com_limites({"max_produtos": valor})
    assert limite_do_tenant(cenario["tenant"], "max_produtos") is None


def test_limite_de_produtos_barra_a_criacao(cenario, client):
    # Já existe 1 produto no cenário; o teto de 1 fecha a porta.
    _plano_com_limites({"max_produtos": 1})
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/produtos",
        data={"nome": "Segundo produto", "preco": "10,00", "disponivel": "on"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert "permite até 1" in resposta.get_data(as_text=True)
    assert Produto.query.filter_by(tenant_id=cenario["tenant"].id).count() == 1


def test_limite_folgado_deixa_criar(cenario, client):
    _plano_com_limites({"max_produtos": 5})
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/produtos",
        data={"nome": "Segundo produto", "preco": "10,00", "disponivel": "on"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert Produto.query.filter_by(tenant_id=cenario["tenant"].id).count() == 2


def test_limite_de_mesas_barra_o_salao_maior(cenario, client):
    _plano_com_limites({"max_mesas": 4})
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/configuracoes",
        data={"qtd_mesas": "30", "tempo_estimado_min": "40", "tempo_estimado_max": "60"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert "permite até 4" in resposta.get_data(as_text=True)
    db.session.refresh(cenario["tenant"])
    assert cenario["tenant"].qtd_mesas == 5, "o valor antigo não pode ser alterado"


def test_limite_de_um_plano_nao_vaza_para_outro(cenario, two_tenants):
    from app.services.recursos import limite_do_tenant

    _plano_com_limites({"max_produtos": 3}, slug="starter")
    _plano_com_limites({"max_produtos": 50}, slug="basico")

    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant_b.plano = "basico"
    db.session.commit()

    assert limite_do_tenant(cenario["tenant"], "max_produtos") == 3
    assert limite_do_tenant(tenant_b, "max_produtos") == 50


def test_super_admin_edita_limite_pela_tela_de_planos(cenario, client, platform_admin):
    plano = _plano(recursos=["cozinha"])
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
    )

    client.post(
        f"/plataforma/planos/{plano.id}/salvar",
        data={
            "nome": plano.nome, "preco_mensal": "99,00", "ordem": "0", "ativo": "on",
            "recursos": ["cozinha"], "limite_max_produtos": "25", "limite_max_mesas": "",
        },
        base_url="http://app.localhost",
        follow_redirects=True,
    )

    db.session.refresh(plano)
    assert plano.limite("max_produtos") == 25
    assert plano.limite("max_mesas") is None


# --------------------------------------------------------------------------- #
# Recursos novos: custos e identidade
# --------------------------------------------------------------------------- #


def test_custos_tem_recurso_proprio_separado_de_estoque(cenario, client):
    """Quem tem estoque mas não custos vê o Estoque e não vê a ficha técnica."""
    _plano(recursos=["estoque"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    assert client.get("/admin/insumos", base_url=BASE_A).status_code == 200

    resposta = client.get("/admin/custos", base_url=BASE_A, follow_redirects=True)
    assert "não está incluído no plano" in resposta.get_data(as_text=True)


def test_identidade_fora_do_plano_nao_troca_a_cor(cenario, client):
    _plano(recursos=["cozinha"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": "#1e88e5"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    db.session.refresh(cenario["tenant"])
    assert cenario["tenant"].cor_marca is None
