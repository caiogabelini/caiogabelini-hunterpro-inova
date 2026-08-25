"""Critérios de pontuação do lead — Inova Contabilidade (agronegócio/grãos, PR).

Estrutura genérica, reaproveitada de cliente pra cliente (seção 3 do
docs_fundacao.md). O que muda por cliente são os *critérios* e os *pesos* —
o motor que os consome é o mesmo.

``SignalLayer`` classifica cada critério pela confiabilidade do sinal, e essa
classificação chega até a UI (dossiê), com cor por camada:

- ``ESTRUTURADO`` — dado direto de fonte oficial (Receita, Sicor, CAR, Places)
- ``INFERENCIA``  — interpretado por IA (leitura de site/redes sociais)
- ``VALIDACAO``   — confirmação de canal (WhatsApp ativo, e-mail entregável)

Regra de ouro: **a soma dos pesos é 100**, garantida pelo ``assert`` no import
deste módulo e por teste dedicado. Desbalancear quebra a build, não a
produção.

---

## Procedência dos pesos (ler antes de mexer em qualquer número)

Pesos fechados com a Carolina (Inova) em sessão de **11/08/2026**. Eles **não
têm todos o mesmo grau de confiança**, e essa diferença é deliberadamente
visível no código, via ``ScoringCriterion.confirmado``:

- ``confirmado=True``  — número dito pela cliente, literal.
- ``confirmado=False`` — a **direção** foi confirmada por ela em áudio ("isso
  pesa mais que aquilo", "isso é secundário"), mas o **número exato é
  estimativa do Caio**. Vale como ponto de partida operacional; não vale
  como "a cliente aprovou 15".

São 4 provisórios hoje: ``semente_sicor_cultura``, ``whatsapp_ativo``,
``email_validado`` e ``presenca_digital``. Eles precisam de uma rodada de
confirmação com a Carolina antes de o score ir pra tela dela — o campo existe
justamente pra a UI do dossiê poder sinalizar "peso em revisão" pro admin, e
pra um teste travar caso alguém apague a marcação numa refatoração.

**Por que um campo no dataclass e não um comentário inline:** comentário não
é verificável. A marcação precisa sobreviver a refatoração, ser lida pela UI e
ser afirmada por teste — três coisas que só um dado de verdade entrega. O
custo é um campo booleano com default; o benefício é que "peso estimado" não
vira "peso aprovado" por esquecimento.

## Critérios com peso 0

``radar_exportacao`` e ``google_rating`` continuam na lista **de propósito**,
com peso 0. Não é sobra nem esquecimento: registra que o critério foi
avaliado e descartado conscientemente, com o motivo no ``source``. Mesmo
padrão adotado no Minotto com a nota do Google. Removê-los da lista apagaria
a decisão — e o próximo a olhar a base reproporia os dois.
"""

from __future__ import annotations

from collections import Counter
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
    #: ``False`` = peso estimado pelo Caio a partir da direção confirmada em
    #: áudio pela cliente, **não** um número que ela disse. Ver o docstring
    #: do módulo. A UI do dossiê usa isso pra sinalizar "peso em revisão".
    confirmado: bool = True


SCORING_CRITERIA: list[ScoringCriterion] = [
    ScoringCriterion(
        key="tamanho_propriedade",
        label="Tamanho da propriedade rural",
        weight=30,
        layer=SignalLayer.ESTRUTURADO,
        source=(
            "Sicor (Bacen) → ponte código CAR → SICAR público. "
            "Nem sempre disponível."
        ),
    ),
    ScoringCriterion(
        key="decisor_identificavel",
        label="Decisor identificável",
        weight=20,
        layer=SignalLayer.ESTRUTURADO,
        source="Receita Federal / CPF direto",
    ),
    # PROVISÓRIO: a Carolina confirmou que a semente Sicor com cultura batendo
    # é sinal forte, logo abaixo dos dois primeiros — mas não cravou o número.
    ScoringCriterion(
        key="semente_sicor_cultura",
        label="Semente Sicor + cultura bate",
        weight=15,
        layer=SignalLayer.ESTRUTURADO,
        source="Sicor (Bacen), direto",
        confirmado=False,
    ),
    # PROVISÓRIO: direção confirmada (canal ativo importa pra abordagem dela),
    # peso estimado.
    ScoringCriterion(
        key="whatsapp_ativo",
        label="WhatsApp ativo",
        weight=15,
        layer=SignalLayer.VALIDACAO,
        source="Evolution API",
        confirmado=False,
    ),
    ScoringCriterion(
        key="valor_financiado",
        label="Valor financiado (Sicor)",
        weight=10,
        layer=SignalLayer.ESTRUTURADO,
        source="Sicor (Bacen), direto",
    ),
    # PROVISÓRIO: ela tratou e-mail como canal secundário; o 5 é estimativa.
    ScoringCriterion(
        key="email_validado",
        label="E-mail validado",
        weight=5,
        layer=SignalLayer.VALIDACAO,
        source="Hunter.io + ZeroBounce",
        confirmado=False,
    ),
    # PROVISÓRIO: idem — sinal de contexto, não de qualificação. Peso estimado.
    ScoringCriterion(
        key="presenca_digital",
        label="Presença digital (site/Instagram)",
        weight=5,
        layer=SignalLayer.INFERENCIA,
        source="Scraping + IA (site/Instagram)",
        confirmado=False,
    ),
    # Peso 0 deliberado — ver "Critérios com peso 0" no docstring do módulo.
    ScoringCriterion(
        key="radar_exportacao",
        label="Habilitação RADAR (exportação)",
        weight=0,
        layer=SignalLayer.ESTRUTURADO,
        source=(
            "Receita Federal — zerado a pedido da cliente (produtor PF é "
            "dispensado de habilitação, sinal não confiável pro perfil dela)"
        ),
    ),
    ScoringCriterion(
        key="google_rating",
        label="Boa nota no Google",
        weight=0,
        layer=SignalLayer.ESTRUTURADO,
        source="Google Places — mantido em 0, não relevante pro perfil rural",
    ),
]

PESO_TOTAL_ESPERADO = 100

# Regra de ouro (seção 3 do docs_fundacao.md): quebra a build se alguém
# desbalancear os pesos sem querer. Não era possível na Fase 1 — a lista
# estava vazia de propósito e somava 0; agora é.
assert sum(c.weight for c in SCORING_CRITERIA) == PESO_TOTAL_ESPERADO, (
    "a soma dos pesos de SCORING_CRITERIA tem que ser "
    f"{PESO_TOTAL_ESPERADO}, está "
    f"{sum(c.weight for c in SCORING_CRITERIA)}"
)

# Chave duplicada colocaria dois critérios disputando a mesma entrada do dict
# de sinais — o segundo venceria silenciosamente no índice abaixo.
assert not [k for k, n in Counter(c.key for c in SCORING_CRITERIA).items() if n > 1], (
    "há chaves duplicadas em SCORING_CRITERIA: "
    f"{[k for k, n in Counter(c.key for c in SCORING_CRITERIA).items() if n > 1]}"
)

#: Índice por chave, pro motor de score não varrer a lista a cada lead.
CRITERIOS_POR_KEY: dict[str, ScoringCriterion] = {c.key: c for c in SCORING_CRITERIA}

#: Critérios cujo peso ainda é estimativa — a UI sinaliza "peso em revisão".
CRITERIOS_PROVISORIOS: tuple[str, ...] = tuple(
    c.key for c in SCORING_CRITERIA if not c.confirmado
)
