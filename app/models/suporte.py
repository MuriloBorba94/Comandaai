"""Passe de entrada para o modo suporte.

Existe por causa de uma decisão de arquitetura tomada lá atrás: o cookie de
sessão é **por host**, sem `SESSION_COOKIE_DOMAIN`. É isso que impede uma sessão
aberta num restaurante de valer em outro. O efeito colateral é que a área da
plataforma (`app.dominio`) não consegue simplesmente criar uma sessão em
`restaurante.dominio` — os dois são hosts diferentes para o navegador.

A ponte é este passe: a plataforma gera um código curto, manda o navegador para
o endereço do restaurante, e lá o código é trocado por uma sessão. Compartilhar
o cookie entre subdomínios resolveria também, e seria muito pior: derrubaria a
separação que protege um restaurante do outro em todo o resto do sistema.

Três travas, e cada uma cobre uma falha diferente:

- **vida curta** (2 minutos): um endereço que vaze no histórico do navegador ou
  num log de proxy não serve para nada depois disso;
- **uso único**: mesmo dentro dos 2 minutos, o passe não é reaproveitável;
- **preso a um restaurante**: o passe do restaurante A não abre o B.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from ..extensions import db

# Tempo entre gerar o passe e usá-lo. É só o de um redirecionamento, então
# 2 minutos é folgado — e curto o bastante para um endereço vazado não valer.
VALIDADE_SEGUNDOS = 120

# Quanto tempo a sessão de suporte dura, no máximo. Diferente da sessão comum,
# que expira por inatividade: aqui o relógio não para enquanto a pessoa mexe.
# Alguém da plataforma dentro da conta de um cliente é uma situação que deve
# terminar sozinha, e não durar até que alguém lembre de sair.
DURACAO_SESSAO_SEGUNDOS = 30 * 60


class PasseSuporte(db.Model):
    __tablename__ = "passe_suporte"

    id = db.Column(db.Integer, primary_key=True)
    # Guardado como hash, como senha: quem lê o banco não consegue entrar em
    # restaurante nenhum com o que está gravado aqui.
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Quem da plataforma pediu o passe. É o nome que vai para o diário de
    # auditoria em tudo o que for feito dentro da sessão.
    admin = db.Column(db.String(80), nullable=False)

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    expira_em = db.Column(db.DateTime, nullable=False)
    usado_em = db.Column(db.DateTime)

    tenant = db.relationship("Tenant")

    @property
    def valido(self) -> bool:
        return self.usado_em is None and datetime.now() <= self.expira_em

    @staticmethod
    def hash_de(token: str) -> str:
        return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()

    @classmethod
    def emitir(cls, tenant, admin: str) -> tuple["PasseSuporte", str]:
        """Cria o passe e devolve (registro, texto). O texto não é guardado."""
        token = secrets.token_urlsafe(32)
        passe = cls(
            token_hash=cls.hash_de(token),
            tenant_id=tenant.id,
            admin=(admin or "super-admin")[:80],
            expira_em=datetime.now() + timedelta(seconds=VALIDADE_SEGUNDOS),
        )
        db.session.add(passe)
        db.session.commit()
        return passe, token

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<PasseSuporte tenant={self.tenant_id} usado={bool(self.usado_em)}>"
