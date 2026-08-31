"""Contagem e limite das gerações de IA por lead.

⚠️ **Controle de custo, não de UX.** Cada "Gerar mensagem"/"Gerar Insights" no
dossiê é uma chamada paga à Anthropic, e a tela é acessível a usuários
"client". Sem isto, alguém explorando a interface geraria indefinidamente.

Este módulo é a **fonte única** da regra: as duas rotas de geração, a rota de
reset do admin e o ``GET /api/leads/{id}`` (que informa ao frontend quantas
restam) passam todos por aqui. Duplicar a contagem seria a receita para
frontend e backend discordarem sobre quantas gerações sobraram.

## Duas formas de contar, porque os dados são guardados diferente

- **e-mail/WhatsApp**: cada geração é uma SEQUÊNCIA de linhas em
  ``lead_messages`` que compartilham um ``grupo_id`` (histórico, nunca
  sobrescrito). Contagem = ``COUNT(DISTINCT grupo_id)`` por lead + canal.

  ⚠️ **Era ``COUNT(*)`` até a Fase 11a e teve que deixar de ser.** Uma
  geração de WhatsApp passou a gravar 3 linhas; contando linhas, a primeira
  geração já estouraria o limite de 2 e o vendedor perderia metade da cota
  no primeiro clique. A regra do limite não mudou — 2 gerações por canal —,
  mudou o que uma geração produz, e a contagem seguiu.

  Isso vale inclusive para o histórico anterior à Fase 11a: a migration deu
  a cada linha legada o próprio ``grupo_id``, então ``COUNT(DISTINCT ...)``
  devolve para elas exatamente o mesmo número que o ``COUNT(*)`` devolvia.
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

import hashlib
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.lead import Lead
from app.models.lead_message import CanalMensagem, LeadMessage

TIPO_INSIGHTS = "insights"

#: Quanto uma requisição espera pelo lock da cota antes de desistir.
#:
#: ⚠️ **Existe pra não trocar gasto indevido por indisponibilidade.** O lock é
#: segurado durante a chamada à IA, que tem timeout de 30 s. Sem teto de
#: espera, N requisições no mesmo lead ficariam presas até 30 s cada — e como
#: rota síncrona do FastAPI ocupa uma thread do pool (40 por padrão), 40
#: cliques no mesmo lead derrubariam a API inteira. Um DoS de
#: disponibilidade no lugar do DoS de custo não é conserto.
#:
#: 5 s cobre o caso real (dois cliques seguidos no mesmo botão) e devolve erro
#: claro em vez de pendurar quem chegou depois.
LOCK_COTA_TIMEOUT_SEGUNDOS = 5

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


class GeracaoEmAndamento(RuntimeError):
    """Outra requisição está gerando para este mesmo lead+tipo agora.

    Exceção própria (e não ``HTTPException``) pra que este módulo continue
    sendo regra de negócio pura: quem traduz em status HTTP é a rota, como já
    acontece com ``limite_atingido`` → 429.
    """


def _chave_de_lock(lead_id: int, tipo: str) -> int:
    """Um bigint estável e determinístico pra ``(lead, tipo)``.

    O advisory lock do Postgres é indexado por número, não por texto. Usa-se
    BLAKE2b truncado em 63 bits (não 64) porque a chave é ``bigint``
    **com sinal**: 64 bits estourariam pro negativo, o que funciona mas
    dificulta a leitura em ``pg_locks`` na hora de investigar um travamento.

    Colisão entre pares diferentes é possível em teoria e inofensiva na
    prática: o pior efeito é dois leads distintos serializarem entre si por um
    instante, nunca cota compartilhada errada — quem decide a cota continua
    sendo o ``COUNT`` de baixo.
    """
    digest = hashlib.blake2b(f"ia:{lead_id}:{tipo}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") >> 1


def travar_cota(db: Session, lead: Lead, tipo: str) -> None:
    """Serializa as gerações de ``(lead, tipo)`` até o fim da transação.

    ⚠️ **Corrige a corrida encontrada na auditoria de 31/08/2026.** A rota de
    geração fazia::

        checar cota (SELECT COUNT)   ← sem lock
        chamar a IA                  ← PAGO, segundos de janela
        gravar + a cota é debitada

    Duas requisições simultâneas liam a mesma contagem antes de qualquer uma
    gravar, ambas passavam, e **ambas pagavam**. O limite de 2 virava N para
    aquela rodada — o único caminho conhecido para gasto acima do previsto.

    ## Por que advisory lock e não ``SELECT ... FOR UPDATE`` no lead

    ``FOR UPDATE`` na linha do lead resolveria a corrida, mas bloquearia
    **qualquer** escrita naquele lead enquanto a IA responde. Na prática:
    arrastar o card no Kanban (``PATCH /status``) ficaria pendurado por
    segundos por causa de uma geração de mensagem que não tem nada a ver com
    o status. O advisory lock é keyed em ``(lead, tipo)``: serializa
    exatamente o que precisa ser serializado e não toca em linha nenhuma.

    ``pg_advisory_xact_lock`` é liberado automaticamente no COMMIT **ou no
    ROLLBACK** — inclusive quando a rota devolve 502 porque a IA falhou. Não
    há caminho que deixe o lock pendurado sem que a transação também tenha
    ficado, e a sessão é fechada no ``finally`` do ``get_db``.

    ⚠️ **No-op fora do Postgres.** SQLite (a suíte) não tem advisory lock nem
    concorrência de escrita real. A garantia é verificada onde ela existe:
    contra Postgres, com requisições paralelas de verdade.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return

    # `SET LOCAL` vale só até o fim desta transação — não vaza o timeout pra
    # próxima requisição que pegar a mesma conexão do pool.
    db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_COTA_TIMEOUT_SEGUNDOS}s'"))
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:chave)"),
            {"chave": _chave_de_lock(lead.id, tipo)},
        )
    except DBAPIError as exc:
        # Estourou os 5 s: já existe uma geração em curso pra este lead+tipo.
        # Devolver erro agora é melhor que esperar a IA da outra requisição
        # terminar pra só então descobrir que a cota acabou.
        raise GeracaoEmAndamento(
            "Já existe uma geração em andamento para este lead neste canal. "
            "Aguarde alguns segundos e tente novamente."
        ) from exc


def contar_geracoes(db: Session, lead: Lead, tipo: str) -> int:
    """Gerações deste tipo desde a última liberação (ou desde sempre)."""
    if tipo == TIPO_INSIGHTS:
        return lead.insights_geracoes_count or 0

    consulta = (
        select(func.count(func.distinct(LeadMessage.grupo_id)))
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
