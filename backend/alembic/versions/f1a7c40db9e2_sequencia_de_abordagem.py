"""Fase 11a: mensagem avulsa vira sequência de abordagem.

Revision ID: f1a7c40db9e2
Revises: e5d29b71ac04

Uma geração deixa de produzir 1 mensagem e passa a produzir uma **sequência**
(WhatsApp 3, e-mail 2). Quatro colunas em ``lead_messages``:

  - ``grupo_id``: liga as mensagens da mesma sequência
  - ``ordem``: posição dentro dela (1, 2, 3)
  - ``status``: ``pendente`` | ``enviada``
  - ``enviada_em``: quando o vendedor marcou como enviada

## ⚠️ Coluna, não tabela nova

``lead_message_groups`` teria só ``(id, lead_id, canal, gerado_em)`` — campos
que já existem na mensagem. Pagaríamos um JOIN em toda leitura por nenhum
atributo novo, e a invariante que realmente importa ("não há duas mensagens na
mesma posição da mesma sequência") sai de um ``UNIQUE (grupo_id, ordem)``, que
só existe se grupo for coluna. A justificativa longa está no docstring de
``app/models/lead_message.py``.

## ⚠️ O backfill é o ponto delicado

Já existem linhas em produção — as gerações reais do Alberto Lemuch. Elas
nascem sem grupo. Duas saídas eram possíveis:

  a) colunas anuláveis, e todo leitor trata ``grupo_id IS NULL``;
  b) backfill total: cada linha antiga vira uma sequência de UMA mensagem,
     com ``grupo_id = id`` (o próprio UUID, garantidamente único), ``ordem
     = 1``, ``status = 'pendente'``.

Escolhida a (b). O custo do backfill é pago uma vez; o do ``None`` seria pago
em cada consulta futura, e é o tipo de ramo legado que alguém esquece de
escrever seis meses depois. Como cada linha antiga vira o seu próprio grupo,
``COUNT(DISTINCT grupo_id)`` sobre o histórico existente dá exatamente o
mesmo número que o ``COUNT(*)`` de antes — **o limite de gerações por lead
não muda de valor para nenhum lead já existente**.

A ordem das operações importa: ``ADD COLUMN`` anulável → ``UPDATE`` →
``ALTER ... SET NOT NULL``. Adicionar já como ``NOT NULL`` sem default falha
contra tabela populada, e com ``server_default`` fixo daria o MESMO
``grupo_id`` a todas as linhas antigas — fundindo gerações independentes numa
sequência falsa e estourando o ``UNIQUE (grupo_id, ordem)``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a7c40db9e2"
down_revision: str | None = "e5d29b71ac04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lead_messages", sa.Column("grupo_id", sa.String(length=36), nullable=True))
    op.add_column("lead_messages", sa.Column("ordem", sa.Integer(), nullable=True))
    op.add_column("lead_messages", sa.Column("status", sa.String(length=10), nullable=True))
    op.add_column("lead_messages", sa.Column("enviada_em", sa.DateTime(), nullable=True))

    # Cada mensagem legada vira uma sequência de uma mensagem só. `grupo_id =
    # id` reaproveita um UUID que já é único — não precisa gerar outro nem
    # depender de extensão do Postgres (`gen_random_uuid()` exige pgcrypto em
    # versões antigas; `id` está garantidamente ali).
    op.execute(
        "UPDATE lead_messages "
        "SET grupo_id = id, ordem = 1, status = 'pendente' "
        "WHERE grupo_id IS NULL"
    )

    op.alter_column("lead_messages", "grupo_id", nullable=False)
    op.alter_column("lead_messages", "ordem", nullable=False)
    op.alter_column("lead_messages", "status", nullable=False)

    op.create_index("ix_lead_messages_grupo_id", "lead_messages", ["grupo_id"])
    op.create_unique_constraint(
        "uq_lead_messages_grupo_ordem", "lead_messages", ["grupo_id", "ordem"]
    )


def downgrade() -> None:
    # Reversível sem perda do que existia antes da Fase 11a: as colunas caem,
    # as mensagens ficam. O que se perde é a estrutura de sequência (ordem e
    # status), que só passou a existir aqui — um follow-up gerado depois vira
    # uma mensagem solta a mais no histórico, não some.
    op.drop_constraint("uq_lead_messages_grupo_ordem", "lead_messages", type_="unique")
    op.drop_index("ix_lead_messages_grupo_id", table_name="lead_messages")
    op.drop_column("lead_messages", "enviada_em")
    op.drop_column("lead_messages", "status")
    op.drop_column("lead_messages", "ordem")
    op.drop_column("lead_messages", "grupo_id")
