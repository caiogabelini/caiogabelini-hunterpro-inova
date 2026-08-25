"""Parser de Dados Abertos do CNPJ, contra os arquivos REAIS da Receita.

Os arquivos moram no projeto irmão (Minotto) e são lidos **por caminho, sem
cópia** — ``Estabelecimentos1.zip`` sozinho tem 342 MB comprimido e 1,08 GB
descomprimido. Quando não estiverem lá, os testes pulam com motivo claro.

Ler o começo de um ``.zip`` não exige varrê-lo inteiro, então quase tudo aqui
roda em menos de um segundo. Só ``TestArquivoCompleto`` faz a varredura toda,
e está marcado como ``integracao``.
"""

from __future__ import annotations

import zipfile
from itertools import islice
from pathlib import Path

import pytest

from app.services.arquivo_utils import ArquivoZipInvalidoError
from app.services.receita_federal import (
    CNAE_DESCRICOES,
    CNAES_AGRO_TODOS,
    CNAES_AGROINDUSTRIA,
    CNAES_APOIO_AGRICULTURA,
    CNAES_GRAOS,
    ESTABELECIMENTOS_COLUNAS,
    EMPRESAS_COLUNAS,
    NATUREZA_JURIDICA_COOPERATIVA,
    SITUACAO_ATIVA,
    SITUACOES_CADASTRAIS,
    EstabelecimentoRFB,
    buscar_semente_cnpj,
    carregar_municipios,
    encontrar_empresas,
    encontrar_estabelecimentos,
    encontrar_municipios,
    filtrar_estabelecimentos,
    iter_estabelecimentos,
)
from tests.conftest import DIR_RFB, exige_arquivos_rfb

EST = DIR_RFB / "Estabelecimentos1.zip"
CABECA = 20_000  # linhas do começo do arquivo real — suficiente e rápido

#: Linhas reais recortadas do começo do arquivo pra os testes de FILTRO.
#: Sem esse limite, um filtro que não casa com nada varre os 4,7 milhões de
#: linhas inteiros — 2 minutos por teste.
LINHAS_AMOSTRA = 400_000


def primeiras_linhas(caminho: Path, n: int = CABECA):
    return list(islice(iter_estabelecimentos(caminho), n))


