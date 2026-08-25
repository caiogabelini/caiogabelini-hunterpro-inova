"""Validação de CPF e CNPJ — algoritmo próprio de cada formato."""

from __future__ import annotations

import pytest

from app.core.documentos import (
    TIPO_CNPJ,
    TIPO_CPF,
    detectar_tipo_documento,
    formatar_documento,
    normalizar_documento,
    validar_cnpj,
    validar_cpf,
    validar_documento,
)
from tests.conftest import CNPJ_VALIDO, CNPJ_VALIDO_2, CPF_VALIDO, CPF_VALIDO_2


class TestNormalizacao:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("529.982.247-25", CPF_VALIDO),
            ("11.222.333/0001-81", CNPJ_VALIDO),
            ("  52998224725  ", CPF_VALIDO),
            ("11222333000181", CNPJ_VALIDO),
        ],
    )
    def test_remove_mascara_e_espaco(self, entrada: str, esperado: str) -> None:
        assert normalizar_documento(entrada) == esperado

    def test_none_e_erro(self) -> None:
        with pytest.raises(ValueError):
            normalizar_documento(None)


class TestCPF:
    @pytest.mark.parametrize("cpf", [CPF_VALIDO, CPF_VALIDO_2, "39053344705"])
    def test_cpf_valido(self, cpf: str) -> None:
        assert validar_cpf(cpf) is True

    @pytest.mark.parametrize(
        ("cpf", "motivo"),
        [
            ("52998224726", "último dígito verificador errado"),
            ("52998224715", "primeiro dígito verificador errado"),
            ("00000000000", "todos os dígitos iguais"),
            ("11111111111", "todos os dígitos iguais"),
            ("5299822472", "10 dígitos — curto demais"),
            ("529982247250", "12 dígitos — longo demais"),
            ("5299822472a", "contém letra"),
            ("", "vazio"),
        ],
    )
    def test_cpf_invalido(self, cpf: str, motivo: str) -> None:
        assert validar_cpf(cpf) is False, motivo

    def test_cnpj_nao_passa_como_cpf(self) -> None:
        """Um CNPJ válido tem 14 dígitos e não pode ser aceito como CPF."""
        assert validar_cpf(CNPJ_VALIDO) is False


class TestCNPJ:
    @pytest.mark.parametrize("cnpj", [CNPJ_VALIDO, CNPJ_VALIDO_2, "33041536000104"])
    def test_cnpj_valido(self, cnpj: str) -> None:
        assert validar_cnpj(cnpj) is True

    @pytest.mark.parametrize(
        ("cnpj", "motivo"),
        [
            ("11222333000182", "último dígito verificador errado"),
            ("11222333000171", "primeiro dígito verificador errado"),
            ("00000000000000", "todos os dígitos iguais"),
            ("1122233300018", "13 dígitos — curto demais"),
            ("112223330001810", "15 dígitos — longo demais"),
            ("1122233300018X", "contém letra"),
            ("", "vazio"),
        ],
    )
    def test_cnpj_invalido(self, cnpj: str, motivo: str) -> None:
        assert validar_cnpj(cnpj) is False, motivo

    def test_cpf_nao_passa_como_cnpj(self) -> None:
        """Um CPF válido tem 11 dígitos e não pode ser aceito como CNPJ."""
        assert validar_cnpj(CPF_VALIDO) is False


class TestDeteccaoDeTipo:
    def test_detecta_cpf(self) -> None:
        assert detectar_tipo_documento(CPF_VALIDO) == TIPO_CPF

    def test_detecta_cnpj(self) -> None:
        assert detectar_tipo_documento(CNPJ_VALIDO) == TIPO_CNPJ

    @pytest.mark.parametrize(
        "documento",
        ["123456789012", "1234567890123", "123", ""],
    )
    def test_comprimento_que_nao_e_cpf_nem_cnpj(self, documento: str) -> None:
        """Nem toda cadeia de dígitos é documento — 12 e 13 não são nada."""
        with pytest.raises(ValueError, match="não é CPF"):
            detectar_tipo_documento(documento)

    def test_comprimento_certo_mas_digito_errado_nao_vira_tipo(self) -> None:
        """A regra NÃO é genérica de comprimento: o DV precisa fechar."""
        with pytest.raises(ValueError, match="CPF inválido"):
            detectar_tipo_documento("52998224726")
        with pytest.raises(ValueError, match="CNPJ inválido"):
            detectar_tipo_documento("11222333000182")


class TestValidarDocumentoPorTipo:
    def test_usa_o_algoritmo_do_tipo_informado(self) -> None:
        assert validar_documento(CPF_VALIDO, TIPO_CPF) is True
        assert validar_documento(CPF_VALIDO, TIPO_CNPJ) is False
        assert validar_documento(CNPJ_VALIDO, TIPO_CNPJ) is True
        assert validar_documento(CNPJ_VALIDO, TIPO_CPF) is False

    def test_tipo_desconhecido(self) -> None:
        with pytest.raises(ValueError, match="tipo_documento desconhecido"):
            validar_documento(CPF_VALIDO, "RG")


class TestFormatacao:
    def test_formata_cpf(self) -> None:
        assert formatar_documento(CPF_VALIDO) == "529.982.247-25"

    def test_formata_cnpj(self) -> None:
        assert formatar_documento(CNPJ_VALIDO) == "11.222.333/0001-81"
