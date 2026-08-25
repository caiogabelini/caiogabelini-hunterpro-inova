"""Receita Federal — Dados Abertos do CNPJ (arquivo em lote).

É a **semente do lado pessoa jurídica** do universo da Inova: varre o arquivo
ESTABELECIMENTOS filtrando por CNAE de agronegócio + UF + situação cadastral,
e devolve os CNPJs encontrados. Junto com o Sicor (que traz sobretudo produtor
rural **pessoa física**), forma as duas fontes candidatas que a Fase 4 vai
orquestrar.

## Portado do Minotto — e o que mudou

Este módulo é o porte de ``app/services/receita_federal_bulk.py`` do projeto
Minotto. ⚠️ **Não** de ``receita_federal.py`` de lá: apesar do nome, aquele é
um cliente HTTP da BrasilAPI (consulta pontual por CNPJ), não tem nada de
arquivo em lote. Ver o relatório da sessão.

O que mudou em relação ao original:

- **CNAE é parâmetro, não constante.** O Minotto embute o prefixo ``"8630"``
  (consultório médico) e uma lista de exclusão. Aqui o filtro recebe um
  conjunto de códigos exatos de 7 dígitos, e os presets de agronegócio ficam
  em constantes nomeadas — a Fase 4 escolhe se busca só grão, ou também
  apoio/agroindústria, sem tocar em código.
- **Leitura vem de ``arquivo_utils``**, não de helpers locais. O
  ``encontrar_arquivos`` (plural) e o ``leitor_csv_posicional`` foram para lá,
  onde o Sicor e a Receita compartilham a mesma garantia de streaming.

## O que este módulo NÃO faz (de propósito)

- **Não traz decisor/sócio.** O arquivo ESTABELECIMENTOS não tem sócio nenhum;
  o quadro societário mora em arquivo separado (SOCIOS), que o Minotto também
  não usa — lá o decisor vem da BrasilAPI, por CNPJ, no pipeline de
  enriquecimento. Não é omissão do porte: o dado não existe nesta fonte.
- **Não baixa arquivo.** Mesma divisão do Minotto: download é externo, os
  arquivos ficam num diretório configurável.
- **Não resolve o lado pessoa física.** Produtor rural PF não tem arquivo em
  lote equivalente — é o Sicor que cobre esse lado, e a busca ativa por PF é
  escopo de sessão futura.
- **Não pontua nem persiste nada.** Devolve dado bruto tipado.

## Layout (confirmado contra o arquivo real, não só contra o PDF)

ESTABELECIMENTOS: **30 colunas, SEM cabeçalho**, ``;`` como separador, campos
entre aspas duplas, latin-1. Confirmado varrendo
``K3241.K03200Y1.D60808.ESTABELE`` inteiro (4.753.435 linhas): **todas** as
linhas têm exatamente 30 colunas.

EMPRESAS: 7 colunas, mesmo formato. Chave de junção é ``cnpj_basico``
(8 primeiros dígitos do CNPJ).

⚠️ **RAZÃO SOCIAL NÃO ESTÁ NO ESTABELECIMENTOS** — só ``nome_fantasia``, que é
opcional e vem vazio pra muita empresa pequena. A razão social exige o join
com EMPRESAS (ver ``enriquecer_com_razao_social``).

⚠️ **MUNICÍPIO É CÓDIGO PRÓPRIO DA RECEITA, NÃO IBGE.** É o código da tabela
de domínio MUNICIPIOS, publicada à parte. Não cruzar com código IBGE direto;
``carregar_municipios`` resolve código → nome.
"""

from __future__ import annotations

import csv
import logging
import re
import zipfile
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.services.arquivo_utils import (
    ArquivoZipInvalidoError,
    encontrar_arquivos,
    leitor_csv_posicional,
)

logger = logging.getLogger(__name__)

ENCODING_PADRAO = "latin-1"
DELIMITADOR_PADRAO = ";"

