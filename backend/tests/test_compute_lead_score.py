"""Motor de cálculo do score, sobre dict de sinais já resolvidos."""

from __future__ import annotations

import pytest

from app.scoring.compute_lead_score import (
    FRACAO_ABAIXO_DO_CORTE,
    GOOGLE_RATING_MAX,
    GOOGLE_RATING_MIN,
    REGRAS,
    TAMANHO_PROPRIEDADE_HA_MAX,
    TAMANHO_PROPRIEDADE_HA_MIN,
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
    "valor_financiado": VALOR_FINANCIADO_MIN,
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

    def test_sinais_booleanos_falsos_dao_0(self) -> None:
        """Booleano falso = 0 ponto. Continua valendo depois da calibragem."""
        falsos = {
            "decisor_identificavel": False,
            "semente_sicor_cultura": False,
            "whatsapp_ativo": False,
            "email_validado": False,
            "presenca_digital": False,
            "radar_exportacao": False,
        }
        resultado = calcular_score(falsos)
        assert resultado.score == 0
        # Falso é sinal PRESENTE que vale 0 — não pode entrar em `ausentes`.
        # Os numéricos não foram informados aqui, então esses SIM ficam
        # ausentes; é a distinção da §6 entre "não achamos" e "não medimos".
        assert not set(falsos) & set(resultado.ausentes)
        assert "tamanho_propriedade" in resultado.ausentes

    def test_sinais_NUMERICOS_no_zero_pontuam_o_piso_nao_zero(self) -> None:
        """⚠️ Mudou na calibragem: antes a rampa dava 0 no valor mínimo.

        Agora "abaixo do corte" vale ``FRACAO_ABAIXO_DO_CORTE``, porque a
        cliente disse "ainda podemos considerar". Um produtor pequeno fica
        atrás na fila, não fora dela.
        """
        resultado = calcular_score({"tamanho_propriedade": 0.0, "valor_financiado": 0.0})
        piso = (30.0 + 10.0) * FRACAO_ABAIXO_DO_CORTE
        assert resultado.pontos == pytest.approx(piso)
        assert resultado.score > 0


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
            (TAMANHO_PROPRIEDADE_HA_MIN, 30.0),   # 100 ha: já é patamar pleno
            (200.0, 30.0),
            (800.0, 30.0),
            (TAMANHO_PROPRIEDADE_HA_MAX, 30.0),   # 1.400 ha: mesmo patamar
            (5000.0, 30.0),                       # acima do máximo NÃO despenca
        ],
    )
    def test_tamanho_e_patamar_unico_na_faixa_e_acima(
        self, hectares: float, esperado: float
    ) -> None:
        """Calibrado com a cliente: 200 ha e 1.200 ha valem o MESMO.

        A rampa linear anterior era suposição nossa e ordenava a pré-seleção
        por hectare — ou seja, por porte, que não é o critério dela.
        """
        assert pontos_de("tamanho_propriedade", hectares) == pytest.approx(esperado)

    @pytest.mark.parametrize("hectares", [0.0, 50.0, 99.9])
    def test_tamanho_abaixo_do_corte_pontua_MENOS_mas_nao_zero(
        self, hectares: float
    ) -> None:
        """Ela disse "ainda podemos considerar", não "descarta".

        Zero tiraria o lead do ranking; a fração baixa o mantém atrás de quem
        está na faixa, sem eliminá-lo.
        """
        pontos = pontos_de("tamanho_propriedade", hectares)
        assert pontos == pytest.approx(30.0 * FRACAO_ABAIXO_DO_CORTE)
        assert 0 < pontos < 30.0

    @pytest.mark.parametrize(
        ("reais", "esperado"),
        [
            (VALOR_FINANCIADO_MIN, 10.0),  # R$ 100 mil: patamar pleno
            (1_000_000.0, 10.0),
            (3_000_000.0, 10.0),           # R$ 3 mi não vale mais que 100 mil
            (50_000_000.0, 10.0),
        ],
    )
    def test_valor_e_patamar_unico_a_partir_de_100_mil(
        self, reais: float, esperado: float
    ) -> None:
        assert pontos_de("valor_financiado", reais) == pytest.approx(esperado)

    @pytest.mark.parametrize("reais", [0.0, 50_000.0, 99_999.0])
    def test_valor_abaixo_do_corte_pontua_menos_mas_nao_zero(
        self, reais: float
    ) -> None:
        pontos = pontos_de("valor_financiado", reais)
        assert pontos == pytest.approx(10.0 * FRACAO_ABAIXO_DO_CORTE)
        assert 0 < pontos < 10.0

    def test_nenhuma_das_duas_reguas_escala_por_tamanho(self) -> None:
        """A prova direta de que não há mais rampa: dobrar não muda nada."""
        assert pontos_de("tamanho_propriedade", 200.0) == pontos_de(
            "tamanho_propriedade", 400.0
        )
        assert pontos_de("valor_financiado", 200_000.0) == pontos_de(
            "valor_financiado", 400_000.0
        )

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
                "tamanho_propriedade": 775.0,  # 30 — patamar pleno (era 15 na rampa)
                "decisor_identificavel": True,  # 20
                "whatsapp_ativo": True,  # 15
            }
        )
        assert resultado.score == 65
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
