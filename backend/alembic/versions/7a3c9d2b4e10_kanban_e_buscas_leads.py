"""Fase 8b: campos de Kanban/fechamento no lead + tabela de buscas.

Revision ID: 7a3c9d2b4e10
Revises: 5ed1d51dfed9

Duas mudanças independentes no mesmo revision porque entram na mesma fase e
não fazem sentido separadas: o Kanban só é útil se o lead tiver status, e a
tela que dispara busca só é útil se houver onde registrar a execução.

⚠️ ``kanban_status`` entra ``NOT NULL`` **com ``server_default``**. Sem o
server_default, o ALTER quebraria em qualquer banco que já tenha linhas — e o
banco local já tem leads reais e de seed. O default fica na tabela de
propósito (não só no model): ``persistir_leads`` e os scripts gravam direto
pelo SQLAlchemy, mas um INSERT manual em SQL também precisa nascer válido.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a3c9d2b4e10"
down_revision: str | None = "5ed1d51dfed9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KANBAN_STATUS_VALIDOS = (
    "novo_lead",
    "qualificacao",
    "contatado",
    "respondeu",
    "reuniao",
    "proposta_enviada",
    "negociacao",
    "ganho",
    "perdido",
)

_CHECK_KANBAN = "kanban_status IN ({})".format(
    ", ".join(f"'{v}'" for v in KANBAN_STATUS_VALIDOS)
)


def upgrade() -> None:
    # --- 1. Colunas de Kanban e fechamento no lead ------------------------
    op.add_column(
        "leads",
        sa.Column(
            "kanban_status",
            sa.String(length=20),
            nullable=False,
            server_default="novo_lead",
        ),
    )
    op.create_index("ix_leads_kanban_status", "leads", ["kanban_status"])
    op.create_check_constraint(
        "ck_leads_kanban_status_valido", "leads", _CHECK_KANBAN
    )

    op.add_column("leads", sa.Column("motivo_perda", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "servicos_vendidos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("leads", sa.Column("tipo_contrato", sa.String(length=20), nullable=True))
    op.add_column("leads", sa.Column("valor_fechamento", sa.Float(), nullable=True))

    # --- 2. Histórico de execuções de busca -------------------------------
    op.create_table(
        "buscas_leads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("iniciado_por_id", sa.String(length=36), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(), nullable=False),
        sa.Column("concluido_em", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_cnpjs_encontrados", sa.Integer(), nullable=True),
        sa.Column("total_cnpjs_selecionados", sa.Integer(), nullable=True),
        sa.Column("total_leads_processados", sa.Integer(), nullable=True),
        sa.Column("erros", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["iniciado_por_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_buscas_leads_iniciado_por_id", "buscas_leads", ["iniciado_por_id"])
    op.create_index("ix_buscas_leads_iniciado_em", "buscas_leads", ["iniciado_em"])
    op.create_index("ix_buscas_leads_status", "buscas_leads", ["status"])


def downgrade() -> None:
    op.drop_index("ix_buscas_leads_status", table_name="buscas_leads")
    op.drop_index("ix_buscas_leads_iniciado_em", table_name="buscas_leads")
    op.drop_index("ix_buscas_leads_iniciado_por_id", table_name="buscas_leads")
    op.drop_table("buscas_leads")

    op.drop_column("leads", "valor_fechamento")
    op.drop_column("leads", "tipo_contrato")
    op.drop_column("leads", "servicos_vendidos")
    op.drop_column("leads", "motivo_perda")
    op.drop_constraint("ck_leads_kanban_status_valido", "leads", type_="check")
    op.drop_index("ix_leads_kanban_status", table_name="leads")
    op.drop_column("leads", "kanban_status")
