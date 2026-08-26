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
from app.core.config import settings
from app.workers.busca import executar_busca_mensal
from app.workers.execucao_busca import executar_busca_completa

celery_app = Celery("hunterpro_inova")

# ⚠️ Broker e backend saem do MESMO Redis que já sustenta o rate limit do
# login (`app.core.rate_limit`). Antes da Fase 8b nada disto era configurado:
# a task existia, mas `.delay()` teria caído no default do Celery
# (`amqp://guest@localhost:5672`) e falhado com "connection refused" apontando
# pra um RabbitMQ que este projeto nunca teve.
#
# `broker_connection_retry_on_startup` explícito porque o Celery 6 muda o
# default e emite deprecation warning sem ele.
celery_app.conf.update(
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,
    broker_connection_retry_on_startup=True,
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


@celery_app.task(name="hunterpro.executar_busca_completa")
def task_executar_busca_completa(busca_id: str) -> dict:
    """Busca completa: sementes → enriquecimento PAGO → persistência.

    É esta que a rota `POST /api/admin/buscas` despacha. Diferente da task
    acima (que só lê as sementes e para antes do custo), **esta gasta dinheiro
    de verdade** — ver o docstring de `app.workers.execucao_busca`.

    Recebe só o `busca_id`: todo o resto vem de `settings`. Argumento que
    entra por aqui atravessa o broker serializado em JSON e vira parte da
    assinatura pública da task; quanto menos, menor a chance de um worker
    antigo receber uma chamada nova e quebrar num deploy parcial.

    Não trata exceção porque não precisa: `executar_busca_completa` nunca
    levanta — falha vira `status="erro"` no registro, que é o que a tela lê.
    """
    return executar_busca_completa(busca_id)


# TODO(Fase 2/3): decidir `task_acks_late=True` + `task_reject_on_worker_lost=True`
# antes da primeira busca real. Com o padrão atual (ack antes de executar) uma
# task perdida no meio de um redeploy/OOM **não volta pra fila** — janela real
# numa busca de 15+ min. O trade-off é re-execução, que exige idempotência nas
# chamadas pagas. Ver seção 6 do docs_fundacao.md; é decisão de custo.
