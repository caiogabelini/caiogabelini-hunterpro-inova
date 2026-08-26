"""Ponto único de registro dos models SQLAlchemy.

**Por que este arquivo existe** (seção 6 do docs_fundacao.md — o erro que
apareceu três vezes no Minotto): um processo Python só "conhece" os models
que foram importados *naquele processo*. O ``uvicorn`` costuma importar tudo
por acidente, via cadeia de rotas; o ``celery worker`` não. Quando isso
diverge, o worker quebra com ``NoReferencedTableError`` mesmo com o banco
perfeitamente correto — e o sintoma aponta pro banco, não pro import.

A solução é esta: **um lugar só** que importa todos os models, importado
explicitamente em *todos* os entrypoints — ``app/main.py``,
``app/workers/celery_app.py``, ``alembic/env.py`` e qualquer script de
``scripts/``.

O ``User`` entrou na Fase 8a exatamente como previsto: uma linha aqui, e o
``BuscaLeadsRegistro`` na Fase 8b, pelo mesmo caminho. ``LeadMessage`` entrou na Fase 10, junto com a geração por IA.
"""

from app.core.database import Base
from app.models.busca_leads import BuscaLeadsRegistro, StatusBusca
from app.models.lead import KanbanStatus, Lead
from app.models.lead_message import CANAIS_VALIDOS, CanalMensagem, LeadMessage
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "BuscaLeadsRegistro",
    "CANAIS_VALIDOS",
    "CanalMensagem",
    "LeadMessage",
    "KanbanStatus",
    "Lead",
    "StatusBusca",
    "User",
    "UserRole",
]
