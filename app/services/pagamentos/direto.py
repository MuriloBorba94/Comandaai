"""PIX direto: o código é gerado aqui e o dinheiro cai na conta do restaurante.

É o meio que funciona para qualquer restaurante no primeiro dia, sem contrato
com gateway, sem taxa por transação e sem chave de API — basta a chave PIX que
ele já usa. Em troca, ninguém de fora avisa que o dinheiro entrou: alguém do
restaurante olha o aplicativo do banco e confirma no painel.

Essa troca é honesta e vale a pena dizer em voz alta: no sistema original, este
mesmo meio respondeu por 159 dos 162 pagamentos online registrados. O gateway
automático foi usado três vezes, num único dia, e abandonado.
"""

from __future__ import annotations

from .base import Cobranca, ProvedorPix
from .brcode import montar


class PixDireto(ProvedorPix):
    slug = "pix_direto"
    nome = "PIX direto"
    confirmacao_manual = True

    def configurado(self, tenant) -> bool:
        return bool((getattr(tenant, "pix_chave", "") or "").strip())

    def falta_configurar(self, tenant) -> str:
        if self.configurado(tenant):
            return ""
        return (
            "Cadastre a chave PIX do restaurante em Loja e identidade para "
            "receber pagamento pelo site."
        )

    def criar(self, pedido) -> Cobranca:
        tenant = pedido.tenant
        if not self.configurado(tenant):
            return Cobranca(False, erro=self.falta_configurar(tenant))

        # O identificador vai para o extrato do banco do restaurante. Curto e
        # legível de propósito: é por ele que a pessoa casa o dinheiro com o
        # pedido quando confere o caixa.
        txid = f"PED{pedido.numero}"
        try:
            codigo = montar(
                chave=tenant.pix_chave,
                valor=float(pedido.total or 0),
                recebedor=tenant.pix_recebedor or tenant.nome_fantasia,
                cidade=tenant.pix_cidade or "",
                txid=txid,
                reserva_recebedor="RESTAURANTE",
                reserva_cidade="BRASIL",
            )
        except ValueError as exc:
            return Cobranca(False, erro=str(exc))

        return Cobranca(True, brcode=codigo, txid=txid, referencia=txid)
