"""A URL do CSS muda quando o CSS muda.

Escrito depois de um prejuízo concreto. Em 24/08/2026 uma correção de CSS subiu
para o servidor e não apareceu para ninguém: o arquivo novo estava lá (90.283
bytes), e o visitante recebia o de dois dias antes (83.002 bytes), guardado na
borda da Cloudflare com `max-age=604800` — sete dias.

O endereço era o mesmo nos dois casos, e endereço igual é a definição de "pode
usar o que já tenho". Limpar o cache na mão consertaria uma vez; o teste aqui
existe para garantir que o conserto seja permanente.
"""

from __future__ import annotations

from pathlib import Path

from flask import url_for


def test_o_css_leva_versao_na_url(app):
    with app.test_request_context():
        assert "?v=" in url_for("static", filename="css/comanda.css")


def test_o_js_leva_versao_na_url(app):
    with app.test_request_context():
        assert "?v=" in url_for("static", filename="js/painel.js")


def test_a_foto_do_cardapio_nao_leva(app):
    """Versionar as fotos faria o cliente rebaixar o cardápio a cada publicação.

    Foto velha não quebra a tela; CSS velho quebra. São problemas diferentes e
    o remédio de um é caro demais para o outro — no celular, na rua, pagando
    dados.
    """
    with app.test_request_context():
        assert "?v=" not in url_for("static", filename="uploads/foto.png")


def test_a_versao_muda_quando_o_arquivo_muda(app):
    """O que dá sentido ao resto: URL fixa em arquivo que mudou é o próprio bug."""
    from app import _versao_dos_estaticos

    antes = _versao_dos_estaticos(app)

    alvo = Path(app.static_folder) / "css" / "comanda.css"
    original = alvo.read_bytes()
    try:
        alvo.write_bytes(original + b"\n/* toque */\n")
        depois = _versao_dos_estaticos(app)
    finally:
        alvo.write_bytes(original)

    assert antes != depois


def test_a_pagina_publica_aponta_para_a_url_versionada(client, app):
    """Não basta o helper devolver certo: o <link> do HTML é quem manda."""
    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert "css/comanda.css?v=" in corpo
