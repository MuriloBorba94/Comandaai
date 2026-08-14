"""Decisões de layout expostas aos templates.

Duas responsabilidades:

1. **Qual shell usar.** O painel (sidebar + commandbar) e a vitrine (topbar
   escura) são estruturas diferentes, e a escolha é derivada do contexto da
   requisição — assim nenhuma das ~30 telas precisa declarar em qual mundo vive.

2. **A identidade do tenant.** Cor de marca e logo saem daqui já validadas. A
   cor entra num bloco `<style>`, então precisa ser validada como cor de fato:
   texto arbitrário ali seria injeção de CSS.
"""

from __future__ import annotations

import colorsys
import re

from flask import g, request, session

# Vermelho do painel do sistema original, e também o do Comanda ai.
COR_PADRAO = "#c8102e"

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Blueprints que rodam dentro do painel do restaurante.
BLUEPRINTS_PAINEL_TENANT = {"admin", "operacao"}


def cor_valida(valor: str | None) -> str | None:
    """Devolve a cor se for um hex de 3 ou 6 dígitos; senão None.

    É a única porta pela qual uma cor escolhida pelo usuário chega ao CSS.
    """
    texto = (valor or "").strip()
    return texto.lower() if _HEX.match(texto) else None


def _hex_para_rgb(cor: str) -> tuple[int, int, int]:
    corpo = cor.lstrip("#")
    if len(corpo) == 3:
        corpo = "".join(ch * 2 for ch in corpo)
    return int(corpo[0:2], 16), int(corpo[2:4], 16), int(corpo[4:6], 16)


def _luminancia(cor: str) -> float:
    """Luminância relativa da WCAG, de 0 (preto) a 1 (branco)."""

    def canal(valor: int) -> float:
        proporcao = valor / 255
        return proporcao / 12.92 if proporcao <= 0.04045 else ((proporcao + 0.055) / 1.055) ** 2.4

    r, g_, b = (canal(v) for v in _hex_para_rgb(cor))
    return 0.2126 * r + 0.7152 * g_ + 0.0722 * b


def _com_luminosidade(cor: str, luz: float) -> str:
    """Mesma cor, com a luminosidade HSL trocada. Preserva matiz e saturação."""
    r, g_, b = (v / 255 for v in _hex_para_rgb(cor))
    matiz, _, saturacao = colorsys.rgb_to_hls(r, g_, b)
    novo = colorsys.hls_to_rgb(matiz, luz, saturacao)
    return "#" + "".join(f"{round(v * 255):02x}" for v in novo)


def contraste_da_marca(cor: str) -> str:
    """Cor de texto legível POR CIMA de um preenchimento com a cor da marca.

    Um restaurante pode escolher amarelo, e branco sobre amarelo não se lê.

    Escolhe entre claro e escuro pelo contraste medido, e não por um limiar de
    luminosidade: com limiar fixo, um tom médio como #7f7f7f caía do lado errado
    e ficava em 4,0:1. Comparando os dois, o pior caso possível é ~4,6:1 — acima
    do mínimo da WCAG AA para qualquer cor que o seletor produza.
    """
    return max(TEXTO_CLARO, TEXTO_ESCURO, key=lambda opcao: _contraste(opcao, cor))


def _contraste(cor_a: str, cor_b: str) -> float:
    """Razão de contraste da WCAG entre duas cores, de 1 a 21."""
    a, b = sorted((_luminancia(cor_a), _luminancia(cor_b)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# As duas opções de texto por cima de um preenchimento colorido. O escuro é o
# --bg do tema escuro, e não um cinza qualquer: quanto mais escuro, maior a
# faixa de cores de marca que consegue passar em AA por este lado.
TEXTO_CLARO = "#ffffff"
TEXTO_ESCURO = "#0b0d10"


# Fundos contra os quais a marca aparece como texto. São os mesmos --panel do
# comanda.css; se mudarem lá, mudam aqui.
FUNDO_CLARO = "#ffffff"
FUNDO_ESCURO = "#14171d"

# 4.5:1 é o mínimo da WCAG AA para texto normal.
CONTRASTE_MINIMO = 4.5


def marca_para_texto(cor: str, *, escuro: bool) -> str:
    """A cor da marca ajustada para ser lida COMO texto, sobre o fundo do tema.

    A mesma cor não serve nos dois lados: um amarelo que funciona sobre o painel
    escuro some sobre o branco, e um azul-marinho some no escuro. Só a
    luminosidade muda — o matiz e a saturação continuam sendo os da marca.

    Escurece (ou clareia) de 2% em 2% até passar de 4.5:1, em vez de aplicar uma
    luminosidade fixa: cor que já tem contraste sobra fica intacta, e cor que
    precisa de muito ajuste recebe o quanto precisar.
    """
    fundo = FUNDO_ESCURO if escuro else FUNDO_CLARO
    if _contraste(cor, fundo) >= CONTRASTE_MINIMO:
        return cor

    r, g_, b = (v / 255 for v in _hex_para_rgb(cor))
    _, luz, _ = colorsys.rgb_to_hls(r, g_, b)

    passo = 0.02 if escuro else -0.02
    for _ in range(50):
        luz = min(1.0, max(0.0, luz + passo))
        candidata = _com_luminosidade(cor, luz)
        if _contraste(candidata, fundo) >= CONTRASTE_MINIMO:
            return candidata
        if luz in (0.0, 1.0):
            break
    # Cor sem saída (marca branca no tema claro, por exemplo): cai no texto
    # normal, que é sempre legível. Melhor perder a marca do que a leitura.
    return "#3f434c" if not escuro else "#c9ced8"


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
    def cor_da_marca() -> str:
        """Cor de destaque do tenant atual, ou a do produto na plataforma."""
        tenant = g.get("tenant")
        if tenant is not None:
            return cor_valida(getattr(tenant, "cor_marca", None)) or COR_PADRAO
        return COR_PADRAO

    @app.template_global()
    def tons_da_marca() -> dict:
        """Tints derivados da cor do tenant, para fundo e borda de destaque.

        Precisam ser derivados (e não fixos) porque a cor é escolhida por cada
        restaurante: um `--brand-bg` fixo brigaria com qualquer cor diferente do
        vermelho padrão. Usar rgba com alfa baixo funciona nos dois temas.
        """
        cor = cor_da_marca()
        r, gg, b = _hex_para_rgb(cor)
        return {
            "fundo": f"rgba({r}, {gg}, {b}, .10)",
            "borda": f"rgba({r}, {gg}, {b}, .35)",
            # Texto por cima de um preenchimento sólido da marca.
            "contraste": contraste_da_marca(cor),
            # A marca usada como texto, um valor por tema.
            "texto_claro": marca_para_texto(cor, escuro=False),
            "texto_escuro": marca_para_texto(cor, escuro=True),
        }

    @app.template_global()
    def logo_do_tenant() -> str | None:
        """Caminho relativo da logo do tenant dentro de static/uploads."""
        tenant = g.get("tenant")
        caminho = getattr(tenant, "logo", None) if tenant is not None else None
        return caminho or None

    @app.template_global()
    def inicial_do_tenant() -> str:
        """Primeira letra do nome, usada quando não há logo enviada."""
        tenant = g.get("tenant")
        nome = (getattr(tenant, "nome_fantasia", "") or "").strip() if tenant else ""
        return nome[0].upper() if nome else "•"
