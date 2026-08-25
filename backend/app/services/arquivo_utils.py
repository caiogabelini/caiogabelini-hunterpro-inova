"""Infra compartilhada de leitura de arquivo em lote — **não é fonte de dado**.

Todo parser de fonte que publica arquivo bruto (Receita Federal, Sicor, PGFN)
passa por aqui. O módulo existe pra concentrar num lugar só as armadilhas que
custaram caro no Minotto (seção 6 do docs_fundacao.md):

- **Ler direto do arquivo comprimido, em streaming.** Nunca extrair pro disco
  (duplica o espaço: comprimido + extraído ao mesmo tempo, e o container fica
  sem espaço no meio do processamento) e nunca carregar tudo em memória. Os
  arquivos do Sicor têm 18 e 27 milhões de linhas.
- **Nome de arquivo real ≠ nome assumido.** Dois bugs distintos dessa família
  no Minotto: glob `*ESTABELE*` maiúsculo contra `Estabelecimentos0.zip`
  (case-sensitivity), e finder que só reconhecia `*.csv` contra
  `Dados_abertos_*.zip` (padrão de nome incompleto). Nos dois o sintoma foi o
  mesmo e silencioso: "sem arquivos" tratado como "concluído com 0 leads".
  Por isso ``encontrar_arquivo`` é case-insensitive e varre várias extensões.

⚠️ **Nunca passar ``comment='#'`` pro leitor de CSV.** O cabeçalho do Sicor
começa com ``#`` (``#REF_BACEN;NU_ORDEM;...``). Um leitor configurado pra
tratar ``#`` como comentário **descarta a linha de cabeçalho inteira** e
desloca todas as colunas em uma — silenciosamente, sem erro nenhum.
``normalizar_cabecalho`` remove o ``#`` só do nome da primeira coluna.
"""

from __future__ import annotations

import csv
import gzip
import io
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

#: Sentinelas que o Sicor usa pra "campo vazio" em coluna de texto. O arquivo
#: mistura os dois: string vazia em algumas colunas e "-1" em outras.
SENTINELAS_VAZIO: tuple[str, ...] = ("", "-1")


class ArquivoZipInvalidoError(ValueError):
    """O ``.zip`` não tem os membros que o formato esperava.

    Herda de ``ValueError`` pra continuar sendo pego por quem captura exceção
    genérica (todo parser daqui embrulha a leitura), mas com tipo próprio pra
    quem quiser tratar especificamente.

    Levantar aqui é deliberado, e é a exceção à regra de "nunca lança": um
    ``.zip`` com número de membros diferente do esperado significa que alguém
    montou o arquivo à mão. Adivinhar qual membro usar processaria **os dados
    errados em silêncio** — pior que falhar alto. Os arquivos de dados abertos
    da Receita vêm sempre com exatamente um membro por ``.zip``.
    """


def encontrar_arquivo(
    diretorio: Path, *padroes: str, extensoes: Sequence[str] = (".gz", ".zip", ".csv")
) -> Path | None:
    """Acha um arquivo por padrão, **ignorando caixa** e testando extensões.

    Devolve ``None`` se não achar — quem chama decide se isso é etapa pulada
    ou erro. Nunca levanta.
    """
    if not diretorio.is_dir():
        return None
    candidatos = sorted(p for p in diretorio.iterdir() if p.is_file())
    for padrao in padroes:
        alvo = padrao.lower()
        for p in candidatos:
            nome = p.name.lower()
            if nome == alvo or (
                nome.startswith(alvo) and any(nome.endswith(e) for e in extensoes)
            ):
                return p
        for p in candidatos:  # 2ª volta, mais frouxa: padrão em qualquer posição
            if alvo in p.name.lower():
                return p
    return None


