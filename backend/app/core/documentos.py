"""Validação e normalização de CPF e CNPJ.

Diferença estrutural em relação ao Minotto: lá o lead é sempre pessoa
jurídica, e o documento é sempre um CNPJ de 14 dígitos. Aqui a Inova
prospecta produtor rural **pessoa física e jurídica juntos**, então o mesmo
campo carrega CPF (11 dígitos) ou CNPJ (14 dígitos).

Por isso a validação é **própria de cada formato**, não uma regra genérica de
comprimento: os dois têm dígitos verificadores calculados por algoritmos
diferentes. Uma regra do tipo "só conta os dígitos" aceitaria lixo dos dois
lados e envenenaria a chave de negócio — que é exatamente onde a
deduplicação acontece (seção 6: "deduplicação por chave de negócio, nunca
por posição/formato de arquivo").

Nota de contexto (2026): a Receita Federal está introduzindo o **CNPJ
alfanumérico** (12 posições alfanuméricas + 2 dígitos verificadores
numéricos) para inscrições novas. Este módulo trata apenas o CNPJ numérico,
que é o formato de todo o estoque atual e de todos os arquivos de dados
abertos publicados até aqui. Quando a primeira fonte em lote da Fase 3
trouxer um CNPJ alfanumérico, ``validar_cnpj`` é o único ponto que precisa
mudar — o resto do modelo já trata o documento como texto.
"""

from __future__ import annotations

import re

TIPO_CPF = "CPF"
TIPO_CNPJ = "CNPJ"
TIPOS_DOCUMENTO = (TIPO_CPF, TIPO_CNPJ)

TAMANHO_CPF = 11
TAMANHO_CNPJ = 14

_NAO_DIGITOS = re.compile(r"\D")


def normalizar_documento(valor: str | None) -> str:
    """Devolve só os dígitos do documento (remove ``.``, ``-``, ``/``, espaço).

    Normalizar antes de gravar é o que faz a deduplicação funcionar: o mesmo
    produtor pode chegar como ``123.456.789-09`` de uma fonte e
    ``12345678909`` de outra.
    """
    if valor is None:
        raise ValueError("documento é obrigatório")
    return _NAO_DIGITOS.sub("", str(valor))


def _digito_verificador(base: str, pesos: list[int]) -> int:
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def validar_cpf(documento: str) -> bool:
    """CPF válido: 11 dígitos, não todos iguais, dois DVs corretos (mod 11)."""
    if len(documento) != TAMANHO_CPF or not documento.isdigit():
        return False
    if len(set(documento)) == 1:  # 000.000.000-00, 111.111.111-11, ...
        return False
    primeiro = _digito_verificador(documento[:9], list(range(10, 1, -1)))
    segundo = _digito_verificador(documento[:10], list(range(11, 1, -1)))
    return documento[9] == str(primeiro) and documento[10] == str(segundo)


def validar_cnpj(documento: str) -> bool:
    """CNPJ válido: 14 dígitos, não todos iguais, dois DVs corretos (mod 11)."""
    if len(documento) != TAMANHO_CNPJ or not documento.isdigit():
        return False
    if len(set(documento)) == 1:
        return False
    pesos_primeiro = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos_segundo = [6] + pesos_primeiro
    primeiro = _digito_verificador(documento[:12], pesos_primeiro)
    segundo = _digito_verificador(documento[:13], pesos_segundo)
    return documento[12] == str(primeiro) and documento[13] == str(segundo)


def detectar_tipo_documento(documento: str) -> str:
    """Deduz ``CPF``/``CNPJ`` pelo comprimento **e** valida o formato detectado.

    Levanta ``ValueError`` se o documento não for um CPF nem um CNPJ válido —
    nunca devolve um tipo "provável" sem que os dígitos verificadores fechem.
    """
    if len(documento) == TAMANHO_CPF:
        if not validar_cpf(documento):
            raise ValueError(f"CPF inválido: {documento!r}")
        return TIPO_CPF
    if len(documento) == TAMANHO_CNPJ:
        if not validar_cnpj(documento):
            raise ValueError(f"CNPJ inválido: {documento!r}")
        return TIPO_CNPJ
    raise ValueError(
        f"documento com {len(documento)} dígitos não é CPF ({TAMANHO_CPF}) "
        f"nem CNPJ ({TAMANHO_CNPJ}): {documento!r}"
    )


def validar_documento(documento: str, tipo: str) -> bool:
    """Valida ``documento`` contra o algoritmo do ``tipo`` informado."""
    if tipo == TIPO_CPF:
        return validar_cpf(documento)
    if tipo == TIPO_CNPJ:
        return validar_cnpj(documento)
    raise ValueError(f"tipo_documento desconhecido: {tipo!r}")


def formatar_documento(documento: str, tipo: str | None = None) -> str:
    """Formata pra exibição: ``000.000.000-00`` ou ``00.000.000/0000-00``."""
    tipo = tipo or detectar_tipo_documento(documento)
    if tipo == TIPO_CPF:
        d = documento
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    d = documento
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
