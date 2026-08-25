"""Esqueleto do motor de score (Fase 2 preenche os critérios)."""

from __future__ import annotations

from app.scoring.rules import SCORING_CRITERIA, ScoringCriterion, SignalLayer


def test_estrutura_do_criterio_existe() -> None:
    criterio = ScoringCriterion(
        key="exemplo",
        label="Exemplo",
        weight=10,
        layer=SignalLayer.ESTRUTURADO,
        source="sicor",
    )
    assert (criterio.key, criterio.weight) == ("exemplo", 10)
    assert criterio.layer is SignalLayer.ESTRUTURADO


def test_as_tres_camadas_de_sinal() -> None:
    assert {c.value for c in SignalLayer} == {
        "estruturado",
        "inferencia",
        "validacao",
    }


def test_lista_vazia_ate_os_pesos_serem_fechados_com_a_cliente() -> None:
    """Fase 1: SEM critérios — faltam 3 pesos a confirmar com a Inova.

    Este teste é um marcador, não uma verificação de comportamento: quando a
    Fase 2 preencher SCORING_CRITERIA ele falha de propósito, e quem for
    trocá-lo deve trocá-lo pelo teste real da regra de ouro
    (soma dos pesos == 100), junto com o assert no import de rules.py.
    """
    assert SCORING_CRITERIA == []
