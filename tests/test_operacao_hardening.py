"""Fase 10 — backup que presta, diário de auditoria e checagem de saúde.

Backup é a parte do sistema que ninguém exercita até precisar, que é o pior
momento possível para descobrir que nunca funcionou. Por isso a maior parte
destes testes é sobre provar que a conferência de fato reprova um backup ruim —
um teste que só verifica o caminho feliz de um backup não vale nada.
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from app.extensions import db
from app.models.auditoria import (
    ACAO_LOGIN,
    ACAO_LOGIN_FALHOU,
    ACAO_PEDIDO_CANCELADO,
    ACAO_PIX_ALTERADO,
    Auditoria,
)
from app.models.pedido import STATUS_CANCELADO, TIPO_RETIRADA
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services import auditoria as servico_auditoria
from app.services import backup as servico_backup
from app.services.backup import BackupInvalido
from app.services.pedidos import criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"


@pytest.fixture()
def loja(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    db.session.add(Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0))
    db.session.commit()
    return tenant


@pytest.fixture()
def banco_em_arquivo(app, tmp_path):
    """Um banco SQLite de verdade em disco, porque backup de :memory: não existe."""
    arquivo = tmp_path / "saas.db"
    conexao = sqlite3.connect(arquivo)
    conexao.execute("create table tenant (id integer primary key, slug text)")
    conexao.execute("create table pedido (id integer primary key)")
    conexao.execute("create table produto (id integer primary key)")
    conexao.executemany("insert into tenant (slug) values (?)", [("a",), ("b",)])
    conexao.execute("insert into pedido default values")
    conexao.commit()
    conexao.close()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{arquivo.as_posix()}"
    return arquivo


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #


def test_backup_copia_e_confere_o_conteudo(banco_em_arquivo, tmp_path):
    resultado = servico_backup.fazer(tmp_path / "backups")

    assert Path(resultado["arquivo"]).exists()
    assert resultado["tenants"] == 2
    assert resultado["pedidos"] == 1
    # A soma vai num arquivo ao lado, para a conferência posterior.
    assert Path(resultado["arquivo"] + ".sha256").exists()


def test_conferencia_reprova_backup_alterado_no_disco(banco_em_arquivo, tmp_path):
    """O teste que dá sentido a todos os outros.

    `integrity_check` do SQLite confere a ESTRUTURA do banco, não o conteúdo
    byte a byte — um arquivo pode ter sido corrompido no disco e ainda passar
    nele. É a soma de verificação que pega isso.
    """
    destino = tmp_path / "backups"
    resultado = servico_backup.fazer(destino)
    arquivo = Path(resultado["arquivo"])

    bruto = bytearray(gzip.decompress(arquivo.read_bytes()))
    bruto[3000:3400] = b"x" * 400
    arquivo.write_bytes(gzip.compress(bytes(bruto)))

    with pytest.raises(BackupInvalido, match="soma de verificação"):
        servico_backup.verificar(destino)


def test_conferencia_reprova_backup_vazio(banco_em_arquivo, tmp_path):
    """Arquivo vazio passa no integrity_check e não é backup de nada."""
    destino = tmp_path / "backups"
    destino.mkdir()
    vazio = destino / "saas-20260101-000000.db"
    sqlite3.connect(vazio).close()

    with pytest.raises(BackupInvalido, match="vazio"):
        servico_backup.verificar(destino)


def test_conferencia_reclama_quando_nao_ha_backup(tmp_path):
    with pytest.raises(BackupInvalido, match="Nenhum backup"):
        servico_backup.verificar(tmp_path / "nao-existe")


def test_backup_reclama_quando_o_banco_sumiu(app, tmp_path):
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{(tmp_path / 'sumido.db').as_posix()}"

    with pytest.raises(BackupInvalido, match="não foi encontrado"):
        servico_backup.fazer(tmp_path / "backups")


def test_comando_de_verificacao_falha_quando_o_backup_esta_velho(app, banco_em_arquivo, tmp_path):
    """Cron que parou de rodar não avisa ninguém; este comando avisa."""
    import os
    import time

    destino = tmp_path / "backups"
    resultado = servico_backup.fazer(destino)
    antigo = time.time() - (60 * 60 * 72)
    for arquivo in destino.iterdir():
        os.utime(arquivo, (antigo, antigo))

    saida = app.test_cli_runner().invoke(
        args=["verificar-backup", "--destino", str(destino), "--maximo-horas", "30"]
    )
    assert saida.exit_code != 0
    assert "parou de rodar" in saida.output


def test_comando_de_verificacao_passa_num_backup_recem_feito(app, banco_em_arquivo, tmp_path):
    destino = tmp_path / "backups"
    servico_backup.fazer(destino)

    saida = app.test_cli_runner().invoke(args=["verificar-backup", "--destino", str(destino)])

    assert saida.exit_code == 0
    assert "OK" in saida.output


# --------------------------------------------------------------------------- #
# Auditoria
# --------------------------------------------------------------------------- #


def test_login_e_tentativa_recusada_ficam_registrados(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-errada")
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    acoes = [registro.acao for registro in Auditoria.query.order_by(Auditoria.id).all()]
    assert acoes == [ACAO_LOGIN_FALHOU, ACAO_LOGIN]

    recusada = Auditoria.query.filter_by(acao=ACAO_LOGIN_FALHOU).one()
    # Quem tentou é desconhecido: o usuário digitado é ALVO, não autor.
    assert recusada.ator == "anônimo"
    assert recusada.alvo == "admin"


def test_cancelar_pedido_registra_quem_e_quanto(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    produto = Produto.query.filter_by(tenant_id=loja.id).first()
    pedido = criar_pedido(
        loja,
        {
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_RETIRADA,
            "pagamento": "Dinheiro",
            "carrinho": [{"produto_id": produto.id, "quantidade": 2}],
        },
    )

    client.post(
        f"/cozinha/pedidos/{pedido.id}/status",
        data={"status": STATUS_CANCELADO},
        base_url=BASE_A,
        follow_redirects=True,
    )

    registro = Auditoria.query.filter_by(acao=ACAO_PEDIDO_CANCELADO).one()
    assert registro.ator == "admin"
    assert registro.alvo == f"Pedido #{pedido.numero}"
    assert "60,00" in registro.detalhes


def test_alterar_a_chave_pix_e_registrado_sem_gravar_a_chave(client, loja):
    """O diário diz QUE mudou, não qual é — log é mais um lugar de onde vaza."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes/pix",
        data={"pix_chave": "chave-secreta@banco.com"},
        base_url=BASE_A,
    )

    registro = Auditoria.query.filter_by(acao=ACAO_PIX_ALTERADO).one()
    assert "chave-secreta@banco.com" not in (registro.detalhes or "")
    assert "cadastrada" in registro.detalhes


