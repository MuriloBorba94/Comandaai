"""libera pix nos planos que ja davam tudo

Mesma situação da Fase 8, mesma regra: recurso novo no catálogo não entra
sozinho num plano com `recursos` preenchido, e a tela nova simplesmente não
aparece para quem já usa o sistema.

Um plano que dava **todos** os recursos que existiam antes desta fase continua
dando tudo. Plano com escolha parcial não é tocado — ali alguém decidiu o que
entra, e isso faz parte do preço que ele cobra.

É a segunda vez que escrevo esta migration. Se acontecer uma terceira, o certo
passa a ser um `Plano.libera_tudo` de verdade — uma marca dizendo "este plano
inclui inclusive o que for criado depois" — em vez de repetir a regra a cada
recurso novo.

Revision ID: 484a46a8d0f4
Revises: 85476076628e
Create Date: 2026-08-21 10:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '484a46a8d0f4'
down_revision = '85476076628e'
branch_labels = None
depends_on = None


NOVO = "pix"

# Os onze recursos que existiam antes da Fase 6 (as fases não foram feitas em
# ordem: a impressão, da Fase 8, veio antes). Escritos aqui, e não importados de
# app.models, porque migration é história: se o catálogo mudar amanhã, esta
# migration precisa continuar decidindo com a régua de hoje.
ANTERIORES = {
    "cozinha",
    "impressao",
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
            # aqui transformaria "libera tudo" em "libera estes onze", e o
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
