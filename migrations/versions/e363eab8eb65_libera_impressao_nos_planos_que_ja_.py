"""libera impressao nos planos que ja davam tudo

Recurso novo no catálogo não entra sozinho nos planos já configurados: quem tem
`recursos` preenchido continua com exatamente o que foi marcado, e a tela de
Impressão simplesmente não aparece — sem explicação nenhuma para quem estiver
usando.

A regra aqui é estreita de propósito: um plano que dava **todos** os recursos
que existiam antes desta fase continua dando tudo. Plano com escolha parcial
(um "starter" que dá cozinha e entrega, por exemplo) não é tocado — ali alguém
decidiu o que entra, e mexer nisso seria dar de graça um recurso que faz parte
do preço.

Revision ID: e363eab8eb65
Revises: 4044254d344a
Create Date: 2026-08-20 17:22:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e363eab8eb65'
down_revision = '4044254d344a'
branch_labels = None
depends_on = None


NOVO = "impressao"

# Os dez recursos que existiam antes da Fase 8. Ficam escritos aqui, e não
# importados de app.models, porque migration é história: se o catálogo mudar
# amanhã, esta migration precisa continuar decidindo com a régua de hoje.
ANTERIORES = {
    "cozinha",
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
    )


def upgrade():
    conexao = op.get_bind()
    plano = _plano_table()

    for linha in conexao.execute(sa.select(plano.c.id, plano.c.recursos)).fetchall():
        texto = (linha.recursos or "").strip()
        if not texto:
            # Plano sem recursos configurados já libera tudo. Escrever a lista
            # aqui transformaria "libera tudo" em "libera estes dez", e o
            # próximo recurso novo passaria a faltar nele também.
            continue

        atuais = {parte.strip() for parte in texto.split(",") if parte.strip()}
        if NOVO in atuais or not ANTERIORES.issubset(atuais):
            continue

        conexao.execute(
            plano.update().where(plano.c.id == linha.id).values(recursos=texto + "," + NOVO)
        )


def downgrade():
    conexao = op.get_bind()
    plano = _plano_table()

    for linha in conexao.execute(sa.select(plano.c.id, plano.c.recursos)).fetchall():
        atuais = [parte.strip() for parte in (linha.recursos or "").split(",") if parte.strip()]
        if NOVO not in atuais:
            continue
        conexao.execute(
            plano.update()
            .where(plano.c.id == linha.id)
            .values(recursos=",".join(p for p in atuais if p != NOVO))
        )
