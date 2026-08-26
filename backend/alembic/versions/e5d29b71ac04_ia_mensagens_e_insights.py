"""Fase 10: geração por IA — mensagens por canal e insights estratégicos.

Revision ID: e5d29b71ac04
Revises: c4e81a06b52f

Reverte a decisão da Fase 6 de não portar a geração por IA. O
``docs_fundacao.md`` sempre a tratou como parte da fundação.

Duas frentes:
  - ``lead_messages``: histórico por canal, uma linha por geração.
  - 4 colunas em ``leads``: os insights (sobrescritos a cada geração), o
    contador que sustenta o limite deles, e a marca d'água de liberação.

⚠️ ``lead_messages.lead_id`` é ``INTEGER``, não ``VARCHAR`` como no Minotto:
o ``leads.id`` desta base é serial. ``ON DELETE CASCADE`` porque mensagem sem
lead não é dado, é lixo.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5d29b71ac04"
down_revision: str | None = "c4e81a06b52f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("canal", sa.String(length=10), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("assunto", sa.Text(), nullable=True),
        sa.Column("gerado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_messages_lead_id", "lead_messages", ["lead_id"])
    op.create_index("ix_lead_messages_gerado_em", "lead_messages", ["gerado_em"])

    op.add_column(
        "leads",
        sa.Column("insights_ia", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("leads", sa.Column("insights_gerado_em", sa.DateTime(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "insights_geracoes_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "ia_limite_resetado_em",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "ia_limite_resetado_em")
    op.drop_column("leads", "insights_geracoes_count")
    op.drop_column("leads", "insights_gerado_em")
    op.drop_column("leads", "insights_ia")
    op.drop_index("ix_lead_messages_gerado_em", table_name="lead_messages")
    op.drop_index("ix_lead_messages_lead_id", table_name="lead_messages")
    op.drop_table("lead_messages")
