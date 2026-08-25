"""Parser do Sicor, contra dado REAL.

Duas camadas:

- ``TestAmostraReal`` — roda o pipeline inteiro contra uma **amostra recortada
  dos arquivos reais** (bytes originais do Bacen, não sintéticos), pequena o
  bastante pra rodar em milissegundos. É o que roda no dia a dia.
- ``TestArquivosCompletos`` — roda contra os 3 arquivos completos
  (47 milhões de linhas, ~2min) e confere os números medidos. Marcado como
  ``integracao``: rodar com ``-m integracao``, pular com ``-m "not integracao"``.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from app.core.documentos import detectar_tipo_documento, normalizar_documento
from app.services.sicor import (
    LeadSicor,
    ResultadoSicor,
    carregar_culturas,
    extrair_leads_sicor,
)
from tests.conftest import DIR_SICOR, exige_arquivos_sicor

DIR_AMOSTRA = Path(__file__).resolve().parent / "dados_teste" / "sicor_amostra"

# Composição da amostra, fixada na hora de recortá-la a partir dos arquivos
# reais: 25 REF_BACEN identificáveis + 10 sem mutuário (o caso dos ~70%).
AMOSTRA_NO_ALVO = 35
AMOSTRA_IDENTIFICADOS = 25
AMOSTRA_SEM_MUTUARIO = 10

exige_amostra = pytest.mark.skipif(
    not (DIR_AMOSTRA / "SICOR_MUTUARIOS.gz").is_file(),
    reason=f"amostra recortada ausente em {DIR_AMOSTRA}",
)


@pytest.fixture(scope="session")
def resultado_amostra() -> ResultadoSicor:
    return extrair_leads_sicor(DIR_AMOSTRA, uf="PR", anos=[2026])


@pytest.fixture(scope="session")
def resultado() -> ResultadoSicor:
    """Parse dos 3 arquivos COMPLETOS. ~2min — roda uma vez por sessão."""
    return extrair_leads_sicor(DIR_SICOR, uf="PR", anos=[2026])


@exige_amostra
class TestAmostraReal:
    def test_contagens_batem_com_o_recorte(self, resultado_amostra: ResultadoSicor) -> None:
        assert resultado_amostra.refs_no_alvo == AMOSTRA_NO_ALVO
        assert resultado_amostra.refs_identificados == AMOSTRA_IDENTIFICADOS
        assert len(resultado_amostra.leads) == AMOSTRA_IDENTIFICADOS

    def test_ref_sem_mutuario_nao_vira_erro_nem_etapa_pulada(
        self, resultado_amostra: ResultadoSicor
    ) -> None:
        """Os ~70% sem mutuário são crédito PRIVADO — resultado normal do domínio.

        Se um dia isso virar exceção ou entrar em `etapas_puladas`, o painel
        admin vai encher de "erro" pra uma situação que é esperada.
        """
        assert resultado_amostra.refs_sem_mutuario == AMOSTRA_SEM_MUTUARIO
        assert resultado_amostra.etapas_puladas == ()

    def test_todo_lead_tem_documento_valido_pro_modelo_da_fase_1(
        self, resultado_amostra: ResultadoSicor
    ) -> None:
        """Cruza o parser com `app.core.documentos`: o dado real tem que passar.

        É o teste que prova que o zero à esquerda sobreviveu — um CPF que
        chegasse com 10 dígitos levantaria aqui.
        """
        for lead in resultado_amostra.leads:
            assert normalizar_documento(lead.documento) == lead.documento
            assert detectar_tipo_documento(lead.documento) in ("CPF", "CNPJ")

    def test_area_dentro_da_faixa_pedida(self, resultado_amostra: ResultadoSicor) -> None:
        for lead in resultado_amostra.leads:
            assert lead.area_ha is not None
            assert 150.0 <= lead.area_ha <= 1400.0

    def test_cd_car_nunca_traz_a_sentinela(self, resultado_amostra: ResultadoSicor) -> None:
        """A amostra inclui linhas REAIS de propriedades com CD_CAR='-1'."""
        for lead in resultado_amostra.leads:
            assert "-1" not in lead.codigos_car
            assert all(c and c.strip() for c in lead.codigos_car)

    def test_cd_car_real_tem_o_formato_do_arquivo(
        self, resultado_amostra: ResultadoSicor
    ) -> None:
        """41 chars: UF(2) + IBGE(5) + hash(34), concatenados, SEM hífen."""
        cars = [c for lead in resultado_amostra.leads for c in lead.codigos_car]
        assert cars, "a amostra deveria ter pelo menos um CAR"
        for car in cars:
            assert len(car) == 41
            assert car.startswith("PR")

    def test_cultura_veio_do_join_por_cd_empreendimento(
        self, resultado_amostra: ResultadoSicor
    ) -> None:
        com_cultura = [l for l in resultado_amostra.leads if l.culturas]
        assert com_cultura, "o join com Empreendimento.csv não produziu nada"

    def test_resultado_e_tipado_e_imutavel(self, resultado_amostra: ResultadoSicor) -> None:
        assert isinstance(resultado_amostra, ResultadoSicor)
        assert all(isinstance(l, LeadSicor) for l in resultado_amostra.leads)
        with pytest.raises(AttributeError):
            resultado_amostra.leads[0].documento = "x"  # type: ignore[misc]


@exige_amostra
class TestFiltros:
    def test_uf_nao_e_hardcode(self) -> None:
        """A amostra só tem PR; pedir SP tem que dar zero, não PR."""
        r = extrair_leads_sicor(DIR_AMOSTRA, uf="SP", anos=[2026])
        assert r.leads == ()
        assert any("SP" in e["motivo"] for e in r.etapas_puladas)

    def test_uf_aceita_minuscula(self, resultado_amostra: ResultadoSicor) -> None:
        r = extrair_leads_sicor(DIR_AMOSTRA, uf="pr", anos=[2026])
        assert r.refs_identificados == resultado_amostra.refs_identificados

    def test_faixa_de_area_e_parametrizavel(self) -> None:
        estreita = extrair_leads_sicor(
            DIR_AMOSTRA, uf="PR", anos=[2026], area_min_ha=150, area_max_ha=200
        )
        larga = extrair_leads_sicor(DIR_AMOSTRA, uf="PR", anos=[2026])
        assert estreita.refs_no_alvo < larga.refs_no_alvo
        for lead in estreita.leads:
            assert lead.area_ha is not None and lead.area_ha <= 200

    def test_filtro_de_cultura(self) -> None:
        r = extrair_leads_sicor(
            DIR_AMOSTRA, uf="PR", anos=[2026], culturas_alvo={"SOJA", "MILHO"}
        )
        for lead in r.leads:
            assert set(lead.culturas) & {"SOJA", "MILHO"}

    def test_incluir_car_false_pula_o_arquivo_de_27_milhoes_de_linhas(self) -> None:
        r = extrair_leads_sicor(DIR_AMOSTRA, uf="PR", anos=[2026], incluir_car=False)
        assert r.refs_identificados == AMOSTRA_IDENTIFICADOS
        assert all(lead.codigos_car == () for lead in r.leads)


class TestNuncaLevanta:
    """Toda falha vira ResultadoSicor com motivo — nunca exceção pro chamador."""

    def test_diretorio_vazio(self, tmp_path: Path) -> None:
        r = extrair_leads_sicor(tmp_path, uf="PR", anos=[2026])
        assert r.leads == () and not r.ok
        assert any(e["etapa"] == "sicor_operacao" for e in r.etapas_puladas)

    def test_diretorio_inexistente(self) -> None:
        r = extrair_leads_sicor("/nao/existe/mesmo", uf="PR", anos=[2026])
        assert r.leads == ()
        assert r.etapas_puladas

    def test_lista_de_anos_vazia(self, tmp_path: Path) -> None:
        r = extrair_leads_sicor(tmp_path, uf="PR", anos=[])
        assert r.leads == ()
        assert "nenhum ano" in r.etapas_puladas[0]["motivo"]

    @exige_amostra
    def test_multi_ano_avisa_em_vez_de_silenciar(self) -> None:
        """Fase 4 implementa; até lá o comportamento tem que ser explícito."""
        r = extrair_leads_sicor(DIR_AMOSTRA, uf="PR", anos=[2026, 2025, 2024])
        assert r.refs_identificados == AMOSTRA_IDENTIFICADOS  # processou 2026
        pulada = next(e for e in r.etapas_puladas if e["etapa"] == "sicor_multi_ano")
        assert "[2025, 2024]" in pulada["motivo"]

    @exige_amostra
    def test_empreendimento_ausente_da_lead_sem_cultura(self, tmp_path: Path) -> None:
        for nome in ("SICOR_OPERACAO_BASICA_ESTADO_2026.gz", "SICOR_MUTUARIOS.gz"):
            (tmp_path / nome).write_bytes((DIR_AMOSTRA / nome).read_bytes())
        r = extrair_leads_sicor(tmp_path, uf="PR", anos=[2026], incluir_car=False)
        assert r.refs_identificados == AMOSTRA_IDENTIFICADOS
        assert all(lead.culturas == () for lead in r.leads)
        assert any(e["etapa"] == "sicor_cultura" for e in r.etapas_puladas)

    @exige_amostra
    def test_mutuarios_ausente_nao_produz_lead(self, tmp_path: Path) -> None:
        nome = "SICOR_OPERACAO_BASICA_ESTADO_2026.gz"
        (tmp_path / nome).write_bytes((DIR_AMOSTRA / nome).read_bytes())
        r = extrair_leads_sicor(tmp_path, uf="PR", anos=[2026])
        assert r.leads == ()
        assert r.refs_no_alvo == AMOSTRA_NO_ALVO
        assert any(e["etapa"] == "sicor_mutuarios" for e in r.etapas_puladas)

    def test_coluna_renomeada_na_origem_vira_etapa_pulada(self, tmp_path: Path) -> None:
        """Se o Bacen renomear uma coluna, tem que aparecer — não silenciar."""
        alvo = tmp_path / "SICOR_OPERACAO_BASICA_ESTADO_2026.gz"
        with gzip.open(alvo, "wt", encoding="latin-1", newline="") as f:
            f.write("#REF_BACEN;CD_UF_NOVO_NOME\n1;PR\n")
        r = extrair_leads_sicor(tmp_path, uf="PR", anos=[2026])
        pulada = next(e for e in r.etapas_puladas if e["etapa"] == "sicor_operacao")
        assert "colunas ausentes" in pulada["motivo"]

    def test_arquivo_corrompido_vira_etapa_pulada(self, tmp_path: Path) -> None:
        (tmp_path / "SICOR_OPERACAO_BASICA_ESTADO_2026.gz").write_bytes(b"nao eh gzip")
        r = extrair_leads_sicor(tmp_path, uf="PR", anos=[2026])
        assert r.leads == ()
        assert any(e["etapa"] == "sicor_operacao" for e in r.etapas_puladas)


class TestSentinelaCarNoPipeline:
    """CD_CAR ausente vs "-1", os dois no caminho completo do parser.

    ⚠️ Fixture construída de propósito (formato e sentinelas reais, pareamento
    montado). Motivo: nos 552 leads identificados do arquivo real, CD_CAR vem
    **100% preenchido** — o caso "-1 num ref que virou lead" não ocorre hoje.
    O tratamento é defensivo, e defensivo só se testa construindo o caso.
    """

    @staticmethod
    def _monta(tmp_path: Path, linhas_prop: str) -> ResultadoSicor:
        with gzip.open(
            tmp_path / "SICOR_OPERACAO_BASICA_ESTADO_2026.gz",
            "wt",
            encoding="latin-1",
            newline="",
        ) as f:
            f.write(
                "#REF_BACEN;CD_ESTADO;VL_AREA_INFORMADA;VL_PARC_CREDITO;CD_EMPREENDIMENTO\n"
                "999;PR;300.00;500000.00;12016720000012\n"
            )
        with gzip.open(
            tmp_path / "SICOR_MUTUARIOS.gz", "wt", encoding="latin-1", newline=""
        ) as f:
            f.write(
                "#CD_SEXO;CD_CPF_CNPJ;CD_TIPO_BENEFICIARIO;CD_DAP;REF_BACEN;CD_PRIMEIRO\n"
                ";52998224725;1;;999;S\n"
            )
        with gzip.open(
            tmp_path / "SICOR_PROPRIEDADES.gz", "wt", encoding="latin-1", newline=""
        ) as f:
            f.write("#REF_BACEN;NU_ORDEM;CD_CNPJ_CPF;CD_SNCR;CD_CIB;CD_CAR\n")
            f.write(linhas_prop)
        return extrair_leads_sicor(tmp_path, uf="PR", anos=[2026])

    def test_menos_um_nao_vira_codigo_de_car(self, tmp_path: Path) -> None:
        r = self._monta(tmp_path, "999;1;52998224725;-1;-1;-1\n")
        assert r.leads[0].codigos_car == ()

    def test_campo_vazio_tambem_nao_vira_codigo(self, tmp_path: Path) -> None:
        r = self._monta(tmp_path, "999;1;52998224725;-1;-1;\n")
        assert r.leads[0].codigos_car == ()

    def test_codigo_real_no_meio_de_sentinelas_sobrevive(self, tmp_path: Path) -> None:
        car = "PR4102208F38AE2DB05A140899725F4343E185A90"
        r = self._monta(
            tmp_path,
            "999;1;52998224725;-1;-1;-1\n"
            f"999;2;52998224725;-1;-1;{car}\n"
            "999;3;52998224725;-1;-1;\n",
        )
        assert r.leads[0].codigos_car == (car,)

    def test_cars_repetidos_sao_deduplicados(self, tmp_path: Path) -> None:
        car = "PR4102208F38AE2DB05A140899725F4343E185A90"
        r = self._monta(tmp_path, f"999;1;x;-1;-1;{car}\n999;2;x;-1;-1;{car}\n")
        assert r.leads[0].codigos_car == (car,)


@exige_arquivos_sicor
class TestCulturas:
    def test_carrega_o_dominio_completo(self) -> None:
        culturas = carregar_culturas(DIR_SICOR / "Empreendimento.csv")
        assert len(culturas) == 3299
        assert set(culturas.values()) >= {"SOJA", "MILHO", "TRIGO"}

    def test_a_chave_do_arquivo_e_CODIGO_nao_CD_EMPREENDIMENTO(self) -> None:
        """O manual do Bacen documenta CD_EMPREENDIMENTO; o arquivo traz CODIGO."""
        culturas = carregar_culturas(DIR_SICOR / "Empreendimento.csv")
        assert culturas["12016720000012"] == "SOJA"

    def test_produto_com_virgula_nao_quebra_o_parse(self) -> None:
        """O arquivo é ;-delimitado e há PRODUTO com vírgula no nome."""
        culturas = carregar_culturas(DIR_SICOR / "Empreendimento.csv")
        com_virgula = [v for v in culturas.values() if "," in v]
        assert com_virgula


@pytest.mark.integracao
@exige_arquivos_sicor
class TestArquivosCompletos:
    """Contra os 3 arquivos completos — 47 milhões de linhas, ~2 min.

    Os números vieram da medição contra o dado real. Se algum dessincronizar,
    a hipótese padrão é **bug no parser**, não dado novo — o arquivo do Bacen
    é um snapshot estável do ano.
    """

    def test_leu_o_arquivo_de_operacao_inteiro(self, resultado: ResultadoSicor) -> None:
        assert resultado.operacoes_lidas == 1_313_316

    def test_universo_alvo_no_pr(self, resultado: ResultadoSicor) -> None:
        assert resultado.refs_no_alvo == 1_856

    def test_identificados_em_mutuarios(self, resultado: ResultadoSicor) -> None:
        assert resultado.refs_identificados == 552
        assert len(resultado.leads) == 552

    def test_taxa_de_identificacao_de_30_por_cento(self, resultado: ResultadoSicor) -> None:
        taxa = resultado.refs_identificados / resultado.refs_no_alvo
        assert taxa == pytest.approx(0.297, abs=0.001)

    def test_os_70_por_cento_restantes_sao_credito_privado(
        self, resultado: ResultadoSicor
    ) -> None:
        assert resultado.refs_sem_mutuario == 1_304
        assert resultado.etapas_puladas == ()

    def test_98_por_cento_sao_pessoa_fisica(self, resultado: ResultadoSicor) -> None:
        """Bate com o ICP da Inova: produtor rural PF é o alvo."""
        tipos = [detectar_tipo_documento(l.documento) for l in resultado.leads]
        assert tipos.count("CPF") == 541
        assert tipos.count("CNPJ") == 11
        assert tipos.count("CPF") / len(tipos) == pytest.approx(0.98, abs=0.005)

    def test_todo_documento_real_passa_na_validacao_da_fase_1(
        self, resultado: ResultadoSicor
    ) -> None:
        """552 CPF/CNPJ reais contra o validador commitado na Fase 1."""
        for lead in resultado.leads:
            assert detectar_tipo_documento(lead.documento) in ("CPF", "CNPJ")

    def test_area_valor_e_cultura_vem_preenchidos(self, resultado: ResultadoSicor) -> None:
        assert all(l.area_ha is not None for l in resultado.leads)
        assert all(l.valor_financiado is not None for l in resultado.leads)
        assert all(l.culturas for l in resultado.leads)

    def test_cd_car_preenchido_em_todos_hoje(self, resultado: ResultadoSicor) -> None:
        """100% hoje. Se cair, o tratamento de "-1" deixa de ser defensivo."""
        assert all(l.codigos_car for l in resultado.leads)
