"""Motor de cálculo do score, sobre dict de sinais já resolvidos."""

from __future__ import annotations

import pytest

from app.scoring.compute_lead_score import (
    GOOGLE_RATING_MAX,
    GOOGLE_RATING_MIN,
    REGRAS,
    TAMANHO_PROPRIEDADE_HA_MAX,
    TAMANHO_PROPRIEDADE_HA_MIN,
    VALOR_FINANCIADO_MAX,
    VALOR_FINANCIADO_MIN,
    calcular_score,
)
from app.scoring.rules import CRITERIOS_POR_KEY, SCORING_CRITERIA

#: Todos os sinais no valor que satura a régua — deve dar 100.
SINAIS_MAXIMOS = {
    "tamanho_propriedade": TAMANHO_PROPRIEDADE_HA_MAX,
    "decisor_identificavel": True,
    "semente_sicor_cultura": True,
    "whatsapp_ativo": True,
    "valor_financiado": VALOR_FINANCIADO_MAX,
    "email_validado": True,
    "presenca_digital": True,
    "radar_exportacao": True,
    "google_rating": GOOGLE_RATING_MAX,
}


def pontos_de(key: str, valor: object) -> float:
    return calcular_score({key: valor}).por_key(key).pontos


class TestCoberturaDasReguas:
    def test_toda_regra_tem_criterio_e_vice_versa(self) -> None:
        """Critério sem régua pontuaria 0 pra sempre, em silêncio."""
        assert set(REGRAS) == set(CRITERIOS_POR_KEY)

    def test_toda_regra_devolve_fracao_entre_0_e_1(self) -> None:
        absurdos = [None, True, False, 0, -1, 10**9, "", "x", -(10**9), 3.5]
        for key, regra in REGRAS.items():
            for valor in absurdos:
                assert 0.0 <= regra(valor) <= 1.0, f"{key} estourou com {valor!r}"


class TestExtremos:
    def test_todos_os_sinais_no_maximo_dao_100(self) -> None:
        assert calcular_score(SINAIS_MAXIMOS).score == 100

    def test_dict_vazio_da_0_sem_levantar(self) -> None:
        resultado = calcular_score({})
        assert resultado.score == 0
        assert len(resultado.ausentes) == len(SCORING_CRITERIA)

    def test_none_como_argumento_da_0(self) -> None:
        assert calcular_score(None).score == 0

    def test_todos_os_sinais_falsos_dao_0(self) -> None:
        falsos = {
            "tamanho_propriedade": 0,
            "decisor_identificavel": False,
            "semente_sicor_cultura": False,
            "whatsapp_ativo": False,
            "valor_financiado": 0,
            "email_validado": False,
            "presenca_digital": False,
            "radar_exportacao": False,
            "google_rating": 0.0,
        }
        resultado = calcular_score(falsos)
        assert resultado.score == 0
        assert resultado.ausentes == (), "falso é sinal PRESENTE que vale 0"


class TestCriteriosIndividuais:
    @pytest.mark.parametrize(
        ("key", "peso"),
        [
            ("decisor_identificavel", 20),
            ("semente_sicor_cultura", 15),
            ("whatsapp_ativo", 15),
            ("email_validado", 5),
        ],
    )
    def test_criterio_booleano_e_tudo_ou_nada(self, key: str, peso: int) -> None:
        assert pontos_de(key, True) == peso
        assert pontos_de(key, False) == 0.0

    def test_decisor_aceita_o_nome_vindo_da_receita(self) -> None:
        """A Fase 3 pode devolver o nome do decisor, não um booleano."""
        assert pontos_de("decisor_identificavel", "Carlos Mendes") == 20
        assert pontos_de("decisor_identificavel", "   ") == 0.0

    @pytest.mark.parametrize(
        ("hectares", "esperado"),
        [
            (0, 0.0),
            (100, 0.0),
            (TAMANHO_PROPRIEDADE_HA_MIN, 0.0),
            (775.0, 15.0),  # meio exato da rampa 150–1400 → metade dos 30
            (TAMANHO_PROPRIEDADE_HA_MAX, 30.0),
            (5000, 30.0),  # satura, não estoura o peso
        ],
    )
    def test_tamanho_propriedade_na_rampa_placeholder(
        self, hectares: float, esperado: float
    ) -> None:
        """⚠️ PLACEHOLDER — a régua real ainda vai ser confirmada com a Carolina.

        Estes números travam o comportamento *atual* (rampa linear 150–1400),
        não uma regra aprovada. Quando ela definir a curva de verdade, este
        teste muda junto — é esperado.
        """
        assert pontos_de("tamanho_propriedade", hectares) == pytest.approx(esperado)

    @pytest.mark.parametrize(
        ("reais", "esperado"),
        [
            (0, 0.0),
            (VALOR_FINANCIADO_MIN, 0.0),
            (1_550_000.0, 5.0),  # meio da rampa → metade dos 10
            (VALOR_FINANCIADO_MAX, 10.0),
            (50_000_000, 10.0),
        ],
    )
    def test_valor_financiado_na_rampa_placeholder(
        self, reais: float, esperado: float
    ) -> None:
        """⚠️ PLACEHOLDER — faixa não veio da cliente. Ver TODO no módulo."""
        assert pontos_de("valor_financiado", reais) == pytest.approx(esperado)

    def test_presenca_digital_aceita_booleano_ou_intensidade(self) -> None:
        """Camada INFERENCIA: a IA pode devolver algo mais rico que sim/não."""
        assert pontos_de("presenca_digital", True) == 5
        assert pontos_de("presenca_digital", False) == 0.0
        assert pontos_de("presenca_digital", 0.5) == pytest.approx(2.5)
        assert pontos_de("presenca_digital", 2.0) == 5, "satura em 1.0"

    def test_valor_de_tipo_inesperado_nao_levanta(self) -> None:
        """Sinal sujo vale 0, nunca derruba o cálculo do lead."""
        assert pontos_de("tamanho_propriedade", "trezentos hectares") == 0.0
        assert pontos_de("valor_financiado", {"bruto": 1}) == 0.0


