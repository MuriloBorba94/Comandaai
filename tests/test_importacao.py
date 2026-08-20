"""Fase 11 — trazer um restaurante do sistema single-tenant.

O que precisa ser provado aqui não é "os dados entraram", e sim que a tradução
entre os dois modelos não corrompe nada:

- categoria era texto livre e vira registro;
- adicional era lista global e passa a ser vinculado por produto;
- a senha do usuário atravessa como hash, então a equipe entra com a senha de
  sempre — e a senha em texto nunca existe em lugar nenhum;
- o que não couber é RECUSADO com aviso, em vez de entrar torto e silencioso.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app
from app.extensions import db
from app.models.adicional import Adicional
from app.models.categoria import Categoria
from app.models.cupom import BairroEntrega, Cupom
from app.models.estoque import FichaTecnica, Insumo
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.models.usuario import Usuario
from app.services.importacao import ErroDeImportacao, importar
from tests.conftest import TestConfig

SENHA_ORIGINAL = "senha-do-murilo-123"


@pytest.fixture()
def app(tmp_path):
    """App com uploads próprios: a importação copia foto de verdade."""

    class ComUploads(TestConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    aplicacao = create_app(ComUploads)
    with aplicacao.app_context():
        db.create_all()
        yield aplicacao
        db.drop_all()


@pytest.fixture()
def legado(tmp_path) -> Path:
    """Um banco no formato do sistema antigo, com o mesmo esquema do real."""
    caminho = tmp_path / "hamburgueria.db"
    con = sqlite3.connect(caminho)
    con.executescript(
        """
        CREATE TABLE loja_config (id INTEGER PRIMARY KEY, margem_lucro FLOAT,
            nome VARCHAR(100), numero_whatsapp VARCHAR(20),
            tempo_estimado_min INTEGER, tempo_estimado_max INTEGER);
        CREATE TABLE produto (id INTEGER PRIMARY KEY, nome VARCHAR(100), descricao VARCHAR(200),
            preco FLOAT, categoria VARCHAR(50), imagem VARCHAR(200), disponivel BOOLEAN,
            combo_promocional BOOLEAN DEFAULT 0);
        CREATE TABLE adicional (id INTEGER PRIMARY KEY, nome VARCHAR(50), preco FLOAT,
            disponivel BOOLEAN DEFAULT 1);
        CREATE TABLE bairro_entrega (id INTEGER PRIMARY KEY, nome VARCHAR(100), taxa FLOAT,
            prazo_adicional_min INTEGER, ativo BOOLEAN, ordem INTEGER);
        CREATE TABLE cupom (id INTEGER PRIMARY KEY, codigo VARCHAR(40), descricao VARCHAR(160),
            tipo VARCHAR(20), valor FLOAT, pedido_minimo FLOAT, limite_usos INTEGER,
            usos_confirmados INTEGER, ativo BOOLEAN, inicio_em DATETIME, fim_em DATETIME,
            permite_combo_promocional BOOLEAN DEFAULT 0);
        CREATE TABLE insumo (id INTEGER PRIMARY KEY, nome VARCHAR(100), preco_compra FLOAT,
            quantidade_compra FLOAT, unidade VARCHAR(10), estoque_atual FLOAT DEFAULT 0,
            estoque_minimo FLOAT DEFAULT 0, controle_estoque BOOLEAN DEFAULT 1);
        CREATE TABLE ficha_tecnica (id INTEGER PRIMARY KEY, produto_id INTEGER,
            insumo_id INTEGER, quantidade_usada FLOAT);
        CREATE TABLE usuario (id INTEGER PRIMARY KEY, nome VARCHAR(100), username VARCHAR(50),
            senha VARCHAR(50), role VARCHAR(20), ativo BOOLEAN DEFAULT 1);
        """
    )
    con.execute(
        "INSERT INTO loja_config VALUES (1, 40.0, \"Borba's Burguer\", '5581994755726', 30, 40)"
    )
    con.executemany(
        "INSERT INTO produto VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "X-Tudo", "O completo", 28.0, "Burgers", "produto_1_abc.webp", 1, 0),
            (2, "X-Bacon", None, 26.0, "Burgers", None, 1, 0),
            (3, "Combo Casal", "2 lanches", 55.0, "Combos", None, 1, 1),
            (4, "Refrigerante", None, 8.0, "Bebidas", None, 0, 0),
        ],
    )
    con.executemany(
        "INSERT INTO adicional VALUES (?,?,?,?)",
        [(1, "Bacon", 4.0, 1), (2, "Cheddar", 3.5, 1)],
    )
    con.executemany(
        "INSERT INTO bairro_entrega VALUES (?,?,?,?,?,?)",
        [(1, "Centro", 5.0, 10, 1, 0), (2, "Buraré", 8.0, 20, 1, 1)],
    )
    con.execute(
        "INSERT INTO cupom VALUES (1,'DEZ','Dez por cento','percentual',10.0,30.0,100,7,1,NULL,NULL,0)"
    )
    con.executemany(
        "INSERT INTO insumo VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "Carne", 200.0, 5000.0, "g", 1000.0, 300.0, 1),
            (2, "Pão", 10.0, 20.0, "un", 50.0, 10.0, 1),
        ],
    )
    con.executemany(
        "INSERT INTO ficha_tecnica VALUES (?,?,?,?)",
        [(1, 1, 1, 150.0), (2, 1, 2, 1.0), (3, 2, 1, 120.0)],
    )
    con.execute(
        "INSERT INTO usuario VALUES (1,'Murilo','murilo',?,'admin',1)",
        (generate_password_hash(SENHA_ORIGINAL),),
    )
    con.execute(
        "INSERT INTO usuario VALUES (2,'Garçom','tekko',?,'garcom',1)",
        (generate_password_hash("outra-senha-456"),),
    )
    con.commit()
    con.close()
    return caminho


def _importar(legado, **extra):
    dados = {"slug": "borbas", "email_contato": "contato@borbas.com.br"}
    dados.update(extra)
    return importar(str(legado), **dados)


# --------------------------------------------------------------------------- #
# O caminho feliz
# --------------------------------------------------------------------------- #


def test_importa_o_restaurante_com_a_config_da_loja(app, legado):
    relatorio = _importar(legado, qtd_mesas=12)

    tenant = Tenant.query.filter_by(slug="borbas").first()
    assert tenant is not None
    assert tenant.nome_fantasia == "Borba's Burguer"
    assert tenant.telefone_contato == "5581994755726"
    assert tenant.tempo_estimado_min == 30
    assert tenant.tempo_estimado_max == 40
    assert tenant.margem_lucro == pytest.approx(40.0)
    # O sistema antigo não tinha mesas configuráveis; vem por parâmetro.
    assert tenant.qtd_mesas == 12
    assert relatorio.contagens["produtos"] == 4


def test_categoria_de_texto_livre_vira_registro_na_ordem_do_cardapio(app, legado):
    _importar(legado)

    tenant = Tenant.query.filter_by(slug="borbas").first()
    categorias = (
        Categoria.query.filter_by(tenant_id=tenant.id).order_by(Categoria.ordem).all()
    )
    assert [c.nome for c in categorias] == ["Burgers", "Combos", "Bebidas"]

    xtudo = Produto.query.filter_by(tenant_id=tenant.id, nome="X-Tudo").first()
    assert xtudo.categoria.nome == "Burgers"


def test_produto_preserva_preco_disponibilidade_e_combo(app, legado):
    _importar(legado)
    tenant = Tenant.query.filter_by(slug="borbas").first()

    combo = Produto.query.filter_by(tenant_id=tenant.id, nome="Combo Casal").first()
    refri = Produto.query.filter_by(tenant_id=tenant.id, nome="Refrigerante").first()

    assert combo.preco == pytest.approx(55.0)
    assert combo.combo_promocional is True
    assert refri.disponivel is False, "produto indisponível não pode virar disponível"


def test_adicional_global_vira_vinculo_por_produto(app, legado):
    """No sistema antigo a regra era "só Burgers", cravada no código."""
    relatorio = _importar(legado)
    tenant = Tenant.query.filter_by(slug="borbas").first()

    xtudo = Produto.query.filter_by(tenant_id=tenant.id, nome="X-Tudo").first()
    combo = Produto.query.filter_by(tenant_id=tenant.id, nome="Combo Casal").first()

    assert {a.nome for a in xtudo.adicionais} == {"Bacon", "Cheddar"}
    assert combo.adicionais == [], "adicional não valia fora de Burgers"
    assert any("Burgers" in aviso for aviso in relatorio.avisos), "a decisão tem que ser relatada"


def test_bairros_cupons_insumos_e_fichas_atravessam(app, legado):
    _importar(legado)
    tenant = Tenant.query.filter_by(slug="borbas").first()

    assert BairroEntrega.query.filter_by(tenant_id=tenant.id).count() == 2
    assert Insumo.query.filter_by(tenant_id=tenant.id).count() == 2

    cupom = Cupom.query.filter_by(tenant_id=tenant.id).first()
    assert cupom.codigo == "DEZ"
    # O contador vem junto: zerar daria ao cliente usos que ele já gastou.
    assert cupom.usos_confirmados == 7
    assert cupom.limite_usos == 100

    xtudo = Produto.query.filter_by(tenant_id=tenant.id, nome="X-Tudo").first()
    assert len(xtudo.ficha) == 2
    # 150 g x R$ 0,04 + 1 pão x R$ 0,50 = R$ 6,50
    assert xtudo.custo_por_ficha == pytest.approx(6.50)


def test_usuario_entra_com_a_senha_de_sempre(app, legado):
    """O hash atravessa; ninguém precisa de senha nova, e a senha em texto
    não existe nem no sistema antigo."""
    _importar(legado)
    tenant = Tenant.query.filter_by(slug="borbas").first()

    murilo = Usuario.query.filter_by(tenant_id=tenant.id, username="murilo").first()
    assert murilo.role == "admin"
    assert murilo.check_password(SENHA_ORIGINAL)
    assert not murilo.check_password("qualquer-outra")
    assert Usuario.query.filter_by(tenant_id=tenant.id).count() == 2


def test_foto_e_copiada_para_a_pasta_do_tenant(app, legado, tmp_path):
    origem = tmp_path / "uploads_legado"
    origem.mkdir()
    (origem / "produto_1_abc.webp").write_bytes(b"conteudo-da-foto")

    relatorio = _importar(legado, pasta_fotos=str(origem))
    tenant = Tenant.query.filter_by(slug="borbas").first()
    xtudo = Produto.query.filter_by(tenant_id=tenant.id, nome="X-Tudo").first()

    assert xtudo.imagem == "borbas/produto_1_abc.webp"
    destino = Path(app.config["UPLOAD_FOLDER"]) / xtudo.imagem
    assert destino.is_file()
    assert destino.read_bytes() == b"conteudo-da-foto"
    assert relatorio.contagens["fotos copiadas"] == 1


def test_foto_faltando_nao_derruba_a_importacao(app, legado, tmp_path):
    """Arquivo perdido é comum em instalação antiga; o produto entra sem foto."""
    origem = tmp_path / "vazia"
    origem.mkdir()

    relatorio = _importar(legado, pasta_fotos=str(origem))
    tenant = Tenant.query.filter_by(slug="borbas").first()
    xtudo = Produto.query.filter_by(tenant_id=tenant.id, nome="X-Tudo").first()

    assert xtudo.imagem is None
    assert Produto.query.filter_by(tenant_id=tenant.id).count() == 4
    assert any("foto não encontrada" in aviso for aviso in relatorio.avisos)


# --------------------------------------------------------------------------- #
# Simulação e recusas
# --------------------------------------------------------------------------- #


def test_simular_nao_grava_nada(app, legado):
    relatorio = _importar(legado, simular=True)

    assert relatorio.simulado is True
    assert relatorio.contagens["produtos"] == 4, "o relatório mostra o que aconteceria"
    assert Tenant.query.filter_by(slug="borbas").first() is None
    assert Produto.query.count() == 0


def test_simular_nao_copia_foto(app, legado, tmp_path):
    origem = tmp_path / "uploads_legado"
    origem.mkdir()
    (origem / "produto_1_abc.webp").write_bytes(b"foto")

    _importar(legado, pasta_fotos=str(origem), simular=True)

    destino = Path(app.config["UPLOAD_FOLDER"]) / "borbas"
    assert not destino.exists(), "simulação não pode deixar arquivo para trás"


def test_recusa_slug_que_ja_existe(app, legado):
    """Importar por cima duplicaria o cardápio inteiro do restaurante."""
    _importar(legado)

    with pytest.raises(ErroDeImportacao, match="Já existe"):
        _importar(legado)

    assert Produto.query.count() == 4, "nada foi duplicado"


def test_recusa_banco_inexistente(app):
    with pytest.raises(ErroDeImportacao, match="não encontrado"):
        importar("/caminho/que/nao/existe.db", slug="x", email_contato="a@b.c")


def test_recusa_pasta_de_fotos_inexistente(app, legado):
    with pytest.raises(ErroDeImportacao, match="Pasta de fotos"):
        _importar(legado, pasta_fotos="/pasta/que/nao/existe")

    assert Tenant.query.filter_by(slug="borbas").first() is None


def test_falha_no_meio_nao_deixa_tenant_pela_metade(app, legado, monkeypatch):
    """Sem rollback, sobraria um restaurante com cardápio parcial no ar."""
    from app.services import importacao

    def explode(*args, **kwargs):
        raise RuntimeError("falha simulada no meio")

    monkeypatch.setattr(importacao, "_importar_insumos", explode)

    with pytest.raises(RuntimeError):
        _importar(legado)

    assert Tenant.query.filter_by(slug="borbas").first() is None
    assert Produto.query.count() == 0
    assert Categoria.query.count() == 0


# --------------------------------------------------------------------------- #
# Instalações antigas, com esquema incompleto
# --------------------------------------------------------------------------- #


def test_banco_sem_estoque_nem_cupom_ainda_importa_o_cardapio(app, tmp_path):
    """Instalação velha do sistema antigo não tem insumo nem cupom. Faltar
    tabela é motivo para pular, não para abortar o cardápio inteiro."""
    caminho = tmp_path / "antigo.db"
    con = sqlite3.connect(caminho)
    con.executescript(
        """
        CREATE TABLE produto (id INTEGER PRIMARY KEY, nome VARCHAR(100), descricao VARCHAR(200),
            preco FLOAT, categoria VARCHAR(50), imagem VARCHAR(200), disponivel BOOLEAN);
        CREATE TABLE usuario (id INTEGER PRIMARY KEY, nome VARCHAR(100), username VARCHAR(50),
            senha VARCHAR(50), role VARCHAR(20));
        """
    )
    con.execute("INSERT INTO produto VALUES (1,'X-Salada',NULL,20.0,'Burgers',NULL,1)")
    con.execute(
        "INSERT INTO usuario VALUES (1,'Dono','dono',?,'admin')",
        (generate_password_hash("senha-antiga-999"),),
    )
    con.commit()
    con.close()

    relatorio = importar(str(caminho), slug="antigo", email_contato="a@b.c")

    tenant = Tenant.query.filter_by(slug="antigo").first()
    assert Produto.query.filter_by(tenant_id=tenant.id).count() == 1
    assert Insumo.query.count() == 0
    assert Cupom.query.count() == 0
    assert relatorio.contagens.get("usuários") == 1


def test_unidade_desconhecida_vira_un_com_aviso(app, tmp_path):
    caminho = tmp_path / "esquisito.db"
    con = sqlite3.connect(caminho)
    con.executescript(
        """
        CREATE TABLE insumo (id INTEGER PRIMARY KEY, nome VARCHAR(100), preco_compra FLOAT,
            quantidade_compra FLOAT, unidade VARCHAR(10));
        """
    )
    con.execute("INSERT INTO insumo VALUES (1,'Tempero',5.0,100.0,'colher')")
    con.commit()
    con.close()

    relatorio = importar(str(caminho), slug="esq", email_contato="a@b.c")

    insumo = Insumo.query.filter_by(nome="Tempero").first()
    assert insumo.unidade == "un"
    assert any("colher" in aviso for aviso in relatorio.avisos)


def test_quantidade_de_compra_zerada_nao_zera_o_custo_dos_pratos(app, tmp_path):
    """Divisão por zero aqui zeraria o custo de todo prato que usa o insumo."""
    caminho = tmp_path / "zerado.db"
    con = sqlite3.connect(caminho)
    con.executescript(
        """
        CREATE TABLE insumo (id INTEGER PRIMARY KEY, nome VARCHAR(100), preco_compra FLOAT,
            quantidade_compra FLOAT, unidade VARCHAR(10));
        """
    )
    con.execute("INSERT INTO insumo VALUES (1,'Sal',3.0,0.0,'g')")
    con.commit()
    con.close()

    relatorio = importar(str(caminho), slug="zer", email_contato="a@b.c")

    insumo = Insumo.query.filter_by(nome="Sal").first()
    assert insumo.quantidade_compra == pytest.approx(1.0)
    assert insumo.custo_unitario == pytest.approx(3.0)
    assert any("zerada" in aviso for aviso in relatorio.avisos)


def test_importacao_nao_encosta_em_outro_tenant(app, legado):
    """A garantia de sempre: o vizinho não pode ser tocado."""
    outro = Tenant(slug="vizinho", nome_fantasia="Vizinho", email_contato="v@e.com", status="active")
    db.session.add(outro)
    db.session.flush()
    db.session.add(Produto(tenant_id=outro.id, nome="Pizza do vizinho", preco=40.0))
    db.session.commit()

    _importar(legado)

    assert Produto.query.filter_by(tenant_id=outro.id).count() == 1
    assert Categoria.query.filter_by(tenant_id=outro.id).count() == 0
    borbas = Tenant.query.filter_by(slug="borbas").first()
    assert Produto.query.filter_by(tenant_id=borbas.id).count() == 4
    assert FichaTecnica.query.count() == 3
