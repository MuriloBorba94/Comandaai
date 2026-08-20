"""Comandos de terminal: descobrir usuários e redefinir senha de tenant.

A senha de um usuário existe apenas como hash, então quando o dono esquece a
única saída é redefinir. O teste que mais importa aqui é o de escopo: dois
tenants podem ter o mesmo username, e trocar a senha do usuário errado seria um
estrago silencioso.
"""

from __future__ import annotations

from app.extensions import db
from app.models.tenant import Tenant
from app.models.usuario import Usuario


def _usuario(tenant_id: int, username: str) -> Usuario:
    return Usuario.query.filter_by(tenant_id=tenant_id, username=username).one()


def test_listar_tenants_mostra_usuarios_e_endereco(app, two_tenants):
    resultado = app.test_cli_runner().invoke(args=["listar-tenants"])

    assert resultado.exit_code == 0
    saida = resultado.output
    assert "Restaurante A" in saida
    assert "Restaurante B" in saida
    assert "tenant-a.localhost" in saida
    assert "usuário: admin" in saida
    # Nenhum hash de senha pode aparecer na listagem.
    assert "scrypt" not in saida


def test_listar_tenants_sem_nenhum_cadastrado(app):
    resultado = app.test_cli_runner().invoke(args=["listar-tenants"])
    assert resultado.exit_code == 0
    assert "Nenhum tenant cadastrado" in resultado.output


def test_definir_senha_troca_a_senha(app, two_tenants):
    resultado = app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "admin"],
        input="senha-nova-123\nsenha-nova-123\n",
    )

    assert resultado.exit_code == 0, resultado.output
    assert "atualizada" in resultado.output

    usuario = _usuario(two_tenants["tenant_a"], "admin")
    assert usuario.check_password("senha-nova-123")
    assert not usuario.check_password("senha-a-123"), "a senha antiga não pode continuar valendo"


def test_definir_senha_nao_afeta_o_mesmo_username_em_outro_tenant(app, two_tenants):
    """Os dois tenants têm um usuário 'admin'. Só o do tenant indicado muda."""
    app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "admin"],
        input="senha-nova-123\nsenha-nova-123\n",
    )

    usuario_b = _usuario(two_tenants["tenant_b"], "admin")
    assert usuario_b.check_password("senha-b-123"), "o usuário do outro tenant foi alterado"
    assert not usuario_b.check_password("senha-nova-123")


def test_definir_senha_com_tenant_inexistente_lista_os_validos(app, two_tenants):
    resultado = app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "nao-existe", "--usuario", "admin"],
        input="qualquer\nqualquer\n",
    )

    assert resultado.exit_code == 1
    assert "não existe" in resultado.output
    assert "tenant-a" in resultado.output and "tenant-b" in resultado.output


def test_definir_senha_com_usuario_inexistente_lista_os_do_tenant(app, two_tenants):
    resultado = app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "ninguem"],
        input="qualquer\nqualquer\n",
    )

    assert resultado.exit_code == 1
    assert "não tem usuário 'ninguem'" in resultado.output
    assert "admin" in resultado.output


def test_definir_senha_exige_confirmacao_igual(app, two_tenants):
    resultado = app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "admin"],
        input="uma-senha\noutra-senha\numa-senha\numa-senha\n",
    )

    # O click repete a pergunta quando a confirmação não bate.
    assert "não" in resultado.output.lower() or "match" in resultado.output.lower()
    assert _usuario(two_tenants["tenant_a"], "admin").check_password("uma-senha")


def test_definir_senha_avisa_quando_a_senha_e_fraca(app, two_tenants):
    resultado = app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "admin"],
        input="6235124\n6235124\n",
    )

    assert resultado.exit_code == 0
    assert "menos de 8 caracteres" in resultado.output
    assert "só com números" in resultado.output
    # Avisa, mas não impede: a escolha é de quem administra.
    assert _usuario(two_tenants["tenant_a"], "admin").check_password("6235124")


def test_definir_senha_recusa_senha_vazia(app, two_tenants):
    resultado = app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "admin"],
        input="   \n   \n",
    )

    assert resultado.exit_code == 1
    assert "vazia" in resultado.output
    assert _usuario(two_tenants["tenant_a"], "admin").check_password("senha-a-123")


def test_senha_continua_como_hash_no_banco(app, two_tenants):
    """Confirma o motivo de não existir "recuperar senha": o banco só tem hash."""
    app.test_cli_runner().invoke(
        args=["definir-senha", "--tenant", "tenant-a", "--usuario", "admin"],
        input="senha-nova-123\nsenha-nova-123\n",
    )
    db.session.expire_all()

    guardado = _usuario(two_tenants["tenant_a"], "admin").senha
    assert "senha-nova-123" not in guardado
    assert guardado.startswith("scrypt:")


def test_saida_dos_comandos_sobrevive_a_console_windows(app):
    """Nenhum texto impresso pelos comandos pode quebrar em console cp1252.

    O `flask seed-planos` já quebrou depois de gravar no banco por causa de uma
    seta "→": o trabalho foi feito, mas o comando terminou em erro.
    """
    runner = app.test_cli_runner()
    for args in (["listar-tenants"], ["seed-planos"], ["ciclo-cobranca", "--simular"]):
        resultado = runner.invoke(args=args)
        assert resultado.exit_code == 0, f"{args} falhou: {resultado.output}"
        # encode() levanta UnicodeEncodeError se houver caractere fora do cp1252.
        resultado.output.encode("cp1252")


