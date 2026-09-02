"""remove a cor de marca por tenant

Revision ID: b41d7c9e5a02
Revises: a90405bf07e5
Create Date: 2026-09-02 08:12:00.000000

Desfaz metade da fb6acdd91454, que criou `logo` e `cor_marca` juntas. A logo
fica: ela é do restaurante e aparece no painel e no cardápio. A cor sai.

O tema Industry fixa `--brand` no CSS, então a cor gravada aqui não pintava
mais nada — o restaurante escolhia e continuava vendo o aço. Uma coluna que
guarda escolha sem efeito é pior que coluna nenhuma: ela faz a próxima pessoa
acreditar que o recurso existe.

O `downgrade` recria a coluna vazia. Ele devolve o formato, não o conteúdo: as
cores que estavam gravadas se perdem no upgrade e não têm de onde voltar. Quem
precisar delas tem o backup que o `atualizar.sh` tira antes de migrar.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b41d7c9e5a02'
down_revision = 'a90405bf07e5'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table porque o SQLite não tem DROP COLUMN de verdade: o
    # Alembic recria a tabela e copia os dados. É o mesmo caminho que a
    # migration que criou esta coluna usou para adicioná-la.
    with op.batch_alter_table('tenant', schema=None) as batch_op:
        batch_op.drop_column('cor_marca')


def downgrade():
    with op.batch_alter_table('tenant', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cor_marca', sa.String(length=7), nullable=True))
