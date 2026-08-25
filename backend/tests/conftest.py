"""Fixtures compartilhadas da suíte.

A Fase 1 não tem nenhuma fonte de dado externa, então ainda **não existe** a
fixture ``autouse`` que bloqueia chamada de rede — a que a seção 6 do
docs_fundacao.md exige (dois incidentes reais no Minotto: 21 s de chamada ao
Google com chave de produção, e um crédito do Hunter.io queimado por um
teste). Ela entra junto com o primeiro módulo de ``app/services/``, no mesmo
commit, não depois.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# CPFs e CNPJs válidos (dígitos verificadores conferem) usados na suíte.
CPF_VALIDO = "52998224725"
CPF_VALIDO_2 = "11144477735"
CNPJ_VALIDO = "11222333000181"
CNPJ_VALIDO_2 = "19012345000193"


@pytest.fixture()
def db() -> Iterator[Session]:
    """Sessão contra SQLite em memória, com o schema criado do metadata.

    SQLite não checa CHECK constraints do mesmo jeito que o Postgres em todo
    caso, mas cobre o que interessa aqui (unicidade da chave de negócio); as
    constraints em si são validadas contra Postgres de verdade no ritual de
    migration (``scripts/validar_migration.sh``).
    """
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover - setup
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False, future=True)
    sessao = fabrica()
    try:
        yield sessao
    finally:
        sessao.close()
        engine.dispose()
