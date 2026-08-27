"""Histórico de mensagens de abordagem geradas por IA, por canal.

Uma linha **por mensagem**, nunca sobrescrita. Desde a Fase 11a, uma geração
não produz mais uma mensagem solta: produz uma **sequência** coordenada
(WhatsApp 3, e-mail 2), e as linhas dela compartilham um ``grupo_id``.
``GET /api/leads/{id}/mensagens`` devolve a sequência mais recente de cada
canal; o histórico das anteriores fica no banco.

## ⚠️ Por que grupo virou COLUNA e não tabela

A alternativa era ``lead_message_groups(id, lead_id, canal, gerado_em)``. Ela
foi descartada porque essa tabela não teria **nenhum atributo próprio**: os
três campos que ela guardaria já vivem na mensagem, e o preço seria um JOIN em
todo caminho de leitura mais um model a manter. O que uma tabela de grupo
compraria — garantir que um grupo existe antes das mensagens — não é um
problema real aqui: grupo sem mensagem nunca é criado (a rota persiste a
sequência inteira numa transação) e a integridade que importa,
"não há duas mensagens na mesma posição da mesma sequência", é justamente a
que uma coluna resolve melhor, com um ``UNIQUE (grupo_id, ordem)`` na própria
tabela em vez de um FK que não diria nada sobre ordem.

O custo aceito é a repetição de ``lead_id``/``canal``/``gerado_em`` nas 2-3
linhas de um grupo. É repetição de dado imutável, gravado de uma vez só —
não há caminho no código que atualize um sem o outro.

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

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tempo import agora_utc


class StatusMensagem(str, Enum):
    """Onde a mensagem está no fluxo de envio.

    Não há "falhou" nem "respondida": o produto não envia nada, quem envia é
    o vendedor no WhatsApp/e-mail dele. ``ENVIADA`` é o vendedor dizendo
    "já mandei esta", nada mais — inventar estados que ninguém consegue
    observar criaria dado que nunca fica correto.
    """

    PENDENTE = "pendente"
    ENVIADA = "enviada"


STATUS_VALIDOS: tuple[str, ...] = tuple(s.value for s in StatusMensagem)


class CanalMensagem(str, Enum):
    """Canais com geração de mensagem. Instagram fora de propósito — não há
    integração de envio, e oferecer o botão sugeriria que há."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"


CANAIS_VALIDOS: tuple[str, ...] = tuple(c.value for c in CanalMensagem)

#: Quantas mensagens uma geração produz em cada canal.
#:
#: WhatsApp comporta 3 toques (inicial + 2 follow-ups) porque a mensagem é
#: curta e o canal é tolerante; e-mail para em 2 — um terceiro e-mail não
#: respondido é spam, não persistência. É o número que o prompt pede à IA e
#: o que a rota exige de volta antes de persistir.
TAMANHO_SEQUENCIA: dict[str, int] = {
    CanalMensagem.WHATSAPP.value: 3,
    CanalMensagem.EMAIL.value: 2,
}


class LeadMessage(Base):
    __tablename__ = "lead_messages"
    __table_args__ = (
        #: A invariante da sequência: nunca duas mensagens na mesma posição.
        #: Sem isto, uma escrita concorrente do mesmo lead produziria duas
        #: "próximas pendentes" e a validação de ordem passaria a depender de
        #: qual linha o SELECT devolvesse primeiro.
        UniqueConstraint("grupo_id", "ordem", name="uq_lead_messages_grupo_ordem"),
    )

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
    #: ⚠️ ``NOT NULL`` mesmo para as linhas anteriores à Fase 11a: a migration
    #: preenche cada uma com o próprio ``id``, virando uma sequência de uma
    #: mensagem só. Coluna anulável obrigaria **todo** leitor a tratar o caso
    #: legado — o custo do backfill é pago uma vez, o do ``None`` seria pago
    #: em cada consulta daqui pra frente.
    grupo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: Posição na sequência, começando em 1.
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: ``pendente`` | ``enviada`` — ver ``StatusMensagem``.
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=StatusMensagem.PENDENTE.value
    )
    #: Quando o vendedor marcou como enviada. ``None`` enquanto pendente.
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    #: Só faz sentido no canal "email" — WhatsApp não tem assunto.
    assunto: Mapped[str | None] = mapped_column(Text, nullable=True)
    gerado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return (
            f"<LeadMessage lead={self.lead_id} canal={self.canal} "
            f"ordem={self.ordem} status={self.status}>"
        )


def proxima_pendente(mensagens: "list[LeadMessage]") -> "LeadMessage | None":
    """A única mensagem da sequência que pode ser marcada como enviada agora.

    ``None`` quando a sequência inteira já foi enviada.

    ⚠️ **Fonte única da regra "não dá pra pular etapa".** A rota que valida o
    PATCH e o campo ``proxima_ordem`` que o dossiê lê saem os dois daqui. Se
    cada um calculasse o seu, a tela habilitaria um botão que o backend
    recusa — a pior versão dessa divergência, porque ela só aparece no clique.

    Pura de propósito (recebe a lista, não a sessão): ordenar em SQL e depois
    "pegar a primeira pendente" espalharia a regra entre uma query e um ``if``.
    A lista tem 2 ou 3 itens.
    """
    pendentes = [m for m in mensagens if m.status == StatusMensagem.PENDENTE.value]
    if not pendentes:
        return None
    return min(pendentes, key=lambda m: m.ordem)
