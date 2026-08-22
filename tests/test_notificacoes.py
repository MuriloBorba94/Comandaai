"""Fase 7 — avisar o cliente pelo WhatsApp.

O que mais importa aqui:

1. **O aviso nunca derruba o pedido.** Uma API de fora fora do ar não pode
   impedir a cozinha de mudar o status de um pedido.
2. **O cliente não recebe mensagem errada.** Nada de "seu pedido está pronto"
   chegando depois de "foi cancelado", nem o mesmo aviso duas vezes.
3. **Um restaurante não avisa pelo número do outro.** Credencial, texto e fila
   são todos por tenant.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.notificacao import (
    EVENTO_CANCELADO,
    EVENTO_CONFIRMADO,
    EVENTO_EM_PREPARO,
    EVENTO_PRONTO,
    STATUS_AGUARDANDO_ENVIO,
    STATUS_CANCELADA,
    STATUS_ENVIADA,
    STATUS_ERRO,
    STATUS_PENDENTE,
    Notificacao,
)
from app.models.pedido import (
    STATUS_CANCELADO as PEDIDO_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_PRONTO,
    TIPO_ENTREGA,
    TIPO_MESA,
    TIPO_RETIRADA,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services import notificacoes
from app.services.notificacoes import link, textos
from app.services.notificacoes.base import Envio
from app.services.pedidos import criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


@pytest.fixture()
def loja(app, two_tenants):
    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant_a.qtd_mesas = 4
    xtudo = Produto(tenant_id=tenant_a.id, nome="X-Tudo", preco=30.0)
    pizza = Produto(tenant_id=tenant_b.id, nome="Pizza", preco=40.0)
    db.session.add_all([xtudo, pizza])
    db.session.commit()
    return {"tenant_a": tenant_a, "tenant_b": tenant_b, "xtudo": xtudo.id, "pizza": pizza.id}


def _pedido(tenant, produto_id, **extra):
    dados = {
        "cliente": "Maria Silva",
        "telefone": "81999998888",
        "tipo": TIPO_RETIRADA,
        "pagamento": "Dinheiro",
        "carrinho": [{"produto_id": produto_id, "quantidade": 2}],
    }
    dados.update(extra)
    # Comanda de mesa só nasce autorizada, como nas rotas do salão.
    return criar_pedido(tenant, dados, permitir_mesa=dados["tipo"] == TIPO_MESA)


def _com_meta(tenant):
    """Liga o envio automático neste restaurante."""
    tenant.whatsapp_provedor = "meta"
    tenant.whatsapp_phone_id = "123456789012345"
    tenant.whatsapp_token = "token-de-mentira"
    db.session.commit()
    return tenant


# --------------------------------------------------------------------------- #
# O texto que o cliente lê
# --------------------------------------------------------------------------- #


def test_mensagem_traz_o_nome_do_restaurante_e_o_link_do_pedido(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    texto = textos.montar(pedido, EVENTO_CONFIRMADO)

    assert "Restaurante A" in texto
    assert f"#{pedido.numero}" in texto
    assert pedido.public_token in texto
    assert "R$ 60,00" in texto
    # Primeiro nome, que é como se fala com alguém no WhatsApp.
    assert "Maria" in texto and "Maria Silva" not in texto


def test_numero_do_aviso_e_o_do_restaurante_nao_o_global(loja):
    """O cliente recebe "#1", que é o que ele vê na tela — não o id do banco."""
    _pedido(loja["tenant_b"], loja["pizza"])  # consome um id global
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    assert pedido.numero == 1
    assert pedido.id != 1
    assert "#1" in textos.montar(pedido, EVENTO_CONFIRMADO)


def test_nome_em_branco_nao_quebra_a_mensagem(loja):
    """No original, `cliente.split()[0]` estourava com nome vazio."""
    assert textos.primeiro_nome("") == ""
    assert textos.primeiro_nome("   ") == ""
    assert textos.primeiro_nome("Ana Paula") == "Ana"

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    pedido.cliente = "   "
    db.session.commit()
    texto = textos.montar(pedido, EVENTO_CONFIRMADO)
    assert "Pedido" in texto
    assert ", ," not in texto


def test_telefone_ganha_o_codigo_do_pais(loja):
    """Sem o 55, o wa.me abre conversa com um número que não existe."""
    assert link.numero_internacional("81999998888") == "5581999998888"
    assert link.numero_internacional("(81) 99999-8888") == "5581999998888"
    assert link.numero_internacional("5581999998888") == "5581999998888"
    assert link.numero_internacional("123") == "123"


# --------------------------------------------------------------------------- #
# Quando o aviso nasce
# --------------------------------------------------------------------------- #


def test_confirmar_o_pedido_prepara_o_aviso_para_alguem_clicar(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    aviso = Notificacao.query.one()
    assert aviso.evento == EVENTO_CONFIRMADO
    assert aviso.status == STATUS_AGUARDANDO_ENVIO
    assert aviso.provedor == "link"


def test_no_modo_link_as_etapas_do_meio_nao_viram_clique(loja):
    """Três cliques por pedido no sábado à noite ninguém faz."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)

    eventos = {aviso.evento for aviso in Notificacao.query.all()}
    assert eventos == {EVENTO_CONFIRMADO}


