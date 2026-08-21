"""Cobrança manual: a mensalidade existe no sistema e você recebe por fora.

Não fala com ninguém e não pode falhar. É o piso da plataforma — o modo que
funciona sem contrato, sem chave de API e sem depender de ninguém estar no ar.
"""

from __future__ import annotations

from ...models.assinatura import PROVEDOR_MANUAL
from .base import ProvedorCobranca, ResultadoCobranca


class Manual(ProvedorCobranca):
    slug = PROVEDOR_MANUAL
    nome = "Manual (você recebe o PIX e marca como pago)"
    automatico = False

    def configurado(self) -> bool:
        return True

    def falta_configurar(self) -> str:
        return ""

    def criar(self, cobranca) -> ResultadoCobranca:
        # Sem id externo e sem link: a cobrança vive só aqui dentro.
        return ResultadoCobranca(True)
