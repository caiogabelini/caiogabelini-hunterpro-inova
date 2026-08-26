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

O ``User`` entrou na Fase 8a exatamente como previsto: uma linha aqui.
``BuscaLeads`` e ``LeadMessage`` seguem o mesmo caminho quando as fases
delas chegarem.
"""

from app.core.database import Base
from app.models.lead import Lead
from app.models.user import User, UserRole

__all__ = ["Base", "Lead", "User", "UserRole"]
