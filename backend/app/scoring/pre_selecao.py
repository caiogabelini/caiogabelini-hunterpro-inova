"""Pré-seleção em 2 fases — o corte de volume antes de gastar API.

É o padrão mais importante do docs_fundacao.md (§3): decidir **quem vale a
pena enriquecer** usando só sinal gratuito e já disponível, antes de qualquer
chamada paga. Sem isso, uma busca processaria o universo inteiro com API paga.

## Por que a estrutura aqui é diferente da do Minotto

No Minotto há **uma** população (empresas achadas por CNAE) e a Fase 1 ordena
todo esse universo por um sinal binário que qualquer uma delas pode ter ou não
(dívida ativa PGFN). As duas fases são recortes da mesma lista.

Aqui são **duas populações de origem diferente**, que só se encontram pelo
documento:

- **Sicor** — produtor rural, 97,4% pessoa física. Já chega do parser com
  ``tamanho_propriedade``, ``valor_financiado`` e cultura **resolvidos**: não
  há nada a buscar, o dado veio no arquivo em lote.
- **Receita Federal** — CNPJ de agroindústria, cooperativa e atacado de
  insumos, achado por CNAE. **Não tem área de propriedade nenhuma** e não tem
  como ter de graça.

Então as fases não são dois recortes de uma lista ordenada: são **duas listas
distintas, em ordem de prioridade**. A Fase 1 é a população que tem o critério
#1 da cliente resolvido; a Fase 2 é a que não tem.

## ⚠️ A Fase 2 não pode ser ordenada por score — nenhum critério é computável

Medido contra o dado real. Dos 9 critérios de ``SCORING_CRITERIA``, o que dá
pra calcular de graça em cada população:

| critério | peso | Sicor | Receita Federal |
|---|---|---|---|
| tamanho_propriedade | 30 | ✅ | ❌ (não existe de graça) |
| decisor_identificavel | 20 | ❌ | ❌ (decisão: BrasilAPI, pós-corte) |
| semente_sicor_cultura | 15 | ✅ | ❌ (por definição) |
| whatsapp_ativo | 15 | ❌ | ❌ (enriquecimento pago) |
| valor_financiado | 10 | ✅ | ❌ |
| email_validado | 5 | ❌ | ❌ (pago) |
| presenca_digital | 5 | ❌ | ❌ (pago) |
| radar_exportacao / google_rating | 0 | ❌ | ❌ |

Teto do score parcial: **55 de 100 no Sicor, 0 de 100 na Receita Federal**.

Ou seja: a Fase 2 ordena por uma **convenção documentada**, não por score —
não há sinal comum pra ranquear. Ver ``ordenar_candidatos_rfb``.

## Por que reusar ``calcular_score`` em vez de somar pesos na mão

A Fase 1 monta um dict só com os sinais gratuitos e chama o motor de score já
calibrado. Assim os pesos vivem num lugar só: recalibrar ``rules.py`` com a
Carolina muda a ordem da pré-seleção junto, sem ninguém lembrar de atualizar
dois lugares. O motor já trata sinal ausente como 0 e lista em ``ausentes`` —
que é exatamente a representação honesta de "esse critério ainda não foi
medido".

⚠️ **A ordenação da Fase 1 pode mudar quando a régua for calibrada.** Duas
pendências conhecidas: a curva de ``tamanho_propriedade`` (peso 30, hoje rampa
linear placeholder) e a agregação de ``valor_financiado`` entre anos (hoje
soma; pode virar "ano mais recente"). Nenhuma trava esta implementação, mas as
duas mexem na ordem.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.scoring.compute_lead_score import calcular_score
from app.services.receita_federal import EstabelecimentoRFB
from app.services.sicor import LeadSicor

logger = logging.getLogger(__name__)

ORIGEM_SICOR = "sicor"
ORIGEM_RFB = "receita_federal"


@dataclass(frozen=True, slots=True)
class Candidato:
    """Um candidato à cota, já com o que se sabe dele de graça."""

    documento: str
    origem: str
    nome: str
    uf: str | None
    municipio: str | None
    #: Pontos do score calculados **só com sinal gratuito**. Não é o score
    #: final do lead — falta tudo que depende de enriquecimento.
    pontos_parciais: float
    #: Critérios que ainda não foram medidos. Vira ``Lead.etapas_puladas``
    #: se o candidato for cortado antes do enriquecimento.
    criterios_ausentes: tuple[str, ...] = ()
    dados_nicho: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResultadoPreSelecao:
    """O corte, com o rastro de como foi feito."""

    selecionados: tuple[Candidato, ...] = ()
    cota: int = 0
    disponiveis_fase1: int = 0
    disponiveis_fase2: int = 0
    selecionados_fase1: int = 0
    selecionados_fase2: int = 0
    #: Candidatos da Fase 2 descartados por já existirem na Fase 1.
    descartados_por_dedup: int = 0

    @property
    def fase2_acionada(self) -> bool:
        """A Fase 2 só roda se sobrar vaga depois da Fase 1."""
        return self.selecionados_fase2 > 0

    @property
    def cota_preenchida(self) -> bool:
        return self.cota > 0 and len(self.selecionados) >= self.cota


def sinais_gratuitos_sicor(
    lead: LeadSicor, *, culturas_alvo: Collection[str] | None = None
) -> dict[str, Any]:
    """Monta o dict de sinais **gratuitos** de um produtor do Sicor.

    Só entra o que já veio do arquivo em lote. Nada aqui custa uma chamada de
    API — é a regra de ouro da pré-seleção (§3).

    ``semente_sicor_cultura``: o "semente Sicor" é verdade por construção
    (o candidato veio dessa semente); o "cultura bate" depende de
    ``culturas_alvo``. Sem alvo informado, basta a cultura ser conhecida.
    """
    if culturas_alvo is None:
        cultura_bate = bool(lead.culturas)
    else:
        alvo = {c.strip().upper() for c in culturas_alvo}
        cultura_bate = any(c.upper() in alvo for c in lead.culturas)
    return {
        "tamanho_propriedade": lead.area_ha,
        "valor_financiado": lead.valor_financiado,
        "semente_sicor_cultura": cultura_bate,
        # decisor_identificavel NÃO entra: é BrasilAPI, uma chamada por
        # documento, e roda DEPOIS do corte (ver o pipeline em workers/busca).
        # whatsapp_ativo / email_validado / presenca_digital: enriquecimento
        # pago, por definição indisponíveis aqui.
    }


def candidato_de_lead_sicor(
    lead: LeadSicor, *, culturas_alvo: Collection[str] | None = None
) -> Candidato:
    sinais = sinais_gratuitos_sicor(lead, culturas_alvo=culturas_alvo)
    resultado = calcular_score(sinais)
    return Candidato(
        documento=lead.documento,
        origem=ORIGEM_SICOR,
        # O Sicor não publica nome do mutuário — só o documento. O nome vem
        # do enriquecimento (BrasilAPI pra CNPJ; pra CPF, ver a ressalva do
        # pipeline sobre pessoa física).
        nome="",
        uf=None,
        municipio=None,
        pontos_parciais=resultado.pontos,
        criterios_ausentes=resultado.ausentes,
        dados_nicho={
            "origem": ORIGEM_SICOR,
            "area_ha": lead.area_ha,
            "valor_financiado": lead.valor_financiado,
            "culturas": list(lead.culturas),
            "codigos_car": list(lead.codigos_car),
            "anos_credito": list(lead.anos),
            # Data (AAAAMMDD) da operação que definiu área e valor. Usada
            # como desempate da Fase 1 — ver `ordenar_candidatos_fase1`.
            "data_operacao": lead.data_operacao,
            "refs_bacen": list(lead.refs_bacen),
            "n_operacoes": lead.n_operacoes,
            "recorrente": lead.recorrente,
            "tipo_beneficiario": lead.tipo_beneficiario,
        },
    )


def candidato_de_estabelecimento_rfb(est: EstabelecimentoRFB) -> Candidato:
    """Candidato vindo da Receita Federal.

    ``pontos_parciais`` é **sempre 0.0**: nenhum critério do score é
    computável de graça pra essa população (ver a tabela no docstring do
    módulo). Não é bug — é o motivo de a Fase 2 existir como fase separada
    em vez de entrar na mesma lista ordenada.
    """
    resultado = calcular_score({})
    return Candidato(
        documento=est.cnpj,
        origem=ORIGEM_RFB,
        nome=est.razao_social or est.nome_fantasia,
        uf=est.uf or None,
        municipio=est.municipio or None,
        pontos_parciais=resultado.pontos,
        criterios_ausentes=resultado.ausentes,
        dados_nicho={
            "origem": ORIGEM_RFB,
            "cnae": est.cnae_fiscal_principal,
            "cnae_descricao": est.cnae_descricao,
            "situacao_cadastral": est.situacao_descricao,
            "eh_cooperativa": est.eh_cooperativa,
            "natureza_juridica": est.natureza_juridica,
            "matriz_ou_filial": est.identificador_matriz_filial,
            "data_inicio_atividade": est.data_inicio_atividade,
        },
    )


#: Valor de ``data_operacao`` quando o candidato não tem data (o lado
#: Receita Federal não tem operação de crédito). Ordena por último.
_SEM_DATA = 0


def chave_desempate_fase1(candidato: Candidato) -> tuple:
    """Chave de ordenação da Fase 1: score parcial e, no empate, recência.

    ⚠️ **O desempate é escolha NOSSA, não da cliente.** Mesmo espírito do
    ``confirmado=False`` de ``SCORING_CRITERIA``: o score em si foi calibrado
    com a Carolina, este critério secundário **não foi**. Ele existe porque
    sem ele a Fase 1 não seleciona nada.

    **O problema que ele resolve.** Depois da calibragem, as réguas de área e
    valor viraram patamar único, e o filtro da semente já garante que todo
    lead está dentro da faixa. Resultado medido na população real do PR
    (2025+2026): **2.779 dos 2.806 leads empatam em 55,0 pontos** — 99%.
    Com 60 vagas, a cota estava sendo preenchida por ordem alfabética de CPF,
    que não é seleção, é sorteio disfarçado.

    **A ordem, e por quê:**

    1. ``pontos_parciais`` — o score calibrado continua mandando. Nada aqui
       substitui ``calcular_score``; o desempate só age quando ele empata.
    2. ``data_operacao`` mais recente primeiro — quem tomou crédito há menos
       tempo tem mais chance de estar operando agora. É o sinal de atividade
       mais barato que temos: já vem do arquivo, custo zero.
    3. **recorrente** antes de não-recorrente — tomar crédito em mais de um
       ano é indício de operação continuada, não pontual.
    4. ``documento`` — determinismo puro, como já era. Sem ele, dois
       candidatos idênticos trocariam de lugar entre execuções e o mesmo
       lead entraria ou sairia da cota por acaso.

    ⚠️ **Levar pra Carolina.** Recência é um palpite razoável, não a
    preferência dela. Se ela disser que prefere, por exemplo, maior área
    dentro da faixa, ou cultura específica de grão, é aqui que muda — e só
    aqui, porque os pesos do score não se alteram.
    """
    data = candidato.dados_nicho.get("data_operacao") or ""
    data_int = int(data) if str(data).isdigit() else _SEM_DATA
    recorrente = bool(candidato.dados_nicho.get("recorrente"))
    return (
        -candidato.pontos_parciais,
        -data_int,          # negativo = mais recente primeiro; sem data vai pro fim
        not recorrente,     # False (0) ordena antes de True (1)
        candidato.documento,
    )


def ordenar_candidatos_fase1(candidatos: Sequence[Candidato]) -> list[Candidato]:
    """Ordena a Fase 1. Ver ``chave_desempate_fase1`` pro critério e a
    ressalva de que o desempate não foi validado com a cliente."""
    return sorted(candidatos, key=chave_desempate_fase1)


def ordenar_candidatos_fase2(candidatos: Sequence[Candidato]) -> list[Candidato]:
    """Convenção documentada, **não score** — não há sinal comum pra ranquear.

    Ordem: cooperativa primeiro, depois matriz antes de filial, depois
    documento (estável). A situação cadastral ativa já foi aplicada como
    filtro na leitura, não é critério de ordem.

    ⚠️ **O sinal de cooperativa é hoje pouco confiável.** ``eh_cooperativa``
    vem de ``natureza_juridica``, que exige o join com o arquivo EMPRESAS — e
    com só 1 das 10 fatias em disco, apenas 37 de 588 CNPJs do PR resolvem.
    Ordenar por isso hoje é, na prática, ordenar por "caiu na fatia que
    baixamos". Com as 10 fatias vira um sinal legítimo. Até lá o efeito é
    pequeno e o desempate estável carrega o resto.

    Matriz antes de filial é desempate barato e defensável: a filial divide
    raiz de CNPJ com a matriz e tende a repetir a mesma empresa.
    """
    return sorted(
        candidatos,
        key=lambda c: (
            not c.dados_nicho.get("eh_cooperativa", False),
            c.dados_nicho.get("matriz_ou_filial", "9") != "1",
            c.documento,
        ),
    )


def pre_selecionar(
    leads_sicor: Sequence[LeadSicor],
    estabelecimentos_rfb: Sequence[EstabelecimentoRFB],
    *,
    cota: int,
    culturas_alvo: Collection[str] | None = None,
) -> ResultadoPreSelecao:
    """Executa as 2 fases e devolve os candidatos que passaram no corte.

    ``cota <= 0`` significa **sem cota** (processa tudo) — a convenção de
    "desligar libera, não bloqueia" da seção 5.
    """
    # --- FASE 1 — população Sicor -----------------------------------------
    candidatos_1 = ordenar_candidatos_fase1(
        [candidato_de_lead_sicor(l, culturas_alvo=culturas_alvo) for l in leads_sicor]
    )
    sem_cota = cota <= 0
    selecionados = list(candidatos_1) if sem_cota else candidatos_1[:cota]
    vagas = 0 if sem_cota else max(cota - len(selecionados), 0)

    logger.info(
        "pre_selecao: fase 1 (Sicor) — %d disponíveis, %d selecionados, %d vagas restantes",
        len(candidatos_1), len(selecionados), vagas,
    )

    # --- Dedup entre populações, ANTES da Fase 2 --------------------------
    # A chave é o documento, a mesma do índice único de Lead.documento — o
    # mesmo CNPJ pode ser produtor no Sicor e ter CNAE agro na Receita.
    #
    # Deduplica contra TODA a população da Fase 1, não só contra quem foi
    # selecionado. Um documento que a Fase 1 avaliou e cortou não pode voltar
    # pela Fase 2: lá ele foi julgado com mais informação (teto de 55 pontos)
    # e perdeu; readmiti-lo com 0 ponto contradiz o próprio ranking.
    #
    # Na prática as duas leituras coincidem — se sobrou vaga pra Fase 2 rodar,
    # é porque a Fase 1 levou todo mundo. A diferença só aparece na contagem
    # de `descartados_por_dedup`, que assim reporta o total real de
    # sobreposição entre as populações, independente da cota.
    documentos_fase1 = {c.documento for c in candidatos_1}
    candidatos_2_brutos = [
        candidato_de_estabelecimento_rfb(e) for e in estabelecimentos_rfb
    ]
    candidatos_2 = [c for c in candidatos_2_brutos if c.documento not in documentos_fase1]
    descartados = len(candidatos_2_brutos) - len(candidatos_2)
    if descartados:
        logger.info(
            "pre_selecao: %d CNPJ da Receita já vieram pelo Sicor — descartados", descartados
        )

    # --- FASE 2 — só se sobrou vaga ---------------------------------------
    selecionados_2: list[Candidato] = []
    if sem_cota:
        selecionados_2 = ordenar_candidatos_fase2(candidatos_2)
    elif vagas > 0:
        selecionados_2 = ordenar_candidatos_fase2(candidatos_2)[:vagas]
    else:
        logger.info(
            "pre_selecao: fase 2 não acionada — a fase 1 preencheu a cota de %d", cota
        )
    selecionados.extend(selecionados_2)

    return ResultadoPreSelecao(
        selecionados=tuple(selecionados),
        cota=cota,
        disponiveis_fase1=len(candidatos_1),
        disponiveis_fase2=len(candidatos_2),
        selecionados_fase1=len(selecionados) - len(selecionados_2),
        selecionados_fase2=len(selecionados_2),
        descartados_por_dedup=descartados,
    )
