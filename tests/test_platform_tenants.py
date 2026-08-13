"""Edição de tenant e reset de senha pela área da plataforma.

O super-admin é quem atende o cliente que perdeu o acesso, então redefine senha
sem a antiga. Os testes que mais importam aqui são os de autorização (só o
super-admin entra) e de escopo (resetar o usuário do restaurante errado seria um
estrago silencioso).
"""

from __future__ import annotations

from app.extensions import db
from app.models.tenant import Tenant
from app.models.usuario import Usuario
from tests.conftest import login_tenant

BASE_PLATAFORMA = "http://app.localhost"


def _login_plataforma(client):
    return client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=False,
    )


def _criar_tenant(slug="loja", nome="Loja Teste", username="dono", senha="senha-antiga-123"):
    tenant = Tenant(slug=slug, nome_fantasia=nome, email_contato=f"{slug}@example.com", status="active")
    db.session.add(tenant)
    db.session.flush()
    usuario = Usuario(tenant_id=tenant.id, nome=f"Admin {nome}", username=username, role="admin")
    usuario.set_password(senha)
    db.session.add(usuario)
    db.session.commit()
    return tenant, usuario


def _editar(client, tenant, **campos):
    dados = {
        "slug": tenant.slug,
        "nome_fantasia": tenant.nome_fantasia,
        "email_contato": tenant.email_contato,
        "plano": tenant.plano,
        "status": tenant.status,
        "ativo": "on" if tenant.ativo else "",
    }
    dados.update(campos)
    return client.post(
        f"/plataforma/tenants/{tenant.id}/editar",
        data=dados,
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )


# --------------------------------------------------------------------------- #
# Autorização
# --------------------------------------------------------------------------- #


def test_editar_tenant_exige_super_admin(client, platform_admin):
    tenant, _ = _criar_tenant()
    resposta = client.get(
        f"/plataforma/tenants/{tenant.id}/editar", base_url=BASE_PLATAFORMA, follow_redirects=False
    )
    assert resposta.status_code in (302, 303)
    assert "/plataforma/login" in resposta.headers["Location"]


