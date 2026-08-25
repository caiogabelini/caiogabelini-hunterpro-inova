"""Sicor (Sistema de Operações do Crédito Rural — Banco Central).

Fonte gratuita e específica do nicho da Inova (agronegócio/grãos, foco Paraná).
Substitui o par PGFN+CNES que o Minotto usava. É arquivo em lote, não API:
o padrão de ``services/`` vale, menos o "cliente HTTP injetável" (não há
cliente HTTP — é leitura de arquivo local, baixado fora daqui).

O que **permanece** do padrão: dataclass de resultado tipado, nunca levantar
exceção pro chamador (falha vira resultado vazio com motivo), e leitura
sempre em streaming via ``arquivo_utils``.

## As três tabelas e por que a ponte é essa

```
SICOR_OPERACAO_BASICA_ESTADO_{ano}.gz   1.313.316 linhas / 47 colunas
   │  PK: REF_BACEN + NU_ORDEM
   │  Tem CD_ESTADO (o filtro de UF), VL_AREA_INFORMADA (a área!),
   │  VL_PARC_CREDITO (valor financiado) e CD_EMPREENDIMENTO.
   │  NÃO tem CPF/CNPJ — cobre crédito público E privado, e o privado
   │  é anônimo por sigilo bancário.
   │
   ├── CD_EMPREENDIMENTO ──> Empreendimento.csv  (domínio, 3.299 linhas)
   │                          dá PRODUTO (a cultura: SOJA, MILHO...)
   │                          ⚠️ liga por CD_EMPREENDIMENTO, NÃO por REF_BACEN
   │
   ├── REF_BACEN ──────────> SICOR_MUTUARIOS.gz    18.362.042 linhas
   │                          dá CD_CPF_CNPJ (a identificação do lead)
   │                          ⚠️ a chave aqui é só REF_BACEN, sem NU_ORDEM
   │
   └── REF_BACEN + NU_ORDEM > SICOR_PROPRIEDADES.gz 27.502.205 linhas
                              dá CD_CAR (bônus pro dossiê)
```

## A chave do resultado é o DOCUMENTO, não o REF_BACEN

Um ``LeadSicor`` é **um produtor**, não uma operação de crédito. Isso importa
porque o mesmo CPF aparece em várias operações — e não só entre anos:

- **Dentro de um único ano.** Em 2026, os 552 ``REF_BACEN`` identificados no
  alvo correspondem a apenas **496 documentos distintos**: 56 operações
  dividem CPF com outra. Emitir um lead por operação produziria 552 registros
  pra 496 produtores, e a persistência da Fase 4 bateria no índice único de
  ``Lead.documento``.
- **Entre anos.** 198 documentos aparecem em 2025 **e** 2026.

Por isso a agregação final é **por documento**, aplicando a mesma regra já
decidida pra múltiplas operações do mesmo REF_BACEN — **área = a maior**
(é a mesma propriedade; parcelas informam áreas parciais), **valor = a soma**
(crédito total tomado). Não é regra nova: é a mesma regra, com o escopo
estendido de "operações do mesmo REF_BACEN" pra "operações do mesmo produtor",
que é o que a chave de negócio sempre foi.

Os números confirmam que a extensão faz sentido: valor mediano de quem aparece
num ano só é R$ 1,0 mi, e de quem aparece nos dois é R$ 2,18 mi (≈2×, como se
espera de dois anos de crédito — não um artefato de dupla contagem). A área
mediana quase não muda (215 ha contra 226 ha), confirmando que ``max`` é
estável: é a mesma propriedade nos dois anos.

⚠️ **Interação com o score, pra decidir na Fase 4:** somar o valor entre anos
faz quem aparece nos dois anos pontuar mais alto em ``valor_financiado``
(peso 10) do que quem aparece num só — em parte por ser produtor maior, em
parte só por ter aparecido duas vezes. Como a régua desse critério ainda é
placeholder (ver ``compute_lead_score``), a calibragem tem que considerar se
o valor usado no score é a soma ou o do ano mais recente.

## ⚠️ 70% das operações não têm mutuário — e isso NÃO é erro

Medido contra o arquivo real (PR, faixa 150–1.400 ha, ano 2026): de 1.856
REF_BACEN no alvo, só **552 (29,7%)** aparecem em ``SICOR_MUTUARIOS``.

Os outros 70% **não são falha de leitura, não são dado corrompido e não são
bug**: são operações de **crédito rural privado**, que o Bacen publica na
tabela de operação (valor, área, cultura, estado) mas cujo mutuário não é
identificado, por sigilo bancário. ``SICOR_MUTUARIOS`` só cobre o universo
de crédito rural **público** (Proagro ou fonte de recurso pública).

Consequência prática: um REF_BACEN sem mutuário **não pode** virar exceção,
nem log de erro, nem `etapa_pulada`. É resultado normal do domínio, contado
em ``ResultadoSicor.refs_sem_mutuario`` pra ficar visível sem virar ruído.
Se esse número um dia cair pra perto de zero, aí sim é sinal de bug — não o
contrário.

## Sobre o ano dos arquivos

O ano é parâmetro, nunca hardcode, e ``anos`` é uma **lista processada de
verdade** — cada ano tem seu próprio arquivo de operação
(``SICOR_OPERACAO_BASICA_ESTADO_{ano}.gz``), e os resultados são cruzados.

Buscar produtor "ativo" exige mais de um ano: quem tomou crédito em 2025 e
não em 2026 continua sendo produtor ativo e lead válido. Medido contra o dado
real (PR, 150–1.400 ha): 2026 sozinho dá 496 produtores identificados; somando
2025, dá **1.439** — 943 a mais, +190%.

⚠️ **O arquivo do ano corrente é PARCIAL.** Os arquivos são particionados por
``DT_EMISSAO``: o de 2025 traz os 12 meses, o de 2026 traz só janeiro a julho
(medido em 25/08/2026). Comparar "2025 vs 2026" como se fossem períodos
equivalentes subestima o ano corrente.

✅ **Não há risco de contar a mesma operação duas vezes.** Confirmado contra
os arquivos reais: a interseção de ``REF_BACEN`` entre 2025 e 2026 é
**exatamente zero** — cada operação aparece só no arquivo do ano em que foi
emitida. Somar valor entre anos soma operações distintas, não a mesma safra
duas vezes.

"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.services.arquivo_utils import (
    decimal_ou_none,
    encontrar_arquivo,
    indices_de,
    leitor_csv,
    texto_ou_none,
)

logger = logging.getLogger(__name__)

#: Faixa de área que interessa à Inova (hectares). Veio da Carolina como
#: intervalo; a régua de pontuação DENTRO dela segue pendente (ver o
#: TODO(Carolina) em app/scoring/compute_lead_score.py).
AREA_MIN_HA_PADRAO = 150.0
AREA_MAX_HA_PADRAO = 1400.0

#: Nome-base dos arquivos. `encontrar_arquivo` ignora caixa e testa extensões,
#: mas o padrão em si vem daqui pra ficar num lugar só.
ARQ_OPERACAO = "SICOR_OPERACAO_BASICA_ESTADO_{ano}"
ARQ_MUTUARIOS = "SICOR_MUTUARIOS"
ARQ_PROPRIEDADES = "SICOR_PROPRIEDADES"
ARQ_EMPREENDIMENTO = "Empreendimento.csv"


@dataclass(frozen=True, slots=True)
class LeadSicor:
    """Um produtor identificado no Sicor, pronto pra virar ``Lead``.

    ``documento`` já vem só com dígitos e com zero à esquerda preservado —
    compatível direto com ``app.core.documentos`` sem normalização extra
    (confirmado contra o arquivo real).
    """

    documento: str
    tipo_beneficiario: str | None
    area_ha: float | None
    valor_financiado: float | None
    culturas: tuple[str, ...]
    codigos_car: tuple[str, ...]
    n_operacoes: int
    #: Todas as operações deste produtor, de todos os anos processados.
    refs_bacen: tuple[str, ...] = ()
    #: Anos em que o produtor tomou crédito. Mais de um = produtor recorrente.
    anos: tuple[int, ...] = ()
    #: Documento dos demais mutuários da operação (avalista, cônjuge, sócio).
    #: O lead é sempre o mutuário principal (CD_PRIMEIRO='S').
    coobrigados: tuple[str, ...] = ()

    @property
    def recorrente(self) -> bool:
        """Tomou crédito em mais de um dos anos processados."""
        return len(self.anos) > 1


@dataclass(frozen=True, slots=True)
class ResultadoSicor:
    """Resultado tipado da extração. Nunca vem de uma exceção vazando."""

    leads: tuple[LeadSicor, ...] = ()
    operacoes_lidas: int = 0
    refs_no_alvo: int = 0
    refs_identificados: int = 0
    #: Esperado ~70%. NÃO é erro — ver o docstring do módulo.
    refs_sem_mutuario: int = 0
    #: Anos efetivamente processados (arquivo encontrado e lido).
    anos_processados: tuple[int, ...] = ()
    #: ``ano -> REF_BACEN no alvo`` naquele ano, pra comparar períodos.
    refs_por_ano: dict[int, int] = field(default_factory=dict)
    #: Etapas puladas com motivo, no mesmo formato de ``Lead.etapas_puladas``.
    etapas_puladas: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return bool(self.leads)


def _pular(motivo: str, etapa: str) -> dict[str, str]:
    logger.warning("sicor: etapa '%s' pulada — %s", etapa, motivo)
    return {"etapa": etapa, "motivo": motivo}


def carregar_culturas(caminho: Path) -> dict[str, str]:
    """``CD_EMPREENDIMENTO`` → ``PRODUTO``. Cabe em memória (3.299 linhas).

    ⚠️ A coluna-chave no arquivo real chama ``CODIGO`` (após tirar o ``#``),
    **não** ``CD_EMPREENDIMENTO`` como o manual do Bacen documenta. O manual
    está desatualizado; o arquivo manda.
    """
    with leitor_csv(caminho) as (cabecalho, linhas):
        i_cod, i_produto = indices_de(cabecalho, "CODIGO", "PRODUTO")
        return {
            linha[i_cod].strip(): linha[i_produto].strip()
            for linha in linhas
            if len(linha) > max(i_cod, i_produto)
        }


def extrair_leads_sicor(
    diretorio: Path | str,
    *,
    uf: str,
    anos: Sequence[int],
    area_min_ha: float = AREA_MIN_HA_PADRAO,
    area_max_ha: float = AREA_MAX_HA_PADRAO,
    culturas_alvo: Collection[str] | None = None,
    incluir_car: bool = True,
) -> ResultadoSicor:
    """Extrai produtores identificáveis do Sicor pra uma UF e uma faixa de área.

    Processa **todos** os ``anos`` informados: um arquivo de operação por ano,
    resultados cruzados, e agregação final **por documento** (ver o docstring
    do módulo). Um ano cujo arquivo não existe vira etapa pulada e os demais
    seguem — perder 2024 não pode custar 2025 e 2026.

    Nunca levanta: qualquer falha vira ``ResultadoSicor`` com ``etapas_puladas``
    preenchido. É o mesmo princípio do ``_rodar_etapa`` do pipeline — uma fonte
    que falhou não pode derrubar a busca inteira.
    """
    diretorio = Path(diretorio)
    puladas: list[dict[str, str]] = []

    if not anos:
        return ResultadoSicor(
            etapas_puladas=(_pular("nenhum ano informado", "sicor_operacao"),)
        )

    # Ano repetido na lista leria o MESMO arquivo duas vezes e dobraria o
    # valor financiado de todo mundo, em silêncio — a agregação soma. Dedup
    # preservando a ordem informada.
    anos = list(dict.fromkeys(anos))

    # --- Culturas: tabela de domínio, pequena, carregada uma vez -----------
    culturas_por_codigo: dict[str, str] = {}
    arq_empreendimento = encontrar_arquivo(diretorio, ARQ_EMPREENDIMENTO)
    if arq_empreendimento is None:
        puladas.append(
            _pular(
                "Empreendimento.csv ausente — leads saem sem cultura", "sicor_cultura"
            )
        )
    else:
        try:
            culturas_por_codigo = carregar_culturas(arq_empreendimento)
        except (OSError, KeyError, ValueError) as exc:
            puladas.append(_pular(f"falha ao ler culturas: {exc}", "sicor_cultura"))

    alvo_normalizado = (
        {c.strip().upper() for c in culturas_alvo} if culturas_alvo else None
    )
    uf_alvo = uf.strip().upper()

    # --- Passo 1: uma passada por ano, acumulando por REF_BACEN -----------
    area_por_ref: dict[str, float] = {}
    valor_por_ref: dict[str, float] = {}
    culturas_por_ref: dict[str, set[str]] = {}
    ops_por_ref: dict[str, int] = {}
    anos_por_ref: dict[str, set[int]] = {}
    refs_por_ano: dict[int, int] = {}
    anos_processados: list[int] = []
    operacoes_lidas = 0

    for ano in anos:
        arq_operacao = encontrar_arquivo(diretorio, ARQ_OPERACAO.format(ano=ano))
        if arq_operacao is None:
            puladas.append(
                _pular(
                    f"arquivo de operação do ano {ano} não encontrado em {diretorio}",
                    "sicor_operacao",
                )
            )
            continue
        refs_do_ano: set[str] = set()
        try:
            with leitor_csv(arq_operacao) as (cabecalho, linhas):
                i_ref, i_uf, i_area, i_valor, i_emp = indices_de(
                    cabecalho,
                    "REF_BACEN",
                    "CD_ESTADO",
                    "VL_AREA_INFORMADA",
                    "VL_PARC_CREDITO",
                    "CD_EMPREENDIMENTO",
                )
                maior = max(i_ref, i_uf, i_area, i_valor, i_emp)
                for linha in linhas:
                    operacoes_lidas += 1
                    if len(linha) <= maior or linha[i_uf].strip().upper() != uf_alvo:
                        continue
                    area = decimal_ou_none(linha[i_area])
                    if area is None or not (area_min_ha <= area <= area_max_ha):
                        continue

                    produto = culturas_por_codigo.get(linha[i_emp].strip())
                    if alvo_normalizado is not None and (
                        produto is None or produto.upper() not in alvo_normalizado
                    ):
                        continue

                    ref = linha[i_ref].strip()
                    refs_do_ano.add(ref)
                    # Regra de agregação (mesma da Fase 3, agora também entre
                    # anos): área = a MAIOR, valor = a SOMA.
                    area_por_ref[ref] = max(area_por_ref.get(ref, 0.0), area)
                    valor = decimal_ou_none(linha[i_valor])
                    if valor is not None:
                        valor_por_ref[ref] = valor_por_ref.get(ref, 0.0) + valor
                    ops_por_ref[ref] = ops_por_ref.get(ref, 0) + 1
                    anos_por_ref.setdefault(ref, set()).add(ano)
                    if produto:
                        culturas_por_ref.setdefault(ref, set()).add(produto)
        except (OSError, KeyError, ValueError) as exc:
            puladas.append(
                _pular(f"falha ao ler operações de {ano}: {exc}", "sicor_operacao")
            )
            continue
        anos_processados.append(ano)
        refs_por_ano[ano] = len(refs_do_ano)
        logger.info(
            "sicor: ano %s -> %d REF_BACEN no alvo (%s, %.0f-%.0f ha)",
            ano, len(refs_do_ano), uf_alvo, area_min_ha, area_max_ha,
        )

    refs_alvo = set(area_por_ref)
    if not refs_alvo:
        return ResultadoSicor(
            operacoes_lidas=operacoes_lidas,
            anos_processados=tuple(anos_processados),
            refs_por_ano=refs_por_ano,
            etapas_puladas=(
                *puladas,
                _pular(
                    f"nenhuma operação em {uf_alvo} na faixa "
                    f"{area_min_ha:.0f}–{area_max_ha:.0f} ha nos anos {list(anos)}",
                    "sicor_operacao",
                ),
            ),
        )

    # --- Passo 2: mutuários (streaming) — segunda passada, só o que casa ---
    # Um arquivo só, cobrindo todos os anos: as 18 milhões de linhas são lidas
    # UMA vez, independente de quantos anos foram pedidos. Indexá-las seria o
    # erro de memória da seção 6; aqui só entra quem está em `refs_alvo`.
    principais: dict[str, tuple[str, str | None]] = {}
    coobrigados: dict[str, list[str]] = {}
    arq_mutuarios = encontrar_arquivo(diretorio, ARQ_MUTUARIOS)
    if arq_mutuarios is None:
        return ResultadoSicor(
            operacoes_lidas=operacoes_lidas,
            refs_no_alvo=len(refs_alvo),
            anos_processados=tuple(anos_processados),
            refs_por_ano=refs_por_ano,
            etapas_puladas=(
                *puladas,
                _pular(
                    "SICOR_MUTUARIOS ausente — sem ele não há lead identificável",
                    "sicor_mutuarios",
                ),
            ),
        )
    try:
        with leitor_csv(arq_mutuarios) as (cabecalho, linhas):
            i_ref, i_doc, i_tipo, i_primeiro = indices_de(
                cabecalho,
                "REF_BACEN",
                "CD_CPF_CNPJ",
                "CD_TIPO_BENEFICIARIO",
                "CD_PRIMEIRO",
            )
            maior = max(i_ref, i_doc, i_tipo, i_primeiro)
            for linha in linhas:
                if len(linha) <= maior:
                    continue
                ref = linha[i_ref].strip()
                if ref not in refs_alvo:
                    continue
                documento = texto_ou_none(linha[i_doc])
                if documento is None:
                    continue
                if linha[i_primeiro].strip().upper() == "S":
                    principais[ref] = (documento, texto_ou_none(linha[i_tipo]))
                else:
                    coobrigados.setdefault(ref, []).append(documento)
    except (OSError, KeyError, ValueError) as exc:
        return ResultadoSicor(
            operacoes_lidas=operacoes_lidas,
            refs_no_alvo=len(refs_alvo),
            anos_processados=tuple(anos_processados),
            refs_por_ano=refs_por_ano,
            etapas_puladas=(
                *puladas,
                _pular(f"falha ao ler mutuários: {exc}", "sicor_mutuarios"),
            ),
        )

    # --- Passo 3: propriedades (streaming) — CD_CAR, bônus pro dossiê ------
    cars_por_ref: dict[str, list[str]] = {}
    if incluir_car:
        arq_propriedades = encontrar_arquivo(diretorio, ARQ_PROPRIEDADES)
        if arq_propriedades is None:
            puladas.append(
                _pular(
                    "SICOR_PROPRIEDADES ausente — leads saem sem código do CAR",
                    "sicor_car",
                )
            )
        else:
            try:
                with leitor_csv(arq_propriedades) as (cabecalho, linhas):
                    i_ref, i_car = indices_de(cabecalho, "REF_BACEN", "CD_CAR")
                    maior = max(i_ref, i_car)
                    identificados = set(principais)
                    for linha in linhas:
                        if len(linha) <= maior:
                            continue
                        ref = linha[i_ref].strip()
                        if ref not in identificados:
                            continue
                        # "-1" é sentinela de vazio nesta coluna, não um código.
                        car = texto_ou_none(linha[i_car])
                        if car is not None and car not in cars_por_ref.setdefault(
                            ref, []
                        ):
                            cars_por_ref[ref].append(car)
            except (OSError, KeyError, ValueError) as exc:
                puladas.append(_pular(f"falha ao ler propriedades: {exc}", "sicor_car"))

    # --- Passo 4: agregação POR DOCUMENTO ---------------------------------
    # A chave de negócio é o documento, não o REF_BACEN: o mesmo CPF aparece
    # em várias operações, dentro do mesmo ano e entre anos. Emitir um lead
    # por operação estouraria o índice único de Lead.documento na Fase 4.
    # Mesma regra de sempre — área = a maior, valor = a soma —, agora no
    # escopo do produtor.
    por_documento: dict[str, dict] = {}
    for ref, (documento, tipo) in principais.items():
        acc = por_documento.setdefault(
            documento,
            {
                "tipo": tipo,
                "area": None,
                "valor": None,
                "culturas": set(),
                "cars": [],
                "refs": [],
                "anos": set(),
                "ops": 0,
                "coobrigados": [],
            },
        )
        area = area_por_ref.get(ref)
        if area is not None:
            acc["area"] = area if acc["area"] is None else max(acc["area"], area)
        valor = valor_por_ref.get(ref)
        if valor is not None:
            acc["valor"] = valor if acc["valor"] is None else acc["valor"] + valor
        acc["culturas"] |= culturas_por_ref.get(ref, set())
        for car in cars_por_ref.get(ref, ()):
            if car not in acc["cars"]:
                acc["cars"].append(car)
        acc["refs"].append(ref)
        acc["anos"] |= anos_por_ref.get(ref, set())
        acc["ops"] += ops_por_ref.get(ref, 0)
        for co in coobrigados.get(ref, ()):
            if co not in acc["coobrigados"]:
                acc["coobrigados"].append(co)
        if acc["tipo"] is None:
            acc["tipo"] = tipo

    leads = tuple(
        LeadSicor(
            documento=documento,
            tipo_beneficiario=acc["tipo"],
            area_ha=acc["area"],
            valor_financiado=acc["valor"],
            culturas=tuple(sorted(acc["culturas"])),
            codigos_car=tuple(acc["cars"]),
            n_operacoes=acc["ops"],
            refs_bacen=tuple(sorted(acc["refs"])),
            anos=tuple(sorted(acc["anos"])),
            coobrigados=tuple(acc["coobrigados"]),
        )
        for documento, acc in sorted(por_documento.items())
    )

    return ResultadoSicor(
        leads=leads,
        operacoes_lidas=operacoes_lidas,
        refs_no_alvo=len(refs_alvo),
        refs_identificados=len(principais),
        # Diferença esperada e normal: crédito privado, sem identificação.
        refs_sem_mutuario=len(refs_alvo) - len(principais),
        anos_processados=tuple(anos_processados),
        refs_por_ano=refs_por_ano,
        etapas_puladas=tuple(puladas),
    )
