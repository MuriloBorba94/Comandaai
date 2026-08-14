"""Conversão de número digitado.

O caso difícil é o ponto sozinho: em "45.90" separa o decimal, em "2.500" separa
o milhar. Um lançamento de estoque de "2.500" entrando como 2,5 é um erro de mil
vezes que ninguém percebe olhando a tela — foi o que motivou estes testes.
"""

from __future__ import annotations

import pytest

from app.utils import para_float, para_int


@pytest.mark.parametrize(
    ("digitado", "esperado"),
    [
        # Vírgula é sempre o decimal.
        ("45,90", 45.90),
        ("0,5", 0.5),
        ("1.234,56", 1234.56),
        ("1.234.567,89", 1234567.89),
        # Ponto sozinho com 1 ou 2 casas: decimal.
        ("45.90", 45.90),
        ("2.5", 2.5),
        # Ponto sozinho com 3 casas: milhar.
        ("2.500", 2500.0),
        ("1.234", 1234.0),
        ("1.234.567", 1234567.0),
        # Parte inteira "0" indica decimal: quem digita 0.500 quer meio.
        ("0.500", 0.5),
        # Sem separador.
        ("2500", 2500.0),
        ("0", 0.0),
        # Entradas que não são número viram zero em vez de estourar.
        ("", 0.0),
        ("   ", 0.0),
        ("abc", 0.0),
        ("12abc", 0.0),
        (None, 0.0),
        # Negativo (usado em ajuste).
        ("-15,5", -15.5),
    ],
)
def test_para_float(digitado, esperado):
    assert para_float(digitado) == pytest.approx(esperado)


def test_para_float_nao_confunde_milhar_com_decimal():
    """Os dois lados da ambiguidade, lado a lado."""
    assert para_float("2.500") == 2500.0, "três casas: milhar"
    assert para_float("2.50") == 2.5, "duas casas: decimal"


@pytest.mark.parametrize(
    ("digitado", "esperado"),
    [("10", 10), ("0", 0), ("", 0), ("abc", 0), (None, 0), ("-3", -3)],
)
def test_para_int(digitado, esperado):
    assert para_int(digitado) == esperado
