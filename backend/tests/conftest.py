"""Fixtures compartilhadas da suíte.

Chegou o primeiro módulo de ``app/services/`` (o Sicor), então chegou junto a
fixture ``autouse`` que **bloqueia qualquer chamada de rede** — como
prometido na Fase 1, no mesmo commit e não depois.

Por que ela existe (seção 6 do docs_fundacao.md, dois incidentes reais no
Minotto): um teste que mockou tudo *menos* ``search_google_places`` fez uma
chamada real de ~21 s ao Google com a chave de produção; outro que esqueceu
de mockar ``enrich_email`` **queimou um crédito** do plano Free do Hunter.io.
A correção não é "lembrar de mockar" — é bloquear por padrão e obrigar quem
precisa do caminho real a liberar explicitamente.

O Sicor lê arquivo local e não faz rede nenhuma, então a fixture não muda
nada hoje. É exatamente por isso que ela entra agora: o primeiro módulo que
*fizer* rede já nasce dentro da rede de proteção.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

#: Arquivos reais do Sicor. Não são versionados (centenas de MB) — os testes
#: que dependem deles pulam com motivo claro quando não estão em disco.
DIR_SICOR = Path(__file__).resolve().parent.parent / "dados_locais" / "sicor"

ARQUIVOS_SICOR = (
    "SICOR_OPERACAO_BASICA_ESTADO_2026.gz",
    "SICOR_MUTUARIOS.gz",
    "SICOR_PROPRIEDADES.gz",
    "Empreendimento.csv",
)


#: Arquivos reais de Dados Abertos do CNPJ. Moram no projeto irmão (Minotto)
#: e são lidos por caminho, **sem cópia** — são centenas de MB. O projeto
#: irmão é read-only nesta sessão.
DIR_RFB = Path("/home/caiogabelini/hunterpro-minotto/backend/data/receita_federal")

ARQUIVOS_RFB = ("Estabelecimentos1.zip", "Empresas1.zip", "Municipios.zip")


def rfb_disponivel() -> bool:
    return all((DIR_RFB / nome).is_file() for nome in ARQUIVOS_RFB)


def sicor_disponivel() -> bool:
    return all((DIR_SICOR / nome).is_file() for nome in ARQUIVOS_SICOR)


#: Marcador pros testes que exigem os arquivos reais em disco.
exige_arquivos_sicor = pytest.mark.skipif(
    not sicor_disponivel(),
    reason=f"arquivos reais do Sicor ausentes em {DIR_SICOR} (não são versionados)",
)


#: Marcador pros testes que exigem os arquivos reais da Receita Federal.
exige_arquivos_rfb = pytest.mark.skipif(
    not rfb_disponivel(),
    reason=(
        f"arquivos reais de Dados Abertos do CNPJ ausentes em {DIR_RFB} "
        f"(ficam no projeto irmão, não são versionados)"
    ),
)


@pytest.fixture(autouse=True)
def sem_rede(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bloqueia toda conexão de socket na suíte inteira.

    Quem precisar de rede de verdade marca o teste com ``@pytest.mark.rede`` —
    liberação explícita e visível na leitura do teste, nunca por esquecimento.
    """
    if request.node.get_closest_marker("rede"):
        return

    def _proibido(self, endereco, *args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError(
            f"chamada de rede bloqueada no teste ({endereco!r}). Toda fonte "
            f"externa tem que ser mockada; se este teste precisa mesmo de "
            f"rede, marque com @pytest.mark.rede."
        )

    monkeypatch.setattr(socket.socket, "connect", _proibido)
    monkeypatch.setattr(socket.socket, "connect_ex", _proibido)

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
