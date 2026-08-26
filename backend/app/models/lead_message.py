"""Histórico de mensagens de abordagem geradas por IA, por canal.

Uma linha **por geração**, nunca sobrescrita. ``GET /api/leads/{id}/mensagens``
devolve só a mais recente de cada canal, mas o histórico fica no banco.

⚠️ **Diferença deliberada em relação ao Minotto.** Lá existe também um campo
``Lead.mensagem_abordagem`` (uma mensagem única, sem canal), marcado como
*deprecated* e mantido só pra não perder dado de teste antigo. Aqui ele
**não foi portado**: não há dado legado pra preservar, e nascer já com uma
coluna morta seria criar a dívida que lá custou uma migração.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tempo import agora_utc


class CanalMensagem(str, Enum):
    """Canais com geração de mensagem. Instagram fora de propósito — não há
    integração de envio, e oferecer o botão sugeriria que há."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"


CANAIS_VALIDOS: tuple[str, ...] = tuple(c.value for c in CanalMensagem)


class LeadMessage(Base):
    __tablename__ = "lead_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    #: ⚠️ ``Integer``, não ``String``: o ``Lead.id`` desta base é serial, não
    #: UUID como no Minotto. FK com tipo trocado passa no Python e falha só
    #: na migration, contra o Postgres.
    lead_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    canal: Mapped[str] = mapped_column(String(10), nullable=False)
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    #: Só faz sentido no canal "email" — WhatsApp não tem assunto.
    assunto: Mapped[str | None] = mapped_column(Text, nullable=True)
    gerado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<LeadMessage lead={self.lead_id} canal={self.canal}>"
