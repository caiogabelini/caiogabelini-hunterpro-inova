"""Infra de leitura de arquivo em lote, exercitada contra os arquivos REAIS.

Ler o cabeçalho de um `.gz` não exige varrer o arquivo, então estes testes
são rápidos mesmo com os arquivos de 200–480 MB em disco.
"""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pytest

from app.services.arquivo_utils import (
    abrir_texto,
    decimal_ou_none,
    encontrar_arquivo,
    indices_de,
    leitor_csv,
    normalizar_cabecalho,
    texto_ou_none,
)
from tests.conftest import DIR_SICOR, exige_arquivos_sicor


class TestCabecalhoComCerquilha:
    """O cabeçalho do Sicor começa com `#`. Tratar isso errado é silencioso."""

    def test_remove_a_cerquilha_so_da_primeira_coluna(self) -> None:
        assert normalizar_cabecalho(["#REF_BACEN", "NU_ORDEM", "CD_CAR"]) == [
            "REF_BACEN",
            "NU_ORDEM",
            "CD_CAR",
        ]

    def test_nao_mexe_em_cabecalho_sem_cerquilha(self) -> None:
        assert normalizar_cabecalho(["A", "B"]) == ["A", "B"]

    def test_apara_espaco(self) -> None:
        assert normalizar_cabecalho(["# A ", " B "]) == ["A", "B"]

    def test_cabecalho_vazio_nao_levanta(self) -> None:
        assert normalizar_cabecalho([]) == []

    @exige_arquivos_sicor
    def test_no_arquivo_real_o_cabecalho_alinha_com_os_dados(self) -> None:
        """A prova de que o `#` foi tratado sem destruir a linha de cabeçalho.

        Se alguém trocar isto por um leitor com `comment='#'`, o cabeçalho
        inteiro vira comentário: a 1ª linha de DADOS passa a ser lida como
        cabeçalho e todas as colunas deslocam. Este teste pega isso porque
        confere o nome esperado E o alinhamento com a primeira linha de dados.
        """
        with leitor_csv(DIR_SICOR / "SICOR_PROPRIEDADES.gz") as (cabecalho, linhas):
            assert cabecalho == [
                "REF_BACEN",
                "NU_ORDEM",
                "CD_CNPJ_CPF",
                "CD_SNCR",
                "CD_CIB",
                "CD_CAR",
            ]
            assert not cabecalho[0].startswith("#")
            assert len(next(linhas)) == len(cabecalho)

    @exige_arquivos_sicor
    def test_operacao_real_tem_as_47_colunas(self) -> None:
        with leitor_csv(DIR_SICOR / "SICOR_OPERACAO_BASICA_ESTADO_2026.gz") as (
            cabecalho,
            linhas,
        ):
            assert len(cabecalho) == 47
            assert cabecalho[0] == "REF_BACEN"
            assert "VL_AREA_INFORMADA" in cabecalho
            assert len(next(linhas)) == 47


class TestEncontrarArquivo:
    """Case-sensitivity e padrão de nome incompleto: dois bugs reais no Minotto."""

    @exige_arquivos_sicor
    @pytest.mark.parametrize(
        "padrao",
        ["SICOR_MUTUARIOS", "sicor_mutuarios", "Sicor_Mutuarios", "SICOR_MUTUARIOS.gz"],
    )
    def test_acha_ignorando_caixa(self, padrao: str) -> None:
        achado = encontrar_arquivo(DIR_SICOR, padrao)
        assert achado is not None and achado.name == "SICOR_MUTUARIOS.gz"

    @exige_arquivos_sicor
    def test_acha_csv_puro_tambem(self) -> None:
        achado = encontrar_arquivo(DIR_SICOR, "Empreendimento.csv")
        assert achado is not None and achado.name == "Empreendimento.csv"

    def test_diretorio_inexistente_devolve_none_sem_levantar(self) -> None:
        assert encontrar_arquivo(Path("/nao/existe"), "qualquer") is None

    @exige_arquivos_sicor
    def test_padrao_sem_correspondencia_devolve_none(self) -> None:
        assert encontrar_arquivo(DIR_SICOR, "PGFN_INEXISTENTE") is None