class TestCriteriosDePesoZero:
    def test_avaliados_mas_sem_pontuar(self) -> None:
        """A fração aparece pro dossiê exibir o sinal; os pontos são 0."""
        resultado = calcular_score({"radar_exportacao": True, "google_rating": 5.0})
        radar = resultado.por_key("radar_exportacao")
        assert (radar.presente, radar.fracao, radar.pontos) == (True, 1.0, 0.0)
        google = resultado.por_key("google_rating")
        assert (google.presente, google.fracao, google.pontos) == (True, 1.0, 0.0)
        assert resultado.score == 0

    def test_regua_do_google_ja_existe_caso_o_peso_volte(self) -> None:
        assert REGRAS["google_rating"](GOOGLE_RATING_MIN) == 0.0
        assert REGRAS["google_rating"](GOOGLE_RATING_MAX) == 1.0
        assert REGRAS["google_rating"](4.0) == pytest.approx(0.5)


class TestSinalAusente:
    def test_criterio_sem_sinal_vale_0_e_nao_levanta(self) -> None:
        resultado = calcular_score({"whatsapp_ativo": True})
        assert resultado.score == 15
        tamanho = resultado.por_key("tamanho_propriedade")
        assert (tamanho.presente, tamanho.pontos) == (False, 0.0)

    def test_none_conta_como_ausente(self) -> None:
        """CAR ilegível chega como None — 0 ponto, e listado em `ausentes`."""
        resultado = calcular_score(
            {"tamanho_propriedade": None, "decisor_identificavel": True}
        )
        assert resultado.score == 20
        assert "tamanho_propriedade" in resultado.ausentes

    def test_ausente_e_diferente_de_presente_valendo_zero(self) -> None:
        """Distinção que o dossiê precisa: "não sabemos" ≠ "sabemos que não"."""
        sem_sinal = calcular_score({}).por_key("whatsapp_ativo")
        negativo = calcular_score({"whatsapp_ativo": False}).por_key("whatsapp_ativo")
        assert sem_sinal.pontos == negativo.pontos == 0.0
        assert sem_sinal.presente is False
        assert negativo.presente is True

    def test_lead_parcial_soma_so_o_que_tem(self) -> None:
        resultado = calcular_score(
            {
                "tamanho_propriedade": 775.0,  # 15 dos 30
                "decisor_identificavel": True,  # 20
                "whatsapp_ativo": True,  # 15
            }
        )
        assert resultado.score == 50
        assert set(resultado.ausentes) == {
            "semente_sicor_cultura",
            "valor_financiado",
            "email_validado",
            "presenca_digital",
            "radar_exportacao",
            "google_rating",
        }


class TestChaveDesconhecida:
    def test_chave_desconhecida_e_reportada_nao_engolida(self) -> None:
        """Erro de digitação na Fase 3 não pode virar no-op silencioso."""
        resultado = calcular_score({"whatsap_ativo": True, "whatsapp_ativo": True})
        assert resultado.ignorados == ("whatsap_ativo",)
        assert resultado.score == 15

    def test_dict_correto_nao_reporta_nada(self) -> None:
        assert calcular_score(SINAIS_MAXIMOS).ignorados == ()


class TestDetalhamentoParaODossie:
    def test_traz_uma_linha_por_criterio_na_ordem_dos_pesos(self) -> None:
        resultado = calcular_score(SINAIS_MAXIMOS)
        assert [c.key for c in resultado.criterios] == [
            c.key for c in SCORING_CRITERIA
        ]

    def test_cada_linha_carrega_camada_peso_e_procedencia(self) -> None:
        linha = calcular_score({"whatsapp_ativo": True}).por_key("whatsapp_ativo")
        assert linha.label == "WhatsApp ativo"
        assert linha.layer is CRITERIOS_POR_KEY["whatsapp_ativo"].layer
        assert linha.weight == 15
        assert linha.confirmado is False, "peso ainda em revisão com a cliente"

    def test_por_key_desconhecida_levanta(self) -> None:
        with pytest.raises(KeyError):
            calcular_score({}).por_key("nao_existe")
