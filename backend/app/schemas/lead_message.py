"""Schemas de leitura da sequência de abordagem.

⚠️ **O contrato mudou na Fase 11a** e o frontend ainda não acompanhou (isso é
a Fase 11b). Até lá, ``GET /api/leads/{id}/mensagens`` devolve um objeto por
canal, não a lista plana que ``fetchMensagens`` espera — o
``Array.isArray(...) ? ... : []`` de ``frontend/src/mensagens.ts`` faz a aba
mostrar "nenhuma mensagem" em vez de quebrar, que é a degradação aceita e
combinada para esta janela.

Já o shape de UMA mensagem continua compatível: ``LeadMessageRead`` só GANHOU
campos (``ordem``, ``status``, ``enviada_em``), e a interface ``LeadMessage``
do frontend ignora o que não conhece.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class LeadMessageRead(BaseModel):
    """Uma mensagem, já dentro de uma sequência."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    #: ⚠️ String, não int — mesma razão de ``LeadRead.id``: o frontend tipa
    #: como string e usa em chave de lista.
    lead_id: str
    canal: str
    #: Posição na sequência: 1 é o primeiro contato.
    ordem: int
    #: ``pendente`` | ``enviada``.
    status: str
    conteudo: str
    assunto: str | None = None
    gerado_em: datetime
    enviada_em: datetime | None = None

    @field_validator("lead_id", mode="before")
    @classmethod
    def _para_texto(cls, v: object) -> str:
        return str(v)


class SequenciaAbordagemRead(BaseModel):
    """Uma geração inteira: as 3 mensagens do WhatsApp ou as 2 do e-mail.

    ``proxima_ordem`` existe para a tela **não reimplementar** a regra de
    ordem: é a ordem da única mensagem que o backend aceita marcar como
    enviada agora, ou ``None`` quando a sequência acabou. Sem ele, o frontend
    teria que deduzir "a primeira pendente" por conta própria e as duas
    versões da regra iam divergir na primeira exceção.
    """

    grupo_id: str
    canal: str
    #: Instante da geração — o mesmo para todas as mensagens do grupo.
    gerado_em: datetime
    total: int
    proxima_ordem: int | None = None
    mensagens: list[LeadMessageRead]


class MensagensDoLeadRead(BaseModel):
    """A sequência ATIVA (mais recente) de cada canal.

    ``None`` no canal que nunca teve geração. As sequências anteriores
    continuam no banco — "gerar novamente" não apaga nada —, mas não voltam
    por aqui: a aba mostra a cadência vigente, e devolver o histórico inteiro
    faria a tela ter que escolher qual delas é a de verdade.

    ⚠️ Os campos são fixos porque ``CanalMensagem`` é um conjunto fechado por
    decisão de produto (Instagram fora — não há integração de envio). Canal
    novo aqui exige mexer na tela de qualquer jeito.
    """

    email: SequenciaAbordagemRead | None = None
    whatsapp: SequenciaAbordagemRead | None = None
