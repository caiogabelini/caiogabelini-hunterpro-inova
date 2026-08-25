"""Helpers de data/hora.

Regra do projeto (seção 6 do docs_fundacao.md): todo timestamp é gravado em
UTC *naive*. Ler o relógio local e comparar com o que está no banco já
desviou uma investigação inteira no Minotto — ao correlacionar "quando isso
rodou" com "quando o código mudou", converter tudo pro mesmo fuso antes de
concluir qualquer coisa.
"""

from __future__ import annotations

from datetime import datetime, timezone


def agora_utc() -> datetime:
    """Agora, em UTC, sem tzinfo (naive-UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
