"""cria tabela leads

Migration inicial. `documento` guarda CPF (11 dígitos) ou CNPJ (14), sempre
normalizado só com dígitos, e é a chave de negócio — daí o índice ÚNICO. As
duas CheckConstraints são o backstop no banco pro invariante que o model já
garante em Python: o tipo declarado tem que bater com o comprimento.

Nenhuma coluna leva `server_default`: a tabela nasce vazia (não há linha
existente pra preencher), e NULL é o estado semanticamente correto pros
campos de pipeline — `score` NULL é "nunca avaliado", `etapas_puladas` NULL é
"pipeline nunca rodou". Ver a regra de `server_default` na seção 7 do
docs_fundacao.md.

Revision ID: 1cedde529e55
Revises: 
Create Date: 2026-08-25 07:15:39.927614

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '1cedde529e55'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('leads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento', sa.String(length=14), nullable=False),
    sa.Column('tipo_documento', sa.String(length=4), nullable=False),
    sa.Column('nome', sa.String(length=255), nullable=False),
    sa.Column('municipio', sa.String(length=120), nullable=True),
    sa.Column('uf', sa.String(length=2), nullable=True),
    sa.Column('telefone', sa.String(length=30), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('site', sa.String(length=500), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('prioridade', sa.String(length=20), nullable=True),
    sa.Column('etapas_puladas', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('dados_nicho', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('observacoes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("(tipo_documento = 'CPF' AND length(documento) = 11) OR (tipo_documento = 'CNPJ' AND length(documento) = 14)", name='ck_leads_documento_coerente_com_tipo'),
    sa.CheckConstraint("tipo_documento IN ('CPF', 'CNPJ')", name='ck_leads_tipo_documento_valido'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_leads_documento'), 'leads', ['documento'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_documento'), table_name='leads')
    op.drop_table('leads')
