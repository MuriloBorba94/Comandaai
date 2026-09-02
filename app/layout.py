"""Decisões de layout expostas aos templates.

Duas responsabilidades:

1. **Qual shell usar.** O painel (sidebar + commandbar) e a vitrine são
   estruturas diferentes, e a escolha é derivada do contexto da requisição —
   assim nenhuma das ~30 telas precisa declarar em qual mundo vive.

2. **A logo do tenant**, que é o que restou de identidade por restaurante.

A cor de marca morava aqui: uma coluna por tenant, validada como hex, com duas
variantes de contraste derivadas dela. Saiu inteira quando o tema Industry
virou padrão e passou a fixar `--brand` — o restaurante escolhia uma cor que
nenhuma tela ia mostrar. Com ela foram embora a coluna, a validação e o bloco
`<style>` do base.html; a cor do produto vive hoje só no CSS, que é o único
lugar onde ela tem efeito.
"""

from __future__ import annotations

from flask import g, request, session

# Blueprints que rodam dentro do painel do restaurante.
BLUEPRINTS_PAINEL_TENANT = {"admin", "operacao"}


def registrar(app) -> None:
    @app.template_global()
    def contexto_layout() -> str:
        """"admin", "plataforma" ou "vitrine"."""
        if g.get("is_platform_area") and session.get("platform_admin_id"):
            return "plataforma"
        tenant = g.get("tenant")
        dentro_do_painel = request.blueprint in BLUEPRINTS_PAINEL_TENANT
        logado_neste_tenant = (
            tenant is not None
            and session.get("logged_in")
            and session.get("tenant_id") == tenant.id
        )
        if dentro_do_painel and logado_neste_tenant:
            return "admin"
        return "vitrine"

    @app.template_global()
    def logo_do_tenant() -> str | None:
        """Caminho relativo da logo do tenant dentro de static/uploads."""
        tenant = g.get("tenant")
        caminho = getattr(tenant, "logo", None) if tenant is not None else None
        return caminho or None

    @app.template_global()
    def interessados_novos() -> int:
        """Contador do menu da plataforma. Zero fora da área da plataforma:
        o contato de venda não é da conta de nenhum restaurante."""
        if not session.get("platform_admin_id"):
            return 0
        from .services.interesses import quantos_novos

        return quantos_novos()

    @app.template_global()
    def menu_do_painel():
        """Seções e itens do menu, conforme quem está logado.

        Vive em `navegacao.py` como dado, e não como marcação, porque há dois
        lugares que o desenham — o painel de menu e o contador de cada item.
        Menu escrito duas vezes é menu que diverge.
        """
        from .navegacao import itens_da_plataforma, itens_do_menu

        # A plataforma vem primeiro: dentro dela também existe `g.tenant` quando
        # se está dando suporte a um restaurante, e checar o tenant antes daria
        # o menu errado justamente nesse caso.
        if session.get("platform_admin_id"):
            return itens_da_plataforma()

        from .services.recursos import tenant_libera

        tenant = g.get("tenant")
        if tenant is None:
            return []
        return itens_do_menu(tenant, lambda slug: tenant_libera(tenant, slug))

    @app.template_global()
    def contador_do_menu(nome: str) -> int:
        """Resolve o contador que o item do menu pediu, pelo nome.

        Os contadores já existem como globais de template — só não havia como
        chamá-los a partir de um dado. Nome desconhecido devolve 0 em vez de
        estourar: menu que derruba a página inteira por causa de um número ao
        lado de um item é troca ruim.
        """
        funcao = app.jinja_env.globals.get(nome)
        if not callable(funcao):
            return 0
        try:
            return int(funcao() or 0)
        except Exception:  # noqa: BLE001 - contador nunca derruba o menu
            return 0

    @app.template_global()
    def item_do_menu_ativo(item) -> bool:
        from .navegacao import item_ativo

        return item_ativo(item, request.endpoint or "")

    @app.template_global()
    def controles_do_turno():
        """Estado da loja, tempos e caixa — para a barra de comando.

        Ficam em toda página do painel, e não só no início, porque abrir e
        fechar a loja é a decisão mais urgente que existe ali: quem percebe às
        22h que o cardápio continua no ar não deveria ter de navegar até o
        painel inicial para desligá-lo.

        Memoizado em `g`: a barra é renderizada uma vez por página, mas a
        conferência é uma consulta agrupada sobre os pedidos, e um descuido de
        template que chamasse isto duas vezes cobraria o preço duas vezes.
        """
        if "controles_do_turno" in g:
            return g.controles_do_turno

        from .services import caixa as caixa_service
        from .services.pedidos import calcular_estimativa
        from .models.pedido import TIPO_ENTREGA, TIPO_RETIRADA

        tenant = g.get("tenant")
        if tenant is None or not session.get("logged_in"):
            g.controles_do_turno = None
            return None

        turno = caixa_service.caixa_aberto(tenant.id)
        dados = {
            "tenant": tenant,
            "caixa": turno,
            "resumo": caixa_service.resumo(turno) if turno else None,
            "entrega": calcular_estimativa(tenant, TIPO_ENTREGA, 0),
            "retirada": calcular_estimativa(tenant, TIPO_RETIRADA, 0),
            "rotulo": caixa_service.rotulo_da_faixa,
        }
        # As opções da gaveta vêm dos valores CADASTRADOS, não dos calculados:
        # `calcular_estimativa` soma a fila do momento, e a gaveta tem de
        # mostrar o que a pessoa escolheu, não o que a fila fez com aquilo.
        cadastrado_entrega = (tenant.tempo_estimado_min or 40, tenant.tempo_estimado_max or 60)
        cadastrado_retirada = (
            tenant.tempo_retirada_min or max(20, cadastrado_entrega[0] - 10),
            tenant.tempo_retirada_max or max(cadastrado_entrega[0] + 10, cadastrado_entrega[1] - 10),
        )
        dados["escolha_entrega"] = cadastrado_entrega
        dados["escolha_retirada"] = cadastrado_retirada
        dados["faixas_entrega"] = caixa_service.faixas_com_a_atual(*cadastrado_entrega)
        dados["faixas_retirada"] = caixa_service.faixas_com_a_atual(*cadastrado_retirada)

        g.controles_do_turno = dados
        return dados

    @app.template_global()
    def whatsapp_do_tenant() -> str | None:
        """Link que abre a conversa do restaurante no WhatsApp.

        O número em si não vai para a tela. Escrito por extenso ele é copiado
        por robô de spam antes de ser usado por cliente, e a pessoa que quer
        falar quer falar — não decorar dez dígitos. O botão leva direto à
        conversa, que é o que ela ia fazer com o número de qualquer jeito.
        """
        from .services.notificacoes.link import numero_internacional

        tenant = g.get("tenant")
        numero = numero_internacional(getattr(tenant, "telefone_contato", "") or "")
        return f"https://wa.me/{numero}" if numero else None

    @app.template_global()
    def inicial_do_tenant() -> str:
        """Primeira letra do nome, usada quando não há logo enviada."""
        tenant = g.get("tenant")
        nome = (getattr(tenant, "nome_fantasia", "") or "").strip() if tenant else ""
        return nome[0].upper() if nome else "•"
