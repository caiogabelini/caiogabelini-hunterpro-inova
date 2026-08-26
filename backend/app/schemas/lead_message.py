"""Schema de leitura de uma mensagem de abordagem gerada.

Campos conferidos contra ``LeadMessage`` em ``frontend/src/api.ts``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class LeadMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    #: ⚠️ String, não int — mesma razão de ``LeadRead.id``: o frontend tipa
    #: como string e usa em chave de lista.
    lead_id: str
    canal: str
    conteudo: str
    assunto: str | None = None
    gerado_em: datetime

    @field_validator("lead_id", mode="before")
    @classmethod
    def _para_texto(cls, v: object) -> str:
        return str(v)
