"""Schemas do Dashboard.

⚠️ **Shapes conferidos campo a campo contra ``frontend/src/api.ts``**, que
consome estas 5 rotas desde a Fase 7 — não há liberdade de invenção aqui.
Campo novo exige mexer na tela junto.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    leads_no_mes: int
    leads_no_mes_limite: int
    score_medio: float
    leads_em_negociacao: int
    taxa_conversao: float
    #: ⚠️ **Sempre 0 nesta base.** Existe só porque o tipo do frontend o
    #: declara. Geração por IA (mensagem de abordagem, insights) ficou
    #: deliberadamente fora do porte na Fase 6 — não há model ``LeadMessage``
    #: nem coluna ``insights_gerado_em`` pra contar. Nenhuma tela renderiza
    #: este número hoje (conferido: só aparece no `api.ts`), então o zero não
    #: chega a mentir pra ninguém — mas se um dia for exibido, ou a IA entra
    #: ou o campo sai dos dois lados.
    total_geracoes_ia_mes: int = 0
    receita_fechada_pontual: float
    receita_fechada_recorrente_mensal: float
    #: ⚠️ Soma naturezas diferentes de receita: ``pontual`` é valor único,
    #: ``recorrente_mensal`` é por mês. É uma visão geral pro card, **não** é
    #: MRR nem receita anual. Os dois componentes vêm separados acima pra
    #: quem precisar da distinção de verdade.
    receita_fechada_total: float


class AcaoRecomendada(BaseModel):
    titulo: str
    quantidade: int
    #: Coluna única do Kanban pra filtrar quando a regra cabe numa só;
    #: ``None`` quando abrange mais de uma ou nenhuma.
    kanban_status_filtro: str | None = None
    #: Slug estável da regra. O frontend ainda não filtra de verdade ao
    #: navegar pro Kanban, mas a chave já vai pronta.
    filtro_chave: str


class DashboardPremissas(BaseModel):
    """Premissas do Simulador de Receita, por usuário.

    Validadas com limites porque vêm de input livre da tela: negativo ou
    percentual acima de 100 produziria uma "receita estimada" sem sentido, e
    o simulador é usado pra conversa comercial.
    """

    leads_qualificados: int = Field(ge=0)
    taxa_fechamento: float = Field(ge=0, le=100)
    ticket_medio: float = Field(ge=0)


class FunilEtapa(BaseModel):
    """Uma etapa do funil.

    ⚠️ ``percentual`` é uma **fotografia** da distribuição atual por coluna do
    Kanban, não taxa de conversão de coorte. ``Lead`` guarda só o status
    ATUAL — não existe tabela de histórico de transição nesta base, então não
    há como dizer "de quem passou por X, quantos chegaram em Y". Não adicione
    campo que implique progressão histórica sem antes construir esse
    histórico.
    """

    status: str
    label: str
    quantidade: int
    percentual: float


class MotivoPerda(BaseModel):
    """Agrupado por texto **exato** de ``motivo_perda``, sem normalização
    semântica: "Preço alto" e "Achou caro" contam como dois motivos."""

    motivo: str
    quantidade: int
