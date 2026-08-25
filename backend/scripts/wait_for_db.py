"""Espera o Postgres aceitar conexão antes de subir uvicorn/celery.

Roda no ``entrypoint.sh``, antes do comando real: sem isso, um boot mais
rápido que o do banco derruba o container numa corrida de inicialização.
"""

from __future__ import annotations

import sys
import time

from sqlalchemy import create_engine, text

from app.core.config import settings

TENTATIVAS = 30
ESPERA_INICIAL_S = 0.5
ESPERA_MAXIMA_S = 5.0


def main() -> int:
    espera = ESPERA_INICIAL_S
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"banco disponível (tentativa {tentativa})")
            return 0
        except Exception as exc:  # noqa: BLE001 — qualquer falha aqui é "ainda não subiu"
            print(f"banco indisponível (tentativa {tentativa}/{TENTATIVAS}): {exc}")
            time.sleep(espera)
            espera = min(espera * 2, ESPERA_MAXIMA_S)
    print("banco não respondeu dentro do limite de tentativas", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
