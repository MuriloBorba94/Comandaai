"""plano completo libera tudo, inclusive o que vier depois

Esta migration encerra um padrão que já se repetiu duas vezes.

Nas Fases 8 e 6 eu escrevi migrations de dados que reescreviam a lista de
recursos de cada plano para incluir o slug novo. Funcionava, mas era o mesmo
trabalho a cada fase — e o custo de esquecer é alto e silencioso: o recurso
novo simplesmente não aparece para quem já paga pelo plano mais caro, sem erro
e sem aviso.

Agora o plano pode DIZER que é completo (`libera_tudo`). Um recurso criado
amanhã entra sozinho nele, e não existe mais migration de dados a escrever.

O `upgrade` marca como completos os planos que hoje liberam todos os recursos
do catálogo — que é exatamente o que as duas migrations anteriores deixaram.
Plano com escolha parcial não é tocado: ali alguém decidiu o que entra, e isso
faz parte do preço.

Revision ID: d7e4c876b207
Revises: bdd821a4c858
Create Date: 2026-08-21 09:40:08.945535

"""
from alembic import op
import sqlalchemy as sa


revision = 'd7e4c876b207'
down_revision = 'bdd821a4c858'
branch_labels = None
depends_on = None


# O catálogo como ele está nesta data. Escrito aqui, e não importado de
# app.models, porque migration é história: se o catálogo mudar amanhã, esta
# migration precisa continuar decidindo com a régua de hoje.
CATALOGO_DE_HOJE = {
    "cozinha",
    "impressao",
    "pix",
    "whatsapp",
    "mesas",
    "cupons",
    "bairros",
    "fotos",
    "identidade",
    "relatorios",
    "estoque",
    "custos",
    "financeiro",
}


def _plano_table() -> sa.Table:
    return sa.table(
        "plano",
        sa.column("id", sa.Integer),
        sa.column("recursos", sa.String),
        sa.column("libera_tudo", sa.Boolean),
    )


def upgrade():
    with op.batch_alter_table("plano", schema=None) as batch_op:
        # server_default: a tabela `plano` já tem linhas no servidor, e coluna
        # NOT NULL sem valor de partida faz o ALTER TABLE falhar no meio da
        # atualização.
        batch_op.add_column(
            sa.Column("libera_tudo", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    conexao = op.get_bind()
    plano = _plano_table()

    for linha in conexao.execute(sa.select(plano.c.id, plano.c.recursos)).fetchall():
        texto = (linha.recursos or "").strip()
        if not texto:
            # `recursos = NULL` já significa "nunca configurado, libera tudo".
            # Marcar aqui trocaria um estado por outro sem necessidade, e
            # apagaria a informação de que o plano nunca foi configurado.
            continue

        atuais = {parte.strip() for parte in texto.split(",") if parte.strip()}
        # Sem o whatsapp na conta: ele nasce nesta fase, então nenhum plano o
        # tem ainda. Quem tinha todos os outros é o plano completo.
        if (CATALOGO_DE_HOJE - {"whatsapp"}).issubset(atuais):
            conexao.execute(
                plano.update().where(plano.c.id == linha.id).values(libera_tudo=True)
            )


def downgrade():
    with op.batch_alter_table("plano", schema=None) as batch_op:
        batch_op.drop_column("libera_tudo")
