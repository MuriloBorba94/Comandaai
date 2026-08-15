"""limites numericos por plano

Revision ID: 225eba0f4d3d
Revises: 063c7aa4b371
Create Date: 2026-08-15 09:14:27.772936

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '225eba0f4d3d'
down_revision = '063c7aa4b371'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('plano', schema=None) as batch_op:
        batch_op.add_column(sa.Column('limites', sa.Text(), nullable=True))

    _ajustar_recursos(adicionar=True)


def downgrade():
    _ajustar_recursos(adicionar=False)

    with op.batch_alter_table('plano', schema=None) as batch_op:
        batch_op.drop_column('limites')


def _ajustar_recursos(*, adicionar: bool) -> None:
    """Acompanha a mudança do catálogo de recursos sem tirar acesso de ninguém.

    Duas coisas mudaram no catálogo:

    1. "Custos e ficha técnica" saiu de dentro de "estoque" e virou recurso
       próprio. Quem já tinha `estoque` liberado precisa ganhar `custos`, senão
       a tela de Custos some do painel de quem hoje a usa.
    2. "identidade" (logo e cor de marca) passou a ser um recurso. Antes era
       liberado para todo mundo, então todo plano JÁ CONFIGURADO recebe.

    Plano com `recursos` NULL não é tocado: NULL já significa "libera tudo", e
    escrever nele transformaria "não configurado" em "configurado".
    """
    conexao = op.get_bind()
    plano = sa.table("plano", sa.column("id", sa.Integer), sa.column("recursos", sa.Text))

    for identificador, recursos in conexao.execute(sa.select(plano.c.id, plano.c.recursos)):
        if recursos is None:
            continue
        slugs = [trecho.strip() for trecho in recursos.split(",") if trecho.strip()]

        if adicionar:
            if "estoque" in slugs and "custos" not in slugs:
                slugs.append("custos")
            if "identidade" not in slugs:
                slugs.append("identidade")
        else:
            slugs = [slug for slug in slugs if slug not in ("custos", "identidade")]

        conexao.execute(
            plano.update().where(plano.c.id == identificador).values(recursos=",".join(slugs))
        )
