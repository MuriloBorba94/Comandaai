"""O menu do painel, definido uma vez só.

Antes ele existia apenas como marcação em `_nav.html`: rótulo, ícone, rota e
regra de plano misturados no HTML. Isso funcionava enquanto havia um lugar para
desenhá-lo. Com o painel de menu (busca, colunas por categoria, favoritos e
histórico) passaram a ser dois, e menu escrito duas vezes é menu que diverge —
o item novo entra num lugar, some no outro, e ninguém percebe até um cliente
reclamar que "sumiu".

Aqui ele é dado. Quem desenha é o template; quem decide o que aparece é
`itens_do_menu()`, que já aplica o plano do tenant: recurso que o plano não
inclui não é ofertado. A rota também barra, mas oferecer para depois negar é
pior do que não oferecer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    rotulo: str
    endpoint: str
    icone: str
    # Recurso do plano que este item exige. None = todo mundo tem.
    recurso: str | None = None
    # Endpoints que também acendem este item (telas filhas, como a ficha
    # técnica de um produto, que pertence a "Produtos" mas tem rota própria).
    tambem: tuple[str, ...] = ()
    # Nome do contador a mostrar ao lado, quando houver o que contar.
    contador: str | None = None
    # Só aparece quando o restaurante atende mesa.
    exige_mesa: bool = False


@dataclass(frozen=True)
class Secao:
    titulo: str
    itens: tuple[Item, ...] = field(default_factory=tuple)


MENU: tuple[Secao, ...] = (
    Secao(
        "Visão geral",
        (
            Item("Monitor", "admin.dashboard", "⌁"),
            Item("Cozinha", "operacao.cozinha", "🔥", recurso="cozinha",
                 tambem=("operacao.pedido_status",)),
            Item("Vendas", "admin.relatorios", "▥", recurso="relatorios"),
            Item("Entregas", "entregas.lista", "🛵", tambem=("entregas.",)),
        ),
    ),
    Secao(
        "Atendimento",
        (
            Item("Mesas", "operacao.mesas", "▦", recurso="mesas",
                 tambem=("operacao.mesa",), exige_mesa=True),
        ),
    ),
    Secao(
        "Cardápio e campanhas",
        (
            Item("Produtos", "admin.produtos", "◫",
                 tambem=("admin.produto_editar", "admin.produto_ficha")),
            Item("Categorias", "admin.categorias", "▤"),
            Item("Adicionais", "admin.adicionais", "✦"),
            Item("Cupons", "admin.cupons", "◆", recurso="cupons"),
        ),
    ),
    Secao(
        "Operação",
        (
            Item("Bairros e taxas", "admin.bairros", "⌖", recurso="bairros"),
            Item("Estoque", "admin.insumos", "▣", recurso="estoque",
                 contador="insumos_para_repor"),
            Item("Custos", "admin.custos", "◒", recurso="custos",
                 tambem=("admin.produto_ficha",)),
        ),
    ),
    Secao(
        "Administração",
        (
            Item("Financeiro", "admin.financeiro", "▤", recurso="financeiro"),
            Item("Impressão", "admin.impressao", "⎙", recurso="impressao",
                 tambem=("admin.impressao",), contador="comandas_na_fila"),
            Item("Equipe", "admin.equipe", "◍", tambem=("admin.equipe",)),
            Item("Atividade", "admin.atividade", "◷"),
            Item("Loja e identidade", "admin.configuracoes", "◎"),
        ),
    ),
)


def item_ativo(item: Item, endpoint: str) -> bool:
    """Este item corresponde à tela aberta?

    `tambem` guarda prefixos, e não nomes exatos, porque telas filhas têm rota
    própria: quem está editando um produto continua "em Produtos", e o menu
    apagado ali seria o sistema dizendo que a pessoa não está onde está.
    """
    if endpoint == item.endpoint:
        return True
    return any(endpoint.startswith(prefixo) for prefixo in item.tambem)


def itens_do_menu(tenant, libera) -> list[tuple[str, list[Item]]]:
    """As seções e itens que ESTE restaurante pode ver.

    `libera` entra como argumento em vez de ser importado: assim esta função
    não sabe nada de request nem de sessão, e o teste consegue passar um plano
    fictício sem montar meia aplicação.

    Seção que fica sem item nenhum não é devolvida — um título sozinho no menu
    é uma promessa vazia.
    """
    visiveis: list[tuple[str, list[Item]]] = []
    atende_mesa = bool(getattr(tenant, "atende_mesa", False))

    for secao in MENU:
        itens = [
            item
            for item in secao.itens
            if (item.recurso is None or libera(item.recurso))
            and (not item.exige_mesa or atende_mesa)
        ]
        if itens:
            visiveis.append((secao.titulo, itens))
    return visiveis
