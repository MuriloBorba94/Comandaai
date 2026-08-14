"""indice unico parcial de cobranca ignora canceladas

Revision ID: 7bb574e2588f
Revises: 6c19ad6ef5bc
Create Date: 2026-08-14 07:45:34.128338

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7bb574e2588f'
down_revision = '6c19ad6ef5bc'
branch_labels = None
depends_on = None


def upgrade():
    # Troca a unique constraint (tenant_id, competencia) por um índice único
    # PARCIAL que ignora cobranças canceladas.
    #
    # Com a constraint cheia, cancelar a cobrança de um mês travava o mês para
    # sempre: não era possível reemitir, e apagar a cancelada para liberar a vaga
    # perderia o registro do cancelamento.
    #
    # Escrita à mão: o autogenerate não representa a cláusula parcial.
    with op.batch_alter_table("cobranca", schema=None) as batch_op:
        batch_op.drop_constraint("uq_cobranca_tenant_competencia", type_="unique")

    op.create_index(
        "uq_cobranca_competencia_viva",
        "cobranca",
        ["tenant_id", "competencia"],
        unique=True,
        sqlite_where=sa.text("status != 'cancelada'"),
        postgresql_where=sa.text("status != 'cancelada'"),
    )


def downgrade():
    op.drop_index("uq_cobranca_competencia_viva", table_name="cobranca")

    # Voltar para a constraint cheia só é possível se não houver mais de uma
    # cobrança por competência — o que passa a ser possível depois desta
    # migration. Canceladas em duplicidade impedem a volta, e é melhor falhar
    # aqui do que perder dado silenciosamente.
    with op.batch_alter_table("cobranca", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_cobranca_tenant_competencia", ["tenant_id", "competencia"]
        )