# --------------------------------------------------------------------------
# Layout oficial (gov.br/receitafederal/dados/cnpj-metadados.pdf), conferido
# contra o arquivo real em 25/08/2026. Ordem exata — o arquivo é posicional.
# --------------------------------------------------------------------------
ESTABELECIMENTOS_COLUNAS = [
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "identificador_matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao_cadastral",
    "motivo_situacao_cadastral",
    "nome_cidade_exterior",
    "pais",
    "data_inicio_atividade",
    "cnae_fiscal_principal",
    "cnae_fiscal_secundaria",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd_1",
    "telefone_1",
    "ddd_2",
    "telefone_2",
    "ddd_fax",
    "fax",
    "correio_eletronico",
    "situacao_especial",
    "data_situacao_especial",
]

EMPRESAS_COLUNAS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo_responsavel",
]

MUNICIPIOS_COLUNAS = ["codigo", "descricao"]

# --------------------------------------------------------------------------
# Situação cadastral
# --------------------------------------------------------------------------
# Códigos do PDF oficial, seção ESTABELECIMENTOS, e as frequências medidas
# na fatia real K3241.K03200Y1.D60808.ESTABELE (4.753.435 linhas):
#
#   '08' BAIXADA  2.692.602      '03' SUSPENSA    24.404
#   '02' ATIVA    1.171.169      '01' NULA         9.463
#   '04' INAPTA     855.797
#
# Ou seja: só ~25% do arquivo está ATIVA. Filtrar não é detalhe.
#
# ⚠️ O PDF oficial é inconsistente na própria grafia — escreve "01 – NULA" e
# "08 – BAIXADA" com zero à esquerda, mas "2 – ATIVA", "3 – SUSPENSA" e
# "4 – INAPTA" sem. O arquivo real usa SEMPRE 2 dígitos com zero. Por isso a
# comparação normaliza com zfill(2) em vez de confiar numa das duas grafias.
SITUACAO_ATIVA = "02"
SITUACOES_CADASTRAIS = {
    "01": "NULA",
    "02": "ATIVA",
    "03": "SUSPENSA",
    "04": "INAPTA",
    "08": "BAIXADA",
}

# --------------------------------------------------------------------------
# CNAEs de agronegócio
# --------------------------------------------------------------------------
# Códigos de SUBCLASSE (7 dígitos) — é o formato de `cnae_fiscal_principal`
# no arquivo. Descrições verbatim da API oficial de CNAE do IBGE/CONCLA
# (servicodados.ibge.gov.br/api/v2/cnae), que é a fonte normativa da
# classificação; a Cnaes.zip da Receita é cópia dela. Cada código abaixo foi
# além disso confirmado como PRESENTE no arquivo real da Receita — nenhum é
# suposição.
#
# ⚠️ NÃO existe CNAE "cultivo de milho" no nível de CLASSE: milho fica dentro
# de 0111-3 CULTIVO DE CEREAIS, e só se separa na subclasse 0111302. Buscar
# por classe traria arroz e trigo junto sem querer.
#
# ⚠️ NÃO existe CNAE de "cooperativa agropecuária". A CNAE classifica
# ATIVIDADE, não forma jurídica — cooperativa é `natureza_juridica` 2143, no
# arquivo EMPRESAS, combinada com um CNAE agro qualquer. Ver
# `NATUREZA_JURIDICA_COOPERATIVA` e o relatório da sessão.

#: Cultivo de grãos — o núcleo do nicho da Inova.
CNAES_GRAOS: frozenset[str] = frozenset(
    {"0115600", "0111302", "0111301", "0111303", "0111399"}
)

#: Serviços prestados à lavoura. Quem presta serviço a produtor de grão
#: costuma ser PJ com contabilidade — perfil comercial da Inova.
CNAES_APOIO_AGRICULTURA: frozenset[str] = frozenset(
    {"0161001", "0161002", "0161003", "0161099", "0163600"}
)

#: Agroindústria e atacado de grãos — o elo seguinte da cadeia.
CNAES_AGROINDUSTRIA: frozenset[str] = frozenset(
    {"4632001", "4623109", "4623199", "4692300", "1061901", "1069400"}
)

CNAES_AGRO_TODOS: frozenset[str] = (
    CNAES_GRAOS | CNAES_APOIO_AGRICULTURA | CNAES_AGROINDUSTRIA
)

