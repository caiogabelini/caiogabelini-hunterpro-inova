"""Orquestração da busca mensal: sementes + pré-seleção, contra dado real.

Usa os recortes reais versionados (``tests/dados_teste/``) — bytes originais
do Bacen e da Receita, só filtrados. Rápido o bastante pro dia a dia.
``TestVolumeReal`` roda contra os arquivos completos e está marcada como
``integracao``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.scoring.pre_selecao import ORIGEM_RFB, ORIGEM_SICOR, pre_selecionar
from app.services.receita_federal import CNAES_AGRO_TODOS, buscar_semente_cnpj
from app.services.sicor import extrair_leads_sicor
from app.workers.busca import (
    ResultadoBusca,
    executar_busca_mensal,
    enriquecer_selecionados,
    verificar_fontes,
)
from tests.conftest import DIR_RFB_AMOSTRA, DIR_SICOR, exige_arquivos_sicor

DIR_SICOR_AMOSTRA = Path(__file__).resolve().parent / "dados_teste" / "sicor_amostra"

exige_amostras = pytest.mark.skipif(
    not (DIR_SICOR_AMOSTRA / "SICOR_MUTUARIOS.gz").is_file()
    or not (DIR_RFB_AMOSTRA / "K3241.K03200Y1.D60808.ESTABELE").is_file(),
    reason="recortes reais ausentes em tests/dados_teste/",
)


@pytest.fixture(scope="session")
def busca_amostra() -> ResultadoBusca:
    return executar_busca_mensal(
        dir_sicor=DIR_SICOR_AMOSTRA,
        dir_rfb=DIR_RFB_AMOSTRA,
        anos=[2025, 2026],
        cota=60,
    )


class TestTravaDeSeguranca:
    """Aborta ANTES de processar. Disciplina, não economia — ainda não há
    custo de API nesta fase, mas quando houver a trava já estará no lugar."""

    def test_sicor_ausente_aborta_com_motivo(self, tmp_path: Path) -> None:
        r = executar_busca_mensal(
            dir_sicor=tmp_path, dir_rfb=DIR_RFB_AMOSTRA, anos=[2026]
        )
        assert not r.ok
        assert "Sicor indisponível" in r.abortada_por
        assert r.selecionados == ()

    @exige_amostras
    def test_receita_ausente_aborta_com_motivo(self, tmp_path: Path) -> None:
        r = executar_busca_mensal(
            dir_sicor=DIR_SICOR_AMOSTRA, dir_rfb=tmp_path, anos=[2026]
        )
        assert not r.ok
        assert "Receita Federal indisponível" in r.abortada_por

    @exige_amostras
    def test_mutuarios_ausente_aborta_mesmo_com_operacao_presente(
        self, tmp_path: Path
    ) -> None:
        """Sem mutuários, nenhum produtor é identificável — abortar é honesto."""
        nome = "SICOR_OPERACAO_BASICA_ESTADO_2026.gz"
        (tmp_path / nome).write_bytes((DIR_SICOR_AMOSTRA / nome).read_bytes())
        r = executar_busca_mensal(
            dir_sicor=tmp_path, dir_rfb=DIR_RFB_AMOSTRA, anos=[2026]
        )
        assert "SICOR_MUTUARIOS ausente" in r.abortada_por

    @exige_amostras
    def test_ano_sem_arquivo_aborta_se_for_o_unico(self, tmp_path: Path) -> None:
        motivo = verificar_fontes(DIR_SICOR_AMOSTRA, DIR_RFB_AMOSTRA, anos=[2019])
        assert motivo is not None and "2019" in motivo

    @exige_amostras
    def test_um_ano_presente_basta_pra_passar_na_trava(self) -> None:
        """A trava exige pelo menos UM ano disponível, não todos."""
        assert verificar_fontes(
            DIR_SICOR_AMOSTRA, DIR_RFB_AMOSTRA, anos=[2019, 2026]
        ) is None

    def test_trava_roda_antes_de_qualquer_leitura(self, tmp_path: Path) -> None:
        """Aborto não pode ter lido semente nenhuma."""
        r = executar_busca_mensal(dir_sicor=tmp_path, dir_rfb=tmp_path, anos=[2026])
        assert r.leads_sicor == 0
        assert r.estabelecimentos_rfb == 0
        assert r.pre_selecao is None


@exige_amostras
class TestBuscaComRecortesReais:
    def test_le_as_duas_sementes(self, busca_amostra: ResultadoBusca) -> None:
        assert busca_amostra.ok
        assert busca_amostra.leads_sicor > 0
        assert busca_amostra.estabelecimentos_rfb > 0

    def test_fase_1_preenche_e_fase_2_nao_aciona(
        self, busca_amostra: ResultadoBusca
    ) -> None:
        """No recorte, como no dado completo, o Sicor sozinho dá conta."""
        p = busca_amostra.pre_selecao
        assert p.selecionados_fase1 == busca_amostra.leads_sicor

    def test_documentos_selecionados_sao_unicos(
        self, busca_amostra: ResultadoBusca
    ) -> None:
        docs = [c.documento for c in busca_amostra.selecionados]
        assert len(docs) == len(set(docs))

    def test_sicor_vem_antes_da_receita_na_lista(
        self, busca_amostra: ResultadoBusca
    ) -> None:
        origens = [c.origem for c in busca_amostra.selecionados]
        if ORIGEM_RFB in origens:
            assert origens.index(ORIGEM_RFB) > max(
                i for i, o in enumerate(origens) if o == ORIGEM_SICOR
            )

    def test_avisos_das_sementes_chegam_ao_resultado(
        self, busca_amostra: ResultadoBusca
    ) -> None:
        """Etapa pulada tem que chegar à tela (§6), não morrer no log."""
        etapas = {e["etapa"] for e in busca_amostra.erros}
        assert "rfb_empresas" in etapas

    def test_so_traz_estabelecimento_ativo(self) -> None:
        """O recorte tem todas as situações; a busca só pode trazer ativos."""
        rfb = buscar_semente_cnpj(DIR_RFB_AMOSTRA, cnaes=CNAES_AGRO_TODOS, ufs={"PR"})
        assert {e.situacao_cadastral for e in rfb.estabelecimentos} == {"02"}

    def test_fase2_aciona_com_cota_maior_que_a_populacao_sicor(self) -> None:
        sic = extrair_leads_sicor(DIR_SICOR_AMOSTRA, uf="PR", anos=[2025, 2026])
        rfb = buscar_semente_cnpj(DIR_RFB_AMOSTRA, cnaes=CNAES_AGRO_TODOS, ufs={"PR"})
        cota = len(sic.leads) + 10
        p = pre_selecionar(sic.leads, rfb.estabelecimentos, cota=cota)
        assert p.selecionados_fase1 == len(sic.leads)
        assert p.selecionados_fase2 == 10
        assert p.fase2_acionada

    def test_dedup_entre_populacoes_reais(self) -> None:
        """Mede a sobreposição real — hoje zero, mas a trava tem que existir."""
        sic = extrair_leads_sicor(DIR_SICOR_AMOSTRA, uf="PR", anos=[2025, 2026])
        rfb = buscar_semente_cnpj(DIR_RFB_AMOSTRA, cnaes=CNAES_AGRO_TODOS, ufs={"PR"})
        docs_sicor = {l.documento for l in sic.leads}
        cnpjs_rfb = {e.cnpj for e in rfb.estabelecimentos}
        p = pre_selecionar(sic.leads, rfb.estabelecimentos, cota=0)
        assert p.descartados_por_dedup == len(docs_sicor & cnpjs_rfb)

    def test_cota_vem_da_config_quando_nao_informada(self) -> None:
        r = executar_busca_mensal(
            dir_sicor=DIR_SICOR_AMOSTRA, dir_rfb=DIR_RFB_AMOSTRA, anos=[2026]
        )
        assert r.pre_selecao.cota == settings.cota_pre_selecao

    def test_uf_e_parametrizavel(self) -> None:
        r = executar_busca_mensal(
            dir_sicor=DIR_SICOR_AMOSTRA, dir_rfb=DIR_RFB_AMOSTRA, anos=[2026], uf="SP"
        )
        assert r.ok
        assert r.leads_sicor == 0 and r.estabelecimentos_rfb == 0


class TestParaAntesDoCusto:
    """A busca lê semente e pré-seleciona. **Não gasta.**

    Desde a Fase 5 o enriquecimento do decisor existe (``api_full`` /
    ``brasil_api``), mas continua fora de ``executar_busca_mensal``: quem
    quiser gastar chama ``enriquecer_selecionados`` explicitamente, depois
    de olhar o corte.
    """

    def test_a_busca_nao_enriquece_sozinha(self, tmp_path: Path) -> None:
        r = executar_busca_mensal(dir_sicor=tmp_path, dir_rfb=tmp_path, anos=[2026])
        assert isinstance(r, ResultadoBusca)

    @exige_amostras
    def test_busca_completa_nao_toca_em_fonte_paga(self) -> None:
        """A fixture de rede levantaria se alguma etapa saísse pra internet."""
        r = executar_busca_mensal(
            dir_sicor=DIR_SICOR_AMOSTRA, dir_rfb=DIR_RFB_AMOSTRA, anos=[2026], cota=60
        )
        assert r.ok
        assert all(not c.nome for c in r.selecionados if c.origem == ORIGEM_SICOR)

    def test_o_stub_agora_delega_em_vez_de_levantar(self) -> None:
        """Fronteira nova: lote vazio devolve lista vazia, sem gastar nada."""
        assert enriquecer_selecionados([]) == []

    def test_as_demais_etapas_pagas_continuam_fora(self) -> None:
        """Google Places, Firecrawl, WhatsApp, e-mail e presença digital
        ainda não existem — se alguém adicionar, este teste avisa."""
        import app.workers.enriquecimento as enr

        for etapa in (
            "search_google_places",
            "enrich_site_firecrawl",
            "validate_whatsapp",
            "enrich_email",
            "enrich_presenca_digital",
        ):
            assert not hasattr(enr, etapa), f"{etapa} entrou sem revisão de custo"


@pytest.fixture(scope="session")
def populacoes():
    """As duas populações completas. ~6min — uma vez por sessão."""
    sic = extrair_leads_sicor(DIR_SICOR, uf="PR", anos=[2025, 2026])
    rfb = buscar_semente_cnpj(
        Path("/home/caiogabelini/hunterpro-minotto/backend/data/receita_federal"),
        cnaes=CNAES_AGRO_TODOS,
        ufs={"PR"},
    )
    return sic.leads, rfb.estabelecimentos


@pytest.mark.integracao
@exige_arquivos_sicor
class TestVolumeReal:
    """A medição que responde "a Fase 2 chega a ser acionada?" em produção."""

    def test_populacoes_reais(self, populacoes) -> None:
        leads, estabs = populacoes
        assert len(leads) == 2_806
        assert len(estabs) == 588

    def test_a_cota_contratada_e_preenchida_so_pela_fase_1(self, populacoes) -> None:
        """A cota configurada cabe inteira na Fase 1 (2.806 disponíveis).

        ⚠️ A expectativa sai de ``settings.cota_pre_selecao``, não do número
        60 fixo que estava aqui antes. O 60 acoplava o teste ao ``.env`` da
        máquina: em 26/08/2026 a suíte quebrou porque ``LEADS_POR_BUSCA``
        estava em 3 (baixado de propósito, pra limitar o gasto da primeira
        busca paga real) — falha que não dizia nada sobre a pré-seleção.

        A invariante que importa é a mesma em qualquer cota: enquanto a Fase
        1 tiver candidatos de sobra, ela preenche tudo e a Fase 2 nem roda.
        """
        leads, estabs = populacoes
        cota = settings.cota_pre_selecao
        assert cota <= len(leads), "cota maior que a Fase 1 mudaria o cenário"
        p = pre_selecionar(leads, estabs, cota=cota)
        assert p.selecionados_fase1 == cota
        assert p.selecionados_fase2 == 0
        assert not p.fase2_acionada
        assert p.cota_preenchida

    def test_fase2_so_acionaria_acima_de_2806(self, populacoes) -> None:
        """O limiar exato. Abaixo dele a Fase 2 é código que nunca roda."""
        leads, estabs = populacoes
        assert not pre_selecionar(leads, estabs, cota=2_806).fase2_acionada
        assert pre_selecionar(leads, estabs, cota=2_807).fase2_acionada

    def test_dedup_entre_populacoes_bate_com_a_sobreposicao_real(
        self, populacoes
    ) -> None:
        """A dedup DEIXOU de ser no-op quando o piso caiu pra 100 ha.

        Com a faixa 150–1.400 ha a interseção era zero: nenhum dos 38 CNPJ
        do Sicor tinha CNAE agro na Receita. Com 100–1.400 ha o Sicor traz
        50 CNPJ, e **1 deles** também aparece na semente da Receita — a
        primeira vez que o descarte por documento efetivamente atua.

        Este teste afirma a INVARIANTE (descartados == interseção medida),
        não um número fixo: assim ele acompanha o universo em vez de
        precisar ser reescrito a cada mudança de faixa.
        """
        leads, estabs = populacoes
        documentos_sicor = {l.documento for l in leads}
        cnpjs_rfb = {e.cnpj for e in estabs}
        p = pre_selecionar(leads, estabs, cota=0)
        assert p.descartados_por_dedup == len(documentos_sicor & cnpjs_rfb)
        assert p.descartados_por_dedup >= 1, (
            "a sobreposição existe desde o piso de 100 ha — se voltou a zero, "
            "o universo mudou e vale entender por quê"
        )

    def test_pontos_parciais_ficam_abaixo_do_teto_de_55(self, populacoes) -> None:
        leads, estabs = populacoes
        p = pre_selecionar(leads, estabs, cota=settings.cota_pre_selecao)
        pontos = [c.pontos_parciais for c in p.selecionados]
        assert max(pontos) <= 55.0
        assert min(pontos) > 0
