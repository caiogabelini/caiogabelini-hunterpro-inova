"""Contagem e limite das gerações de IA por lead.

⚠️ **Controle de custo, não de UX.** Cada "Gerar mensagem"/"Gerar Insights" no
dossiê é uma chamada paga à Anthropic, e a tela é acessível a usuários
"client". Sem isto, alguém explorando a interface geraria indefinidamente.

Este módulo é a **fonte única** da regra: as duas rotas de geração, a rota de
reset do admin e o ``GET /api/leads/{id}`` (que informa ao frontend quantas
restam) passam todos por aqui. Duplicar a contagem seria a receita para
frontend e backend discordarem sobre quantas gerações sobraram.

## Duas formas de contar, porque os dados são guardados diferente

- **e-mail/WhatsApp**: cada geração é uma linha em ``lead_messages``
  (histórico, nunca sobrescrito). Contagem = ``COUNT(*)`` por lead + canal.
- **insights**: ``Lead.insights_ia`` é **sobrescrito**, não há histórico. Daí
  o contador ``Lead.insights_geracoes_count``.

## ⚠️ Reset não apaga histórico

Para e-mail/WhatsApp, zerar o contador deletando linhas de ``lead_messages``
destruiria exatamente o que aquela tabela existe para guardar. Em vez disso o
reset grava um instante em ``Lead.ia_limite_resetado_em[tipo]``, e a contagem
passa a considerar só as gerações posteriores a ele. Para insights, que não
tem histórico, o reset zera o inteiro mesmo — e ainda grava o carimbo, como
trilha de auditoria de quando um admin liberou.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.lead import Lead
from app.models.lead_message import CanalMensagem, LeadMessage

TIPO_INSIGHTS = "insights"

#: Os canais saem de ``CanalMensagem`` para não virarem uma segunda lista que
#: alguém esquece de atualizar ao criar um canal novo.
TIPOS_GERACAO_IA: tuple[str, ...] = tuple(c.value for c in CanalMensagem) + (
    TIPO_INSIGHTS,
)


def _resetado_em(lead: Lead, tipo: str) -> datetime | None:
    """Instante da última liberação, ou ``None`` se nunca houve.

    Carimbo corrompido devolve ``None``, o que faz contar **todas** as
    gerações — erra para o lado restritivo. Um limite de custo que falha
    aberto é pior que um que falha fechado.
    """
    marcas = lead.ia_limite_resetado_em or {}
    if not isinstance(marcas, dict):
        return None
    bruto = marcas.get(tipo)
    if not isinstance(bruto, str):
        return None
    try:
        return datetime.fromisoformat(bruto)
    except ValueError:
        return None


def contar_geracoes(db: Session, lead: Lead, tipo: str) -> int:
    """Gerações deste tipo desde a última liberação (ou desde sempre)."""
    if tipo == TIPO_INSIGHTS:
        return lead.insights_geracoes_count or 0

    consulta = (
        select(func.count())
        .select_from(LeadMessage)
        .where(LeadMessage.lead_id == lead.id, LeadMessage.canal == tipo)
    )
    resetado = _resetado_em(lead, tipo)
    if resetado is not None:
        consulta = consulta.where(LeadMessage.gerado_em > resetado)
    return db.execute(consulta).scalar() or 0


def limite_atingido(db: Session, lead: Lead, tipo: str) -> bool:
    """``True`` se o lead já esgotou as gerações deste tipo.

    Limite ``<= 0`` **desliga** a checagem (libera geral) em vez de bloquear
    tudo — ver ``settings.LIMITE_GERACOES_IA_POR_LEAD``.
    """
    limite = settings.LIMITE_GERACOES_IA_POR_LEAD
    if limite <= 0:
        return False
    return contar_geracoes(db, lead, tipo) >= limite


def resumo_geracoes(db: Session, lead: Lead) -> dict[str, int]:
    """Contagem por tipo + o limite vigente, no formato que o dossiê consome
    para desabilitar os botões ANTES do clique.

    O limite vai junto de propósito: sem ele o frontend teria que fixar ``2``
    no código, e mudar ``LIMITE_GERACOES_IA_POR_LEAD`` no ``.env`` passaria a
    exigir deploy do frontend para continuar coerente.
    """
    resumo = {tipo: contar_geracoes(db, lead, tipo) for tipo in TIPOS_GERACAO_IA}
    resumo["limite"] = settings.LIMITE_GERACOES_IA_POR_LEAD
    return resumo


def registrar_reset(lead: Lead, tipo: str, agora: datetime) -> None:
    """Libera novas gerações deste tipo.

    ⚠️ Reatribui ``ia_limite_resetado_em`` a um dict **novo** em vez de mutar
    o existente: é coluna JSON, e o SQLAlchemy não detecta mutação in-place de
    dict sem ``MutableDict`` — alterar a chave sem reatribuir não marcaria o
    objeto como sujo e o UPDATE não sairia. Erro clássico e silencioso.
    """
    marcas = dict(lead.ia_limite_resetado_em or {})
    marcas[tipo] = agora.isoformat()
    lead.ia_limite_resetado_em = marcas

    if tipo == TIPO_INSIGHTS:
        lead.insights_geracoes_count = 0