def test_admin_de_tenant_nao_acessa_a_area_da_plataforma(client, platform_admin, two_tenants):
    """Estar logado como dono de restaurante não dá acesso à plataforma."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get(
        f"/plataforma/tenants/{two_tenants['tenant_a']}/editar",
        base_url=BASE_PLATAFORMA,
        follow_redirects=False,
    )
    assert resposta.status_code in (302, 303)
    assert "/plataforma/login" in resposta.headers["Location"]


def test_resetar_senha_exige_super_admin(client, platform_admin):
    tenant, usuario = _criar_tenant()
    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/usuarios/{usuario.id}/senha",
        data={"nova_senha": "invadida-123", "repetir_senha": "invadida-123"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=False,
    )
    assert resposta.status_code in (302, 303)
    assert "/plataforma/login" in resposta.headers["Location"]
    assert usuario.check_password("senha-antiga-123"), "a senha não podia ter mudado"


def test_editar_tenant_inexistente_avisa(client, platform_admin):
    _login_plataforma(client)
    resposta = client.get(
        "/plataforma/tenants/9999/editar", base_url=BASE_PLATAFORMA, follow_redirects=True
    )
    assert "não encontrado" in resposta.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Edição dos dados
# --------------------------------------------------------------------------- #


def test_editar_dados_do_tenant(client, platform_admin):
    tenant, _ = _criar_tenant()
    _login_plataforma(client)

    _editar(
        client,
        tenant,
        nome_fantasia="Nome Novo",
        email_contato="novo@example.com",
        plano="pro",
        razao_social="Loja Teste LTDA",
        cnpj="12.345.678/0001-99",
        telefone_contato="81999990000",
    )

    db.session.refresh(tenant)
    assert tenant.nome_fantasia == "Nome Novo"
    assert tenant.email_contato == "novo@example.com"
    assert tenant.plano == "pro"
    assert tenant.razao_social == "Loja Teste LTDA"
    assert tenant.cnpj == "12.345.678/0001-99"


def test_plano_e_status_invalidos_sao_recusados(client, platform_admin):
    tenant, _ = _criar_tenant()
    _login_plataforma(client)

    _editar(client, tenant, plano="ilimitado-gratis")
    db.session.refresh(tenant)
    assert tenant.plano == "trial"

    _editar(client, tenant, status="inventado")
    db.session.refresh(tenant)
    assert tenant.status == "active"


def test_suspender_pela_plataforma_bloqueia_a_loja(client, platform_admin):
    """O status escolhido aqui tem efeito imediato na vitrine do restaurante."""
    tenant, _ = _criar_tenant(slug="loja")
    assert client.get("/", base_url="http://loja.localhost").status_code == 200

    _login_plataforma(client)
    _editar(client, tenant, status="suspended")

    resposta = client.get("/", base_url="http://loja.localhost")
    assert resposta.status_code == 402, "tenant suspenso precisa ser bloqueado"


def test_desativar_tenant_bloqueia_mesmo_com_assinatura_ativa(client, platform_admin):
    tenant, _ = _criar_tenant(slug="loja")
    _login_plataforma(client)

    _editar(client, tenant, ativo="")  # checkbox desmarcado

    db.session.refresh(tenant)
    assert tenant.ativo is False
    assert tenant.status == "active", "o kill-switch é independente da cobrança"
    assert client.get("/", base_url="http://loja.localhost").status_code == 402


# --------------------------------------------------------------------------- #
# Slug
# --------------------------------------------------------------------------- #


def test_mudar_o_slug_muda_o_endereco_da_loja(client, platform_admin):
    tenant, _ = _criar_tenant(slug="antiga")
    _login_plataforma(client)

    _editar(client, tenant, slug="nova")

    assert client.get("/", base_url="http://nova.localhost").status_code == 200
    assert client.get("/", base_url="http://antiga.localhost").status_code == 404


def test_slug_com_ponto_e_recusado(client, platform_admin):
    """Um ponto quebraria a identificação: o host é fatiado no primeiro ponto."""
    tenant, _ = _criar_tenant(slug="loja")
    _login_plataforma(client)

    resposta = _editar(client, tenant, slug="minha.loja")
    assert "Slug inválido" in resposta.get_data(as_text=True)
    db.session.refresh(tenant)
    assert tenant.slug == "loja"


def test_slug_com_caracteres_invalidos_e_recusado(client, platform_admin):
    tenant, _ = _criar_tenant(slug="loja")
    _login_plataforma(client)

    for invalido in ("minha loja", "loja_1", "-loja", "loja-", "Loja!", "aç"):
        _editar(client, tenant, slug=invalido)
        db.session.refresh(tenant)
        assert tenant.slug == "loja", f"o slug {invalido!r} não devia ser aceito"


def test_slug_reservado_e_recusado(client, platform_admin):
    """"app" é o host da própria plataforma: o tenant ficaria inacessível."""
    tenant, _ = _criar_tenant(slug="loja")
    _login_plataforma(client)

    for reservado in ("app", "www", "api", "admin"):
        resposta = _editar(client, tenant, slug=reservado)
        assert "reservado" in resposta.get_data(as_text=True)
        db.session.refresh(tenant)
        assert tenant.slug == "loja"


def test_slug_duplicado_e_recusado(client, platform_admin):
    primeiro, _ = _criar_tenant(slug="primeira", nome="Primeira", username="a")
    segundo, _ = _criar_tenant(slug="segunda", nome="Segunda", username="b")
    _login_plataforma(client)

    resposta = _editar(client, segundo, slug="primeira")
    assert "Já existe" in resposta.get_data(as_text=True)
    db.session.refresh(segundo)
    assert segundo.slug == "segunda"


def test_manter_o_proprio_slug_nao_acusa_duplicidade(client, platform_admin):
    """Salvar sem mexer no slug não pode colidir com o próprio tenant."""
    tenant, _ = _criar_tenant(slug="loja")
    _login_plataforma(client)

    resposta = _editar(client, tenant, nome_fantasia="Outro Nome")
    assert "Já existe" not in resposta.get_data(as_text=True)
    db.session.refresh(tenant)
    assert tenant.slug == "loja"
    assert tenant.nome_fantasia == "Outro Nome"


def test_criacao_tambem_valida_o_slug(client, platform_admin):
    """A validação faltava na criação; um slug com ponto entrava no banco."""
    _login_plataforma(client)

    resposta = client.post(
        "/plataforma/tenants/novo",
        data={
            "slug": "minha.loja",
            "nome_fantasia": "Loja",
            "email_contato": "l@example.com",
            "plano": "trial",
            "admin_username": "dono",
            "admin_password": "senha-123",
        },
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "Slug inválido" in resposta.get_data(as_text=True)
    assert Tenant.query.filter_by(slug="minha.loja").first() is None


def test_criacao_recusa_senha_curta(client, platform_admin):
    _login_plataforma(client)

    resposta = client.post(
        "/plataforma/tenants/novo",
        data={
            "slug": "loja",
            "nome_fantasia": "Loja",
            "email_contato": "l@example.com",
            "plano": "trial",
            "admin_username": "dono",
            "admin_password": "123",
        },
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "ao menos" in resposta.get_data(as_text=True)
    assert Tenant.query.filter_by(slug="loja").first() is None


# --------------------------------------------------------------------------- #
# Reset de senha
# --------------------------------------------------------------------------- #


def test_super_admin_redefine_senha_sem_a_antiga(client, platform_admin):
    tenant, usuario = _criar_tenant(slug="loja", username="dono")
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/usuarios/{usuario.id}/senha",
        data={"nova_senha": "senha-nova-123", "repetir_senha": "senha-nova-123"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "redefinida" in resposta.get_data(as_text=True)

    db.session.refresh(usuario)
    assert usuario.check_password("senha-nova-123")
    assert not usuario.check_password("senha-antiga-123")


def test_dono_entra_na_loja_com_a_senha_nova(client, platform_admin):
    """Prova de ponta a ponta: o reset precisa habilitar o login de verdade."""
    tenant, usuario = _criar_tenant(slug="loja", username="dono")
    _login_plataforma(client)
    client.post(
        f"/plataforma/tenants/{tenant.id}/usuarios/{usuario.id}/senha",
        data={"nova_senha": "senha-nova-123", "repetir_senha": "senha-nova-123"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    client.get("/plataforma/logout", base_url=BASE_PLATAFORMA)

    entrada = login_tenant(client, "loja", "dono", "senha-nova-123")
    assert entrada.status_code in (302, 303)
    assert "/admin" in entrada.headers["Location"]


def test_senhas_diferentes_nao_alteram_nada(client, platform_admin):
    """Um erro de digitação aqui trancaria o cliente fora do sistema."""
    tenant, usuario = _criar_tenant()
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/usuarios/{usuario.id}/senha",
        data={"nova_senha": "senha-nova-123", "repetir_senha": "senha-nova-124"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "não são iguais" in resposta.get_data(as_text=True)
    db.session.refresh(usuario)
    assert usuario.check_password("senha-antiga-123")


def test_senha_curta_e_recusada(client, platform_admin):
    tenant, usuario = _criar_tenant()
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/usuarios/{usuario.id}/senha",
        data={"nova_senha": "123", "repetir_senha": "123"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "ao menos" in resposta.get_data(as_text=True)
    db.session.refresh(usuario)
    assert usuario.check_password("senha-antiga-123")


def test_senha_fraca_avisa_mas_aplica(client, platform_admin):
    tenant, usuario = _criar_tenant()
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant.id}/usuarios/{usuario.id}/senha",
        data={"nova_senha": "123456", "repetir_senha": "123456"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    corpo = resposta.get_data(as_text=True)
    assert "menos de 8 caracteres" in corpo
    assert "só de números" in corpo
    db.session.refresh(usuario)
    assert usuario.check_password("123456"), "avisa, mas não impede"


def test_nao_reseta_usuario_de_outro_tenant(client, platform_admin):
    """O usuário é buscado com tenant_id no filtro.

    Sem isso, informar o id de outro tenant na URL trocaria a senha do usuário
    do restaurante errado.
    """
    _, usuario_a = _criar_tenant(slug="loja-a", nome="Loja A", username="dono", senha="senha-a-123")
    tenant_b, _ = _criar_tenant(slug="loja-b", nome="Loja B", username="outro", senha="senha-b-123")
    _login_plataforma(client)

    resposta = client.post(
        f"/plataforma/tenants/{tenant_b.id}/usuarios/{usuario_a.id}/senha",
        data={"nova_senha": "senha-invasora", "repetir_senha": "senha-invasora"},
        base_url=BASE_PLATAFORMA,
        follow_redirects=True,
    )
    assert "não encontrado neste tenant" in resposta.get_data(as_text=True)
    db.session.refresh(usuario_a)
    assert usuario_a.check_password("senha-a-123"), "o usuário do outro tenant foi alterado"


def test_tela_de_edicao_lista_os_usuarios_e_o_endereco(client, platform_admin):
    tenant, _ = _criar_tenant(slug="loja", username="dono")
    _login_plataforma(client)

    corpo = client.get(
        f"/plataforma/tenants/{tenant.id}/editar", base_url=BASE_PLATAFORMA
    ).get_data(as_text=True)

    assert "dono" in corpo
    assert "http://loja.localhost:5000/" in corpo
    assert "nova_senha" in corpo
    # A senha antiga não é pedida em nenhum lugar do formulário.
    assert "senha_antiga" not in corpo


def test_lista_de_tenants_leva_para_a_edicao(client, platform_admin):
    tenant, _ = _criar_tenant(slug="loja")
    _login_plataforma(client)

    corpo = client.get("/plataforma/tenants", base_url=BASE_PLATAFORMA).get_data(as_text=True)
    assert f"/plataforma/tenants/{tenant.id}/editar" in corpo
