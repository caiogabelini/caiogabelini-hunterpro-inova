"""Critérios de pontuação do lead.

Estrutura genérica, reaproveitada de cliente pra cliente (seção 3 do
docs_fundacao.md). O que muda por cliente são os *critérios* e os *pesos* —
o motor que os consome é o mesmo.

``SignalLayer`` classifica cada critério pela confiabilidade do sinal, e essa
classificação chega até a UI (dossiê), com cor por camada:

- ``ESTRUTURADO`` — dado direto de fonte oficial (Receita, Sicor, CAR, Places)
- ``INFERENCIA``  — interpretado por IA (leitura de site/redes sociais)
- ``VALIDACAO``   — confirmação de canal (WhatsApp ativo, e-mail entregável)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalLayer(str, Enum):
    ESTRUTURADO = "estruturado"
    INFERENCIA = "inferencia"
    VALIDACAO = "validacao"


@dataclass(frozen=True, slots=True)
class ScoringCriterion:
    key: str
    label: str
    weight: int
    layer: SignalLayer
    source: str


# TODO(Fase 2): a lista está vazia DE PROPÓSITO — não é esquecimento.
#
# Os pesos da Inova ainda não estão fechados: faltam 3 critérios pendentes de
# confirmação final com a cliente (Carolina). Preencher com números
# provisórios agora só criaria um score que parece calibrado e não é.
#
# Junto com os critérios, entram nesta fase — e não antes:
#   - o `assert sum(c.weight for c in SCORING_CRITERIA) == 100` no import do
#     módulo, que quebra a build se alguém desbalancear sem querer (é a
#     "regra de ouro" da seção 3);
#   - o teste dedicado que cobre a mesma soma.
# O assert NÃO está aqui hoje porque uma lista vazia soma 0 e ele falharia no
# import, derrubando a aplicação inteira por um estado que é esperado agora.
SCORING_CRITERIA: list[ScoringCriterion] = []
