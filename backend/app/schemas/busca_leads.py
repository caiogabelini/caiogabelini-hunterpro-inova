"""Schema de leitura do registro de busca.

Campos e nomes conferidos contra ``BuscaLeadsRegistro`` em
``frontend/src/api.ts`` — a tela de Busca de Leads já consome esse shape
desde a Fase 7, então aqui não há liberdade de invenção.

Ver ``app/models/busca_leads.py`` sobre por que os dois campos ``*_cnpjs_*``
têm nome impreciso nesta base (contam documento, não CNPJ).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BuscaLeadsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    iniciado_por_id: str
    iniciado_em: datetime
    concluido_em: datetime | None = None
    status: str
    total_cnpjs_encontrados: int | None = None
    total_cnpjs_selecionados: int | None = None
    total_leads_processados: int | None = None
    #: ``None`` = ainda executando; ``[]`` = terminou sem erro nenhum.
    erros: list[str] | None = None
