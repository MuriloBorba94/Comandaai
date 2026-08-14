"""Conversões usadas por mais de um lugar do sistema."""

from __future__ import annotations

import re

_SO_NUMERO = re.compile(r"^-?[\d.,]+$")


def para_float(valor) -> float:
    """Interpreta número digitado no padrão brasileiro, sem erro silencioso.

    O caso difícil é o ponto sozinho, que é ambíguo: em "45.90" ele separa o
    decimal, em "2.500" separa o milhar. A desambiguação usa o tamanho do último
    grupo, que é a convenção brasileira:

        "45,90"        -> 45.9     vírgula é sempre decimal
        "45.90"        -> 45.9     grupo final de 2 dígitos: decimal
        "2.500"        -> 2500.0   grupo final de 3 dígitos: milhar
        "1.234,56"     -> 1234.56  ponto é milhar quando há vírgula
        "1.234.567"    -> 1234567.0
        "0.500"        -> 0.5      parte inteira "0" indica decimal

    Sem essa distinção, um lançamento de "2.500" no estoque entrava como 2,5 —
    erro de mil vezes que ninguém percebe olhando a tela.
    """
    texto = str(valor or "").strip()
    if not texto or not _SO_NUMERO.match(texto):
        return 0.0

    negativo = texto.startswith("-")
    texto = texto.lstrip("-")

    if "," in texto:
        # Vírgula presente: o ponto só pode ser separador de milhar.
        texto = texto.replace(".", "").replace(",", ".")
    elif "." in texto:
        grupos = texto.split(".")
        ultimo = grupos[-1]
        # Milhar quando o último grupo tem exatamente 3 dígitos e a parte inteira
        # não é só "0" (quem digita "0.500" quer meio, não quinhentos).
        e_milhar = len(ultimo) == 3 and ultimo.isdigit() and grupos[0] not in ("", "0")
        if e_milhar:
            texto = "".join(grupos)

    try:
        numero = float(texto)
    except ValueError:
        return 0.0
    return -numero if negativo else numero


def para_int(valor) -> int:
    try:
        return int(str(valor or "0").strip())
    except ValueError:
        return 0
