"""Modelo de usuário — autenticação enxuta.

Porte de ``app/models/user.py`` do Minotto. Cadastro é feito por admin via
``scripts/create_user.py``, **não** por endpoint público de self-service:
mesma decisão de lá, e pelo mesmo motivo — só a Inova e a 4Hands usam o
sistema. Não há refresh token, recuperação de senha nem registro público.

``dashboard_premissas`` entrou na Fase 9, exatamente como o docstring da Fase
8a previa: a coluna foi deixada de fora enquanto ninguém a lia ("entra junto
com a tela que a usa", §5 — o caso da ``SERP_API_KEY`` morta), e a tela que a
usa é o Simulador de Receita do Dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from typing import Any

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base
from app.core.tempo import agora_utc


class UserRole(str, Enum):
    ADMIN = "admin"
    CLIENT = "client"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    #: "admin" | "client". Só admin vê a tela de Busca de Leads.
    role: Mapped[str] = mapped_column(String(10), default=UserRole.CLIENT.value)
    #: Usuário inativo não loga — e o login NÃO retorna cedo por causa disso
    #: (ver `_autenticar`), pra não abrir um oráculo de tempo.
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Premissas do Simulador de Receita — ``{leads_qualificados,
    #: taxa_fechamento, ticket_medio}``. **Por usuário**, não global: cada um
    #: pode estar testando um cenário comercial diferente.
    #:
    #: ``None`` = nunca salvou; a rota devolve um padrão calculado da base
    #: real e **não persiste esse padrão sozinha** — só quando o usuário
    #: confirma no modal.
    dashboard_premissas: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<User {self.email} role={self.role} ativo={self.ativo}>"