def encontrar_arquivos(
    diretorio: Path, *, marcador_csv: str, prefixo_zip: str
) -> list[Path]:
    """Acha **todos** os arquivos de um tipo de dado da Receita numa pasta.

    Plural de propósito, e por dois motivos distintos:

    1. **A Receita fatia o arquivo completo em 10** (``Estabelecimentos0.zip``
       .. ``Estabelecimentos9.zip``). Cada CNPJ cai numa fatia por hash; o
       layout é idêntico. Pegar só uma dá 1/10 do país, sem sinal nenhum de
       que faltou coisa.
    2. **Os dois formatos convivem**: o ``.zip`` baixado do site e o CSV já
       extraído (``K3241.K03200Y1.D60808.ESTABELE``). O marcador do CSV é
       MAIÚSCULO e o nome do zip é Capitalizado — foi exatamente aí que o
       glob case-sensitive do Minotto ignorou os ``.zip`` em silêncio, e quem
       esquecesse de descompactar via a busca "concluir com sucesso" com zero
       leads (seção 6 do docs_fundacao.md). A comparação aqui ignora caixa.

    ⚠️ Se a pasta tiver os dois formatos do mesmo dado, **os dois voltam** na
    lista. Deduplicar é responsabilidade de quem consome (por CNPJ, não por
    nome de arquivo): correlacionar ``Estabelecimentos1.zip`` com
    ``K3241.K03200Y1.D60808.ESTABELE`` pelo nome não é confiável, e eleger um
    formato "vencedor" descartaria fatias de quem extraiu só parte dos zips.

    Difere de ``encontrar_arquivo`` (singular), que serve fonte publicada em
    arquivo único — o caso do Sicor.
    """
    if not diretorio.is_dir():
        return []
    marcador, prefixo = marcador_csv.lower(), prefixo_zip.lower()
    encontrados = []
    for caminho in diretorio.iterdir():
        if not caminho.is_file():
            continue
        nome = caminho.name.lower()
        eh_csv_extraido = marcador in nome and not nome.endswith(".zip")
        eh_zip_da_receita = nome.startswith(prefixo) and nome.endswith(".zip")
        if eh_csv_extraido or eh_zip_da_receita:
            encontrados.append(caminho)
    return sorted(encontrados)


@contextmanager
def abrir_texto(caminho: Path, *, encoding: str = "latin-1") -> Iterator[TextIO]:
    """Abre ``.gz``, ``.zip`` ou texto puro como stream de texto.

    ``newline=""`` é obrigatório: sem isso o módulo ``csv`` não consegue tratar
    quebra de linha dentro de campo entre aspas, e um ``\\r`` de arquivo CRLF
    gruda no último campo de cada linha.
    """
    sufixo = caminho.suffix.lower()
    if sufixo == ".gz":
        with gzip.open(caminho, mode="rt", encoding=encoding, newline="") as f:
            yield f
    elif sufixo == ".zip":
        with zipfile.ZipFile(caminho) as z:
            membros = [m for m in z.infolist() if not m.is_dir()]
            if len(membros) != 1:
                nomes = [m.filename for m in membros]
                raise ArquivoZipInvalidoError(
                    f"{caminho.name} tem {len(membros)} arquivos dentro "
                    f"(esperado exatamente 1): {nomes}. Extraia o que você quer "
                    f"usar e coloque o CSV direto na pasta."
                )
            with z.open(membros[0]) as bruto:
                yield io.TextIOWrapper(bruto, encoding=encoding, newline="")
    else:
        with open(caminho, encoding=encoding, newline="") as f:
            yield f


def normalizar_cabecalho(cabecalho: Sequence[str]) -> list[str]:
    """Tira o ``#`` do nome da primeira coluna e apara espaço de todas.

    Ver o aviso no docstring do módulo: essa é a alternativa correta a
    ``comment='#'``, que destruiria o cabeçalho inteiro.
    """
    colunas = [c.strip() for c in cabecalho]
    if colunas and colunas[0].startswith("#"):
        colunas[0] = colunas[0][1:].strip()
    return colunas


