"""Critérios e pesos do score — Inova (pesos de 11/08/2026)."""

from __future__ import annotations

from app.scoring.rules import (
    CRITERIOS_POR_KEY,
    CRITERIOS_PROVISORIOS,
    PESO_TOTAL_ESPERADO,
    SCORING_CRITERIA,
    ScoringCriterion,
    SignalLayer,
)


class TestEstrutura:
    def test_estrutura_do_criterio(self) -> None:
        criterio = ScoringCriterion(
            key="exemplo",
            label="Exemplo",
            weight=10,
            layer=SignalLayer.ESTRUTURADO,
            source="sicor",
        )
        assert (criterio.key, criterio.weight) == ("exemplo", 10)
        assert criterio.layer is SignalLayer.ESTRUTURADO
        assert criterio.confirmado is True, "o default é 'peso confirmado'"

    def test_as_tres_camadas_de_sinal(self) -> None:
        assert {c.value for c in SignalLayer} == {
            "estruturado",
            "inferencia",
            "validacao",
        }

    def test_indice_por_key_cobre_todos(self) -> None:
        assert set(CRITERIOS_POR_KEY) == {c.key for c in SCORING_CRITERIA}
        assert len(CRITERIOS_POR_KEY) == len(SCORING_CRITERIA), "há chave duplicada"


class TestRegraDeOuro:
    def test_soma_dos_pesos_e_100(self) -> None:
        """Trava de build (seção 3 do docs_fundacao.md).

        Se este teste falhar, alguém recalibrou um peso sem compensar em
        outro. NÃO ajuste o número esperado — reequilibre os pesos.
        """
        assert sum(c.weight for c in SCORING_CRITERIA) == PESO_TOTAL_ESPERADO

    def test_nenhum_peso_negativo(self) -> None:
        assert all(c.weight >= 0 for c in SCORING_CRITERIA)

    def test_o_assert_do_import_existe_de_verdade(self) -> None:
        """A trava tem que estar no import do módulo, não só neste teste.

        Um teste sozinho só protege quem roda a suíte; o assert no import
        protege qualquer processo que carregue o módulo.
        """
        import inspect

        from app.scoring import rules

        fonte = inspect.getsource(rules)
        assert "assert sum(c.weight for c in SCORING_CRITERIA)" in fonte


class TestPesosFechadosComACliente:
    ESPERADO = {
        "tamanho_propriedade": 30,
        "decisor_identificavel": 20,
        "semente_sicor_cultura": 15,
        "whatsapp_ativo": 15,
        "valor_financiado": 10,
        "email_validado": 5,
        "presenca_digital": 5,
        "radar_exportacao": 0,
        "google_rating": 0,
    }

    def test_sao_os_nove_criterios_acordados(self) -> None:
        assert {c.key for c in SCORING_CRITERIA} == set(self.ESPERADO)

    def test_cada_peso_bate_com_a_sessao_de_11_08_2026(self) -> None:
        assert {c.key: c.weight for c in SCORING_CRITERIA} == self.ESPERADO

    def test_camada_de_cada_criterio(self) -> None:
        esperado = {
            "tamanho_propriedade": SignalLayer.ESTRUTURADO,
            "decisor_identificavel": SignalLayer.ESTRUTURADO,
            "semente_sicor_cultura": SignalLayer.ESTRUTURADO,
            "whatsapp_ativo": SignalLayer.VALIDACAO,
            "valor_financiado": SignalLayer.ESTRUTURADO,
            "email_validado": SignalLayer.VALIDACAO,
            "presenca_digital": SignalLayer.INFERENCIA,
            "radar_exportacao": SignalLayer.ESTRUTURADO,
            "google_rating": SignalLayer.ESTRUTURADO,
        }
        assert {c.key: c.layer for c in SCORING_CRITERIA} == esperado

    def test_todo_criterio_tem_fonte_documentada(self) -> None:
        assert all(c.source.strip() for c in SCORING_CRITERIA)

    def test_tamanho_da_propriedade_e_o_criterio_principal(self) -> None:
        """Critério #1 da cliente — vira a Fase 1 da pré-seleção (Fase 4)."""
        principal = max(SCORING_CRITERIA, key=lambda c: c.weight)
        assert principal.key == "tamanho_propriedade"


class TestCriteriosComPesoZero:
    """Peso 0 é decisão registrada, não sobra — apagar apagaria a decisão."""

    def test_radar_e_google_seguem_na_lista_com_peso_zero(self) -> None:
        zerados = {c.key for c in SCORING_CRITERIA if c.weight == 0}
        assert zerados == {"radar_exportacao", "google_rating"}

    def test_o_motivo_do_zero_esta_no_source(self) -> None:
        radar = CRITERIOS_POR_KEY["radar_exportacao"]
        assert "dispensado de habilitação" in radar.source
        google = CRITERIOS_POR_KEY["google_rating"]
        assert "não relevante pro perfil rural" in google.source


class TestPesosProvisorios:
    """Trava contra "peso estimado" virar "peso aprovado" por esquecimento.

    Os 4 abaixo têm a DIREÇÃO confirmada pela Carolina em áudio, mas o número
    exato é estimativa do Caio. Se este teste falhar porque alguém marcou um
    deles como confirmado, a pergunta certa é "a cliente confirmou esse
    número?" — não "como faço o teste passar?".
    """

    PROVISORIOS = {
        "semente_sicor_cultura",
        "whatsapp_ativo",
        "email_validado",
        "presenca_digital",
    }

    def test_os_quatro_provisorios_estao_marcados(self) -> None:
        marcados = {c.key for c in SCORING_CRITERIA if not c.confirmado}
        assert marcados == self.PROVISORIOS

    def test_o_resto_esta_marcado_como_confirmado(self) -> None:
        confirmados = {c.key for c in SCORING_CRITERIA if c.confirmado}
        assert confirmados == set(CRITERIOS_POR_KEY) - self.PROVISORIOS

    def test_constante_exportada_bate_com_a_lista(self) -> None:
        """É essa constante que a UI do dossiê vai ler pra sinalizar revisão."""
        assert set(CRITERIOS_PROVISORIOS) == self.PROVISORIOS

    def test_peso_em_revisao_soma_40_dos_100(self) -> None:
        """Dimensiona o risco: 40% do score ainda não foi cravado pela cliente.

        Se este número cair pra 0, a rodada de confirmação acabou — e este
        teste deve ser removido junto com o campo, não "ajustado".
        """
        em_revisao = sum(c.weight for c in SCORING_CRITERIA if not c.confirmado)
        assert em_revisao == 40
