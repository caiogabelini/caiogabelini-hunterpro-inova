"""Execução completa de uma busca, ligada ao banco — Fase 8b.

## O que este módulo é, e o que ``busca.py`` continua sendo

``app/workers/busca.py`` faz a parte **gratuita e pura**: lê as duas sementes,
pré-seleciona e devolve um ``ResultadoBusca``. Não conhece banco, não conhece
``BuscaLeadsRegistro`` e não gasta um centavo. Aquele módulo continua exatamente
assim — é o que permite a suíte exercitar a busca inteira contra arquivos reais
sem subir nada.

Este módulo é a camada de cima: pega um ``busca_id`` já criado pela rota,
executa as fases na ordem, **gasta dinheiro na fase paga** e vai gravando o
progresso no registro para a tela admin conseguir acompanhar.

```
rota POST /api/admin/buscas   →  cria BuscaLeadsRegistro (status=executando)
                              →  despacha pro Celery e responde 201 na hora
worker                        →  executar_busca_completa(busca_id)
                                   1. executar_busca_mensal   (grátis)
                                   2. enriquecer_selecionados (💸 PAGO)
                                   3. persistir_leads         (banco)
                                   4. fecha o registro
```

## Por que tudo é injetável

Cada etapa entra por parâmetro com o valor real como default. Não é
indireção decorativa: é o que permite a suíte cobrir o fluxo inteiro —
incluindo os caminhos de erro — **sem nunca chamar a fase paga**. A alternativa
(monkeypatch de módulo) já mordeu neste projeto, porque depende de acertar o
caminho de import de quem chama.

## Resiliência, em três níveis

1. ``_rodar_etapa`` dentro de ``enriquecimento.py`` — uma etapa que falha não
   derruba as outras daquele lead.
2. ``enriquecer_lote_completo`` — um lead que falha inteiro não derruba o lote.
3. Aqui — a busca inteira nunca levanta pro chamador: vira ``status="erro"``
   com o motivo no registro. Uma exceção vazando mataria a task do Celery e
   deixaria o registro preso em "executando" para sempre.

## O que aborta ANTES de gastar

A trava de ``verificar_fontes`` (chamada dentro de ``executar_busca_mensal``)
roda antes de qualquer etapa paga. Semente ausente ⇒ ``status="erro"``, zero
gasto. Semente vazia ⇒ ``status="concluido"`` com zero leads: não houve falha,
e também não se gastou nada.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.segredos import erro_redigido, traceback_redigido
from app.core.tempo import agora_utc

logger = logging.getLogger(__name__)

#: Acima desta fração de leads com falha, a busca é marcada como "erro" em vez
#: de "concluído". Calculada sobre os **selecionados** (o que entrou no
#: enriquecimento), nunca sobre o universo — senão 50 processados de 2.806
#: encontrados leriam como 98% de falha.
CRITERIO_ERRO_TAXA_FALHA = 0.5


def _resumir_erro(item: Any) -> str:
    """Achata um item de erro em string legível pro painel.

    ⚠️ Existe porque as duas pontas discordam de tipo: ``ResultadoBusca.erros``
    e ``Lead.etapas_puladas`` carregam ``{"etapa": ..., "motivo": ...}``, mas o
    frontend (``BuscaLeadsRegistro.erros``) espera ``string[]`` — porte fiel do
    Minotto, onde a lista sempre foi de strings. Converter aqui é mais barato
    que mudar o contrato de uma tela já pronta.
    """
    if isinstance(item, dict):
        etapa = item.get("etapa") or "etapa desconhecida"
        motivo = item.get("motivo") or "sem motivo registrado"
        return f"{etapa}: {motivo}"
    return str(item)


def executar_busca_completa(
    busca_id: str,
    *,
    sessao_factory: Callable[[], Any] | None = None,
    buscar: Callable[..., Any] | None = None,
    enriquecer: Callable[..., Any] | None = None,
    persistir: Callable[..., int] | None = None,
    dir_sicor: Path | str | None = None,
    dir_rfb: Path | str | None = None,
    anos: Sequence[int] | None = None,
    uf: str | None = None,
    apenas_decisor: bool = False,
    **clientes: Any,
) -> dict[str, Any]:
    """Roda a busca inteira e fecha o registro. **Nunca levanta.**

    ⚠️ **Chamar isto gasta dinheiro real** (API Full por CPF, Evolution,
    Hunter/ZeroBounce, Anthropic), por lead selecionado. Não há modo de
    simulação embutido: quem quer ensaiar injeta ``enriquecer``.

    Devolve um resumo serializável — é o valor de retorno da task do Celery,
    então tudo aqui tem que sobreviver a JSON.
    """
    from app.core.database import SessionLocal
    from app.models.busca_leads import BuscaLeadsRegistro, StatusBusca
    from app.workers.busca import enriquecer_selecionados, executar_busca_mensal
    from app.workers.busca import persistir_leads

    sessao_factory = sessao_factory or SessionLocal
    buscar = buscar or executar_busca_mensal
    enriquecer = enriquecer or enriquecer_selecionados
    persistir = persistir or persistir_leads

    dir_sicor = settings.SICOR_DADOS_DIR if dir_sicor is None else dir_sicor
    dir_rfb = settings.RFB_DADOS_DIR if dir_rfb is None else dir_rfb
    anos = settings.busca_anos if anos is None else anos
    uf = settings.BUSCA_UF if uf is None else uf

    sessao = sessao_factory()
    try:
        registro = sessao.get(BuscaLeadsRegistro, busca_id)
        if registro is None:
            # Não há onde registrar o erro; só log. Acontece se alguém apagar
            # a linha entre o despacho e o worker pegar a task.
            logger.error(
                "executar_busca_completa: registro %s não encontrado — abortando "
                "antes de qualquer gasto.",
                busca_id,
            )
            return {"busca_id": busca_id, "status": "erro", "motivo": "registro não encontrado"}

        def _fechar(status: str, erros: list[str]) -> dict[str, Any]:
            registro.status = status
            registro.erros = erros
            registro.concluido_em = agora_utc()
            sessao.commit()
            logger.info(
                "busca %s finalizada: status=%s, encontrados=%s, selecionados=%s, "
                "processados=%s, erros=%d",
                busca_id, status, registro.total_cnpjs_encontrados,
                registro.total_cnpjs_selecionados, registro.total_leads_processados,
                len(erros),
            )
            return {
                "busca_id": busca_id,
                "status": status,
                "total_cnpjs_encontrados": registro.total_cnpjs_encontrados,
                "total_cnpjs_selecionados": registro.total_cnpjs_selecionados,
                "total_leads_processados": registro.total_leads_processados,
                "erros": erros,
            }

        registro.status = StatusBusca.EXECUTANDO.value
        sessao.commit()

        # --- 1. Sementes + pré-seleção (grátis) ---------------------------
        try:
            resultado = buscar(
                dir_sicor=dir_sicor, dir_rfb=dir_rfb, anos=list(anos), uf=uf
            )
        except Exception as exc:  # noqa: BLE001 — nada pode vazar pro worker
            logger.error(
                "busca %s: leitura das sementes falhou.\n%s", busca_id, traceback_redigido()
            )
            return _fechar(StatusBusca.ERRO.value, [f"sementes: {erro_redigido(exc)}"])

        avisos = [_resumir_erro(e) for e in (resultado.erros or ())]

        if resultado.abortada_por:
            # Trava de segurança: fonte ausente. Zero gasto.
            logger.error("busca %s abortada: %s", busca_id, resultado.abortada_por)
            return _fechar(StatusBusca.ERRO.value, [resultado.abortada_por, *avisos])

        # Universo varrido = as duas sementes somadas. Não é uma contagem de
        # documentos únicos: um produtor que aparece no Sicor e também tem
        # CNPJ na Receita conta duas vezes aqui, e só é deduplicado na
        # pré-seleção. É o número de "linhas que olhamos", que é o que o
        # painel quer dizer com "encontrados".
        registro.total_cnpjs_encontrados = (
            resultado.leads_sicor + resultado.estabelecimentos_rfb
        )
        selecionados = list(resultado.selecionados)
        registro.total_cnpjs_selecionados = len(selecionados)
        sessao.commit()

        if not selecionados:
            # Nenhum candidato não é falha — e não se gastou nada.
            registro.total_leads_processados = 0
            return _fechar(StatusBusca.CONCLUIDO.value, avisos)

        # --- 2. 💸 Enriquecimento pago ------------------------------------
        logger.warning(
            "busca %s: iniciando enriquecimento PAGO de %d leads.",
            busca_id, len(selecionados),
        )
        try:
            enriquecidos = enriquecer(
                selecionados, apenas_decisor=apenas_decisor, **clientes
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "busca %s: enriquecimento falhou em bloco.\n%s",
                busca_id, traceback_redigido(),
            )
            return _fechar(
                StatusBusca.ERRO.value,
                [f"enriquecimento: {erro_redigido(exc)}", *avisos],
            )

        # Erros por lead vêm de `etapas_puladas`, não de exceção — o lote já
        # isola cada falha (§6). Aqui eles só sobem pro painel.
        erros_por_lead: list[str] = []
        falhas_totais = 0
        for enriquecido in enriquecidos:
            etapas = list(getattr(enriquecido, "etapas_puladas", ()) or ())
            documento = getattr(getattr(enriquecido, "candidato", None), "documento", "?")
            if any(e.get("etapa") == "enriquecimento" for e in etapas if isinstance(e, dict)):
                falhas_totais += 1
            for etapa in etapas:
                erros_por_lead.append(f"{documento} — {_resumir_erro(etapa)}")

        # --- 3. Persistência ----------------------------------------------
        try:
            gravados = persistir(sessao, enriquecidos)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "busca %s: persistência falhou.\n%s", busca_id, traceback_redigido()
            )
            return _fechar(
                StatusBusca.ERRO.value,
                [f"persistência: {erro_redigido(exc)}", *avisos, *erros_por_lead],
            )

        registro.total_leads_processados = gravados
        sessao.commit()

        # --- 4. Veredito ---------------------------------------------------
        taxa_falha = falhas_totais / len(selecionados) if selecionados else 0.0
        status_final = (
            StatusBusca.ERRO.value
            if taxa_falha > CRITERIO_ERRO_TAXA_FALHA
            else StatusBusca.CONCLUIDO.value
        )
        if status_final == StatusBusca.ERRO.value:
            logger.error(
                "busca %s: %d de %d leads falharam por inteiro (%.0f%%) — "
                "marcada como erro.",
                busca_id, falhas_totais, len(selecionados), taxa_falha * 100,
            )
        return _fechar(status_final, [*avisos, *erros_por_lead])

    except Exception as exc:  # noqa: BLE001 — rede de segurança final
        # Se chegou aqui, algo quebrou fora das etapas (ex.: o próprio commit).
        # Tenta registrar; se nem isso der, o log é o que resta.
        logger.error("busca %s: falha inesperada.\n%s", busca_id, traceback_redigido())
        try:
            from app.models.busca_leads import BuscaLeadsRegistro, StatusBusca

            sessao.rollback()
            registro = sessao.get(BuscaLeadsRegistro, busca_id)
            if registro is not None:
                registro.status = StatusBusca.ERRO.value
                registro.erros = [f"falha inesperada: {erro_redigido(exc)}"]
                registro.concluido_em = agora_utc()
                sessao.commit()
        except Exception:  # noqa: BLE001 - pragma: no cover
            logger.error("busca %s: não foi possível registrar a falha.", busca_id)
        return {"busca_id": busca_id, "status": "erro", "motivo": erro_redigido(exc)}
    finally:
        sessao.close()