#: Descrição oficial de cada código, pro dossiê e pros logs.
CNAE_DESCRICOES: dict[str, str] = {
    "0111301": "CULTIVO DE ARROZ",
    "0111302": "CULTIVO DE MILHO",
    "0111303": "CULTIVO DE TRIGO",
    "0111399": "CULTIVO DE OUTROS CEREAIS NÃO ESPECIFICADOS ANTERIORMENTE",
    "0115600": "CULTIVO DE SOJA",
    "0161001": "SERVIÇO DE PULVERIZAÇÃO E CONTROLE DE PRAGAS AGRÍCOLAS",
    "0161002": "SERVIÇO DE PODA DE ÁRVORES PARA LAVOURAS",
    "0161003": "SERVIÇO DE PREPARAÇÃO DE TERRENO, CULTIVO E COLHEITA",
    "0161099": "ATIVIDADES DE APOIO À AGRICULTURA NÃO ESPECIFICADAS ANTERIORMENTE",
    "0163600": "ATIVIDADES DE PÓS COLHEITA",
    "1061901": "BENEFICIAMENTO DE ARROZ",
    "1069400": "MOAGEM E FABRICAÇÃO DE PRODUTOS AMILÁCEOS E DE ALIMENTOS PARA ANIMAIS",
    "4623109": "COMÉRCIO ATACADISTA DE ALIMENTOS PARA ANIMAIS",
    "4623199": "COMÉRCIO ATACADISTA DE MATÉRIAS-PRIMAS AGRÍCOLAS NÃO ESPECIFICADAS ANTERIORMENTE",
    "4632001": "COMÉRCIO ATACADISTA DE CEREAIS E LEGUMINOSAS BENEFICIADOS",
    "4692300": "COMÉRCIO ATACADISTA DE MERCADORIAS EM GERAL, COM PREDOMINÂNCIA DE INSUMOS AGROPECUÁRIOS",
}

#: Natureza jurídica de cooperativa (arquivo EMPRESAS). Confirmada presente no
#: arquivo real: 19 dos 343 CNPJs agro resolvidos na amostra de teste.
NATUREZA_JURIDICA_COOPERATIVA = "2143"

UFS_POR_REGIAO: dict[str, frozenset[str]] = {
    "norte": frozenset({"AC", "AP", "AM", "PA", "RO", "RR", "TO"}),
    "nordeste": frozenset({"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"}),
    "centro-oeste": frozenset({"DF", "GO", "MT", "MS"}),
    "sudeste": frozenset({"ES", "MG", "RJ", "SP"}),
    "sul": frozenset({"PR", "RS", "SC"}),
}

ERROS_DE_LEITURA = (
    OSError,
    UnicodeDecodeError,
    csv.Error,
    zipfile.BadZipFile,
    ArquivoZipInvalidoError,
)


@dataclass(frozen=True, slots=True)
class EstabelecimentoRFB:
    """Um estabelecimento filtrado, normalizado a partir do arquivo.

    ``razao_social`` nasce vazia e só é preenchida por
    ``enriquecer_com_razao_social``. Fica string vazia, nunca ``None``, pra
    quem consome não ter que checar None em todo lugar — mesma convenção do
    Minotto.
    """

    cnpj: str
    nome_fantasia: str
    situacao_cadastral: str
    cnae_fiscal_principal: str
    data_inicio_atividade: str
    municipio_codigo_rfb: str
    uf: str
    identificador_matriz_filial: str
    razao_social: str = ""
    natureza_juridica: str = ""
    municipio: str = ""

    @property
    def cnpj_basico(self) -> str:
        return self.cnpj[:8]

    @property
    def cnae_descricao(self) -> str:
        return CNAE_DESCRICOES.get(self.cnae_fiscal_principal, "")

    @property
    def situacao_descricao(self) -> str:
        return SITUACOES_CADASTRAIS.get(self.situacao_cadastral.zfill(2), "")

    @property
    def eh_cooperativa(self) -> bool:
        return self.natureza_juridica == NATUREZA_JURIDICA_COOPERATIVA


@dataclass(frozen=True, slots=True)
class ResultadoReceitaFederal:
    """Resultado tipado. Nunca vem de uma exceção vazando pro chamador."""

    estabelecimentos: tuple[EstabelecimentoRFB, ...] = ()
    linhas_lidas: int = 0
    arquivos_lidos: tuple[str, ...] = ()
    etapas_puladas: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return bool(self.estabelecimentos)


