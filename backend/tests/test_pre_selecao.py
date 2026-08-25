"""Pré-seleção em 2 fases.

Duas camadas:

- ``TestFase*`` / ``TestDedup`` — objetos construídos. A pré-seleção é lógica
  pura sobre dataclasses; construir os candidatos é a forma direta de
  exercitar cota, ordem e desempate sem depender de qual dado o Bacen
  publicou este mês.
- ``TestComDadosReais`` — recorte real do Sicor e da Receita Federal, pra
  confirmar que a lógica se comporta com o dado que existe de verdade.
"""

from __future__ import annotations

import pytest

from app.scoring.compute_lead_score import calcular_score
from app.scoring.pre_selecao import (
    ORIGEM_RFB,
    ORIGEM_SICOR,
    Candidato,
    candidato_de_estabelecimento_rfb,
    candidato_de_lead_sicor,
    ordenar_candidatos_fase2,
    pre_selecionar,
    sinais_gratuitos_sicor,
)
from app.services.receita_federal import EstabelecimentoRFB
from app.services.sicor import LeadSicor

CPF_BASE = "5299822472"  # + 1 dígito → documentos distintos e plausíveis


def lead_sicor(
    doc: str,
    *,
    area: float | None = 300.0,
    valor: float | None = 500_000.0,
    culturas: tuple[str, ...] = ("SOJA",),
    anos: tuple[int, ...] = (2026,),
) -> LeadSicor:
    return LeadSicor(
        documento=doc,
        tipo_beneficiario="1",
        area_ha=area,
        valor_financiado=valor,
        culturas=culturas,
        codigos_car=("PR41" + "0" * 37,),
        n_operacoes=1,
        refs_bacen=("999",),
        anos=anos,
    )


def estab_rfb(cnpj: str, *, coop: bool = False, matriz: str = "1") -> EstabelecimentoRFB:
    return EstabelecimentoRFB(
        cnpj=cnpj,
        nome_fantasia="AGRO EXEMPLO",
        situacao_cadastral="02",
        cnae_fiscal_principal="0115600",
        data_inicio_atividade="20200101",
        municipio_codigo_rfb="7107",
        uf="PR",
        identificador_matriz_filial=matriz,
        razao_social="AGRO EXEMPLO LTDA",
        natureza_juridica="2143" if coop else "2062",
        municipio="CASCAVEL",
    )


class TestSinaisGratuitos:
    """Só sinal que já está em disco entra — a regra de ouro da §3."""

    def test_traz_os_tres_criterios_disponiveis(self) -> None:
        sinais = sinais_gratuitos_sicor(lead_sicor("1"))
        assert set(sinais) == {
            "tamanho_propriedade",
            "valor_financiado",
            "semente_sicor_cultura",
        }

    def test_decisor_NAO_entra_na_pre_selecao(self) -> None:
        """Decisão de 25/08/2026: é BrasilAPI, 1 chamada por documento."""
        assert "decisor_identificavel" not in sinais_gratuitos_sicor(lead_sicor("1"))

    def test_nenhum_sinal_de_enriquecimento_pago_entra(self) -> None:
        sinais = sinais_gratuitos_sicor(lead_sicor("1"))
        for pago in ("whatsapp_ativo", "email_validado", "presenca_digital"):
            assert pago not in sinais

    def test_cultura_bate_quando_ha_alvo(self) -> None:
        lead = lead_sicor("1", culturas=("SOJA", "MILHO"))
        assert sinais_gratuitos_sicor(lead, culturas_alvo={"SOJA"})[
            "semente_sicor_cultura"
        ]
        assert not sinais_gratuitos_sicor(lead, culturas_alvo={"CAFE"})[
            "semente_sicor_cultura"
        ]

    def test_sem_alvo_basta_a_cultura_ser_conhecida(self) -> None:
        assert sinais_gratuitos_sicor(lead_sicor("1"))["semente_sicor_cultura"]
        assert not sinais_gratuitos_sicor(lead_sicor("1", culturas=()))[
            "semente_sicor_cultura"
        ]


