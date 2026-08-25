"""Entrypoint da API (FastAPI).

Fase 1: só o app e o ``/health``. As rotas de ``app/api/routes`` (leads,
dashboard, admin, auth) entram nas fases seguintes.
"""

from __future__ import annotations

from fastapi import FastAPI

import app.models  # noqa: F401  — registra os models neste processo
from app.core.config import settings

app = FastAPI(title=settings.PROJETO_NOME)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
