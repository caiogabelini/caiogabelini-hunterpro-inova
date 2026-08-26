"""Entrypoint da API (FastAPI) — HunterPro Inova."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Registra todos os models em Base.metadata (ver app/models/__init__.py).
# Explícito aqui pra a API não depender de alguma rota importar por acaso —
# é a lição da §6 que mordeu 3 vezes no Minotto.
import app.models  # noqa: F401
from app.api.routes import auth, health, leads
from app.core.config import settings

# ⚠️ /docs e /openapi.json FECHADOS fora de desenvolvimento.
#
# `ENVIRONMENT` existe desde a Fase 1 e nunca era lida — o docstring do
# config registrava isso como dívida explícita, citando que no Minotto
# esses endpoints ficam PÚBLICOS em produção. Esta é a primeira vez que a
# variável decide alguma coisa.
#
# Por que fechar: o schema aberto entrega a superfície inteira da API a
# quem não está autenticado — nomes de rota, formato de payload, campos.
# Não é segredo de verdade (segurança não pode depender de esconder isso,
# e não depende: toda rota de lead exige JWT), mas é reconhecimento
# gratuito pra um atacante. Em dev continua aberto porque é onde ele serve.
_docs_abertos = not settings.em_producao

app = FastAPI(
    title=settings.PROJETO_NOME,
    version="0.1.0",
    description=(
        "Motor de prospecção HunterPro parametrizado para o nicho de "
        "agronegócio/produtores de grãos (Inova Contabilidade)."
    ),
    docs_url="/docs" if _docs_abertos else None,
    redoc_url="/redoc" if _docs_abertos else None,
    openapi_url="/openapi.json" if _docs_abertos else None,
)

# ⚠️ Origem explícita, nunca "*". Com `allow_credentials=True`, o wildcard é
# recusado pelo próprio navegador — e mesmo que passasse, abriria a API pra
# qualquer site.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(leads.router, prefix="/api/leads", tags=["leads"])


@app.get("/")
def root() -> dict[str, str]:
    return {"project": settings.PROJETO_NOME, "status": "ok"}