class TestTetoDoScoreParcial:
    def test_sicor_chega_no_maximo_a_55_de_100(self) -> None:
        """30 (área) + 15 (cultura) + 10 (valor). O resto exige enriquecimento."""
        c = candidato_de_lead_sicor(lead_sicor("1", area=99_999, valor=99_999_999))
        assert c.pontos_parciais == pytest.approx(55.0)

    def test_receita_federal_pontua_ZERO(self) -> None:
        """Nenhum critério do score é computável de graça pra CNPJ agro.

        Não é bug: é exatamente o motivo de a Fase 2 ser fase separada em vez
        de entrar na mesma lista ordenada da Fase 1.
        """
        assert candidato_de_estabelecimento_rfb(estab_rfb("1" * 14)).pontos_parciais == 0.0

    def test_criterios_ausentes_sao_reportados(self) -> None:
        c = candidato_de_lead_sicor(lead_sicor("1"))
        assert "decisor_identificavel" in c.criterios_ausentes
        assert "whatsapp_ativo" in c.criterios_ausentes
        assert "tamanho_propriedade" not in c.criterios_ausentes


class TestFase1:
    def test_ordena_por_pontos_e_corta_na_cota(self) -> None:
        leads = [
            lead_sicor("a", area=200.0),
            lead_sicor("b", area=1400.0),
            lead_sicor("c", area=800.0),
        ]
        r = pre_selecionar(leads, [], cota=2)
        assert [c.documento for c in r.selecionados] == ["b", "c"]
        assert r.selecionados_fase1 == 2

    def test_area_maior_ganha_por_ser_o_criterio_de_peso_30(self) -> None:
        maior_area = lead_sicor("a", area=1400.0, valor=0.0)
        maior_valor = lead_sicor("b", area=150.0, valor=99_999_999.0)
        r = pre_selecionar([maior_area, maior_valor], [], cota=1)
        assert r.selecionados[0].documento == "a"

    def test_desempate_e_estavel_entre_execucoes(self) -> None:
        leads = [lead_sicor(d) for d in ("c", "a", "b")]
        primeira = [c.documento for c in pre_selecionar(leads, [], cota=3).selecionados]
        segunda = [
            c.documento
            for c in pre_selecionar(list(reversed(leads)), [], cota=3).selecionados
        ]
        assert primeira == segunda == ["a", "b", "c"]

    def test_cota_maior_que_a_populacao_leva_todos(self) -> None:
        r = pre_selecionar([lead_sicor("a"), lead_sicor("b")], [], cota=99)
        assert r.selecionados_fase1 == 2
        assert not r.cota_preenchida

    def test_cota_zero_libera_tudo(self) -> None:
        """Convenção da §5: desligar LIBERA, não bloqueia."""
        leads = [lead_sicor(str(i)) for i in range(10)]
        r = pre_selecionar(leads, [estab_rfb("9" * 14)], cota=0)
        assert r.selecionados_fase1 == 10
        assert r.selecionados_fase2 == 1

    def test_populacao_vazia_nao_levanta(self) -> None:
        r = pre_selecionar([], [], cota=60)
        assert r.selecionados == ()
        assert not r.fase2_acionada


class TestFase2:
    def test_nao_aciona_se_a_fase_1_preencheu(self) -> None:
        leads = [lead_sicor(str(i)) for i in range(60)]
        r = pre_selecionar(leads, [estab_rfb("9" * 14)], cota=60)
        assert r.selecionados_fase1 == 60
        assert r.selecionados_fase2 == 0
        assert not r.fase2_acionada

    def test_aciona_exatamente_nas_vagas_que_sobraram(self) -> None:
        leads = [lead_sicor(str(i)) for i in range(3)]
        estabs = [estab_rfb(f"{i:014d}") for i in range(10)]
        r = pre_selecionar(leads, estabs, cota=5)
        assert r.selecionados_fase1 == 3
        assert r.selecionados_fase2 == 2
        assert r.fase2_acionada
        assert len(r.selecionados) == 5

    def test_fase1_sempre_vem_antes_na_lista(self) -> None:
        r = pre_selecionar(
            [lead_sicor("a")], [estab_rfb(f"{i:014d}") for i in range(3)], cota=4
        )
        origens = [c.origem for c in r.selecionados]
        assert origens == [ORIGEM_SICOR, ORIGEM_RFB, ORIGEM_RFB, ORIGEM_RFB]

    def test_cooperativa_vem_antes(self) -> None:
        ordenados = ordenar_candidatos_fase2(
            [
                candidato_de_estabelecimento_rfb(estab_rfb("2" * 14)),
                candidato_de_estabelecimento_rfb(estab_rfb("1" * 14, coop=True)),
            ]
        )
        assert ordenados[0].dados_nicho["eh_cooperativa"]

    def test_matriz_vem_antes_de_filial(self) -> None:
        ordenados = ordenar_candidatos_fase2(
            [
                candidato_de_estabelecimento_rfb(estab_rfb("2" * 14, matriz="2")),
                candidato_de_estabelecimento_rfb(estab_rfb("1" * 14, matriz="1")),
            ]
        )
        assert ordenados[0].dados_nicho["matriz_ou_filial"] == "1"

    def test_sem_populacao_rfb_a_fase_2_nao_quebra(self) -> None:
        r = pre_selecionar([lead_sicor("a")], [], cota=60)
        assert r.disponiveis_fase2 == 0
        assert not r.fase2_acionada


