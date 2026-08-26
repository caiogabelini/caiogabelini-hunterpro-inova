"""Telefone alternativo no lead.

Revision ID: 9b12f4c7d833
Revises: 7a3c9d2b4e10

Nasce da primeira busca paga real: a API Full devolveu até 5 números pra um
mesmo CPF e o pipeline guardava só um, descartando contato já pago. Coluna
própria (não ``dados_nicho``) porque telefone é dado genérico de contato —
``telefone`` já é coluna desde a Fase 1.

Aditiva e nullable: nenhuma linha existente precisa de valor, nenhum backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b12f4c7d833"
down_revision: str | None = "7a3c9d2b4e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leads", sa.Column("telefone_secundario", sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("leads", "telefone_secundario")
