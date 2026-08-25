"""Orquestração da busca mensal — leitura das sementes + pré-seleção.

## Por que num módulo próprio, e não dentro de ``celery_app.py``

O Minotto põe ``executar_busca_mensal`` dentro do ``celery_app.py``, junto com
as tasks. Aqui está separado por dois motivos concretos:

1. **Testabilidade.** Assim a orquestração roda numa chamada de função direta,
   sem broker, sem worker, sem Redis. A suíte exercita a busca inteira contra
   os arquivos reais sem subir nada.
2. **A lição do worker (seção 6, que mordeu 3 vezes no Minotto).** Quanto
   menos coisa nasce dentro do ``celery_app``, menor a chance de o processo do
   worker conhecer um conjunto de módulos diferente do processo do
   ``uvicorn``. ``celery_app.py`` fica sendo só o registro da task, e importa
   este módulo e ``app.models`` explicitamente.

## O que esta fase faz — e onde ela para

Segue os passos da §3 do docs_fundacao.md até o corte de volume, **e para**:

```
1. Trava de segurança: as duas fontes gratuitas têm arquivo? Senão, aborta.
2. Lê a semente Sicor      (produtor rural, PF+PJ, multi-ano)
3. Lê a semente Receita    (CNPJ agro por CNAE, situação ativa)
4. Pré-seleciona em 2 fases, deduplicando por documento
5. ⛔ PARA AQUI — o enriquecimento pago é fase futura
```

Os passos 5 a 10 da §3 (Google Places, Firecrawl, WhatsApp, e-mail, BrasilAPI
pro decisor, persistência e ``compute_lead_score`` final) **não** estão aqui.
Ver ``enriquecer_selecionados`` no fim do módulo.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.scoring.pre_selecao import Candidato, ResultadoPreSelecao, pre_selecionar
from app.services.receita_federal import (
    CNAES_AGRO_TODOS,
    buscar_semente_cnpj,
    encontrar_estabelecimentos,
)
from app.services.sicor import ARQ_OPERACAO, encontrar_arquivo, extrair_leads_sicor

logger = logging.getLogger(__name__)

UF_PADRAO = "PR"  # foco da Inova; parametrizável em toda função pública


@dataclass(frozen=True, slots=True)
class ResultadoBusca:
    """Resultado da busca. Nunca vem de uma exceção vazando pro chamador."""

    pre_selecao: ResultadoPreSelecao | None = None
    #: Motivo do aborto, quando a trava de segurança dispara. ``None`` = correu.
    abortada_por: str | None = None
    #: Avisos agregados, renderizados no painel admin (seção 6: etapa pulada
    #: tem que chegar à tela, senão o erro fica invisível).
    erros: tuple[dict[str, str], ...] = field(default_factory=tuple)
    leads_sicor: int = 0
    estabelecimentos_rfb: int = 0

    @property
    def ok(self) -> bool:
        return self.abortada_por is None and self.pre_selecao is not None

    @property
    def selecionados(self) -> tuple[Candidato, ...]:
        return self.pre_selecao.selecionados if self.pre_selecao else ()


def verificar_fontes(
    dir_sicor: Path, dir_rfb: Path, *, anos: Sequence[int]
) -> str | None:
    """Trava de segurança: confere que as fontes gratuitas existem.

    Devolve o motivo do aborto, ou ``None`` se está tudo no lugar.

    **Roda ANTES de qualquer processamento**, de propósito. Nesta fase não há
    dinheiro em jogo — o enriquecimento pago ainda não existe —, mas a
    disciplina é a mesma da seção 6: uma fonte gratuita não populada tem que
    abortar antes, não depois. Quando o enriquecimento entrar, a trava já vai
    estar no lugar certo, e não é preciso lembrar de adicioná-la.

    Sem isso o modo de falha é o pior possível e já aconteceu no Minotto:
    "busca concluída com sucesso, 0 leads", sem nenhuma pista.
    """
    if not any(
        encontrar_arquivo(dir_sicor, ARQ_OPERACAO.format(ano=ano)) for ano in anos
    ):
        return (
            f"semente Sicor indisponível: nenhum arquivo de operação para os "
            f"anos {list(anos)} em {dir_sicor}"
        )
    if encontrar_arquivo(dir_sicor, "SICOR_MUTUARIOS") is None:
        return (
            f"semente Sicor incompleta: SICOR_MUTUARIOS ausente em {dir_sicor} — "
            f"sem ele nenhum produtor é identificável"
        )
    if not encontrar_estabelecimentos(dir_rfb):
        return (
            f"semente Receita Federal indisponível: nenhum arquivo "
            f"ESTABELECIMENTOS em {dir_rfb}"
        )
    return None


def executar_busca_mensal(
    *,
    dir_sicor: Path | str,
    dir_rfb: Path | str,
    anos: Sequence[int],
    uf: str = UF_PADRAO,
    cnaes: Collection[str] = CNAES_AGRO_TODOS,
    culturas_alvo: Collection[str] | None = None,
    cota: int | None = None,
) -> ResultadoBusca:
    """Lê as duas sementes, pré-seleciona em 2 fases e para antes do custo.

    ``cota`` vem de ``settings.cota_pre_selecao`` quando não informada
    (``LEADS_POR_BUSCA × LEADS_MARGEM_PRE_SELECAO``). Nunca levanta: falha
    vira ``ResultadoBusca`` com ``abortada_por`` ou ``erros`` preenchidos.
    """
    dir_sicor, dir_rfb = Path(dir_sicor), Path(dir_rfb)
    cota = settings.cota_pre_selecao if cota is None else cota

    # --- 1. Trava de segurança, ANTES de qualquer processamento ----------
    motivo = verificar_fontes(dir_sicor, dir_rfb, anos=anos)
    if motivo is not None:
        logger.error("busca abortada na trava de segurança: %s", motivo)
        return ResultadoBusca(abortada_por=motivo)

    erros: list[dict[str, str]] = []

    # --- 2. Semente Sicor -------------------------------------------------
    resultado_sicor = extrair_leads_sicor(
        dir_sicor, uf=uf, anos=anos, culturas_alvo=culturas_alvo
    )
    erros.extend(resultado_sicor.etapas_puladas)
    logger.info(
        "busca: semente Sicor -> %d produtores (%d REF_BACEN no alvo, %d sem mutuário)",
        len(resultado_sicor.leads),
        resultado_sicor.refs_no_alvo,
        resultado_sicor.refs_sem_mutuario,
    )

    # --- 3. Semente Receita Federal ---------------------------------------
    resultado_rfb = buscar_semente_cnpj(dir_rfb, cnaes=cnaes, ufs={uf})
    erros.extend(resultado_rfb.etapas_puladas)
    logger.info(
        "busca: semente Receita Federal -> %d estabelecimentos agro ativos em %s",
        len(resultado_rfb.estabelecimentos),
        uf,
    )

    # --- 4. Pré-seleção em 2 fases ---------------------------------------
    pre = pre_selecionar(
        resultado_sicor.leads,
        resultado_rfb.estabelecimentos,
        cota=cota,
        culturas_alvo=culturas_alvo,
    )
    logger.info(
        "busca: pré-seleção -> %d selecionados (fase 1: %d, fase 2: %d, "
        "dedup descartou %d) para cota de %d",
        len(pre.selecionados),
        pre.selecionados_fase1,
        pre.selecionados_fase2,
        pre.descartados_por_dedup,
        cota,
    )

    # --- 5. ⛔ PARA AQUI --------------------------------------------------
    # Daqui pra frente é tudo custo. Ver `enriquecer_selecionados`.

    return ResultadoBusca(
        pre_selecao=pre,
        erros=tuple(erros),
        leads_sicor=len(resultado_sicor.leads),
        estabelecimentos_rfb=len(resultado_rfb.estabelecimentos),
    )


def enriquecer_selecionados(
    selecionados: Sequence[Candidato],
    *,
    cliente_api_full=None,
    cliente_brasil_api=None,
) -> list:
    """Enriquecimento pago dos selecionados — hoje **só o decisor**.

    Delega pra ``app.workers.enriquecimento``, onde mora a lógica e a
    documentação de custo. Aqui fica só o ponto de entrada que a orquestração
    referencia, pra quem lê ``busca.py`` achar a fronteira sem procurar.

    ⚠️ **Custa dinheiro**: uma chamada por candidato, e ~97% deles são CPF,
    que vai pra fonte paga (API Full). Só é chamada DEPOIS da pré-seleção ter
    cortado o volume — nunca antes.

    As demais etapas pagas da §3 (Google Places, Firecrawl, WhatsApp, e-mail,
    presença digital) continuam fora desta fase; ver os TODO em
    ``app.workers.enriquecimento.enriquecer_decisor``.
    """
    from app.workers.enriquecimento import enriquecer_lote

    return enriquecer_lote(
        selecionados,
        cliente_api_full=cliente_api_full,
        cliente_brasil_api=cliente_brasil_api,
    )
