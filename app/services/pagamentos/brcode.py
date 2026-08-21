"""Gerador do BR Code — o "PIX copia e cola" e o que vira o QR na tela.

O código é montado aqui, no servidor, a partir da chave PIX **do restaurante**.
Não há intermediário: o dinheiro cai direto na conta dele, e a plataforma nunca
toca no valor. É por isso que este arquivo não fala com ninguém de fora — é
aritmética e formatação, e roda igual com ou sem internet.

O formato é o EMV QRCPS do Banco Central: uma sequência de campos
`ID + tamanho + valor` (TLV), fechada por um CRC de 4 dígitos. O manual está em
https://www.bcb.gov.br/estabilidadefinanceira/pix — os números de campo abaixo
são os de lá.

Uma correção em relação ao sistema original: lá existiam DUAS cópias deste
gerador (uma em `pix_service.py`, outra dentro de `routes/public.py`) e elas já
tinham divergido — o texto de reserva do recebedor era diferente em cada uma.
Aqui é uma função só.
"""

from __future__ import annotations

import re
import unicodedata

# Identificador do arranjo PIX dentro do campo 26. É fixo e definido pelo BCB.
DOMINIO_PIX = "br.gov.bcb.pix"

MOEDA_REAL = "986"
PAIS = "BR"

# Limites do manual do BCB. Estourar qualquer um deles faz o aplicativo do banco
# recusar o código — em silêncio, com uma mensagem genérica de "QR inválido".
MAX_NOME = 25
MAX_CIDADE = 15
MAX_TXID = 25


def _campo(identificador: str, valor: str) -> str:
    """Um campo TLV: id + tamanho em dois dígitos + valor."""
    valor = str(valor)
    return f"{identificador}{len(valor):02d}{valor}"


# Sinais que grudam duas partes de uma palavra só. Viram nada, não viram espaço:
# com espaço, "Borba's Burguer" sairia "BORBA S BURGUER" no aplicativo do banco.
_SINAIS_QUE_SOMEM = re.compile(r"['’`´]")


def _texto_seguro(valor: str, tamanho: int, reserva: str) -> str:
    """Deixa o texto no que o BR Code aceita: ASCII, sem símbolo, em maiúsculas.

    "Vicência" vira "VICENCIA". Não é firula: acento no campo do recebedor faz
    os aplicativos de banco recusarem o código, e o cliente só vê "QR inválido"
    sem saber por quê.
    """
    sem_acento = unicodedata.normalize("NFKD", valor or "").encode("ascii", "ignore").decode("ascii")
    limpo = _SINAIS_QUE_SOMEM.sub("", sem_acento)
    limpo = re.sub(r"[^A-Za-z0-9 ]", " ", limpo)
    limpo = " ".join(limpo.split()).upper()
    return (limpo or reserva)[:tamanho]


def limpar_txid(valor: str, reserva: str = "PEDIDO") -> str:
    """O identificador da transação aceita só letras e números."""
    limpo = re.sub(r"[^A-Za-z0-9]", "", valor or "")
    return (limpo or reserva)[:MAX_TXID]


def crc16(payload: str) -> str:
    """CRC-16/CCITT-FALSE, que é o que o BR Code usa.

    Polinômio 0x1021, valor inicial 0xFFFF, sem inversão de bits nem do
    resultado. Os quatro dígitos hexadecimais em MAIÚSCULAS fecham o código.
    """
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def montar(
    *,
    chave: str,
    valor: float,
    recebedor: str,
    cidade: str,
    txid: str,
    reserva_recebedor: str = "RESTAURANTE",
    reserva_cidade: str = "BRASIL",
) -> str:
    """Monta o BR Code de um pagamento com valor e identificador próprios.

    `valor` vem em reais e entra no código com duas casas e ponto — nunca
    vírgula, mesmo sendo um formato brasileiro: o campo 54 é numérico.
    """
    chave = (chave or "").strip()
    if not chave:
        raise ValueError("A chave PIX do restaurante ainda não foi cadastrada.")
    if len(chave) > 77:
        raise ValueError("Esta chave PIX é longa demais para caber no código.")
    if valor is None or valor <= 0:
        raise ValueError("O valor do pagamento precisa ser maior que zero.")

    conta_do_recebedor = _campo("00", DOMINIO_PIX) + _campo("01", chave)

    payload = (
        _campo("00", "01")  # versão do formato
        # 11 = código reutilizável. Ele carrega valor e txid próprios deste
        # pedido, mas continua válido se o cliente fechar a tela e voltar —
        # que é o caso comum de quem sai do site para abrir o aplicativo do
        # banco. Com 12 (uso único) alguns bancos recusam a segunda leitura.
        + _campo("01", "11")
        + _campo("26", conta_do_recebedor)
        + _campo("52", "0000")  # categoria do estabelecimento: não informada
        + _campo("53", MOEDA_REAL)
        + _campo("54", f"{valor:.2f}")
        + _campo("58", PAIS)
        + _campo("59", _texto_seguro(recebedor, MAX_NOME, reserva_recebedor))
        + _campo("60", _texto_seguro(cidade, MAX_CIDADE, reserva_cidade))
        + _campo("62", _campo("05", limpar_txid(txid)))
        # O campo do CRC entra com o cabeçalho ANTES do cálculo: os quatro
        # caracteres "6304" fazem parte do que é somado.
        + "6304"
    )
    return payload + crc16(payload)
