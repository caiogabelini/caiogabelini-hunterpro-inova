"""Motor de cálculo do score do lead.

## Onde esta função se encaixa

Ela opera sobre um **dict de sinais já resolvidos**, não sobre um ``Lead``
populado por serviços reais:

    calcular_score({"tamanho_propriedade": 340.5, "whatsapp_ativo": True, ...})

Isso é deliberado, e não é só consequência de o pipeline de enriquecimento
ainda não existir (Fase 3):

- **Testável sem rede e sem banco.** Nenhuma etapa paga é tocada pra exercitar
  uma régua de pontuação.
- **Separa "obter o sinal" de "pontuar o sinal".** Na ordem do pipeline
  (seção 3 do docs_fundacao.md) o score é a *última* etapa, depois da
  persistência — quem chama já tem todos os sinais em mãos. O motor não
  precisa saber de onde vieram.
- **A régua muda sem tocar em serviço.** Recalibrar com a cliente mexe só
  aqui.

Na Fase 3/4 o chamador monta o dict a partir do lead persistido + do retorno
das etapas, e grava ``ResultadoScore.score`` em ``Lead.score``.

## Contrato do dict de sinais

- **Chave ausente ou valor ``None`` → 0 ponto pro critério, nunca exceção.**
  Um lead sem CAR legível tem que pontuar, não quebrar a busca inteira.
- ⚠️ Este motor **não distingue** "o sinal não existe" de "não conseguimos
  ler o sinal" — pra ele os dois são ``None``. Essa distinção é
  responsabilidade de quem monta o dict (seção 6 do docs_fundacao.md:
  persistir um booleano de sucesso de leitura ao lado de cada fonte não
  confiável, e registrar o motivo em ``Lead.etapas_puladas``). Se a Fase 3
  jogar tudo pra ``None`` sem registrar o motivo, o dossiê vai mostrar um
  campo vazio ambíguo — que é exatamente a armadilha que o manual descreve.
- Chave desconhecida no dict é **reportada** em ``ResultadoScore.ignorados``,
  não descartada em silêncio. Um erro de digitação em ``whatsapp_ativo`` na
  Fase 3 viraria um critério de 15 pontos que nunca pontua — um no-op
  silencioso, o modo de falha mais repetido do manual.

## Como o valor bruto vira pontos

Cada critério tem uma **régua própria** (``REGRAS``), que converte o valor
bruto numa **fração de 0.0 a 1.0**; os pontos são ``weight * fracao``. Um
booleano simples (``whatsapp_ativo``) e uma faixa contínua
(``tamanho_propriedade``) não podem compartilhar a mesma regra — daí uma
função por critério, e não um ``if`` gigante.

Critérios de peso 0 (``radar_exportacao``, ``google_rating``) **continuam
sendo avaliados**: a fração aparece no detalhamento pro dossiê exibir o sinal,
só não vira ponto. Se a cliente reativar o peso, a régua já existe e já foi
testada.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.scoring.rules import (
    SCORING_CRITERIA,
    CRITERIOS_POR_KEY,
    ScoringCriterion,
    SignalLayer,
)

#: Régua de um critério: valor bruto do sinal → fração de 0.0 a 1.0.
Regra = Callable[[Any], float]


# --------------------------------------------------------------------------
# Helpers de régua
# --------------------------------------------------------------------------


def _fracao_valida(valor: float) -> float:
    """Prende a fração em [0.0, 1.0] — régua nova não estoura o peso."""
    return max(0.0, min(1.0, float(valor)))


def _booleano(valor: Any) -> float:
    """Sinal binário: presente/verdadeiro = 1.0, ausente/falso = 0.0.

    Aceita ``bool`` e também um valor "preenchido" (ex: o nome do decisor
    vindo da Receita), porque na Fase 3 nem toda etapa devolve booleano puro.
    """
    if isinstance(valor, bool):
        return 1.0 if valor else 0.0
    if isinstance(valor, (int, float)):
        return 1.0 if valor else 0.0
    if isinstance(valor, str):
        return 1.0 if valor.strip() else 0.0
    return 1.0 if valor else 0.0


def _rampa_linear(valor: Any, minimo: float, maximo: float) -> float:
    """Rampa linear: ``<= minimo`` → 0.0, ``>= maximo`` → 1.0, linear no meio."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if maximo <= minimo:  # guarda contra calibragem errada
        return 1.0 if v >= maximo else 0.0
    return _fracao_valida((v - minimo) / (maximo - minimo))


