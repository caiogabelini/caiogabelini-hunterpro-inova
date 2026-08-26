"""Schemas de leitura de Lead.

Contrato conferido contra ``frontend/src/api.ts`` (Fase 7), que é quem
define o que cada tela consome — não inventado aqui.

⚠️ **Os sinais do Sicor são DESEMPACOTADOS de ``dados_nicho``.** No banco eles
vivem dentro de um JSON livre (decisão da Fase 1, mantida porque os parsers
de nicho ainda podem mudar). Na API eles sobem pra campos de primeiro nível
— a tela não deveria precisar saber que a origem é um JSON, e desempacotar
aqui evita uma migration só pra mudar a forma da resposta.

⚠️ **``score_detalhes`` é recalculado a cada resposta**, não persistido — ver
``app/api/routes/leads.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class ScoreCriterioRead(BaseModel):
    """Um critério pontuado. Mesmo shape de ``ScoreBreakdownItem`` no
    frontend e de ``PontuacaoCriterio`` em ``compute_lead_score``."""

    key: str
    label: str
    weight: int
    layer: str
    points: float


class ScoreDetalhesRead(BaseModel):
    breakdown: list[ScoreCriterioRead]


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    #: ⚠️ String, não int. O frontend tipa `Lead.id` como `string` e usa na
    #: URL do dossiê; devolver int faria o React Router comparar tipos
    #: diferentes sem erro visível.
    id: str
    documento: str
    tipo_documento: str
    nome: str
    municipio: str | None = None
    uf: str | None = None
    telefone: str | None = None
    #: Contato alternativo, quando a fonte trouxe mais de um número. **Não**
    #: é o número do WhatsApp — esse é sempre ``telefone``.
    telefone_secundario: str | None = None
    email: str | None = None
    site: str | None = None
    score: int | None = None
    prioridade: str | None = None
    etapas_puladas: list[dict[str, Any]] | None = None
    dados_nicho: dict[str, Any] | None = None
    observacoes: str | None = None
    created_at: datetime
    updated_at: datetime

    # --- Desempacotados de `dados_nicho` (ver docstring do módulo) --------
    area_ha: float | None = None
    valor_financiado: float | None = None
    culturas: list[str] = []
    data_operacao: str | None = None
    recorrente: bool | None = None
    anos_credito: list[int] = []
    codigos_car: list[str] = []
    #: Municípios do produtor, o principal primeiro. Mais de um quando a
    #: operação mais recente cobre propriedades em municípios diferentes
    #: (8,5% dos casos medidos) — a tela mostra "Douradina (+1)".
    municipios: list[str] = []
    n_operacoes: int | None = None
    decisor: str | None = None
    whatsapp_ativo: bool | None = None
    email_status: str | None = None
    presenca_digital: float | None = None
    instagram: str | None = None
    cnae_descricao: str | None = None
    eh_cooperativa: bool | None = None

    # --- Kanban e fechamento (Fase 8b) ------------------------------------
    #: Coluna do funil. ``NOT NULL`` no banco, então nunca vem ausente de um
    #: lead persistido — o default do schema serve só pra montar um LeadRead
    #: fora do banco (testes).
    kanban_status: str = "novo_lead"
    motivo_perda: str | None = None
    servicos_vendidos: list[str] | None = None
    tipo_contrato: str | None = None
    valor_fechamento: float | None = None

    # --- Insights por IA (Fase 10) ----------------------------------------
    insights_ia: dict[str, Any] | None = None
    insights_gerado_em: datetime | None = None
    #: Contagem de gerações por tipo + o limite vigente, no formato que
    #: `statusLimiteIa` (frontend) consome pra desabilitar os botões ANTES do
    #: clique. Só preenchido nas rotas de dossiê/geração — as de lista não
    #: calculam (seriam N consultas por página).
    geracoes_ia: dict[str, int] | None = None

    # --- Calculado na hora ------------------------------------------------
    score_detalhes: ScoreDetalhesRead | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _id_para_texto(cls, v: Any) -> str:
        return str(v)


class LeadStatusUpdate(BaseModel):
    """Corpo de ``PATCH /api/leads/{id}/status``.

    ⚠️ Shape conferido contra ``updateLeadStatus`` em ``frontend/src/api.ts``:
    a tela manda os campos de fechamento **achatados no mesmo objeto** que
    ``kanban_status``/``motivo_perda`` (``{...dadosFechamento}``), não
    aninhados. Um objeto aninhado aqui faria a tela receber 422 em toda venda
    fechada.

    Todos opcionais no schema de propósito. A obrigatoriedade é **condicional
    ao status** (``motivo_perda`` só em "perdido"; os três de fechamento só em
    "ganho"), o que Pydantic sozinho não expressa sem um validator que
    devolveria erro pior. Validado na rota, com mensagem específica.
    """

    kanban_status: str
    motivo_perda: str | None = None
    servicos_vendidos: list[str] | None = None
    tipo_contrato: str | None = None
    valor_fechamento: float | None = None


class LeadListaResponse(BaseModel):
    """Resposta paginada de ``GET /api/leads/lista``.

    Mesmos nomes de campo que ``LeadListaResposta`` no frontend.
    """

    items: list[LeadRead]
    total: int
    pagina: int
    por_pagina: int
