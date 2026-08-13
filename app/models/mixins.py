from __future__ import annotations

from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class PasswordMixin:
    senha = db.Column(db.String(255), nullable=False)

    def set_password(self, password: str) -> None:
        self.senha = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.senha, password)
