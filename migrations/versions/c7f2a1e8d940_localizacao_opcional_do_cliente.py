"""localizacao opcional do cliente no pedido de entrega

Revision ID: c7f2a1e8d940
Revises: b41d7c9e5a02
Create Date: 2026-09-03 09:30:00.000000

Três colunas anuláveis, e anuláveis de propósito: compartilhar a localização é
opcional para o cliente, então "não informado" é um estado normal do pedido, e
não uma falha de preenchimento. Nenhum pedido existente precisa ser tocado.

Não confundir com `entrega_lat`/`entrega_lng`, que já existiam: aquelas são a
posição do ENTREGADOR enquanto ele roda, e servem ao mapa que o cliente olha.
Estas são o ponto do CLIENTE, gravado uma vez no checkout, e servem à rota que
o entregador abre.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7f2a1e8d940'
down_revision = 'b41d7c9e5a02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pedido', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cliente_lat', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('cliente_lng', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('cliente_local_precisao', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('pedido', schema=None) as batch_op:
        batch_op.drop_column('cliente_local_precisao')
        batch_op.drop_column('cliente_lng')
        batch_op.drop_column('cliente_lat')