def test_cancelar_avisa_mesmo_no_modo_link(loja):
    """Cancelar o jantar de alguém sem avisar é pior do que um clique a mais."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, PEDIDO_CANCELADO)

    aviso = Notificacao.query.filter_by(evento=EVENTO_CANCELADO).one()
    assert aviso.status == STATUS_AGUARDANDO_ENVIO


def test_com_envio_automatico_todas_as_etapas_avisam(loja, monkeypatch):
    _com_meta(loja["tenant_a"])
    monkeypatch.setattr(
        "app.services.notificacoes.meta.MetaCloud.enviar",
        lambda self, n: Envio(True, id_externo="wamid.1"),
    )

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)

    eventos = {aviso.evento for aviso in Notificacao.query.all()}
    assert eventos == {EVENTO_CONFIRMADO, EVENTO_EM_PREPARO, EVENTO_PRONTO}
    assert all(aviso.status == STATUS_ENVIADA for aviso in Notificacao.query.all())


def test_pedido_de_mesa_nao_gera_aviso(loja):
    """Não tem telefone, e o cliente está sentado ali."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"], tipo=TIPO_MESA, mesa=1, telefone="")
    transicionar(pedido, STATUS_CONFIRMADO)

    assert Notificacao.query.count() == 0


def test_o_mesmo_evento_nao_avisa_duas_vezes(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    # Enfileirar de novo o mesmo evento devolve o que já existe.
    notificacoes.enfileirar(pedido, EVENTO_CONFIRMADO)

    assert Notificacao.query.filter_by(evento=EVENTO_CONFIRMADO).count() == 1


def test_cancelar_derruba_o_que_ainda_nao_saiu(loja, monkeypatch):
    """"Seu pedido está pronto" não pode chegar depois de "foi cancelado"."""
    _com_meta(loja["tenant_a"])
    # A Meta está fora do ar: o aviso de "em preparo" fica na fila.
    monkeypatch.setattr(
        "app.services.notificacoes.meta.MetaCloud.enviar",
        lambda self, n: Envio(False, erro="timeout"),
    )
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    assert Notificacao.query.filter_by(status=STATUS_ERRO).count() == 2

    transicionar(pedido, PEDIDO_CANCELADO)

    parados = Notificacao.query.filter(Notificacao.evento != EVENTO_CANCELADO).all()
    assert all(aviso.status == STATUS_CANCELADA for aviso in parados)
    # O aviso do cancelamento em si é novo e continua valendo.
    assert Notificacao.query.filter_by(evento=EVENTO_CANCELADO).one().status != STATUS_CANCELADA


def test_plano_sem_whatsapp_nao_avisa(loja):
    from app.models.assinatura import Plano

    db.session.add(Plano(slug="basico", nome="Básico", recursos="cozinha"))
    loja["tenant_a"].plano = "basico"
    db.session.commit()

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert Notificacao.query.count() == 0


# --------------------------------------------------------------------------- #
# O aviso nunca derruba o pedido
# --------------------------------------------------------------------------- #


def test_api_fora_do_ar_nao_impede_o_pedido_de_avancar(loja, monkeypatch):
    _com_meta(loja["tenant_a"])
    monkeypatch.setattr(
        "app.services.notificacoes.meta.MetaCloud.enviar",
        lambda self, n: (_ for _ in ()).throw(RuntimeError("a rede caiu")),
    )

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    # O que importa: a cozinha conseguiu confirmar o pedido.
    assert pedido.status == STATUS_CONFIRMADO


def test_erro_ao_montar_o_texto_nao_impede_o_pedido_de_avancar(loja, monkeypatch):
    monkeypatch.setattr(
        "app.services.notificacoes.montar",
        lambda pedido, evento: (_ for _ in ()).throw(RuntimeError("texto quebrou")),
    )

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert pedido.status == STATUS_CONFIRMADO
    assert Notificacao.query.count() == 0


def test_aviso_que_falha_fica_na_fila_e_o_comando_reenvia(loja, monkeypatch):
    _com_meta(loja["tenant_a"])
    tentativas = {"n": 0}

    def instavel(self, notificacao):
        tentativas["n"] += 1
        if tentativas["n"] == 1:
            return Envio(False, erro="a Meta demorou")
        return Envio(True, id_externo="wamid.2")

    monkeypatch.setattr("app.services.notificacoes.meta.MetaCloud.enviar", instavel)

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    assert Notificacao.query.one().status == STATUS_ERRO

    resultado = notificacoes.despachar_pendentes()

    assert resultado["enviadas"] == 1
    assert Notificacao.query.one().status == STATUS_ENVIADA


def test_aviso_desiste_depois_de_muitas_falhas(loja, monkeypatch):
    """Número inválido não pode fazer o disparador girar para sempre."""
    from app.models.notificacao import MAX_TENTATIVAS

    _com_meta(loja["tenant_a"])
    monkeypatch.setattr(
        "app.services.notificacoes.meta.MetaCloud.enviar",
        lambda self, n: Envio(False, erro="número inválido"),
    )

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    for _ in range(MAX_TENTATIVAS + 3):
        notificacoes.despachar_pendentes()

    assert Notificacao.query.one().tentativas == MAX_TENTATIVAS


def test_aviso_esperando_clique_nao_gasta_tentativa(loja):
    """Ele nunca foi oferecido a ninguém; contar falha seria mentira."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    aviso = Notificacao.query.one()

    notificacoes.despachar(aviso)
    notificacoes.despachar(aviso)

    assert aviso.tentativas == 0
    assert aviso.status == STATUS_AGUARDANDO_ENVIO


# --------------------------------------------------------------------------- #
# Escolha do provedor
# --------------------------------------------------------------------------- #


def test_sem_configurar_nada_o_provedor_e_o_link(loja):
    assert notificacoes.provedor_do_tenant(loja["tenant_a"]).slug == "link"


def test_meta_sem_credencial_cai_para_o_link_em_vez_de_falhar(loja):
    """Aviso que não sai é pior do que aviso que exige um clique."""
    loja["tenant_a"].whatsapp_provedor = "meta"
    db.session.commit()

    assert notificacoes.provedor_do_tenant(loja["tenant_a"]).slug == "link"
    assert notificacoes.caiu_para_o_link(loja["tenant_a"]) is True


def test_meta_configurada_passa_a_atender(loja):
    _com_meta(loja["tenant_a"])

    assert notificacoes.provedor_do_tenant(loja["tenant_a"]).slug == "meta"
    assert notificacoes.caiu_para_o_link(loja["tenant_a"]) is False


def test_modelos_aprovados_sao_lidos_por_evento(loja):
    from app.services.notificacoes.meta import modelo_do_evento, modelos_do_tenant

    loja["tenant_a"].whatsapp_modelos = "confirmado=pedido_ok\npronto = pedido_pronto ,lixo"
    db.session.commit()

    assert modelos_do_tenant(loja["tenant_a"]) == {
        "confirmado": "pedido_ok",
        "pronto": "pedido_pronto",
    }
    assert modelo_do_evento(loja["tenant_a"], "confirmado") == "pedido_ok"
    assert modelo_do_evento(loja["tenant_a"], "entregue") == ""


def test_meta_envia_modelo_quando_ha_um_e_texto_quando_nao_ha(loja, monkeypatch):
    """Fora da janela de 24h a Meta só aceita modelo aprovado."""
    from app.services.notificacoes.meta import MetaCloud

    _com_meta(loja["tenant_a"])
    loja["tenant_a"].whatsapp_modelos = "confirmado=pedido_ok"
    db.session.commit()

    enviados = []
    monkeypatch.setattr(
        MetaCloud, "_post", lambda self, tenant, corpo: enviados.append(corpo) or Envio(True)
    )

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)

    assert enviados[0]["type"] == "template"
    assert enviados[0]["template"]["name"] == "pedido_ok"
    assert enviados[0]["template"]["language"]["code"] == "pt_BR"
    # Sem modelo para "em preparo", tenta texto livre — e o erro da Meta, se
    # vier, é o que diz à pessoa o que falta aprovar.
    assert enviados[1]["type"] == "text"


def test_chamada_http_para_a_meta_sai_como_ela_espera(loja, monkeypatch):
    """Fala com um servidor local que responde como a Graph API.

    Isto é o máximo que dá para provar sem uma conta na Meta, e prova o que é
    meu: o cabeçalho de autorização, o corpo em JSON, a leitura do id da
    mensagem. O que fica sem verificação é o comportamento da Meta em si — e
    isso está dito em voz alta em vez de escondido atrás de um teste que finge
    cobrir.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.services.notificacoes import meta as meta_mod

    recebido = {}

    class Falsa(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = self.rfile.read(int(self.headers["Content-Length"]))
            recebido["caminho"] = self.path
            recebido["autorizacao"] = self.headers.get("Authorization")
            recebido["tipo"] = self.headers.get("Content-Type")
            recebido["corpo"] = _json.loads(corpo)
            resposta = _json.dumps({"messages": [{"id": "wamid.TESTE"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resposta)))
            self.end_headers()
            self.wfile.write(resposta)

        def log_message(self, *args):  # silencia o log do servidor no pytest
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Falsa)
    threading.Thread(target=servidor.handle_request, daemon=True).start()
    monkeypatch.setattr(meta_mod, "BASE_API", f"http://127.0.0.1:{servidor.server_port}")

    _com_meta(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    servidor.server_close()

    assert recebido["caminho"] == f"/{meta_mod.VERSAO_API}/123456789012345/messages"
    assert recebido["autorizacao"] == "Bearer token-de-mentira"
    assert recebido["tipo"] == "application/json"
    assert recebido["corpo"]["messaging_product"] == "whatsapp"
    assert recebido["corpo"]["to"] == "5581999998888"
    assert "Pedido #1 confirmado" in recebido["corpo"]["text"]["body"]

    aviso = Notificacao.query.one()
    assert aviso.status == STATUS_ENVIADA
    assert aviso.id_externo == "wamid.TESTE"


def test_erro_da_meta_chega_inteiro_ate_o_painel(loja, monkeypatch):
    """A explicação dela diz o que fazer; trocá-la por "erro ao enviar" não."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.services.notificacoes import meta as meta_mod

    class Recusa(BaseHTTPRequestHandler):
        def do_POST(self):
            # Ler o corpo ANTES de responder não é formalidade: responder e
            # fechar com dados ainda no buffer faz o Windows abortar a conexão,
            # e o cliente recebe WinError 10053 em vez da resposta. Foi o que
            # deixou este teste instável.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            corpo = _json.dumps(
                {"error": {"message": "Template name does not exist in the translation"}}
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args):
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Recusa)
    threading.Thread(target=servidor.handle_request, daemon=True).start()
    monkeypatch.setattr(meta_mod, "BASE_API", f"http://127.0.0.1:{servidor.server_port}")

    _com_meta(loja["tenant_a"])
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, STATUS_CONFIRMADO)
    servidor.server_close()

    aviso = Notificacao.query.one()
    assert aviso.status == STATUS_ERRO
    assert "Template name does not exist" in aviso.erro


# --------------------------------------------------------------------------- #
# Telas
# --------------------------------------------------------------------------- #


def test_botao_da_cozinha_abre_o_whatsapp_e_marca_como_enviado(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    pedido = _pedido(loja["tenant_a"], loja["xtudo"], tipo=TIPO_ENTREGA, endereco="Rua A, 100")
    transicionar(pedido, STATUS_CONFIRMADO)
    aviso = Notificacao.query.one()

    resposta = client.post(
        f"/cozinha/pedidos/{pedido.id}/whatsapp/{aviso.id}", base_url=BASE_A
    )

    assert resposta.status_code == 302
    destino = resposta.headers["Location"]
    assert destino.startswith("https://wa.me/5581999998888?text=")
    assert aviso.status == STATUS_ENVIADA
    assert aviso.enviada_por == "admin"


def test_cozinha_nao_avisa_pelo_pedido_de_outro_restaurante(client, loja):
    pedido_b = _pedido(loja["tenant_b"], loja["pizza"])
    transicionar(pedido_b, STATUS_CONFIRMADO)
    aviso_b = Notificacao.query.one()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(
        f"/cozinha/pedidos/{pedido_b.id}/whatsapp/{aviso_b.id}",
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert aviso_b.status == STATUS_AGUARDANDO_ENVIO


def test_painel_salva_a_configuracao_sem_devolver_o_token_para_a_tela(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes/whatsapp",
        data={
            "whatsapp_provedor": "meta",
            "whatsapp_phone_id": "123456789012345",
            "whatsapp_token": "segredo-super-secreto",
            "whatsapp_modelos": "confirmado=pedido_ok",
        },
        base_url=BASE_A,
    )
    assert loja["tenant_a"].whatsapp_token == "segredo-super-secreto"

    texto = client.get("/admin/configuracoes", base_url=BASE_A).get_data(as_text=True)
    # Bastaria "ver código-fonte" para ler o token se ele voltasse para a tela.
    assert "segredo-super-secreto" not in texto
    assert "secreto"[-6:] in texto  # só o final, para reconhecer que há um gravado


def test_salvar_sem_digitar_o_token_mantem_o_que_ja_estava(client, loja):
    _com_meta(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes/whatsapp",
        data={"whatsapp_provedor": "meta", "whatsapp_phone_id": "999", "whatsapp_token": ""},
        base_url=BASE_A,
    )

    assert loja["tenant_a"].whatsapp_token == "token-de-mentira"
    assert loja["tenant_a"].whatsapp_phone_id == "999"


def test_token_de_um_restaurante_nao_e_editavel_pelo_outro(client, loja):
    _com_meta(loja["tenant_b"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes/whatsapp",
        data={"whatsapp_provedor": "meta", "whatsapp_token": "invadido"},
        base_url=BASE_A,
    )

    assert loja["tenant_b"].whatsapp_token == "token-de-mentira"


def test_tela_avisa_quando_o_automatico_nao_esta_de_pe(client, loja):
    loja["tenant_a"].whatsapp_provedor = "meta"
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    texto = client.get("/admin/configuracoes", base_url=BASE_A).get_data(as_text=True)
    assert "ainda não está de pé" in texto


# --------------------------------------------------------------------------- #
# Plano que libera tudo
# --------------------------------------------------------------------------- #


def test_plano_marcado_como_completo_ganha_recurso_novo_sozinho():
    """A razão de o `libera_tudo` existir: não reescrever plano a cada fase."""
    from app.models.assinatura import RECURSOS_SLUGS, Plano

    plano = Plano(slug="completo", nome="Completo")
    plano.definir_recursos(["cozinha"], tudo=True)

    assert plano.recursos_liberados == set(RECURSOS_SLUGS)
    # A lista marcada continua gravada: desmarcar "tudo" volta ao que estava,
    # em vez de zerar o plano.
    assert plano.recursos == "cozinha"

    plano.definir_recursos(["cozinha"], tudo=False)
    assert plano.recursos_liberados == {"cozinha"}