class TestSentinelas:
    """O Sicor mistura "" e "-1" como campo vazio, dependendo da coluna."""

    @pytest.mark.parametrize("valor", ["-1", "", "   ", None])
    def test_sentinela_vira_none(self, valor: str | None) -> None:
        assert texto_ou_none(valor) is None

    def test_valor_real_sobrevive(self) -> None:
        car = "PR4102208F38AE2DB05A140899725F4343E185A90"
        assert texto_ou_none(car) == car

    def test_menos_um_dentro_de_texto_maior_nao_e_sentinela(self) -> None:
        assert texto_ou_none("-12") == "-12"


class TestDecimalAmericano:
    """A fonte é brasileira, mas o decimal é PONTO. Confirmado no arquivo real."""

    def test_ponto_decimal(self) -> None:
        assert decimal_ou_none("3000.00") == 3000.0
        assert decimal_ou_none("1234.56") == pytest.approx(1234.56)

    @pytest.mark.parametrize("valor", ["-1", "", None, "abc", "1.2.3"])
    def test_ilegivel_vira_none_nunca_levanta(self, valor: str | None) -> None:
        assert decimal_ou_none(valor) is None

    def test_virgula_nao_e_interpretada_como_decimal(self) -> None:
        """Se um dia a origem virar vírgula, isto falha em vez de silenciar."""
        assert decimal_ou_none("1234,56") is None


class TestIndicesDe:
    def test_resolve_indices(self) -> None:
        assert indices_de(["A", "B", "C"], "C", "A") == (2, 0)

    def test_coluna_ausente_levanta_com_cabecalho_real_na_mensagem(self) -> None:
        """Coluna renomeada na origem tem que estourar, não virar campo vazio."""
        with pytest.raises(KeyError, match="CD_INEXISTENTE"):
            indices_de(["A", "B"], "CD_INEXISTENTE")


class TestFormatosDeArquivo:
    def test_le_gz(self, tmp_path: Path) -> None:
        alvo = tmp_path / "x.gz"
        with gzip.open(alvo, "wt", encoding="latin-1", newline="") as f:
            f.write("#A;B\n1;ção\n")
        with leitor_csv(alvo) as (cabecalho, linhas):
            assert cabecalho == ["A", "B"]
            assert next(linhas) == ["1", "ção"]

    def test_le_zip(self, tmp_path: Path) -> None:
        """Reaproveitado pelos arquivos da Receita Federal, que são .zip."""
        alvo = tmp_path / "x.zip"
        with zipfile.ZipFile(alvo, "w") as z:
            z.writestr("dados.csv", "#A;B\n1;ção\n".encode("latin-1"))
        with leitor_csv(alvo) as (cabecalho, linhas):
            assert cabecalho == ["A", "B"]
            assert next(linhas) == ["1", "ção"]

    def test_le_csv_puro(self, tmp_path: Path) -> None:
        alvo = tmp_path / "x.csv"
        alvo.write_bytes("#A;B\n1;ção\n".encode("latin-1"))
        with leitor_csv(alvo) as (cabecalho, linhas):
            assert cabecalho == ["A", "B"]

    def test_arquivo_vazio_nao_levanta(self, tmp_path: Path) -> None:
        alvo = tmp_path / "vazio.csv"
        alvo.write_text("")
        with leitor_csv(alvo) as (cabecalho, linhas):
            assert cabecalho == []
            assert list(linhas) == []

    def test_crlf_nao_gruda_no_ultimo_campo(self, tmp_path: Path) -> None:
        """tipoBeneficiario.csv é CRLF; Empreendimento.csv é LF. Os dois têm que ler."""
        alvo = tmp_path / "crlf.csv"
        alvo.write_bytes(b"#A;B\r\n1;2\r\n")
        with leitor_csv(alvo) as (cabecalho, linhas):
            assert cabecalho == ["A", "B"]
            assert next(linhas) == ["1", "2"]

    def test_encoding_latin1_preserva_acento(self, tmp_path: Path) -> None:
        alvo = tmp_path / "x.csv"
        alvo.write_bytes("#A\nAgrícola\n".encode("latin-1"))
        with abrir_texto(alvo) as f:
            assert "Agrícola" in f.read()