@contextmanager
def leitor_csv(
    caminho: Path, *, delimitador: str = ";", encoding: str = "latin-1"
) -> Iterator[tuple[list[str], Iterator[list[str]]]]:
    """Devolve ``(cabecalho_normalizado, linhas)`` — as linhas são um gerador.

    Quem chama resolve os índices das colunas UMA vez a partir do cabeçalho e
    depois itera. Isso é bem mais barato que ``DictReader`` (que constrói um
    dict por linha) quando o arquivo tem dezenas de milhões de linhas.
    """
    with abrir_texto(caminho, encoding=encoding) as f:
        leitor = csv.reader(f, delimiter=delimitador)  # NUNCA comment='#'
        try:
            cabecalho = normalizar_cabecalho(next(leitor))
        except StopIteration:
            yield [], iter(())
            return
        yield cabecalho, leitor


@contextmanager
def leitor_csv_posicional(
    caminho: Path,
    colunas: Sequence[str],
    *,
    delimitador: str = ";",
    encoding: str = "latin-1",
    quotechar: str = '"',
) -> Iterator[Iterator[dict[str, str]]]:
    """Lê arquivo **SEM cabeçalho**, nomeando as colunas por posição.

    É o formato dos dados abertos da Receita Federal: sem linha de cabeçalho,
    ``;`` como separador, campos entre aspas duplas, latin-1. O nome de cada
    coluna vem de ``colunas``, na ordem exata do layout oficial.

    Linha com menos colunas que o layout é **pulada** em vez de derrubar o
    parser — arquivo truncado não pode custar as outras milhões de linhas.
    """
    with abrir_texto(caminho, encoding=encoding) as f:
        leitor = csv.reader(f, delimiter=delimitador, quotechar=quotechar)
        esperado = len(colunas)

        def linhas() -> Iterator[dict[str, str]]:
            for row in leitor:
                if len(row) < esperado:
                    continue
                yield {c: (v or "").strip().strip('"').strip() for c, v in zip(colunas, row)}

        yield linhas()


def indices_de(cabecalho: Sequence[str], *colunas: str) -> tuple[int, ...]:
    """Resolve o índice de cada coluna pelo nome, levantando se faltar alguma.

    Falhar aqui é **melhor** que devolver ``None`` mais tarde: uma coluna que
    mudou de nome na origem vira erro imediato e explícito, não um campo vazio
    que ninguém nota. Quem chama captura e transforma em etapa pulada.
    """
    faltando = [c for c in colunas if c not in cabecalho]
    if faltando:
        raise KeyError(
            f"colunas ausentes no arquivo: {faltando}. Cabeçalho real: {list(cabecalho)}"
        )
    return tuple(cabecalho.index(c) for c in colunas)


def texto_ou_none(
    valor: str | None, *, sentinelas: Sequence[str] = SENTINELAS_VAZIO
) -> str | None:
    """Normaliza campo de texto vazio pra ``None``, tratando sentinelas.

    O Sicor usa ``""`` **e** ``"-1"`` pra dizer "sem valor", dependendo da
    coluna — tratar só um dos dois deixa passar ``"-1"`` como se fosse um
    código de CAR de verdade.
    """
    if valor is None:
        return None
    limpo = valor.strip()
    return None if limpo in sentinelas else limpo


def decimal_ou_none(valor: str | None) -> float | None:
    """Lê número decimal, **com ponto** — o Sicor publica no formato americano.

    Seção 6 do docs_fundacao.md: "formato de número pode ser americano (ponto
    decimal) mesmo em fonte brasileira — não presumir vírgula sem checar".
    Confirmado contra o arquivo real: ``VL_AREA_INFORMADA`` vem como
    ``3000.00``. Valor ilegível ou sentinela vira ``None``, nunca exceção.
    """
    limpo = texto_ou_none(valor)
    if limpo is None:
        return None
    try:
        return float(limpo)
    except ValueError:
        return None