def test_seed_planos_nao_duplica(app):
    runner = app.test_cli_runner()
    primeiro = runner.invoke(args=["seed-planos"])
    assert "Planos criados" in primeiro.output

    segundo = runner.invoke(args=["seed-planos"])
    assert "já tem planos" in segundo.output

    from app.models.assinatura import Plano

    assert Plano.query.count() == 3


def test_ciclo_simulado_nao_grava(app, two_tenants):
    from app.models.assinatura import Cobranca

    runner = app.test_cli_runner()
    runner.invoke(args=["seed-planos"])
    resultado = runner.invoke(args=["ciclo-cobranca", "--simular"])

    assert resultado.exit_code == 0
    assert "nada será gravado" in resultado.output
    assert Cobranca.query.count() == 0


# --------------------------------------------------------------------------- #
# Remover tenant
#
# Existe para a migração poder ser refeita. Como não tem desfazer, o que precisa
# ser provado é que ele apaga TUDO do restaurante certo e NADA do vizinho.
# --------------------------------------------------------------------------- #


def _com_dados(tenant_id: int) -> None:
    """Dá ao tenant um cardápio e um pedido, para o delete ter o que arrastar."""
    from app.models.pedido import TIPO_RETIRADA
    from app.models.produto import Produto
    from app.services.pedidos import criar_pedido

    produto = Produto.query.filter_by(tenant_id=tenant_id).first()
    if produto is None:
        produto = Produto(tenant_id=tenant_id, nome="X-Tudo", preco=25.0, disponivel=True)
        db.session.add(produto)
        db.session.commit()

    criar_pedido(
        db.session.get(Tenant, tenant_id),
        {
            "cliente": "Maria", "telefone": "81999998888", "tipo": TIPO_RETIRADA,
            "pagamento": "Dinheiro",
            "carrinho": [{"produto_id": produto.id, "quantidade": 2}],
        },
    )


def test_remover_tenant_apaga_tudo_dele(app, two_tenants):
    from app.models.pedido import Pedido
    from app.models.produto import Produto

    alvo = two_tenants["tenant_a"]
    _com_dados(alvo)
    assert Pedido.query.filter_by(tenant_id=alvo).count() == 1

    resultado = app.test_cli_runner().invoke(args=["remover-tenant", "--slug", "tenant-a", "--sim"])

    assert resultado.exit_code == 0, resultado.output
    assert Tenant.query.filter_by(slug="tenant-a").first() is None
    assert Produto.query.filter_by(tenant_id=alvo).count() == 0
    assert Pedido.query.filter_by(tenant_id=alvo).count() == 0
    assert Usuario.query.filter_by(tenant_id=alvo).count() == 0


def test_remover_tenant_nao_encosta_no_vizinho(app, two_tenants):
    """A garantia de sempre, e aqui ela vale mais: delete não tem desfazer."""
    from app.models.pedido import Pedido
    from app.models.produto import Produto

    vizinho = two_tenants["tenant_b"]
    _com_dados(two_tenants["tenant_a"])
    _com_dados(vizinho)

    app.test_cli_runner().invoke(args=["remover-tenant", "--slug", "tenant-a", "--sim"])

    assert Tenant.query.filter_by(slug="tenant-b").first() is not None
    assert Produto.query.filter_by(tenant_id=vizinho).count() == 1
    assert Pedido.query.filter_by(tenant_id=vizinho).count() == 1
    assert Usuario.query.filter_by(tenant_id=vizinho).count() == 1


def test_remover_tenant_exige_digitar_o_slug(app, two_tenants):
    """Confirmação de uma tecla é o que faz alguém apagar o tenant errado."""
    resultado = app.test_cli_runner().invoke(
        args=["remover-tenant", "--slug", "tenant-a"], input="sim\n"
    )

    assert resultado.exit_code == 1
    assert "Nao confere" in resultado.output
    assert Tenant.query.filter_by(slug="tenant-a").first() is not None


def test_remover_tenant_avisa_quando_ha_venda(app, two_tenants):
    _com_dados(two_tenants["tenant_a"])

    resultado = app.test_cli_runner().invoke(
        args=["remover-tenant", "--slug", "tenant-a"], input="nao-vou-confirmar\n"
    )

    assert "ATENCAO" in resultado.output
    assert "1 pedido(s)" in resultado.output


def test_remover_tenant_inexistente_lista_os_que_existem(app, two_tenants):
    resultado = app.test_cli_runner().invoke(args=["remover-tenant", "--slug", "nao-existe", "--sim"])

    assert resultado.exit_code == 1
    assert "tenant-a" in resultado.output


def test_remover_tenant_preserva_as_fotos_por_padrao(app, two_tenants, tmp_path):
    """Apagar imagem é irreversível e não é o que se quer ao refazer import."""
    from pathlib import Path

    pasta = Path(app.config["UPLOAD_FOLDER"]) / "tenant-a"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "foto.webp").write_bytes(b"x")

    app.test_cli_runner().invoke(args=["remover-tenant", "--slug", "tenant-a", "--sim"])

    assert (pasta / "foto.webp").is_file()


def test_remover_tenant_com_apagar_fotos_limpa_a_pasta(app, two_tenants):
    from pathlib import Path

    pasta = Path(app.config["UPLOAD_FOLDER"]) / "tenant-a"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "foto.webp").write_bytes(b"x")

    app.test_cli_runner().invoke(
        args=["remover-tenant", "--slug", "tenant-a", "--sim", "--apagar-fotos"]
    )

    assert not pasta.exists()
