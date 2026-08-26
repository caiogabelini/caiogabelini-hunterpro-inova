"""cria tabela users

Autenticação da Fase 8a. Sem `dashboard_premissas` (existe no User do
Minotto) — o Dashboard é Fase 8b, e coluna que ninguém lê é dívida.

`ativo` e `role` levam server_default porque a tabela pode receber linha por
caminho que não passa pelo default do Python (ex.: INSERT direto no psql pra
criar o primeiro admin). Sem ele, um INSERT sem essas colunas falharia com
NOT NULL — e este é o caminho de recuperação quando o script não roda.

Revision ID: 5ed1d51dfed9
Revises: 1cedde529e55
Create Date: 2026-08-26 09:17:26.951955

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '5ed1d51dfed9'
down_revision: str | None = '1cedde529e55'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('senha_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False, server_default='client'),
    sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
