"""Premissas do Simulador de Receita, por usuário.

Revision ID: c4e81a06b52f
Revises: 9b12f4c7d833

A coluna foi deliberadamente deixada de fora na Fase 8a ("entra junto com a
tela que a usa", pra não criar dívida de coluna morta). A tela chegou na Fase
9: o Simulador de Receita do Dashboard, que salva via
``PUT /api/dashboard/premissas``.

Aditiva e nullable — ``NULL`` significa "este usuário nunca salvou", e a rota
devolve um padrão calculado da base real nesse caso. Sem backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e81a06b52f"
down_revision: str | None = "9b12f4c7d833"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "dashboard_premissas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "dashboard_premissas")