# --------------------------------------------------------------------------
# Réguas por critério
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Réguas CALIBRADAS com a cliente (Carolina) — não são mais placeholder.
# ---------------------------------------------------------------------------
#
# As duas seguem a mesma forma: **patamar único (flat)** a partir de um corte,
# e uma **fração baixa** abaixo dele. Não escalam por tamanho nem por valor.
#
# ⚠️ Por que flat, e por que isso importa: a cliente foi explícita de que um
# produtor de 200 ha e um de 1.200 ha valem o MESMO pra ela. A rampa linear
# anterior era suposição nossa, e ordenava a pré-seleção por hectare —
# efetivamente rankeando por porte, que não é o critério dela.
#
# ⚠️ Abaixo do corte NÃO é zero. Ela disse "ainda podemos considerar", não
# "descarta". Zero excluiria o lead do ranking; a fração baixa mantém ele
# atrás de quem está na faixa, sem eliminá-lo.

#: Faixa plena de tamanho_propriedade. Acima do máximo **também é patamar
#: pleno** — ela confirmou que não há teto que reduza pontuação.
TAMANHO_PROPRIEDADE_HA_MIN = 100.0
TAMANHO_PROPRIEDADE_HA_MAX = 1400.0

#: Corte de valor_financiado. R$ 100 mil já é plenamente relevante, e
#: R$ 3 milhões não vale mais que isso.
VALOR_FINANCIADO_MIN = 100_000.0

#: Fração de quem fica abaixo do corte. Não é zero de propósito (ver acima).
FRACAO_ABAIXO_DO_CORTE = 0.25

# Nota do Google: 3.0 é o piso do que conta como "boa nota", 5.0 é o teto.
# Peso 0 hoje — a régua existe pro dossiê exibir o sinal (ver docstring).
GOOGLE_RATING_MIN = 3.0
GOOGLE_RATING_MAX = 5.0


def _patamar(valor: Any, corte: float) -> float:
    """1.0 a partir de ``corte``; ``FRACAO_ABAIXO_DO_CORTE`` abaixo dele.

    Valor ilegível vira 0.0 — "não sabemos" não pode virar meio ponto.
    """
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 if v >= corte else FRACAO_ABAIXO_DO_CORTE


def _regra_tamanho_propriedade(valor: Any) -> float:
    """Patamar único de 100 ha pra cima; 0,25 abaixo disso.

    ⚠️ O máximo de 1.400 ha NÃO reduz a pontuação — está aqui só como
    documentação da faixa que a cliente descreveu. Acima dele o produtor
    pontua igual.
    """
    return _patamar(valor, TAMANHO_PROPRIEDADE_HA_MIN)


def _regra_valor_financiado(valor: Any) -> float:
    """Patamar único de R$ 100 mil pra cima; 0,25 abaixo disso."""
    return _patamar(valor, VALOR_FINANCIADO_MIN)


def _regra_google_rating(valor: Any) -> float:
    return _rampa_linear(valor, GOOGLE_RATING_MIN, GOOGLE_RATING_MAX)


def _regra_presenca_digital(valor: Any) -> float:
    """Aceita booleano ou uma intensidade 0.0–1.0 vinda da leitura por IA.

    A camada é ``INFERENCIA``: a Fase 3 pode devolver "tem site e Instagram
    ativo" como algo mais rico que um sim/não, e a régua já comporta isso sem
    precisar mudar.
    """
    if isinstance(valor, bool):
        return _booleano(valor)
    if isinstance(valor, (int, float)):
        return _fracao_valida(valor)
    return _booleano(valor)


