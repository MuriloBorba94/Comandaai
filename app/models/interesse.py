"""Quem clicou em "Quero este" e deixou contato.

Antes o botão do plano rolava a página até a chamada final e parava ali. Quem
estava decidido não tinha o que fazer, e do lado de cá não chegava sinal nenhum
— visita interessada e visita que só passou eram a mesma coisa no servidor.

Isto é a caixa de entrada do DONO DA PLATAFORMA, e por isso não tem
`tenant_id`: o contato ainda não é de nenhum restaurante, é de alguém que quer
virar um. Fica na área da plataforma, ao lado dos planos e das cobranças.
"""

from __future__ import annotations

from datetime import datetime

from ..extensions import db

# O que fazer com um contato, na ordem em que acontece.
SITUACAO_NOVO = "novo"
SITUACAO_EM_CONTATO = "em_contato"
SITUACAO_FECHADO = "fechado"
SITUACAO_DESCARTADO = "descartado"

SITUACOES = (SITUACAO_NOVO, SITUACAO_EM_CONTATO, SITUACAO_FECHADO, SITUACAO_DESCARTADO)

ROTULO_DA_SITUACAO = {
    SITUACAO_NOVO: "Novo",
    SITUACAO_EM_CONTATO: "Em contato",
    SITUACAO_FECHADO: "Virou cliente",
    SITUACAO_DESCARTADO: "Descartado",
}


class Interesse(db.Model):
    __tablename__ = "interesse"

    id = db.Column(db.Integer, primary_key=True)

    nome = db.Column(db.String(120), nullable=False)
    telefone = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(160))
    mensagem = db.Column(db.String(1000))

    # Qual plano a pessoa clicou. Texto congelado, e não chave para `plano`:
    # o catálogo muda de nome e de preço com o tempo, e o que interessa aqui é
    # o que ela viu na tela naquele dia.
    plano = db.Column(db.String(80))

    situacao = db.Column(db.String(20), default=SITUACAO_NOVO, nullable=False, index=True)
    anotacao = db.Column(db.String(1000))

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    # Origem técnica, para separar contato de gente de contato de robô quando um
    # dia aparecer enxurrada. Não vira coluna de rastreio de pessoa.
    ip = db.Column(db.String(45))

    @property
    def rotulo_situacao(self) -> str:
        return ROTULO_DA_SITUACAO.get(self.situacao, self.situacao)

    @property
    def novo(self) -> bool:
        return self.situacao == SITUACAO_NOVO

    @property
    def whatsapp(self) -> str | None:
        """Link que abre a conversa — é por onde o retorno acontece de fato."""
        from ..services.notificacoes.link import numero_internacional

        numero = numero_internacional(self.telefone or "")
        return f"https://wa.me/{numero}" if numero else None

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Interesse {self.nome!r} {self.situacao}>"