def test_registro_de_um_restaurante_nao_aparece_no_outro(client, loja, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    login_tenant(client, "tenant-b", "admin", "senha-b-123")

    do_a = servico_auditoria.do_tenant(two_tenants["tenant_a"])
    do_b = servico_auditoria.do_tenant(two_tenants["tenant_b"])

    assert len(do_a) == 1 and len(do_b) == 1
    assert do_a[0].tenant_id != do_b[0].tenant_id


def test_falha_ao_registrar_nao_derruba_a_operacao(loja, monkeypatch):
    """Não se desfaz um cancelamento porque o diário não coube."""
    monkeypatch.setattr(
        servico_auditoria.db.session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("banco travou")),
    )

    assert servico_auditoria.registrar(ACAO_LOGIN, tenant=loja) is None


def test_tela_de_atividade_e_so_de_quem_administra(client, loja, two_tenants):
    from app.models.usuario import Usuario

    atendente = Usuario(tenant_id=loja.id, nome="Ana", username="ana", role="atendente")
    atendente.set_password("senha-ana-123")
    db.session.add(atendente)
    db.session.commit()

    login_tenant(client, "tenant-a", "ana", "senha-ana-123")
    resposta = client.get("/admin/atividade", base_url=BASE_A, follow_redirects=False)

    # Vigilância entre colegas não ajuda a operar o restaurante.
    assert resposta.status_code in (302, 403)


# --------------------------------------------------------------------------- #
# Saúde
# --------------------------------------------------------------------------- #


def test_saude_responde_curto_para_quem_pergunta_de_fora(client, app):
    resposta = client.get("/saude", base_url="http://app.localhost")

    assert resposta.status_code in (200, 503)
    corpo = resposta.get_json()
    # Nada de contar qual é o banco, quanto disco resta ou quantos clientes há.
    assert set(corpo) == {"status"}


def test_banco_fora_do_ar_e_grave(app, monkeypatch):
    from app.services import saude

    monkeypatch.setattr(
        saude.db.session,
        "execute",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sem conexão")),
    )
    resultado = saude.checar()

    assert resultado["grave"] is True
    assert resultado["verificacoes"]["banco"]["ok"] is False


def test_disco_cheio_e_aviso_e_nao_derruba_a_resposta(app, monkeypatch):
    """Alarme que dispara por qualquer coisa é alarme que se aprende a ignorar."""
    import shutil

    from app.services import saude

    class Uso:
        total, used, free = 100, 99, 1

    monkeypatch.setattr(shutil, "disk_usage", lambda caminho: Uso())
    resultado = saude.checar()

    assert resultado["verificacoes"]["disco"]["ok"] is False
    assert "disco" in resultado["avisos"]
    assert resultado["grave"] is False


def test_uma_verificacao_que_estoura_nao_derruba_as_outras(app, monkeypatch):
    from app.services import saude

    monkeypatch.setitem(
        saude.VERIFICACOES,
        "disco",
        lambda: (_ for _ in ()).throw(RuntimeError("quebrou")),
    )
    resultado = saude.checar()

    assert resultado["verificacoes"]["disco"]["ok"] is False
    assert resultado["verificacoes"]["banco"]["ok"] is True
    # Uma checagem quebrada é um aviso, não motivo para dizer que o site caiu.
    assert resultado["grave"] is False


def test_banco_sem_migrations_e_aviso_e_nao_grave(app):
    """Banco de teste é criado por create_all: não é o mesmo que estar atrasado."""
    from app.services import saude

    resultado = saude._migrations()

    assert resultado["ok"] is False
    assert resultado.get("aviso") is True


def test_tela_da_plataforma_mostra_saude_e_diario(client, platform_admin, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
    )

    texto = client.get("/plataforma/operacao", base_url="http://app.localhost").get_data(
        as_text=True
    )

    assert "Diário de atividade" in texto
    assert "Entrou no sistema" in texto
    assert "/saude" in texto
