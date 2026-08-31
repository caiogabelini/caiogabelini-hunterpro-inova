"""Espera o Postgres aceitar conexão antes de subir uvicorn/celery.

Roda no ``entrypoint.sh``, antes do comando real: sem isso, um boot mais
rápido que o do banco derruba o container numa corrida de inicialização.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# ⚠️ **Sem esta linha o container não sobe.**
#
# O entrypoint chama `python scripts/wait_for_db.py`, e nessa forma o Python
# coloca em `sys.path[0]` o diretório do SCRIPT (`/app/scripts`), não o
# diretório de trabalho (`/app`) — então `from app.core.config import ...`
# levanta `ModuleNotFoundError: No module named 'app'` na primeira linha do
# boot, antes de qualquer log útil.
#
# Descoberto na Fase 12 (31/08/2026), ao montar os artefatos de deploy: este
# era o ÚNICO script de `scripts/` sem o ajuste — os outros três
# (`create_user`, `seed_leads_teste`, `reprocessar_leads`) já o tinham. Passou
# despercebido porque é também o único que ninguém roda à mão; só o
# entrypoint o executa, e o entrypoint não existia até agora.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import settings  # noqa: E402

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
