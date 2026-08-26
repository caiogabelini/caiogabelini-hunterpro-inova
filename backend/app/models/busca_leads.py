"""Registro de execução de uma busca mensal.

Uma linha por disparo, **nunca sobrescrita** — é o histórico de quando cada
busca rodou, quanto universo ela varreu, quantos leads entraram no
enriquecimento pago e que erros individuais aconteceram sem derrubar o lote.

## Por que existe uma tabela pra isso

A busca é a única operação do sistema que **gasta dinheiro de verdade** (API
Full por CPF, Evolution, Hunter/ZeroBounce, Anthropic) e leva minutos. Sem
registro persistido, um disparo que morre no meio não deixa rastro nenhum: o
dinheiro sai e ninguém sabe o que aconteceu. Com ele, a tela admin consegue
mostrar "executando desde …" e o operador consegue distinguir "ainda rodando"
de "morreu calado".

## Contrato com o frontend

Os nomes de campo espelham ``BuscaLeadsRegistro`` em ``frontend/src/api.ts``,
que a tela de Busca já consome desde a Fase 7 — porte fiel do Minotto.

⚠️ **``total_cnpjs_encontrados`` e ``total_cnpjs_selecionados`` são nomes
herdados e imprecisos aqui.** No Minotto todo lead é PJ; na Inova ~98% do
universo é **CPF** de produtor rural. O que estes campos contam é *documento*
(CPF ou CNPJ), não CNPJ. Os nomes foram mantidos porque são o contrato já
publicado pro frontend — renomear exigiria mexer em ``api.ts`` e na tela, que
não é escopo desta fase. Registrado aqui pra que a próxima pessoa saiba que o
nome mente antes de confiar nele.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base
from app.core.tempo import agora_utc


class StatusBusca(str, Enum):
    """Os três estados possíveis, iguais aos do Minotto e aos que o frontend
    compara por igualdade (ver ``BuscaLeadsRegistro.status`` em api.ts)."""

    EXECUTANDO = "executando"
    CONCLUIDO = "concluido"
    ERRO = "erro"


STATUS_BUSCA_VALIDOS: tuple[str, ...] = tuple(s.value for s in StatusBusca)


class BuscaLeadsRegistro(Base):
    """Uma execução de busca.

    Nome da classe segue o pedido da Fase 8b (e o tipo do frontend); a
    **tabela** se chama ``buscas_leads``, como no Minotto.
    """

    __tablename__ = "buscas_leads"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    #: Quem disparou. FK pra ``users.id`` — só admin dispara (``require_admin``),
    #: mas a coluna guarda o usuário, não o papel: papel muda, autoria não.
    iniciado_por_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    iniciado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )
    #: ``None`` até terminar (com sucesso **ou** erro). Junto com ``status``, é
    #: o que o polling do frontend lê pra saber se ainda está rodando.
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=StatusBusca.EXECUTANDO.value, index=True
    )

    #: Universo bruto varrido pelas duas sementes gratuitas — ordem de milhares.
    #: ``None`` enquanto a leitura das sementes não terminou (a busca começa
    #: sem saber quanto vai achar).
    total_cnpjs_encontrados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Quantos sobreviveram à pré-seleção e **entraram no enriquecimento pago**
    #: (a cota, ~60). Fica entre `encontrados` e `processados`.
    #:
    #: Sem esta coluna o painel mostraria "2.806 encontrados / 50 processados",
    #: que se lê como 98% de falha em vez de "o corte funcionou".
    total_cnpjs_selecionados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Quantos leads foram efetivamente gravados/atualizados no banco.
    total_leads_processados: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Lista de strings legíveis — uma por etapa pulada ou lead que falhou.
    #:
    #: ⚠️ ``None`` enquanto a busca não terminou (mesmo com zero erros até
    #: agora); vira lista, ainda que vazia, só quando conclui. A distinção é a
    #: mesma de ``Lead.etapas_puladas``: "não sabemos ainda" ≠ "sabemos que
    #: não houve".
    erros: Mapped[list[Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<BuscaLeadsRegistro {self.id} {self.status}>"
