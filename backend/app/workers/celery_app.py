"""Aplicação Celery.

Fase 1: só a instância e o registro de models. **Nenhuma task ainda** — a
orquestração (``executar_busca_mensal``, ``process_lead_pipeline``) é fase
posterior.

O import de ``app.models`` abaixo não é decorativo: é ele que faz o processo
do worker conhecer o mapeamento SQLAlchemy inteiro (ver o docstring de
``app/models/__init__.py``). Não remover por parecer "import não usado".
"""

from __future__ import annotations

from celery import Celery

import app.models  # noqa: F401  — registra os models neste processo

celery_app = Celery("hunterpro_inova")

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)

# TODO(Fase 2/3): decidir `task_acks_late=True` + `task_reject_on_worker_lost=True`
# antes da primeira busca real. Com o padrão atual (ack antes de executar) uma
# task perdida no meio de um redeploy/OOM **não volta pra fila** — janela real
# numa busca de 15+ min. O trade-off é re-execução, que exige idempotência nas
# chamadas pagas. Ver seção 6 do docs_fundacao.md; é decisão de custo.
