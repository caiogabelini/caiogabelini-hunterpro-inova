"""Dashboard — resumo executivo, ações, simulador, funil e motivos de perda.

## Autorização: qualquer papel autenticado, **não** só admin

Confirmado contra o Minotto real: lá as 5 rotas usam ``get_current_user``,
não ``require_admin``. Faz sentido e foi mantido — o Dashboard é a tela de
trabalho de quem vende, e os leads são compartilhados entre a Inova e a
4Hands (mesmo critério já usado em ``GET /api/leads``). ``require_admin``
segue reservado a ``/api/admin/*``, onde uma ação **gasta dinheiro**.

## Tudo é agregado no banco, nada é contado em Python

Nenhuma rota aqui carrega leads pra memória: são ``COUNT``/``SUM``/``AVG``/
``GROUP BY`` no Postgres. Ver a nota de performance no fim do módulo sobre
por que **não** foi criado índice novo.

## O que mudou em relação ao Minotto

O Minotto é 100% CNPJ; aqui o universo é ~98% CPF de produtor rural. Nenhuma
métrica deste módulo conta "empresas" — todas contam **lead** (a linha,
chaveada por ``documento``), que é a unidade correta nas duas bases. Onde o
Minotto usava sinal do nicho de saúde, ver ``ACAO_WHATSAPP_NAO_ABORDADO``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.tempo import agora_utc
from app.models.lead import KanbanStatus, Lead
from app.models.user import User
from app.schemas.dashboard import (
    AcaoRecomendada,
    DashboardPremissas,
    DashboardSummary,
    FunilEtapa,
    MotivoPerda,
)

router = APIRouter()

STATUS_EM_NEGOCIACAO = (
    KanbanStatus.PROPOSTA_ENVIADA.value,
    KanbanStatus.NEGOCIACAO.value,
)

#: "Ainda não abordado" = o lead nem saiu da entrada do funil.
STATUS_NAO_ABORDADO = (
    KanbanStatus.NOVO_LEAD.value,
    KanbanStatus.QUALIFICACAO.value,
)

#: Lead "qualificado" pro simulador = **posição no funil**, não score. Já saiu
#: de "novo_lead" e ainda não terminou em "ganho"/"perdido".
#:
#: ⚠️ A confusão que isto evita já mordeu no Minotto: contar
#: ``prioridade == 'ALTA'`` aqui misturaria duas coisas independentes —
#: ``prioridade`` traduz o score (quão bom o lead parece), ``kanban_status``
#: diz onde ele está no processo (quão longe já foi). Um lead ALTA que
#: ninguém contatou não está qualificado; um BAIXA em negociação está.
STATUS_QUALIFICADOS_NO_FUNIL = (
    KanbanStatus.QUALIFICACAO.value,
    KanbanStatus.CONTATADO.value,
    KanbanStatus.RESPONDEU.value,
    KanbanStatus.REUNIAO.value,
    KanbanStatus.PROPOSTA_ENVIADA.value,
    KanbanStatus.NEGOCIACAO.value,
)

TIPO_CONTRATO_PONTUAL = "pontual"
TIPO_CONTRATO_RECORRENTE = "recorrente"

#: Rótulo da prioridade mais alta. ⚠️ No Minotto é ``"A"``; aqui é ``"ALTA"``
#: (ver ``prioridade_do_score`` em ``app/workers/enriquecimento.py``). Copiar
#: o ``"A"`` daria contagem zero silenciosa — um card sumindo do dashboard
#: sem erro nenhum.
PRIORIDADE_ALTA = "ALTA"

#: Ordem do funil + rótulos. ⚠️ Espelha ``frontend/src/kanbanStatuses.ts``,
#: conferido valor a valor. Nada sincroniza os dois automaticamente: etapa
#: nova exige mexer aqui, lá, no enum ``KanbanStatus`` e numa migration (há
#: CHECK no banco).
ETAPAS_FUNIL: tuple[tuple[str, str], ...] = (
    (KanbanStatus.NOVO_LEAD.value, "Novo Lead"),
    (KanbanStatus.QUALIFICACAO.value, "Qualificação"),
    (KanbanStatus.CONTATADO.value, "Contatado"),
    (KanbanStatus.RESPONDEU.value, "Respondeu"),
    (KanbanStatus.REUNIAO.value, "Reunião"),
    (KanbanStatus.PROPOSTA_ENVIADA.value, "Proposta Enviada"),
    (KanbanStatus.NEGOCIACAO.value, "Negociação"),
    (KanbanStatus.GANHO.value, "Ganho"),
    (KanbanStatus.PERDIDO.value, "Perdido"),
)

LIMITE_MOTIVOS_PERDA = 10

#: Padrões do simulador enquanto o usuário não salvar os próprios.
#: ⚠️ **Não vêm de dado real** — são chute inicial, herdados do Minotto e
#: nunca confirmados com a Carolina. ``leads_qualificados``, ao contrário
#: destes dois, É calculado da base.
TAXA_FECHAMENTO_PADRAO = 20.0
TICKET_MEDIO_PADRAO = 1500.0


def _inicio_do_mes_atual() -> datetime:
    agora = agora_utc()
    return datetime(agora.year, agora.month, 1)


def _contar(db: Session, *condicoes: Any) -> int:
    consulta = select(func.count()).select_from(Lead)
    if condicoes:
        consulta = consulta.where(*condicoes)
    return db.execute(consulta).scalar() or 0


def _texto_nicho(chave: str):
    """Sinal de texto dentro de ``Lead.dados_nicho``, comparável com ``NULL``.

    ⚠️ **O ``.as_string()`` não é decorativo.** Sem ele, ``dados_nicho[chave]``
    devolve um tipo JSON e a comparação com ``NULL`` mente nas duas direções —
    medido em 26/08/2026 com dois leads, um com ``decisor`` e outro sem:

    ===========================  ========  ============
    condição                     casou     correto
    ===========================  ========  ============
    ``is_not(None)``             2         1
    ``as_string().is_not(None)`` 1         1
    ``is_(None)``                0         1
    ``as_string().is_(None)``    1         1
    ===========================  ========  ============

    Ou seja: sem o cast, "leads com decisor identificado" contaria **todo
    lead**, e "leads que precisam de revisão manual" contaria **nenhum**. Os
    dois erram calados, com número plausível na tela — que é o pior modo de
    falha possível num dashboard.

    Os sinais de enriquecimento moram num JSON, não em colunas (decisão da
    Fase 1, mantida porque os parsers de nicho ainda podem mudar). Extrair com
    o operador do banco mantém a agregação **no servidor** — trazer os leads
    pra memória só pra ler uma chave de dict seria o oposto do que um
    dashboard aberto o tempo todo precisa.
    """
    return Lead.dados_nicho[chave].as_string()


def _booleano_nicho(chave: str):
    """Sinal booleano de ``dados_nicho``, com a mesma ressalva de cast.

    ⚠️ Chave ausente vira ``NULL``, e ``NULL IS TRUE`` é falso — que é o
    comportamento certo: ``None`` significa "não medimos" e não pode contar
    como sinal confirmado (§6, a distinção entre ``None`` e ``False``).
    """
    return Lead.dados_nicho[chave].as_boolean()


@router.get("/summary", response_model=DashboardSummary)
def get_summary(
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> DashboardSummary:
    """Resumo executivo. Requer autenticação, qualquer papel.

    ``receita_fechada_*`` é receita **real** (leads em "ganho", valores de
    ``Lead.valor_fechamento``) — diferente da receita **estimada** que o
    Simulador calcula sobre as premissas. Os dois números nunca devem ser
    confundidos, por isso ficam em campos de nomes bem distintos.

    ``func.avg``/``func.sum`` ignoram ``NULL`` sozinhos: um lead sem score, ou
    um "ganho" sem valor preenchido (não acontece pela API, que exige o campo,
    mas pode existir em dado antigo), não quebra a conta — só não contribui.
    Sem nenhuma linha, o SQL devolve ``NULL``, tratado aqui como ``0.0``.
    """
    total_leads = _contar(db)
    leads_ganhos = _contar(db, Lead.kanban_status == KanbanStatus.GANHO.value)

    score_medio_bruto = db.execute(
        select(func.avg(Lead.score.cast(Float)))
    ).scalar()

    def _receita(tipo: str) -> float:
        return db.execute(
            select(func.sum(Lead.valor_fechamento)).where(
                Lead.kanban_status == KanbanStatus.GANHO.value,
                Lead.tipo_contrato == tipo,
            )
        ).scalar() or 0.0

    pontual = _receita(TIPO_CONTRATO_PONTUAL)
    recorrente = _receita(TIPO_CONTRATO_RECORRENTE)

    return DashboardSummary(
        leads_no_mes=_contar(db, Lead.created_at >= _inicio_do_mes_atual()),
        # ⚠️ `LEADS_POR_BUSCA` é a cota contratada (50/mês), não a margem de
        # pré-seleção. O Minotto usa `PLANO_LEADS_MES`, que não existe aqui.
        leads_no_mes_limite=settings.LEADS_POR_BUSCA,
        score_medio=round(score_medio_bruto, 1) if score_medio_bruto is not None else 0.0,
        leads_em_negociacao=_contar(db, Lead.kanban_status.in_(STATUS_EM_NEGOCIACAO)),
        taxa_conversao=(
            round(leads_ganhos / total_leads * 100, 1) if total_leads > 0 else 0.0
        ),
        # Ver o schema: sempre 0 nesta base, geração por IA não foi portada.
        total_geracoes_ia_mes=0,
        receita_fechada_pontual=pontual,
        receita_fechada_recorrente_mensal=recorrente,
        receita_fechada_total=pontual + recorrente,
    )


#: ⚠️ **Substitui** a 4ª ação do Minotto ("Leads com dívida ativa PGFN ainda
#: não abordados"). Dívida ativa é o sinal de dor do nicho de saúde e **não
#: tem equivalente** no agro — não há nada nesta base que diga "este produtor
#: está em apuro fiscal".
#:
#: A substituição escolhida é WhatsApp confirmado + ainda na entrada do funil:
#: é o sinal de maior peso (15) depois do decisor, é **binário** (não exige
#: inventar um corte de "área grande" que ninguém calibrou), e produz uma
#: lista imediatamente acionável — dá pra mandar mensagem hoje. As
#: alternativas consideradas foram área da propriedade e recorrência no
#: crédito; as duas exigiriam um limiar arbitrário.
ACAO_WHATSAPP_NAO_ABORDADO = "whatsapp_ativo_nao_abordado"


@router.get("/acoes-recomendadas", response_model=list[AcaoRecomendada])
def get_acoes_recomendadas(
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> list[AcaoRecomendada]:
    """Até 4 categorias fixas de "próxima ação", cada uma com sua contagem.

    Categoria com contagem zero é **omitida** — mostrar "0 leads" não ajuda
    ninguém a priorizar. Ordem fixa, não por contagem.
    """
    candidatas = [
        AcaoRecomendada(
            titulo="Leads com decisor identificado prontos para abordagem",
            quantidade=_contar(
                db,
                _texto_nicho("decisor").is_not(None),
                Lead.kanban_status == KanbanStatus.NOVO_LEAD.value,
            ),
            kanban_status_filtro=KanbanStatus.NOVO_LEAD.value,
            filtro_chave="decisor_identificado",
        ),
        AcaoRecomendada(
            titulo="Leads de alta prioridade ainda não contatados",
            quantidade=_contar(
                db,
                Lead.prioridade == PRIORIDADE_ALTA,
                Lead.kanban_status == KanbanStatus.NOVO_LEAD.value,
            ),
            kanban_status_filtro=KanbanStatus.NOVO_LEAD.value,
            filtro_chave="prioridade_a",
        ),
        AcaoRecomendada(
            titulo="Leads que precisam de revisão manual",
            quantidade=_contar(
                db,
                _texto_nicho("decisor").is_(None),
                Lead.telefone.is_(None),
                Lead.email.is_(None),
            ),
            kanban_status_filtro=None,
            filtro_chave="revisao_manual",
        ),
        AcaoRecomendada(
            titulo="Produtores com WhatsApp confirmado ainda não abordados",
            quantidade=_contar(
                db,
                _booleano_nicho("whatsapp_ativo").is_(True),
                Lead.kanban_status.in_(STATUS_NAO_ABORDADO),
            ),
            kanban_status_filtro=None,
            filtro_chave=ACAO_WHATSAPP_NAO_ABORDADO,
        ),
    ]
    return [acao for acao in candidatas if acao.quantidade > 0]


def _leads_qualificados_padrao(db: Session) -> int:
    """Quantos leads estão ativamente no meio do funil.

    Por ``kanban_status`` (posição), **não** por ``prioridade`` (score) — ver
    ``STATUS_QUALIFICADOS_NO_FUNIL``.
    """
    return _contar(db, Lead.kanban_status.in_(STATUS_QUALIFICADOS_NO_FUNIL))


@router.get("/premissas", response_model=DashboardPremissas)
def get_premissas(
    db: Session = Depends(get_db),
    usuario: User = Depends(get_current_user),
) -> DashboardPremissas:
    """Premissas do Simulador de Receita **do usuário logado**.

    Nunca salvas ⇒ devolve um padrão: ``leads_qualificados`` calculado da base
    real, ``taxa_fechamento``/``ticket_medio`` fixos (não há histórico de
    conversão nem de ticket real pra derivar). O padrão **não é persistido
    aqui** — só quando o usuário confirma no modal (``PUT``).
    """
    if usuario.dashboard_premissas:
        return DashboardPremissas(**usuario.dashboard_premissas)

    return DashboardPremissas(
        leads_qualificados=_leads_qualificados_padrao(db),
        taxa_fechamento=TAXA_FECHAMENTO_PADRAO,
        ticket_medio=TICKET_MEDIO_PADRAO,
    )


@router.put("/premissas", response_model=DashboardPremissas)
def put_premissas(
    dados: DashboardPremissas,
    db: Session = Depends(get_db),
    usuario: User = Depends(get_current_user),
) -> DashboardPremissas:
    """Salva as premissas do usuário logado — por usuário, não global.

    ⚠️ Esta 6ª rota **não estava nas 5 pedidas**, mas o frontend já a chama:
    ``salvarPremissas`` em ``api.ts``, usada por ``SimuladorReceitaModal``.
    Sem ela o botão "Salvar" do simulador daria 404 — exatamente a classe de
    bug que já corrigimos nas abas Mensagens e Insights.
    """
    usuario.dashboard_premissas = dados.model_dump()
    db.commit()
    db.refresh(usuario)
    return DashboardPremissas(**usuario.dashboard_premissas)


@router.get("/funil", response_model=list[FunilEtapa])
def get_funil(
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> list[FunilEtapa]:
    """As 9 etapas, **sempre todas**, mesmo com quantidade 0.

    Diferente de ``acoes-recomendadas`` (que omite zero): aqui o zero **é**
    informação — "esta etapa está vazia agora" é um dado válido de mostrar.

    ⚠️ **Fotografia, não taxa de conversão.** ``percentual`` é "quantidade
    nesta etapa ÷ total de leads × 100", com "perdido" **dentro** do
    denominador. Sem "perdido" ali, a soma dos percentuais fecharia 100% de um
    universo que não é "todos os leads", e o total deixaria de bater com a
    ``taxa_conversao`` do ``/summary``, que conta sobre todos. Ver o schema
    sobre a ausência de histórico de transição.
    """
    contagem = dict(
        db.execute(
            select(Lead.kanban_status, func.count()).group_by(Lead.kanban_status)
        ).all()
    )
    total = sum(contagem.values())

    return [
        FunilEtapa(
            status=status,
            label=label,
            quantidade=contagem.get(status, 0),
            percentual=(
                round(contagem.get(status, 0) / total * 100, 1) if total > 0 else 0.0
            ),
        )
        for status, label in ETAPAS_FUNIL
    ]


@router.get("/motivos-perda", response_model=list[MotivoPerda])
def get_motivos_perda(
    db: Session = Depends(get_db),
    _usuario: User = Depends(get_current_user),
) -> list[MotivoPerda]:
    """Ranking dos motivos de perda, do mais comum pro menos.

    Agrupado pelo texto **exato**, sem normalização semântica. Lista vazia
    (não erro) quando não há perdido com motivo — o frontend trata isso como
    estado vazio de tom positivo.
    """
    linhas = db.execute(
        select(Lead.motivo_perda, func.count())
        .where(
            Lead.kanban_status == KanbanStatus.PERDIDO.value,
            Lead.motivo_perda.is_not(None),
            Lead.motivo_perda != "",
        )
        .group_by(Lead.motivo_perda)
        .order_by(func.count().desc(), Lead.motivo_perda.asc())
        .limit(LIMITE_MOTIVOS_PERDA)
    ).all()

    return [MotivoPerda(motivo=motivo, quantidade=n) for motivo, n in linhas]


# --- Nota de performance ----------------------------------------------------
#
# Todas as consultas acima são agregações no banco; nenhuma materializa leads
# em Python. O carregamento da tela dispara 5 requisições, que somam ~12
# consultas — todas `COUNT`/`SUM`/`AVG`/`GROUP BY` sobre `leads`.
#
# **Nenhum índice novo foi criado, de propósito.** O volume contratado é 50
# leads/mês; mesmo alguns anos de operação deixam a tabela na casa de poucos
# milhares de linhas, onde o Postgres escolhe seq scan e está certo — um
# índice seria ignorado no plano e ainda custaria escrita em toda busca
# mensal. `kanban_status` já tem índice desde a Fase 8b (criado pelo Kanban,
# não por aqui), e é o que mais se beneficiaria.
#
# O gatilho pra revisitar isto é volume, não tempo: se `leads` passar da
# ordem de dezenas de milhares, medir com EXPLAIN ANALYZE antes de indexar —
# em especial `created_at` (usado por `leads_no_mes`) e uma expressão sobre
# `dados_nicho->>'decisor'`/`'whatsapp_ativo'`, que hoje não têm índice algum.
