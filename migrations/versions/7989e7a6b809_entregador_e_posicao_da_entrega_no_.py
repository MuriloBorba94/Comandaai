"""entregador e posicao da entrega no pedido

Revision ID: 7989e7a6b809
Revises: 3f196c4fe4b6
Create Date: 2026-08-21 16:50:20.167836

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7989e7a6b809'
down_revision = '3f196c4fe4b6'
branch_labels = None
depends_on = None


# A chave estrangeira precisa de NOME.
#
# O autogenerate criou `create_foreign_key(None, ...)`, e no SQLite o Alembic
# recria a tabela inteira para alterá-la (`batch_alter_table`) — nessa recriação
# ele exige que toda constraint tenha nome, e a migration morre com
# "Constraint must have a name". Não dá erro ao gerar: dá na hora de aplicar,
# que no servidor é no meio da publicação.
FK_ENTREGADOR = "fk_pedido_entregador"


def upgrade():
    with op.batch_alter_table("pedido", schema=None) as batch_op:
        batch_op.add_column(sa.Column("entregador_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("entrega_lat", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("entrega_lng", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("entrega_atualizado_em", sa.DateTime(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_pedido_entregador_id"), ["entregador_id"], unique=False
        )
        # SET NULL: desativar ou remover um usuário não pode apagar o histórico
        # de quem entregou o quê.
        batch_op.create_foreign_key(
            FK_ENTREGADOR, "usuario", ["entregador_id"], ["id"], ondelete="SET NULL"
        )


def downgrade():
    with op.batch_alter_table("pedido", schema=None) as batch_op:
        batch_op.drop_constraint(FK_ENTREGADOR, type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_pedido_entregador_id"))
        batch_op.drop_column("entrega_atualizado_em")
        batch_op.drop_column("entrega_lng")
        batch_op.drop_column("entrega_lat")
        batch_op.drop_column("entregador_id")

    # ### end Alembic commands ###
