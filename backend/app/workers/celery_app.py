"""Aplicação Celery — registro de tasks, nada de lógica de negócio.

A orquestração mora em ``app.workers.busca``, não aqui. Ver o docstring
daquele módulo sobre por quê: testar sem broker, e reduzir o que nasce dentro
do processo do worker (a lição da seção 6 que mordeu 3 vezes no Minotto).

O import de ``app.models`` abaixo não é decorativo: é ele que faz o processo
do worker conhecer o mapeamento SQLAlchemy inteiro (ver o docstring de
``app/models/__init__.py``). Não remover por parecer "import não usado".
"""

from __future__ import annotations

from celery import Celery

import app.models  # noqa: F401  — registra os models neste processo
from app.workers.busca import executar_busca_mensal

celery_app = Celery("hunterpro_inova")

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)

@celery_app.task(name="hunterpro.executar_busca_mensal")
def task_executar_busca_mensal(**kwargs) -> dict:
    """Task fina: só chama a orquestração e devolve um resumo serializável.

    Task chamada direto no console morre se a sessão cair — usar `.delay()`
    (seção 6). E reiniciar o worker depois de qualquer deploy: Python carrega
    módulo na inicialização do processo, editar arquivo depois não afeta um
    processo já rodando.
    """
    resultado = executar_busca_mensal(**kwargs)
    return {
        "abortada_por": resultado.abortada_por,
        "selecionados": len(resultado.selecionados),
        "leads_sicor": resultado.leads_sicor,
        "estabelecimentos_rfb": resultado.estabelecimentos_rfb,
        "erros": list(resultado.erros),
    }


# TODO(Fase 2/3): decidir `task_acks_late=True` + `task_reject_on_worker_lost=True`
# antes da primeira busca real. Com o padrão atual (ack antes de executar) uma
# task perdida no meio de um redeploy/OOM **não volta pra fila** — janela real
# numa busca de 15+ min. O trade-off é re-execução, que exige idempotência nas
# chamadas pagas. Ver seção 6 do docs_fundacao.md; é decisão de custo.