@pytest.fixture(scope="session")
def amostra_est(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Recorte REAL do começo do arquivo, em arquivo temporário.

    Bytes originais da Receita, só truncados — não é dado sintético. Fica em
    ``tmp_path`` e some no fim da sessão: o arquivo de origem tem 1,08 GB e
    não é copiado pra dentro deste projeto.
    """
    from app.services.arquivo_utils import abrir_texto

    destino = tmp_path_factory.mktemp("rfb") / "K3241.K03200Y1.D60808.ESTABELE"
    with abrir_texto(EST) as origem, open(
        destino, "w", encoding="latin-1", newline=""
    ) as saida:
        for linha in islice(origem, LINHAS_AMOSTRA):
            saida.write(linha)
    return destino


@exige_arquivos_rfb
class TestLayoutReal:
    """30 colunas, SEM cabeçalho, ``;``, aspas duplas, latin-1."""

    def test_o_arquivo_nao_tem_linha_de_cabecalho(self) -> None:
        """A 1ª linha já é DADO. Tratá-la como cabeçalho perderia um CNPJ."""
        primeira = next(iter_estabelecimentos(EST))
        assert primeira["cnpj_basico"].isdigit()
        assert primeira["cnpj_basico"] != "cnpj_basico"

    def test_todas_as_colunas_do_layout_aparecem(self) -> None:
        primeira = next(iter_estabelecimentos(EST))
        assert set(primeira) == set(ESTABELECIMENTOS_COLUNAS)
        assert len(ESTABELECIMENTOS_COLUNAS) == 30

    def test_aspas_duplas_sao_removidas(self) -> None:
        """Os campos vêm entre aspas no arquivo; não podem sobrar no valor."""
        for linha in primeiras_linhas(EST, 2_000):
            assert not linha["cnpj_basico"].startswith('"')
            assert not linha["uf"].endswith('"')

    def test_latin1_preserva_acento_nos_campos_que_tem_acento(self) -> None:
        """O acento vive nos campos de ENDEREÇO, não no nome.

        Medido em 20 mil linhas reais: 189 caracteres não-ASCII, dos quais
        157 em ``bairro`` e 22 em ``tipo_logradouro``. Se este teste falhar,
        o encoding foi lido errado (latin-1 virou utf-8 ou cp1252).
        """
        acentos = "ÁÀÂÃÇÉÊÍÓÔÕÚÜáàâãçéêíóôõúü"
        bairros = [l["bairro"] for l in primeiras_linhas(EST)]
        assert any(any(c in b for c in acentos) for b in bairros)

    def test_nome_fantasia_vem_SEM_acento_no_arquivo_real(self) -> None:
        """Achado do dado real, não bug: a Receita normaliza o nome pra ASCII.

        Zero acentos em ``nome_fantasia`` em 20 mil linhas — e o mesmo vale
        pra ``razao_social`` no arquivo EMPRESAS ("ASSOCIACAO", "CONSTRUCAO").
        Importa pra Fase 4: nome vindo da Receita não bate string a string
        com nome vindo do Google Places, que tem acento. Cruzar os dois exige
        normalizar antes.
        """
        acentos = "ÁÀÂÃÇÉÊÍÓÔÕÚÜáàâãçéêíóôõúü"
        nomes = [l["nome_fantasia"] for l in primeiras_linhas(EST) if l["nome_fantasia"]]
        assert nomes, "nenhum nome_fantasia preenchido — amostra suspeita"
        assert not any(any(c in n for c in acentos) for n in nomes)

    def test_cnpj_montado_tem_14_digitos(self, amostra_est: Path) -> None:
        for est in islice(
            filtrar_estabelecimentos([amostra_est], cnaes=CNAES_AGRO_TODOS), 50
        ):
            assert len(est.cnpj) == 14 and est.cnpj.isdigit()
            assert est.cnpj_basico == est.cnpj[:8]


@exige_arquivos_rfb
class TestSituacaoCadastral:
    """Confirmado contra os valores REAIS, não copiado do Minotto."""

    def test_valores_reais_sao_dois_digitos_com_zero(self) -> None:
        vistos = {l["situacao_cadastral"] for l in primeiras_linhas(EST)}
        assert vistos <= set(SITUACOES_CADASTRAIS), f"código novo: {vistos}"
        assert all(len(v) == 2 for v in vistos), "o arquivo real zero-preenche"

    def test_ativa_e_o_codigo_02(self) -> None:
        assert SITUACAO_ATIVA == "02"
        assert SITUACOES_CADASTRAIS["02"] == "ATIVA"

    def test_o_filtro_de_ativos_realmente_corta(self) -> None:
        """Se este teste ver 100%, o filtro virou no-op (a lição da §6)."""
        linhas = primeiras_linhas(EST)
        ativos = [l for l in linhas if l["situacao_cadastral"] == "02"]
        assert 0 < len(ativos) < len(linhas)

    def test_apenas_ativos_false_traz_mais(self, amostra_est: Path) -> None:
        so_ativos = list(
            filtrar_estabelecimentos([amostra_est], cnaes=CNAES_AGRO_TODOS)
        )
        todos = list(
            filtrar_estabelecimentos(
                [amostra_est], cnaes=CNAES_AGRO_TODOS, apenas_ativos=False
            )
        )
        assert len(todos) > len(so_ativos)
        assert {e.situacao_cadastral for e in so_ativos} == {"02"}
        assert {e.situacao_cadastral for e in todos} > {"02"}


class TestCnaesAgro:
    """Códigos confirmados contra o CNAE oficial do IBGE/CONCLA."""

    def test_todo_cnae_tem_descricao(self) -> None:
        assert set(CNAES_AGRO_TODOS) <= set(CNAE_DESCRICOES)

    def test_todo_codigo_tem_7_digitos(self) -> None:
        """É o formato de `cnae_fiscal_principal`, não o de classe (5)."""
        for c in CNAES_AGRO_TODOS:
            assert len(c) == 7 and c.isdigit()

    def test_soja_e_milho_estao_nos_graos(self) -> None:
        assert "0115600" in CNAES_GRAOS
        assert CNAE_DESCRICOES["0115600"] == "CULTIVO DE SOJA"
        assert "0111302" in CNAES_GRAOS
        assert CNAE_DESCRICOES["0111302"] == "CULTIVO DE MILHO"

    def test_milho_nao_tem_classe_propria_so_subclasse(self) -> None:
        """0111-3 é CULTIVO DE CEREAIS; milho só existe em 0111302.

        Filtrar por classe (5 dígitos) traria arroz e trigo junto sem querer.
        """
        assert CNAE_DESCRICOES["0111301"] == "CULTIVO DE ARROZ"
        assert CNAE_DESCRICOES["0111303"] == "CULTIVO DE TRIGO"
        assert {"0111301", "0111302", "0111303"} <= CNAES_GRAOS

    def test_os_tres_presets_nao_se_sobrepoem(self) -> None:
        assert not (CNAES_GRAOS & CNAES_APOIO_AGRICULTURA)
        assert not (CNAES_GRAOS & CNAES_AGROINDUSTRIA)
        assert not (CNAES_APOIO_AGRICULTURA & CNAES_AGROINDUSTRIA)

    def test_todos_e_a_uniao_dos_tres(self) -> None:
        assert CNAES_AGRO_TODOS == (
            CNAES_GRAOS | CNAES_APOIO_AGRICULTURA | CNAES_AGROINDUSTRIA
        )

    def test_cooperativa_nao_e_cnae_e_sim_natureza_juridica(self) -> None:
        """A CNAE classifica ATIVIDADE, não forma jurídica."""
        assert NATUREZA_JURIDICA_COOPERATIVA == "2143"
        assert not any(
            "COOPERATIV" in d.upper() for d in CNAE_DESCRICOES.values()
        )


@exige_arquivos_rfb
class TestFiltroContraDadoReal:
    def test_so_traz_cnae_pedido(self, amostra_est: Path) -> None:
        achados = list(filtrar_estabelecimentos([amostra_est], cnaes=CNAES_GRAOS))
        assert achados
        assert {e.cnae_fiscal_principal for e in achados} <= CNAES_GRAOS

    def test_filtro_de_uf(self, amostra_est: Path) -> None:
        achados = list(
            filtrar_estabelecimentos(
                [amostra_est], cnaes=CNAES_AGRO_TODOS, ufs={"PR"}
            )
        )
        assert achados
        assert {e.uf for e in achados} == {"PR"}

    def test_filtro_por_regiao_sul(self, amostra_est: Path) -> None:
        achados = list(
            filtrar_estabelecimentos(
                [amostra_est], cnaes=CNAES_AGRO_TODOS, regiao="sul"
            )
        )
        assert achados
        assert {e.uf for e in achados} <= {"PR", "RS", "SC"}

    def test_cnae_inexistente_nao_traz_nada(self, amostra_est: Path) -> None:
        assert list(filtrar_estabelecimentos([amostra_est], cnaes={"9999999"})) == []

    def test_descricao_do_cnae_chega_no_resultado(self, amostra_est: Path) -> None:
        est = next(iter(filtrar_estabelecimentos([amostra_est], cnaes={"0115600"})))
        assert est.cnae_descricao == "CULTIVO DE SOJA"
        assert est.situacao_descricao == "ATIVA"


@exige_arquivos_rfb
class TestFinderPlural:
    """A Receita fatia o arquivo em 10 — o finder tem que ser plural."""

    def test_acha_estabelecimentos(self) -> None:
        achados = encontrar_estabelecimentos(DIR_RFB)
        assert [p.name for p in achados] == ["Estabelecimentos1.zip"]

    def test_acha_empresas(self) -> None:
        assert [p.name for p in encontrar_empresas(DIR_RFB)] == ["Empresas1.zip"]

    def test_acha_municipios(self) -> None:
        assert [p.name for p in encontrar_municipios(DIR_RFB)] == ["Municipios.zip"]

    def test_nao_confunde_empresas_com_estabelecimentos(self) -> None:
        """`Empresas1.zip` não pode casar com o prefixo `Estabelecimentos`."""
        assert encontrar_empresas(DIR_RFB) != encontrar_estabelecimentos(DIR_RFB)

    def test_diretorio_inexistente_devolve_lista_vazia(self) -> None:
        assert encontrar_estabelecimentos(Path("/nao/existe")) == []

    def test_acha_zip_capitalizado_e_csv_maiusculo(self, tmp_path: Path) -> None:
        """O bug real do Minotto: glob case-sensitive ignorava o .zip calado."""
        (tmp_path / "Estabelecimentos0.zip").write_bytes(b"")
        (tmp_path / "K3241.K03200Y7.D60808.ESTABELE").write_bytes(b"")
        achados = [p.name for p in encontrar_estabelecimentos(tmp_path)]
        assert len(achados) == 2, "os DOIS formatos têm que vir"

    def test_ordem_e_estavel(self, tmp_path: Path) -> None:
        for i in (3, 1, 2):
            (tmp_path / f"Estabelecimentos{i}.zip").write_bytes(b"")
        nomes = [p.name for p in encontrar_estabelecimentos(tmp_path)]
        assert nomes == sorted(nomes)


@exige_arquivos_rfb
class TestMunicipios:
    """Tabela de domínio pequena (43 KB) — carregada inteira de propósito."""

    def test_carrega_o_indice(self) -> None:
        indice = carregar_municipios(encontrar_municipios(DIR_RFB))
        assert 5_000 < len(indice) < 6_000, "o Brasil tem ~5.570 municípios"

    def test_codigo_da_receita_nao_e_o_ibge(self) -> None:
        """Código interno da RFB tem 4 dígitos; o do IBGE tem 7."""
        indice = carregar_municipios(encontrar_municipios(DIR_RFB))
        assert all(len(c) <= 5 for c in list(indice)[:200])

    def test_arquivo_ausente_devolve_indice_vazio_sem_levantar(self) -> None:
        assert carregar_municipios([Path("/nao/existe.zip")]) == {}


class TestNuncaLevanta:
    def test_diretorio_sem_arquivo(self, tmp_path: Path) -> None:
        r = buscar_semente_cnpj(tmp_path)
        assert r.estabelecimentos == () and not r.ok
        assert r.etapas_puladas[0]["etapa"] == "rfb_estabelecimentos"

    def test_zip_corrompido_vira_etapa_pulada(self, tmp_path: Path) -> None:
        (tmp_path / "Estabelecimentos0.zip").write_bytes(b"nao eh zip")
        r = buscar_semente_cnpj(tmp_path)
        assert r.estabelecimentos == ()
        assert any(e["etapa"] == "rfb_estabelecimentos" for e in r.etapas_puladas)

    def test_zip_com_dois_membros_e_recusado(self, tmp_path: Path) -> None:
        """Adivinhar o membro processaria os dados errados EM SILÊNCIO."""
        alvo = tmp_path / "Estabelecimentos0.zip"
        with zipfile.ZipFile(alvo, "w") as z:
            z.writestr("a.csv", "1;2;3")
            z.writestr("b.csv", "4;5;6")
        r = buscar_semente_cnpj(tmp_path)
        assert r.estabelecimentos == ()
        motivo = r.etapas_puladas[0]["motivo"]
        assert "2 arquivos dentro" in motivo

    def test_linha_truncada_e_pulada_sem_derrubar_o_resto(self, tmp_path: Path) -> None:
        completa = ";".join(
            ["12345678", "0001", "95", "1", "FAZENDA X", "02", "", "", "", "",
             "20200101", "0115600", "", "", "", "", "", "", "", "PR", "7107",
             "", "", "", "", "", "", "", "", ""]
        )
        alvo = tmp_path / "K3241.K03200Y0.D60808.ESTABELE"
        alvo.write_text(f"linha;truncada\n{completa}\n", encoding="latin-1")
        r = buscar_semente_cnpj(tmp_path, cnaes={"0115600"}, resolver_municipio=False)
        assert len(r.estabelecimentos) == 1
        assert r.estabelecimentos[0].cnpj == "12345678000195"

    def test_sem_empresas_avisa_e_segue(self, tmp_path: Path) -> None:
        completa = ";".join(
            ["12345678", "0001", "95", "1", "FAZENDA X", "02", "", "", "", "",
             "20200101", "0115600", "", "", "", "", "", "", "", "PR", "7107",
             "", "", "", "", "", "", "", "", ""]
        )
        (tmp_path / "K3241.K03200Y0.D60808.ESTABELE").write_text(
            completa + "\n", encoding="latin-1"
        )
        r = buscar_semente_cnpj(tmp_path, cnaes={"0115600"})
        assert len(r.estabelecimentos) == 1
        assert r.estabelecimentos[0].razao_social == ""
        assert {e["etapa"] for e in r.etapas_puladas} == {"rfb_empresas", "rfb_municipios"}


class TestEstabelecimentoRFB:
    def test_e_imutavel(self) -> None:
        e = EstabelecimentoRFB(
            cnpj="12345678000195", nome_fantasia="", situacao_cadastral="02",
            cnae_fiscal_principal="0115600", data_inicio_atividade="",
            municipio_codigo_rfb="7107", uf="PR", identificador_matriz_filial="1",
        )
        with pytest.raises(AttributeError):
            e.cnpj = "x"  # type: ignore[misc]

    def test_cooperativa_e_derivada_da_natureza_juridica(self) -> None:
        base = dict(
            cnpj="12345678000195", nome_fantasia="", situacao_cadastral="02",
            cnae_fiscal_principal="0115600", data_inicio_atividade="",
            municipio_codigo_rfb="7107", uf="PR", identificador_matriz_filial="1",
        )
        assert EstabelecimentoRFB(**base, natureza_juridica="2143").eh_cooperativa
        assert not EstabelecimentoRFB(**base, natureza_juridica="2062").eh_cooperativa
        assert not EstabelecimentoRFB(**base).eh_cooperativa

    def test_layout_de_empresas_tem_7_colunas(self) -> None:
        assert len(EMPRESAS_COLUNAS) == 7
        assert EMPRESAS_COLUNAS[0] == "cnpj_basico"


@pytest.fixture(scope="session")
def semente_rfb():
    """Varredura completa da fatia real. ~2min — uma vez por sessão."""
    return buscar_semente_cnpj(DIR_RFB, cnaes=CNAES_AGRO_TODOS)


@pytest.mark.integracao
@exige_arquivos_rfb
class TestArquivoCompleto:
    """Varredura completa de K3241.K03200Y1.D60808.ESTABELE (~2 min).

    Números medidos contra a fatia real. Se dessincronizarem com um arquivo
    do MESMO mês, a hipótese padrão é bug no parser.
    """

    def test_varre_o_arquivo_inteiro(self, semente_rfb) -> None:
        assert semente_rfb.linhas_lidas == 4_753_435
        assert semente_rfb.arquivos_lidos == ("Estabelecimentos1.zip",)

    def test_encontra_agro_ativo(self, semente_rfb) -> None:
        assert len(semente_rfb.estabelecimentos) == 10_511

    def test_soja_e_milho_lideram(self, semente_rfb) -> None:
        from collections import Counter

        por_cnae = Counter(
            e.cnae_fiscal_principal for e in semente_rfb.estabelecimentos
        )
        assert por_cnae["0111302"] == 3_744  # milho
        assert por_cnae["0115600"] == 2_786  # soja

    def test_todos_ativos_e_no_cnae_pedido(self, semente_rfb) -> None:
        assert {e.situacao_cadastral for e in semente_rfb.estabelecimentos} == {"02"}
        assert {
            e.cnae_fiscal_principal for e in semente_rfb.estabelecimentos
        } <= CNAES_AGRO_TODOS

    def test_cooperativas_aparecem_via_natureza_juridica(self, semente_rfb) -> None:
        coops = [e for e in semente_rfb.estabelecimentos if e.eh_cooperativa]
        assert coops, "nenhuma cooperativa — o join com EMPRESAS falhou?"

    def test_razao_social_incompleta_e_avisada_nao_silenciada(self, semente_rfb) -> None:
        """Só 1 das 10 fatias de EMPRESAS está em disco — tem que avisar."""
        sem_razao = [e for e in semente_rfb.estabelecimentos if not e.razao_social]
        assert sem_razao
        assert any(e["etapa"] == "rfb_empresas" for e in semente_rfb.etapas_puladas)

    def test_municipio_resolvido_pelo_nome(self, semente_rfb) -> None:
        com_nome = [e for e in semente_rfb.estabelecimentos if e.municipio]
        assert len(com_nome) / len(semente_rfb.estabelecimentos) > 0.99
