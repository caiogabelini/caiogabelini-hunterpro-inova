"""Configuração da aplicação, lida do ambiente.

Fase 1 declara só o que já é usado de verdade. Chaves de API das fontes
externas entram junto com o módulo que as consome — declarar config que
ninguém lê foi um problema real no Minotto (``SERP_API_KEY``, seção 5).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLAlchemy exige o esquema "postgresql://", nunca "postgres://".
    DATABASE_URL: str = "postgresql://hunterpro:hunterpro@localhost:5432/hunterpro_inova"

    PROJETO_NOME: str = "HunterPro — Inova Contabilidade"


settings = Settings()