def _pular(motivo: str, etapa: str) -> dict[str, str]:
    logger.warning("receita_federal: etapa '%s' pulada — %s", etapa, motivo)
    return {"etapa": etapa, "motivo": motivo}


def encontrar_estabelecimentos(diretorio: Path | str) -> list[Path]:
    """``Estabelecimentos0.zip``..``9.zip`` e/ou ``K3241...ESTABELE`` extraído."""
    return encontrar_arquivos(
        Path(diretorio), marcador_csv="ESTABELE", prefixo_zip="Estabelecimentos"
    )


def encontrar_empresas(diretorio: Path | str) -> list[Path]:
    """``Empresas0.zip``..``9.zip`` e/ou ``K3241...EMPRECSV`` extraído."""
    return encontrar_arquivos(
        Path(diretorio), marcador_csv="EMPRECSV", prefixo_zip="Empresas"
    )


def encontrar_municipios(diretorio: Path | str) -> list[Path]:
    """``Municipios.zip`` e/ou ``F.K03200$Z.D<data>.MUNICCSV`` extraído."""
    return encontrar_arquivos(
        Path(diretorio), marcador_csv="MUNICCSV", prefixo_zip="Municipios"
    )


def _normalizar_cnae(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _eh_situacao_ativa(situacao: str) -> bool:
    return situacao.strip().zfill(2) == SITUACAO_ATIVA


def _montar_cnpj(basico: str, ordem: str, dv: str) -> str:
    return f"{basico}{ordem}{dv}"


def iter_estabelecimentos(caminho: Path) -> Iterator[dict[str, str]]:
    """Lê um arquivo ESTABELECIMENTOS em streaming, linha a linha."""
    with leitor_csv_posicional(
        caminho,
        ESTABELECIMENTOS_COLUNAS,
        delimitador=DELIMITADOR_PADRAO,
        encoding=ENCODING_PADRAO,
    ) as linhas:
        yield from linhas


def carregar_municipios(caminhos: list[Path]) -> dict[str, str]:
    """Índice ``código RFB do município`` → ``nome``.

    Carrega tudo em memória de propósito, ao contrário dos outros parsers
    daqui: são ~5.570 municípios (algumas centenas de KB), não os milhões de
    linhas do ESTABELECIMENTOS.

    Nunca levanta: arquivo ilegível é pulado e o índice sai sem ele.
    """
    indice: dict[str, str] = {}
    for caminho in caminhos:
        try:
            with leitor_csv_posicional(
                caminho,
                MUNICIPIOS_COLUNAS,
                delimitador=DELIMITADOR_PADRAO,
                encoding=ENCODING_PADRAO,
            ) as linhas:
                for linha in linhas:
                    codigo, descricao = linha["codigo"], linha["descricao"]
                    if codigo and descricao:
                        indice[codigo] = descricao
        except ERROS_DE_LEITURA:
            logger.exception("falha lendo tabela de municípios em %s — ignorando", caminho)
            continue
    return indice


def carregar_dados_empresa(
    caminhos: list[Path], cnpjs_basicos: Collection[str]
) -> dict[str, tuple[str, str]]:
    """``cnpj_basico`` → ``(razão social, natureza jurídica)``.

    Indexa **só** os ``cnpjs_basicos`` pedidos — normalmente os que já
    sobraram do filtro de CNAE. O arquivo EMPRESAS completo tem dezenas de
    milhões de linhas; indexar tudo pra usar um punhado seria o mesmo erro de
    memória da seção 6. Mesma lógica de "filtra primeiro, cruza depois" do
    parser do Sicor.
    """
    alvo = set(cnpjs_basicos)
    indice: dict[str, tuple[str, str]] = {}
    if not alvo:
        return indice
    for caminho in caminhos:
        try:
            with leitor_csv_posicional(
                caminho,
                EMPRESAS_COLUNAS,
                delimitador=DELIMITADOR_PADRAO,
                encoding=ENCODING_PADRAO,
            ) as linhas:
                for linha in linhas:
                    basico = linha["cnpj_basico"]
                    if basico in alvo:
                        indice[basico] = (
                            linha["razao_social"],
                            linha["natureza_juridica"],
                        )
        except ERROS_DE_LEITURA:
            logger.exception("falha lendo EMPRESAS em %s — ignorando", caminho)
            continue
    return indice


def enriquecer_com_razao_social(
    estabelecimentos: Collection[EstabelecimentoRFB], caminhos_empresas: list[Path]
) -> list[EstabelecimentoRFB]:
    """Junta razão social e natureza jurídica, pela raiz do CNPJ.

    ⚠️ **Precisa de TODAS as fatias de EMPRESAS**, não da fatia de mesmo
    número. Medido contra os arquivos reais: ``Estabelecimentos1`` e
    ``Empresas1`` compartilham só uma fração dos ``cnpj_basico`` — as duas
    séries não são particionadas pela mesma chave. Com fatias faltando, o
    lead sai com ``razao_social`` vazia (degrada, não quebra), o que na Fase 4
    pareceria bug de parser e não é.
    """
    indice = carregar_dados_empresa(
        caminhos_empresas, {e.cnpj_basico for e in estabelecimentos}
    )
    return [
        replace(
            e,
            razao_social=indice.get(e.cnpj_basico, ("", ""))[0],
            natureza_juridica=indice.get(e.cnpj_basico, ("", ""))[1],
        )
        for e in estabelecimentos
    ]


def _resolver_ufs(ufs: Collection[str] | None, regiao: str | None) -> set[str]:
    alvo = {uf.upper() for uf in (ufs or ())}
    if regiao:
        alvo |= set(UFS_POR_REGIAO.get(regiao.lower(), ()))
    return alvo


def _filtrar_arquivo(
    caminho: Path,
    *,
    cnaes_alvo: set[str],
    ufs_alvo: set[str],
    apenas_ativos: bool,
) -> tuple[list[EstabelecimentoRFB], int]:
    """Filtra UM arquivo. Devolve ``(achados, linhas_lidas)``.

    Os conjuntos-alvo chegam prontos de propósito: normalizá-los aqui dentro
    custaria uma reconstrução por linha, em arquivo de milhões de linhas.
    """
    achados: list[EstabelecimentoRFB] = []
    linhas_lidas = 0
    for linha in iter_estabelecimentos(caminho):
        linhas_lidas += 1
        if _normalizar_cnae(linha["cnae_fiscal_principal"]) not in cnaes_alvo:
            continue
        if apenas_ativos and not _eh_situacao_ativa(linha["situacao_cadastral"]):
            continue
        if ufs_alvo and linha["uf"].upper() not in ufs_alvo:
            continue
        achados.append(
            EstabelecimentoRFB(
                cnpj=_montar_cnpj(
                    linha["cnpj_basico"], linha["cnpj_ordem"], linha["cnpj_dv"]
                ),
                nome_fantasia=linha["nome_fantasia"],
                situacao_cadastral=linha["situacao_cadastral"],
                cnae_fiscal_principal=linha["cnae_fiscal_principal"],
                data_inicio_atividade=linha["data_inicio_atividade"],
                municipio_codigo_rfb=linha["municipio"],
                uf=linha["uf"],
                identificador_matriz_filial=linha["identificador_matriz_filial"],
            )
        )
    return achados, linhas_lidas


def filtrar_estabelecimentos(
    caminhos: list[Path],
    *,
    cnaes: Collection[str],
    ufs: Collection[str] | None = None,
    regiao: str | None = None,
    apenas_ativos: bool = True,
) -> Iterator[EstabelecimentoRFB]:
    """Filtra por CNAE + UF/região + situação cadastral, em streaming.

    ``cnaes`` são códigos de subclasse (7 dígitos) exatos — ver os presets
    ``CNAES_*`` deste módulo. ``ufs`` e ``regiao`` se combinam (passa quem
    estiver numa UF de ``ufs`` OU na ``regiao``); sem nenhum dos dois, não
    filtra por localização.

    Levanta o que a leitura levantar — quem quer o resultado embrulhado e
    nunca-levanta chama ``buscar_semente_cnpj``.
    """
    cnaes_alvo = {_normalizar_cnae(c) for c in cnaes}
    ufs_alvo = _resolver_ufs(ufs, regiao)
    for caminho in caminhos:
        achados, _ = _filtrar_arquivo(
            caminho,
            cnaes_alvo=cnaes_alvo,
            ufs_alvo=ufs_alvo,
            apenas_ativos=apenas_ativos,
        )
        yield from achados


def buscar_semente_cnpj(
    diretorio: Path | str,
    *,
    cnaes: Collection[str] = CNAES_AGRO_TODOS,
    ufs: Collection[str] | None = None,
    regiao: str | None = None,
    apenas_ativos: bool = True,
    resolver_razao_social: bool = True,
    resolver_municipio: bool = True,
) -> ResultadoReceitaFederal:
    """Ponto de entrada: devolve a semente de CNPJs do nicho.

    Nunca levanta — qualquer falha vira ``etapas_puladas`` com motivo, no
    mesmo formato de ``Lead.etapas_puladas``. Um ``.zip`` malformado derruba
    só a leitura daquele arquivo, não a busca inteira.
    """
    diretorio = Path(diretorio)
    puladas: list[dict[str, str]] = []

    caminhos = encontrar_estabelecimentos(diretorio)
    if not caminhos:
        return ResultadoReceitaFederal(
            etapas_puladas=(
                _pular(
                    f"nenhum arquivo ESTABELECIMENTOS encontrado em {diretorio} "
                    f"(esperado Estabelecimentos*.zip ou *ESTABELE)",
                    "rfb_estabelecimentos",
                ),
            )
        )

    cnaes_alvo = {_normalizar_cnae(c) for c in cnaes}
    ufs_alvo = _resolver_ufs(ufs, regiao)
    encontrados: list[EstabelecimentoRFB] = []
    lidos: list[str] = []
    linhas_lidas = 0
    for caminho in caminhos:
        try:
            achados, linhas = _filtrar_arquivo(
                caminho,
                cnaes_alvo=cnaes_alvo,
                ufs_alvo=ufs_alvo,
                apenas_ativos=apenas_ativos,
            )
        except ERROS_DE_LEITURA as exc:
            puladas.append(
                _pular(f"falha lendo {caminho.name}: {exc}", "rfb_estabelecimentos")
            )
            continue
        encontrados.extend(achados)
        linhas_lidas += linhas
        lidos.append(caminho.name)
        logger.info(
            "receita_federal: %s -> %d estabelecimentos no filtro (%d linhas)",
            caminho.name, len(achados), linhas,
        )

    if resolver_razao_social and encontrados:
        caminhos_empresas = encontrar_empresas(diretorio)
        if not caminhos_empresas:
            puladas.append(
                _pular(
                    "nenhum arquivo EMPRESAS encontrado — leads saem sem razão "
                    "social e sem natureza jurídica",
                    "rfb_empresas",
                )
            )
        else:
            encontrados = enriquecer_com_razao_social(encontrados, caminhos_empresas)
            sem_razao = sum(1 for e in encontrados if not e.razao_social)
            if sem_razao:
                puladas.append(
                    _pular(
                        f"{sem_razao} de {len(encontrados)} CNPJs sem razão social: "
                        f"as fatias de EMPRESAS presentes não cobrem todos os "
                        f"cnpj_basico. Baixar as 10 fatias resolve.",
                        "rfb_empresas",
                    )
                )

    if resolver_municipio and encontrados:
        caminhos_municipios = encontrar_municipios(diretorio)
        if not caminhos_municipios:
            puladas.append(
                _pular(
                    "tabela MUNICIPIOS ausente — leads saem só com o código "
                    "interno da Receita, sem nome do município",
                    "rfb_municipios",
                )
            )
        else:
            indice = carregar_municipios(caminhos_municipios)
            encontrados = [
                replace(e, municipio=indice.get(e.municipio_codigo_rfb, ""))
                for e in encontrados
            ]

    return ResultadoReceitaFederal(
        estabelecimentos=tuple(encontrados),
        linhas_lidas=linhas_lidas,
        arquivos_lidos=tuple(lidos),
        etapas_puladas=tuple(puladas),
    )
