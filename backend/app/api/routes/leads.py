"""Leitura de leads — lista, lista paginada e dossiê.

⚠️ **Escrita mínima.** Quem cria lead é o pipeline em lote
(``persistir_leads``), não a API. A única rota de escrita é o
``PATCH /{id}/status`` do Kanban (Fase 8b) — não há POST nem DELETE.

Contrato conferido contra ``frontend/src/api.ts``, que é quem define o que
cada tela consome.

## ``score_detalhes`` é recalculado, não persistido

O ``Lead`` guarda só ``score`` (int). O detalhamento por critério é
recomputado a cada resposta com ``calcular_score``, a partir dos sinais que
já estão no lead.

Medi antes de decidir: ``calcular_score`` é aritmética pura sobre 9
critérios, **~7 µs por lead** — uma página de 50 leads custa 0,35 ms, contra
os ~1–3 ms que a própria consulta ao banco leva. Persistir economizaria
menos que o ruído da medição e custaria uma coluna, uma migration e um
caminho novo pra dado desatualizar (score gravado com pesos antigos depois
de uma recalibragem). Recalcular também faz o dossiê refletir imediatamente
qualquer mudança em ``rules.py``, que é o comportamento desejado enquanto os
pesos ainda estão sendo calibrados com a cliente.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.lead import (
    KANBAN_STATUS_VALIDOS,
    TIPOS_CONTRATO_VALIDOS,
    KanbanStatus,
    Lead,
)
from app.models.user import User
from app.api.routes.limites_ia import (
    TIPO_INSIGHTS,
    limite_atingido,
    resumo_geracoes,
)
from app.core.tempo import agora_utc
from app.models.lead_message import (
    CANAIS_VALIDOS,
    LeadMessage,
    StatusMensagem,
    proxima_pendente,
)
from app.scoring.compute_lead_score import calcular_score
from app.schemas.lead import LeadListaResponse, LeadRead, LeadStatusUpdate
from app.schemas.lead_message import (
    LeadMessageRead,
    MensagensDoLeadRead,
    SequenciaAbordagemRead,
)
from app.services import ai_enrichment

router = APIRouter()

#: Teto de itens por página. Sem isso, `por_pagina=100000` viraria um jeito
#: trivial de derrubar a API a partir de uma tela autenticada.
POR_PAGINA_MAXIMO = 200
POR_PAGINA_PADRAO = 25

#: Campos que a lista aceita ordenar. Lista fechada de propósito: interpolar
#: um nome de coluna vindo da query string é injeção esperando acontecer.
#: `score_total` é o nome que o frontend usa (herdado do Minotto); aqui a
#: coluna se chama `score`.
ORDENACOES: dict[str, Any] = {
    "score_total": Lead.score,
    "score": Lead.score,
    "created_at": Lead.created_at,
}


def _sinais_do_lead(lead: Lead) -> dict[str, Any]:
    """Monta o dict de sinais pro motor de score a partir do lead persistido.

    Espelha o que `enriquecer_lead` monta no pipeline — os mesmos nomes de
    critério de ``SCORING_CRITERIA``. Sinal ausente fica de fora do dict (não
    entra como ``False``): o motor distingue "não medimos" de "medimos e não
    achamos", e achatar os dois aqui apagaria a distinção no dossiê.
    """
    nicho = lead.dados_nicho or {}
    sinais: dict[str, Any] = {}
    if nicho.get("area_ha") is not None:
        sinais["tamanho_propriedade"] = nicho["area_ha"]
    if nicho.get("valor_financiado") is not None:
        sinais["valor_financiado"] = nicho["valor_financiado"]
    if "culturas" in nicho:
        sinais["semente_sicor_cultura"] = bool(nicho.get("culturas"))
    if nicho.get("decisor") is not None:
        sinais["decisor_identificavel"] = nicho["decisor"]
    if nicho.get("whatsapp_ativo") is not None:
        sinais["whatsapp_ativo"] = nicho["whatsapp_ativo"]
    if nicho.get("email_status") is not None:
        sinais["email_validado"] = nicho["email_status"] in ("valid", "catch-all")
    if nicho.get("presenca_digital") is not None:
        sinais["presenca_digital"] = nicho["presenca_digital"]
    return sinais


def montar_lead_read(lead: Lead) -> LeadRead:
    """Serializa um lead, desempacotando ``dados_nicho`` e recalculando o
    detalhamento do score."""
    nicho = lead.dados_nicho or {}
    resultado = calcular_score(_sinais_do_lead(lead))
    return LeadRead(
        id=str(lead.id),
        documento=lead.documento,
        tipo_documento=lead.tipo_documento,
        nome=lead.nome,
        municipio=lead.municipio,
        uf=lead.uf,
        telefone=lead.telefone,
        telefone_secundario=lead.telefone_secundario,
        email=lead.email,
        site=lead.site,
        score=lead.score,
        prioridade=lead.prioridade,
        etapas_puladas=lead.etapas_puladas,
        dados_nicho=nicho or None,
        observacoes=lead.observacoes,
        insights_ia=lead.insights_ia,
        insights_gerado_em=lead.insights_gerado_em,
        kanban_status=lead.kanban_status,
        motivo_perda=lead.motivo_perda,
        servicos_vendidos=lead.servicos_vendidos,
        tipo_contrato=lead.tipo_contrato,
        valor_fechamento=lead.valor_fechamento,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        # --- desempacotado de dados_nicho ---
        area_ha=nicho.get("area_ha"),
        valor_financiado=nicho.get("valor_financiado"),
        culturas=list(nicho.get("culturas") or []),
        data_operacao=nicho.get("data_operacao"),
        recorrente=nicho.get("recorrente"),
        anos_credito=list(nicho.get("anos_credito") or []),
        codigos_car=list(nicho.get("codigos_car") or []),
        municipios=list(nicho.get("municipios") or []),
        n_operacoes=nicho.get("n_operacoes"),
        decisor=nicho.get("decisor"),
        whatsapp_ativo=nicho.get("whatsapp_ativo"),
        email_status=nicho.get("email_status"),
        presenca_digital=nicho.get("presenca_digital"),
        instagram=nicho.get("instagram"),
        cnae_descricao=nicho.get("cnae_descricao"),
        eh_cooperativa=nicho.get("eh_cooperativa"),
        # --- recalculado ---
        score_detalhes={
            "breakdown": [
                {
                    "key": c.key,
                    "label": c.label,
                    "weight": c.weight,
                    "layer": c.layer.value,
                    "points": c.pontos,
                }
                for c in resultado.criterios
            ]
        },
    )


def _resolver_lead(db: Session, identificador: str) -> Lead | None:
    """Acha um lead por **id** ou por **documento**. ``None`` se não existir.

    ⚠️ Aceitar os dois é deliberado (decisão da Fase 8a): o pedido falava em
    ``/api/leads/{documento}``, mas o frontend portado navega por ``lead.id``.
    Como CPF tem 11 dígitos e CNPJ 14, e um id de banco é bem menor, os dois
    espaços não colidem — a checagem é por comprimento, não por adivinhação.

    Extraído em função na Fase 8b porque o ``PATCH /{id}/status`` precisa da
    mesma resolução: o Kanban manda ``lead.id``, mas nada impede que uma tela
    futura mande o documento, e divergir aqui geraria um 404 fantasma.
    """
    alvo = identificador.strip()
    digitos = "".join(c for c in alvo if c.isdigit())

    lead = None
    if len(digitos) in (11, 14):
        lead = db.execute(
            select(Lead).where(Lead.documento == digitos)
        ).scalar_one_or_none()
    if lead is None and alvo.isdigit():
        lead = db.execute(select(Lead).where(Lead.id == int(alvo))).scalar_one_or_none()
    return lead


@router.get("", response_model=list[LeadRead])
def listar_leads(
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> list[LeadRead]:
    """Todos os leads. Consumido pelo Kanban, que precisa do quadro inteiro.

    ⚠️ Sem paginação de propósito — é o contrato do frontend
    (``fetchLeads``), e o Kanban não tem como montar as colunas com meia
    lista. O volume contratado é 50/mês, então a lista completa é pequena;
    se um dia crescer, quem muda é o Kanban, não este endpoint.
    """
    leads = db.execute(select(Lead).order_by(Lead.score.desc().nullslast())).scalars().all()
    return [montar_lead_read(lead) for lead in leads]


@router.get("/lista", response_model=LeadListaResponse)
def listar_leads_paginado(
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
    busca: str | None = Query(None, description="Nome, CPF ou CNPJ"),
    prioridade: str | None = Query(None),
    kanban_status: str | None = Query(None),
    ordenar_por: str = Query("score_total"),
    ordem: str = Query("desc"),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(POR_PAGINA_PADRAO, ge=1, le=POR_PAGINA_MAXIMO),
) -> LeadListaResponse:
    """Lista paginada com busca e filtro — consumida pela tela Lista de Leads.

    ⚠️ **Esta rota precisa ser declarada ANTES de ``/{identificador}``.** O
    FastAPI casa na ordem de declaração; invertido, ``/lista`` seria lido
    como um identificador e a tela quebraria com 404.

    ``kanban_status`` passou a ser aceito na Fase 8b (a coluna existe desde a
    migration ``7a3c9d2b4e10``). Valor desconhecido é **ignorado**, não vira
    422: o filtro vem de uma tela que o usuário não controla diretamente, e
    devolver erro ali só quebraria a lista.
    """
    consulta = select(Lead)

    if busca:
        termo = busca.strip()
        digitos = "".join(c for c in termo if c.isdigit())
        condicoes = [Lead.nome.ilike(f"%{termo}%")]
        # Busca por documento é por dígitos: o usuário digita com máscara,
        # o banco guarda sem.
        if digitos:
            condicoes.append(Lead.documento.like(f"%{digitos}%"))
        consulta = consulta.where(or_(*condicoes))

    if prioridade:
        consulta = consulta.where(Lead.prioridade == prioridade.strip().upper())

    if kanban_status and kanban_status.strip() in KANBAN_STATUS_VALIDOS:
        consulta = consulta.where(Lead.kanban_status == kanban_status.strip())

    total = db.execute(
        select(func.count()).select_from(consulta.subquery())
    ).scalar_one()

    coluna = ORDENACOES.get(ordenar_por, Lead.score)
    direcao = coluna.asc() if ordem.lower() == "asc" else coluna.desc()
    consulta = consulta.order_by(direcao.nullslast(), Lead.id.asc())
    consulta = consulta.offset((pagina - 1) * por_pagina).limit(por_pagina)

    leads = db.execute(consulta).scalars().all()
    return LeadListaResponse(
        items=[montar_lead_read(lead) for lead in leads],
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
    )


@router.get("/{identificador}", response_model=LeadRead)
def obter_lead(
    identificador: str,
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> LeadRead:
    """Dossiê de um lead, por **id** ou por **documento**.

    ⚠️ Aceita os dois de propósito. O pedido da Fase 8a foi
    ``GET /api/leads/{documento}``, mas o frontend portado navega por
    ``lead.id`` (``/leads/${lead.id}``) e chama ``fetchLead(token, id)``.
    Aceitar só documento quebraria a tela; aceitar só id ignoraria o pedido.
    Como CPF tem 11 dígitos e CNPJ 14, e um id de banco é bem menor, os dois
    espaços não colidem na prática — e a checagem abaixo é por comprimento,
    não por adivinhação.
    """
    lead = _resolver_lead(db, identificador)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado"
        )
    resposta = montar_lead_read(lead)
    # Só no dossiê: as rotas de lista fariam N consultas por página pra
    # calcular isto, e nenhuma tela de lista usa o contador.
    resposta.geracoes_ia = resumo_geracoes(db, lead)
    return resposta


@router.patch("/{lead_id}/status", response_model=LeadRead)
def atualizar_status_lead(
    lead_id: str,
    dados: LeadStatusUpdate,
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> LeadRead:
    """Move um lead entre colunas do Kanban.

    Exige autenticação, **qualquer papel** — mover card é o trabalho diário do
    vendedor, não operação administrativa. Só as rotas de busca exigem admin.

    ## Validação condicional

    - ``perdido``  ⇒ ``motivo_perda`` obrigatório.
    - ``ganho``    ⇒ ``servicos_vendidos`` (lista não-vazia), ``tipo_contrato``
      e ``valor_fechamento`` (> 0) obrigatórios — o contrato de
      ``FechamentoModal.tsx``.
    - Qualquer outra coluna não exige nada.

    Tudo validado aqui, não no banco: as colunas são nullable porque só fazem
    sentido nesses dois status. A exceção é ``kanban_status``, que **tem**
    CHECK no banco (ver ``app/models/lead.py``) — a validação aqui existe pra
    devolver 422 com a lista de valores aceitos em vez de um erro de
    integridade do Postgres.

    ## O que NÃO é limpo

    Sair de "perdido" limpa ``motivo_perda`` (o motivo deixou de valer). Sair
    de "ganho" **não** limpa os campos de fechamento: uma venda que aconteceu
    continua tendo acontecido, mesmo que o card seja movido de volta por engano
    ou reclassificado depois. Assimetria deliberada, herdada do Minotto.
    """
    if dados.kanban_status not in KANBAN_STATUS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "kanban_status inválido. Valores aceitos: "
                f"{list(KANBAN_STATUS_VALIDOS)}"
            ),
        )

    if dados.kanban_status == KanbanStatus.PERDIDO.value and not (
        dados.motivo_perda and dados.motivo_perda.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="motivo_perda é obrigatório quando kanban_status é 'perdido'",
        )

    if dados.kanban_status == KanbanStatus.GANHO.value:
        if not dados.servicos_vendidos:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "servicos_vendidos é obrigatório (lista não-vazia) quando "
                    "kanban_status é 'ganho'"
                ),
            )
        if dados.tipo_contrato not in TIPOS_CONTRATO_VALIDOS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "tipo_contrato é obrigatório e deve ser um de "
                    f"{list(TIPOS_CONTRATO_VALIDOS)} quando kanban_status é 'ganho'"
                ),
            )
        if dados.valor_fechamento is None or dados.valor_fechamento <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "valor_fechamento é obrigatório e deve ser maior que zero "
                    "quando kanban_status é 'ganho'"
                ),
            )

    lead = _resolver_lead(db, lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado"
        )

    lead.kanban_status = dados.kanban_status
    lead.motivo_perda = (
        dados.motivo_perda.strip()
        if dados.kanban_status == KanbanStatus.PERDIDO.value and dados.motivo_perda
        else None
    )
    if dados.kanban_status == KanbanStatus.GANHO.value:
        lead.servicos_vendidos = list(dados.servicos_vendidos or [])
        lead.tipo_contrato = dados.tipo_contrato
        lead.valor_fechamento = dados.valor_fechamento

    db.commit()
    db.refresh(lead)
    return montar_lead_read(lead)


# --- Geração por IA (Fase 10) ----------------------------------------------
#
# ⚠️ **Cada rota abaixo gasta dinheiro.** A ordem das checagens não é estética:
# canal → lead → limite → IA. O limite é verificado ANTES da chamada porque o
# ponto dele é não gastar; validar depois não economizaria nada. E a cota só é
# consumida DEPOIS de a geração dar certo — uma falha da IA não pode queimar
# a tentativa do usuário.

DETALHE_LIMITE_IA = (
    "Limite de gerações atingido para este lead. "
    "Contate um administrador para liberar mais."
)


def _barrar_se_limite_atingido(db: Session, lead: Lead, tipo: str) -> None:
    """429 quando o lead já esgotou as gerações deste tipo."""
    if limite_atingido(db, lead, tipo):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=DETALHE_LIMITE_IA
        )


def _dados_para_ia(lead: Lead) -> dict[str, Any]:
    """Achata o lead no dict que os prompts consomem.

    Junta coluna e ``dados_nicho`` num lugar só — os prompts não deveriam
    precisar saber onde cada sinal mora. Mesma lição de `getContatos` no
    frontend: um caminho de leitura, não dois.
    """
    nicho = lead.dados_nicho or {}
    return {
        "nome": lead.nome,
        "municipio": lead.municipio,
        "uf": lead.uf,
        "telefone": lead.telefone,
        "telefone_secundario": lead.telefone_secundario,
        "email": lead.email,
        "site": lead.site,
        "score": lead.score,
        "prioridade": lead.prioridade,
        "score_detalhes": montar_lead_read(lead).score_detalhes.model_dump()
        if lead.score is not None
        else None,
        "area_ha": nicho.get("area_ha"),
        "valor_financiado": nicho.get("valor_financiado"),
        "culturas": nicho.get("culturas"),
        "anos_credito": nicho.get("anos_credito"),
        "decisor": nicho.get("decisor"),
        "fonte_decisor": nicho.get("fonte_decisor"),
        "whatsapp_ativo": nicho.get("whatsapp_ativo"),
        "email_status": nicho.get("email_status"),
        "presenca_digital": nicho.get("presenca_digital"),
        "instagram": nicho.get("instagram"),
        "eh_cooperativa": nicho.get("eh_cooperativa"),
    }


# --- Sequência de abordagem (Fase 11a) -------------------------------------
#
# Uma geração deixou de produzir uma mensagem e passou a produzir uma
# **sequência**: 3 mensagens no WhatsApp, 2 no e-mail, todas com o mesmo
# ``grupo_id``. As três funções abaixo são a leitura dessa estrutura; a regra
# de ordem em si mora em ``proxima_pendente`` (models/lead_message.py), para
# não existir aqui uma segunda versão dela.


def _sequencia_read(mensagens: list[LeadMessage]) -> SequenciaAbordagemRead:
    """Monta a resposta de UM grupo. Assume grupo não vazio (não existe grupo
    sem mensagem: a rota grava a sequência inteira ou nada)."""
    ordenadas = sorted(mensagens, key=lambda m: m.ordem)
    proxima = proxima_pendente(ordenadas)
    return SequenciaAbordagemRead(
        grupo_id=ordenadas[0].grupo_id,
        canal=ordenadas[0].canal,
        gerado_em=ordenadas[0].gerado_em,
        total=len(ordenadas),
        proxima_ordem=proxima.ordem if proxima is not None else None,
        mensagens=[LeadMessageRead.model_validate(m) for m in ordenadas],
    )


def _grupos_do_lead(db: Session, lead: Lead) -> list[list[LeadMessage]]:
    """Todas as mensagens do lead, fatiadas por ``grupo_id``.

    Agrupa em Python, não em SQL: o volume é o limite de gerações vezes o
    tamanho da sequência (na prática ~12 linhas por lead), e uma consulta com
    ``GROUP BY`` teria que voltar ao banco para buscar as linhas de cada grupo
    mesmo assim.
    """
    mensagens = db.execute(
        select(LeadMessage).where(LeadMessage.lead_id == lead.id)
    ).scalars().all()

    grupos: dict[str, list[LeadMessage]] = {}
    for mensagem in mensagens:
        grupos.setdefault(mensagem.grupo_id, []).append(mensagem)
    return list(grupos.values())


def _sequencia_ativa(
    grupos: list[list[LeadMessage]], canal: str
) -> list[LeadMessage] | None:
    """A sequência vigente do canal: a do grupo gerado por último.

    "Gerar novamente" cria um grupo novo e este passa a ser o ativo; o
    anterior continua no banco, fora desta resposta.

    O desempate por ``grupo_id`` só existe para o resultado ser determinístico
    se dois grupos dividirem o mesmo ``gerado_em`` (possível em teste, que
    gera duas vezes seguidas). Sem ele a "ativa" dependeria da ordem em que o
    banco devolveu as linhas.
    """
    do_canal = [g for g in grupos if g[0].canal == canal]
    if not do_canal:
        return None
    return max(
        do_canal, key=lambda g: (max(m.gerado_em for m in g), g[0].grupo_id)
    )


@router.get("/{lead_id}/mensagens", response_model=MensagensDoLeadRead)
def listar_mensagens(
    lead_id: str,
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> MensagensDoLeadRead:
    """A sequência ATIVA de cada canal, ordenada, com o status de cada mensagem.

    404 se o lead não existir. Lead que existe mas nunca teve geração devolve
    ``{"email": null, "whatsapp": null}`` — ausência não é erro.

    ⚠️ **Formato novo na Fase 11a**: objeto por canal, não mais lista plana de
    mensagens. Ver o docstring de ``app/schemas/lead_message.py`` para o que
    isso faz com o frontend atual enquanto a Fase 11b não chega.
    """
    lead = _resolver_lead(db, lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado"
        )

    grupos = _grupos_do_lead(db, lead)
    ativas = {
        canal: _sequencia_ativa(grupos, canal) for canal in CANAIS_VALIDOS
    }
    return MensagensDoLeadRead(
        **{
            canal: _sequencia_read(grupo) if grupo else None
            for canal, grupo in ativas.items()
        }
    )


@router.post(
    "/{lead_id}/gerar-abordagem/{canal}", response_model=SequenciaAbordagemRead
)
def gerar_abordagem(
    lead_id: str,
    canal: str,
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> SequenciaAbordagemRead:
    """Gera uma NOVA sequência de abordagem — **uma chamada paga**.

    Cria um grupo novo (3 linhas no WhatsApp, 2 no e-mail); nunca sobrescreve
    nem completa um grupo existente. "Gerar novamente" é exatamente esta rota
    de novo: o grupo anterior vira histórico e o novo passa a ser o ativo.
    Requer autenticação, qualquer papel (é o trabalho de quem vende).

    ⚠️ **O grupo inteiro consome UMA geração da cota**, não uma por mensagem —
    ver ``contar_geracoes``, que conta ``grupo_id`` distintos.

    422 canal inválido · 404 lead inexistente · 429 limite atingido ·
    502 se a IA não devolveu a sequência completa.

    ⚠️ O 502 existe para **não persistir sequência vazia ou pela metade**:
    ``gerar_sequencia_abordagem`` nunca levanta, devolve ``[]`` tanto em erro
    de rede quanto em resposta malformada ou truncada. Sem esta checagem, o
    vendedor veria uma cadência em branco sem saber que houve falha.
    """
    if canal not in CANAIS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Canal inválido. Valores aceitos: {list(CANAIS_VALIDOS)}",
        )

    lead = _resolver_lead(db, lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado"
        )

    _barrar_se_limite_atingido(db, lead, canal)

    sequencia = ai_enrichment.gerar_sequencia_abordagem(_dados_para_ia(lead), canal=canal)
    if not sequencia:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível gerar a sequência agora. Tente novamente.",
        )

    grupo_id = str(uuid.uuid4())
    # Um único instante para todas as linhas: elas nasceram da mesma chamada.
    # Carimbar cada uma com o seu ``default`` faria a sequência ter 3
    # "momentos de geração" e a busca pelo grupo mais recente comparar
    # timestamps de mensagens em vez de gerações.
    agora = agora_utc()
    linhas = [
        LeadMessage(
            lead_id=lead.id,
            canal=canal,
            grupo_id=grupo_id,
            ordem=mensagem.ordem,
            status=StatusMensagem.PENDENTE.value,
            conteudo=mensagem.conteudo,
            assunto=mensagem.assunto,
            gerado_em=agora,
        )
        for mensagem in sequencia
    ]
    db.add_all(linhas)
    db.commit()
    for linha in linhas:
        db.refresh(linha)
    return _sequencia_read(linhas)


@router.patch(
    "/{lead_id}/mensagens/{mensagem_id}/enviada",
    response_model=SequenciaAbordagemRead,
)
def marcar_mensagem_enviada(
    lead_id: str,
    mensagem_id: str,
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> SequenciaAbordagemRead:
    """Marca uma mensagem como enviada. **Não envia nada** — quem envia é o
    vendedor, no WhatsApp ou no e-mail dele; aqui ele só registra que mandou.

    ⚠️ **Só a próxima pendente da sequência é aceita.** Marcar o follow-up
    antes do primeiro contato descreveria uma cadência que não aconteceu, e é
    dela que a tela tira o que oferecer em seguida. Fora de ordem (ou já
    enviada) é **422 com o motivo**, não 500: é entrada inválida do usuário,
    não defeito do servidor.

    Devolve a sequência inteira atualizada, para a tela redesenhar a partir do
    estado do servidor em vez de adivinhar qual botão liberar.

    404 lead ou mensagem inexistente · 422 fora de ordem.
    """
    lead = _resolver_lead(db, lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado"
        )

    mensagem = db.get(LeadMessage, mensagem_id)
    # A checagem de dono é o que impede usar o id de uma mensagem de outro
    # lead para mexer nela por uma URL que parece inocente.
    if mensagem is None or mensagem.lead_id != lead.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mensagem não encontrada para este lead",
        )

    if mensagem.status == StatusMensagem.ENVIADA.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Esta mensagem já foi marcada como enviada.",
        )

    grupo = db.execute(
        select(LeadMessage).where(LeadMessage.grupo_id == mensagem.grupo_id)
    ).scalars().all()

    proxima = proxima_pendente(grupo)
    if proxima is None or proxima.id != mensagem.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Não dá para pular etapa da sequência: a próxima mensagem "
                f"pendente é a {proxima.ordem} de {len(grupo)}. "
                f"Marque-a como enviada antes desta."
            ),
        )

    mensagem.status = StatusMensagem.ENVIADA.value
    mensagem.enviada_em = agora_utc()
    db.commit()
    return _sequencia_read(grupo)


@router.post("/{lead_id}/gerar-insights", response_model=LeadRead)
def gerar_insights(
    lead_id: str,
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> LeadRead:
    """Gera (ou regenera) a análise estratégica — **chamada paga**.

    Sobrescreve ``insights_ia``/``insights_gerado_em``: diferente das
    mensagens, insights não mantêm histórico (cada geração substitui a
    anterior, que é o que "Gerar novamente" significa na tela).

    404 · 429 · 502, pelas mesmas razões de ``gerar_abordagem``.
    """
    lead = _resolver_lead(db, lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado"
        )

    _barrar_se_limite_atingido(db, lead, TIPO_INSIGHTS)

    resultado = ai_enrichment.gerar_insights_estrategicos(_dados_para_ia(lead))
    if not resultado:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível gerar os insights agora. Tente novamente.",
        )

    lead.insights_ia = resultado
    lead.insights_gerado_em = agora_utc()
    # ⚠️ Incrementado só APÓS o 502 acima: uma geração que falhou não gasta
    # cota do usuário.
    lead.insights_geracoes_count = (lead.insights_geracoes_count or 0) + 1
    db.commit()
    db.refresh(lead)

    resposta = montar_lead_read(lead)
    resposta.geracoes_ia = resumo_geracoes(db, lead)
    return resposta
