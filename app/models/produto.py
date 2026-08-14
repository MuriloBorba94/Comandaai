from __future__ import annotations

from ..extensions import db
from .adicional import Adicional, produto_adicional
from .mixins import TimestampMixin


class Produto(TimestampMixin, db.Model):
    """Item do cardápio de um tenant."""

    __tablename__ = "produto"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey("categoria.id", ondelete="SET NULL"), index=True)
    nome = db.Column(db.String(100), nullable=False)
    descricao = db.Column(db.String(300))
    preco = db.Column(db.Float, nullable=False)
    # Caminho relativo dentro de static/uploads, incluindo a pasta do tenant
    # (ex.: "pizzaria-joao/produto_3_a1b2c3.webp"). Guardar o caminho completo
    # em vez de só o nome mantém a imagem válida mesmo se o slug do tenant
    # mudar depois.
    imagem = db.Column(db.String(200))
    disponivel = db.Column(db.Boolean, default=True, nullable=False)
    # Item que já está com preço promocional. Cupom não incide sobre ele, a
    # menos que o cupom marque permite_combo_promocional — é o que evita
    # empilhar desconto sobre desconto.
    combo_promocional = db.Column(db.Boolean, default=False, nullable=False)

    tenant = db.relationship("Tenant", back_populates="produtos")
    categoria = db.relationship("Categoria", back_populates="produtos")
    adicionais = db.relationship("Adicional", secondary=produto_adicional, back_populates="produtos")
    ficha = db.relationship(
        "FichaTecnica", back_populates="produto", cascade="all, delete-orphan"
    )

    @property
    def custo_por_ficha(self) -> float:
        """Custo de produzir uma unidade, somando a ficha técnica."""
        return round(sum(linha.custo for linha in self.ficha), 2)

    @property
    def margem(self) -> float | None:
        """Lucro por unidade. None quando não há ficha para comparar."""
        if not self.ficha:
            return None
        return round(float(self.preco or 0) - self.custo_por_ficha, 2)

    def definir_adicionais(self, ids) -> None:
        """Substitui os adicionais deste produto, ignorando ids de outro tenant.

        A tabela de ligação não carrega tenant_id, então este filtro é a única
        barreira contra vincular o produto de um tenant ao adicional de outro.
        O filtro é por tenant_id do próprio produto — nunca por uma lista de
        ids vinda do formulário.
        """
        ids = {int(valor) for valor in ids or [] if str(valor).strip().isdigit()}
        if not ids:
            self.adicionais = []
            return
        self.adicionais = (
            Adicional.query.filter(Adicional.id.in_(ids), Adicional.tenant_id == self.tenant_id).all()
        )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Produto {self.nome!r} tenant={self.tenant_id}>"
