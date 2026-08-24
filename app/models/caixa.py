"""Abertura e fechamento do dia, com o dinheiro que estava na gaveta.

Um "caixa" aqui é o turno: alguém abre a loja de manhã com um valor de troco na
gaveta, vende o dia inteiro, e no fim confere quanto sobrou. É o registro que
responde a pergunta que todo dono faz no fim da noite — "bateu?".

O que este modelo NÃO é: um livro-caixa contábil. As despesas e as receitas
avulsas continuam em `financeiro.py`, e o faturamento sai dos pedidos. Aqui só
mora o turno: quando abriu, com quanto, quem abriu, e o que foi contado no fim.

**A loja aberta é consequência, não configuração.** Ter um caixa aberto é o que
significa "estamos atendendo". Um interruptor separado permitiria os dois
estados sem sentido: loja aberta sem caixa (vendendo sem ninguém responsável
pelo dinheiro) e caixa aberto com loja fechada (gaveta destrancada e ninguém no
salão).
"""

from __future__ import annotations

from datetime import datetime

from ..extensions import db


class Caixa(db.Model):
    __tablename__ = "caixa"

    __table_args__ = (
        # Um caixa aberto por restaurante. Sem isto, dois cliques no botão de
        # abrir criariam dois turnos, e o fechamento contaria as vendas do dia
        # duas vezes.
        db.Index(
            "uq_caixa_aberto_por_tenant",
            "tenant_id",
            unique=True,
            sqlite_where=db.text("fechado_em IS NULL"),
            postgresql_where=db.text("fechado_em IS NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE", name="fk_caixa_tenant"), nullable=False, index=True
    )

    aberto_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    aberto_por = db.Column(db.String(80))
    # O troco que ficou na gaveta no começo do turno. Sem ele, a conferência do
    # fim não fecha: o dinheiro contado inclui um valor que nunca foi venda.
    valor_inicial = db.Column(db.Float, default=0.0, nullable=False)

    fechado_em = db.Column(db.DateTime)
    fechado_por = db.Column(db.String(80))
    # Quanto foi CONTADO na gaveta ao fechar. Fica separado do que o sistema
    # calculou de propósito: a diferença entre os dois é a informação que
    # interessa, e escondê-la seria fingir que ela não existe.
    valor_contado = db.Column(db.Float)
    observacao = db.Column(db.String(300))

    tenant = db.relationship("Tenant")

    @property
    def aberto(self) -> bool:
        return self.fechado_em is None

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        estado = "aberto" if self.aberto else "fechado"
        return f"<Caixa tenant={self.tenant_id} {estado}>"
