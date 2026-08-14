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
        r, gg, b = _hex_para_rgb(cor_da_marca())
        return {
            "fundo": f"rgba({r}, {gg}, {b}, .10)",
            "borda": f"rgba({r}, {gg}, {b}, .35)",
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