#: Régua de cada critério, por chave. Toda chave de ``SCORING_CRITERIA``
#: precisa estar aqui — o assert no fim do módulo garante isso.
REGRAS: dict[str, Regra] = {
    "tamanho_propriedade": _regra_tamanho_propriedade,
    "decisor_identificavel": _booleano,
    "semente_sicor_cultura": _booleano,
    "whatsapp_ativo": _booleano,
    "valor_financiado": _regra_valor_financiado,
    "email_validado": _booleano,
    "presenca_digital": _regra_presenca_digital,
    "radar_exportacao": _booleano,
    "google_rating": _regra_google_rating,
}


# --------------------------------------------------------------------------
# Resultado
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PontuacaoCriterio:
    """Detalhamento de um critério — é o que o dossiê renderiza, linha a linha."""

    key: str
    label: str
    layer: SignalLayer
    weight: int
    #: ``False`` = sinal ausente ou ``None``. Distinto de fração 0.0 com sinal
    #: presente ("temos o dado, e ele não pontua").
    presente: bool
    valor_bruto: Any
    fracao: float
    pontos: float
    #: Espelha ``ScoringCriterion.confirmado`` — peso ainda em revisão.
    confirmado: bool


@dataclass(frozen=True, slots=True)
class ResultadoScore:
    """Score final + o rastro de como ele foi montado."""

    score: int
    pontos: float
    criterios: tuple[PontuacaoCriterio, ...]
    #: Critérios sem sinal no dict — 0 ponto, e visíveis pra quem for auditar.
    ausentes: tuple[str, ...]
    #: Chaves do dict que não correspondem a nenhum critério. Não vazio =
    #: alguém errou o nome de um sinal; investigar antes de confiar no score.
    ignorados: tuple[str, ...]

    def por_key(self, key: str) -> PontuacaoCriterio:
        for c in self.criterios:
            if c.key == key:
                return c
        raise KeyError(key)


def _pontuar(criterio: ScoringCriterion, sinais: Mapping[str, Any]) -> PontuacaoCriterio:
    bruto = sinais.get(criterio.key)
    presente = bruto is not None
    fracao = _fracao_valida(REGRAS[criterio.key](bruto)) if presente else 0.0
    return PontuacaoCriterio(
        key=criterio.key,
        label=criterio.label,
        layer=criterio.layer,
        weight=criterio.weight,
        presente=presente,
        valor_bruto=bruto,
        fracao=fracao,
        pontos=criterio.weight * fracao,
        confirmado=criterio.confirmado,
    )


def calcular_score(sinais: Mapping[str, Any] | None = None) -> ResultadoScore:
    """Calcula o score (0–100) a partir de um dict de sinais já resolvidos.

    Nunca levanta por sinal ausente, ``None`` ou tipo inesperado: o pior caso
    de um sinal é valer 0 ponto. Isso é o mesmo princípio do ``_rodar_etapa``
    do pipeline (seção 6) — uma fonte que falhou não pode derrubar o lead.
    """
    sinais = sinais or {}
    criterios = tuple(_pontuar(c, sinais) for c in SCORING_CRITERIA)
    pontos = sum(c.pontos for c in criterios)
    return ResultadoScore(
        score=round(pontos),
        pontos=pontos,
        criterios=criterios,
        ausentes=tuple(c.key for c in criterios if not c.presente),
        ignorados=tuple(k for k in sinais if k not in CRITERIOS_POR_KEY),
    )


# Um critério sem régua pontuaria 0 pra sempre, em silêncio — e uma régua sem
# critério é código morto que finge estar em uso. Quebra a build nos dois casos.
assert set(REGRAS) == set(CRITERIOS_POR_KEY), (
    "REGRAS e SCORING_CRITERIA divergem — "
    f"sem régua: {sorted(set(CRITERIOS_POR_KEY) - set(REGRAS))}; "
    f"régua órfã: {sorted(set(REGRAS) - set(CRITERIOS_POR_KEY))}"
)