class TestDedupEntrePopulacoes:
    """A chave é o documento — a mesma do índice único de ``Lead.documento``."""

    def test_cnpj_que_veio_pelo_sicor_nao_repete_pela_receita(self) -> None:
        cnpj = "11222333000181"
        r = pre_selecionar([lead_sicor(cnpj)], [estab_rfb(cnpj)], cota=60)
        assert r.descartados_por_dedup == 1
        assert [c.documento for c in r.selecionados] == [cnpj]
        assert r.selecionados_fase2 == 0

    def test_dedup_roda_ANTES_da_fase_2_ocupar_vaga(self) -> None:
        """O descartado não pode consumir uma vaga que outro CNPJ usaria."""
        cnpj_repetido = "11222333000181"
        outro = "19012345000193"
        r = pre_selecionar(
            [lead_sicor(cnpj_repetido)],
            [estab_rfb(cnpj_repetido), estab_rfb(outro)],
            cota=2,
        )
        assert r.descartados_por_dedup == 1
        assert [c.documento for c in r.selecionados] == [cnpj_repetido, outro]

    def test_documentos_do_resultado_sao_sempre_unicos(self) -> None:
        cnpj = "11222333000181"
        r = pre_selecionar(
            [lead_sicor(cnpj), lead_sicor("52998224725")],
            [estab_rfb(cnpj), estab_rfb("19012345000193")],
            cota=60,
        )
        documentos = [c.documento for c in r.selecionados]
        assert len(documentos) == len(set(documentos))

    def test_deduplica_contra_toda_a_populacao_da_fase_1(self) -> None:
        """Quem a Fase 1 avaliou e cortou não volta pela Fase 2.

        Lá ele foi julgado com mais informação (teto de 55 pontos) e perdeu;
        readmiti-lo com 0 ponto contradiria o ranking. Na prática as duas
        leituras coincidem — se sobrou vaga pra Fase 2, a Fase 1 levou todo
        mundo —, mas a contagem de descartados fica correta em qualquer cota.
        """
        cnpj = "11222333000181"
        r = pre_selecionar(
            [lead_sicor("52998224725", area=1400.0), lead_sicor(cnpj, area=150.0)],
            [estab_rfb(cnpj), estab_rfb("19012345000193")],
            cota=1,
        )
        assert r.selecionados_fase1 == 1  # só o de maior área entrou
        assert r.descartados_por_dedup == 1  # o cortado ainda assim bloqueia
        assert r.disponiveis_fase2 == 1
        assert cnpj not in [
            c.documento for c in r.selecionados if c.origem == ORIGEM_RFB
        ]


class TestRastroDoCorte:
    def test_contadores_batem(self) -> None:
        r = pre_selecionar(
            [lead_sicor(str(i)) for i in range(3)],
            [estab_rfb(f"{i:014d}") for i in range(4)],
            cota=5,
        )
        assert r.disponiveis_fase1 == 3
        assert r.disponiveis_fase2 == 4
        assert r.selecionados_fase1 + r.selecionados_fase2 == len(r.selecionados)
        assert r.cota == 5

    def test_dados_nicho_do_sicor_carrega_o_que_o_parser_achou(self) -> None:
        c = candidato_de_lead_sicor(lead_sicor("a", anos=(2025, 2026)))
        assert c.dados_nicho["anos_credito"] == [2025, 2026]
        assert c.dados_nicho["recorrente"] is True
        assert c.dados_nicho["origem"] == ORIGEM_SICOR

    def test_dados_nicho_da_receita_carrega_cnae_e_natureza(self) -> None:
        c = candidato_de_estabelecimento_rfb(estab_rfb("1" * 14, coop=True))
        assert c.dados_nicho["cnae"] == "0115600"
        assert c.dados_nicho["cnae_descricao"] == "CULTIVO DE SOJA"
        assert c.dados_nicho["eh_cooperativa"] is True

    def test_candidato_e_imutavel(self) -> None:
        c = candidato_de_lead_sicor(lead_sicor("a"))
        with pytest.raises(AttributeError):
            c.documento = "x"  # type: ignore[misc]
