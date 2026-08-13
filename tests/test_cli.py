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
