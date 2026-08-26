import {
  ArrowRight,
  DollarSign,
  FileSearch,
  Funnel,
  Gauge,
  Handshake,
  Landmark,
  ListChecks,
  Package,
  PartyPopper,
  Percent,
  SlidersHorizontal,
  Target,
  ThumbsDown,
  Trophy,
  UserCheck,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchAcoesRecomendadas,
  fetchDashboardSummary,
  fetchFunil,
  fetchMotivosPerda,
  fetchPremissas,
  UnauthorizedError,
  type AcaoRecomendada,
  type DashboardPremissas,
  type DashboardSummary,
  type FunilEtapa,
  type MotivoPerda,
} from "../api";
import { SimuladorReceitaModal } from "../components/SimuladorReceitaModal";
import { useAuth } from "../context/AuthContext";
import { formatCurrencyBRL } from "../format";
import "./DashboardPage.css";

/** Ícone por `filtro_chave` (ver app/schemas/dashboard.py::AcaoRecomendada)
 * -- uma entrada por categoria hoje implementada no backend, mais um
 * fallback genérico pra uma categoria nova que apareça sem entrada aqui
 * (nunca quebra por falta de ícone mapeado).
 *
 * ⚠️ As chaves aqui são as do Minotto -- este backend não tem rota de
 * dashboard nenhuma (Fase 8b), então nada deste mapa é exercitado hoje.
 * `divida_pgfn_nao_abordada` em particular não tem equivalente no agro;
 * ficou intocada porque é dado de mapeamento, não texto de tela. */
const ACAO_ICONES: Record<string, LucideIcon> = {
  decisor_identificado: UserCheck,
  prioridade_a: Target,
  revisao_manual: FileSearch,
  divida_pgfn_nao_abordada: Landmark,
};
const ACAO_ICONE_PADRAO = ListChecks;

const PREMISSAS_PADRAO: DashboardPremissas = { leads_qualificados: 0, taxa_fechamento: 20, ticket_medio: 1500 };

export function DashboardPage() {
  const { token, logout } = useAuth();

  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [acoes, setAcoes] = useState<AcaoRecomendada[]>([]);
  const [funil, setFunil] = useState<FunilEtapa[]>([]);
  const [motivosPerda, setMotivosPerda] = useState<MotivoPerda[]>([]);
  // Premissas JÁ SALVAS do simulador -- o que o card de destaque mostra.
  // Só muda quando SimuladorReceitaModal chama `onSalvo` depois de um
  // PUT bem-sucedido; editar dentro do modal não toca este estado (ver
  // docstring de SimuladorReceitaModal pro porquê).
  const [premissas, setPremissas] = useState<DashboardPremissas>(PREMISSAS_PADRAO);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [modalAberto, setModalAberto] = useState(false);

  useEffect(() => {
    if (!token) return;
    Promise.all([
      fetchDashboardSummary(token),
      fetchAcoesRecomendadas(token),
      fetchPremissas(token),
      fetchFunil(token),
      fetchMotivosPerda(token),
    ])
      .then(([summaryData, acoesData, premissasData, funilData, motivosPerdaData]) => {
        setSummary(summaryData);
        setAcoes(acoesData);
        setPremissas(premissasData);
        setFunil(funilData);
        setMotivosPerda(motivosPerdaData);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => setCarregando(false));
  }, [token, logout]);

  const receitaEstimada = premissas.leads_qualificados * (premissas.taxa_fechamento / 100) * premissas.ticket_medio;

  return (
    <div className="dashboard-page">
      <header className="dashboard-header">
        <div className="dashboard-eyebrow">HunterPro</div>
        <h1 className="dashboard-title">Dashboard</h1>
      </header>

      {carregando && <p className="dashboard-status">Carregando...</p>}
      {erro && <p className="dashboard-status dashboard-erro">{erro}</p>}

      {summary && (
        <>
          <SecaoResumoExecutivo
            summary={summary}
            premissas={premissas}
            receitaEstimada={receitaEstimada}
            onAbrirSimulador={() => setModalAberto(true)}
          />
          <SecaoConsumoPlano summary={summary} />
          <SecaoFunil funil={funil} />
          <SecaoMotivosPerda motivos={motivosPerda} />
          <SecaoAcoesRecomendadas acoes={acoes} />
        </>
      )}

      {modalAberto && (
        <SimuladorReceitaModal
          premissasAtuais={premissas}
          onClose={() => setModalAberto(false)}
          onSalvo={setPremissas}
        />
      )}
    </div>
  );
}

// --- Resumo Executivo ----------------------------------------------------

function SecaoResumoExecutivo({
  summary,
  premissas,
  receitaEstimada,
  onAbrirSimulador,
}: {
  summary: DashboardSummary;
  premissas: DashboardPremissas;
  receitaEstimada: number;
  onAbrirSimulador: () => void;
}) {
  return (
    <section className="dashboard-section">
      <SectionHeading icon={Gauge}>Resumo executivo</SectionHeading>
      <div className="dashboard-kpi-grid">
        <KpiCard icon={Gauge} label="Score médio" valor={summary.score_medio} sufixo="/100" />
        <KpiCard icon={Handshake} label="Leads em negociação" valor={summary.leads_em_negociacao} />
        <KpiCard icon={Percent} label="Taxa de conversão" valor={summary.taxa_conversao} sufixo="%" />
        <CardReceitaEstimada premissas={premissas} receitaEstimada={receitaEstimada} onAbrirSimulador={onAbrirSimulador} />
        <CardReceitaFechada summary={summary} />
      </div>
    </section>
  );
}

function KpiCard({ icon: Icon, label, valor, sufixo }: { icon: LucideIcon; label: string; valor: number; sufixo?: string }) {
  return (
    <div className="dashboard-kpi-card">
      <div className="dashboard-kpi-icone">
        <Icon size={20} />
      </div>
      <div className="dashboard-kpi-valor">
        {valor}
        {sufixo && <span className="dashboard-kpi-sufixo">{sufixo}</span>}
      </div>
      <div className="dashboard-kpi-label">{label}</div>
    </div>
  );
}

/**
 * 4º card do Resumo Executivo -- visualmente diferenciado dos outros 3
 * (pedido explícito: "destaque visual diferente... pra chamar atenção
 * como 'esse é especial'"), fundo em tom suave de --color-orange em vez
 * do branco/navy-accent dos outros três. Mostra o mesmo cálculo que já
 * existia na antiga seção "Simulador de Receita" (agora só editável via
 * modal, ver SimuladorReceitaModal.tsx), com as premissas atuais como
 * texto pequeno de contexto embaixo do valor.
 */
function CardReceitaEstimada({
  premissas,
  receitaEstimada,
  onAbrirSimulador,
}: {
  premissas: DashboardPremissas;
  receitaEstimada: number;
  onAbrirSimulador: () => void;
}) {
  return (
    <div className="dashboard-kpi-card dashboard-kpi-card-destaque">
      <div className="dashboard-kpi-icone">
        <DollarSign size={20} />
      </div>
      <div className="dashboard-kpi-valor">{formatCurrencyBRL(receitaEstimada)}</div>
      <div className="dashboard-kpi-label">Receita Estimada do Pipeline</div>
      <p className="dashboard-kpi-premissas">
        {premissas.leads_qualificados} leads · {premissas.taxa_fechamento}% conversão ·{" "}
        {formatCurrencyBRL(premissas.ticket_medio)} ticket médio
      </p>
      <button type="button" className="dashboard-kpi-simular-btn" onClick={onAbrirSimulador}>
        <SlidersHorizontal size={13} />
        <span>Simular cenário</span>
      </button>
    </div>
  );
}

/**
 * 5º card do Resumo Executivo -- receita REAL já fechada (leads "ganho"
 * com valor_fechamento), distinta da 4ª (CardReceitaEstimada, que é
 * projeção do Simulador). Destaque em VERDE (--color-success), não
 * dourado -- pedido explícito pra diferenciar visualmente "projeção" de
 * "fato consumado". A linha pequena embaixo só lista as partes (pontual/
 * recorrente) que forem > 0 -- ex.: um cliente só-pontual não mostra
 * "R$ 0/mês recorrente" pendurado ali.
 */
function CardReceitaFechada({ summary }: { summary: DashboardSummary }) {
  const partes: string[] = [];
  if (summary.receita_fechada_pontual > 0) {
    partes.push(`${formatCurrencyBRL(summary.receita_fechada_pontual)} pontual`);
  }
  if (summary.receita_fechada_recorrente_mensal > 0) {
    partes.push(`${formatCurrencyBRL(summary.receita_fechada_recorrente_mensal)}/mês recorrente`);
  }

  return (
    <div className="dashboard-kpi-card dashboard-kpi-card-destaque-verde">
      <div className="dashboard-kpi-icone">
        <Trophy size={20} />
      </div>
      <div className="dashboard-kpi-valor">{formatCurrencyBRL(summary.receita_fechada_total)}</div>
      <div className="dashboard-kpi-label">Receita Fechada</div>
      {partes.length > 0 && <p className="dashboard-kpi-premissas">{partes.join(" · ")}</p>}
    </div>
  );
}

// --- Consumo do Plano ------------------------------------------------------
// Barra de progresso: preenchimento normal em --color-primary; a partir
// de 90% do limite, vira --color-alert (laranja) -- pedido explícito
// oferecia "laranja/vermelho" como exemplos de UMA cor de alerta, não
// dois patamares distintos; laranja foi o escolhido, deixando o
// vermelho (--color-danger) reservado só pra estados de erro/perigo já
// estabelecidos no resto do app (ex.: PGFN ajuizado). Largura da barra
// nunca passa de 100% visualmente mesmo se o consumo ultrapassar o
// limite (`Math.min`), mas o número/texto ao lado sempre mostra a
// contagem real.

function SecaoConsumoPlano({ summary }: { summary: DashboardSummary }) {
  const percentual = summary.leads_no_mes_limite > 0 ? (summary.leads_no_mes / summary.leads_no_mes_limite) * 100 : 0;
  const emAlerta = percentual >= 90;

  return (
    <section className="dashboard-section">
      <SectionHeading icon={Package}>Consumo do plano</SectionHeading>
      <div className="dashboard-card dashboard-plano-card">
        <div className="dashboard-plano-topo">
          <span className="dashboard-plano-contagem">
            {summary.leads_no_mes} / {summary.leads_no_mes_limite} leads este mês
          </span>
          <span className={`dashboard-plano-percentual ${emAlerta ? "dashboard-plano-percentual-alerta" : ""}`}>
            {Math.round(percentual)}%
          </span>
        </div>
        <div className="dashboard-progress-track">
          <div
            className={`dashboard-progress-fill ${emAlerta ? "dashboard-progress-fill-alerta" : ""}`}
            style={{ width: `${Math.min(percentual, 100)}%` }}
          />
        </div>
        {emAlerta && (
          <p className="dashboard-plano-aviso">
            {percentual >= 100
              ? "Limite do plano atingido este mês."
              : "Perto de atingir o limite do plano este mês."}
          </p>
        )}
      </div>
    </section>
  );
}

// --- Funil de Conversão ------------------------------------------------------
// Barras horizontais, largura proporcional ao `percentual` de cada etapa
// (0-100% sobre o TOTAL de leads, "perdido" incluído no denominador
// desde 21/08/2026 -- ver a mesma ressalva abaixo do funil e o
// docstring de get_funil no backend). Preenchimento em --color-primary
// pra etapas de progresso, --color-success só pra "ganho" (judgment
// call já existente, verde = sucesso) e --color-danger pra "perdido"
// (mesmo vermelho já usado na seção "Motivos de perda" logo abaixo,
// consistência entre as duas seções da mesma página) -- pedido
// explícito: "perdido" precisa ficar visualmente distinto de navy/verde
// pra ficar claro que é uma SAÍDA do funil, não mais uma etapa de
// progresso, mesmo aparecendo na mesma lista de 9.

function _classeBarraFunil(status: string): string {
  if (status === "ganho") return "dashboard-funil-barra-fill-ganho";
  if (status === "perdido") return "dashboard-funil-barra-fill-perdido";
  return "";
}

function SecaoFunil({ funil }: { funil: FunilEtapa[] }) {
  return (
    <section className="dashboard-section">
      <SectionHeading icon={Funnel}>Funil de conversão</SectionHeading>
      <div className="dashboard-card">
        <div className="dashboard-funil-lista">
          {funil.map((etapa) => (
            <div key={etapa.status} className="dashboard-funil-etapa">
              <span className="dashboard-funil-label">{etapa.label}</span>
              <div className="dashboard-funil-barra-track">
                <div
                  className={`dashboard-funil-barra-fill ${_classeBarraFunil(etapa.status)}`}
                  style={{ width: `${etapa.percentual}%` }}
                />
              </div>
              <span className="dashboard-funil-numeros">
                {etapa.quantidade} · {etapa.percentual}%
              </span>
            </div>
          ))}
        </div>
        <p className="dashboard-funil-nota">
          Esta é a distribuição atual dos leads por etapa, não uma taxa de conversão histórica -- o sistema ainda não
          guarda um histórico de quando cada lead mudou de etapa, só o status de agora. "Perdido" entra no total
          (denominador do percentual de cada barra) pra bater com a contagem real de leads, mas continua sendo uma
          saída do funil, não uma etapa de progresso -- por isso a cor diferente.
        </p>
      </div>
    </section>
  );
}

// --- Motivos de Perda ------------------------------------------------------
// Mesmo padrão visual de barra horizontal do funil, mas em --color-danger
// (perda = semântica de "negativo", mesma cor já usada em outros lugares
// do app) e a largura é relativa ao motivo MAIS frequente da própria
// lista (não a um total geral) -- não faz sentido comparar contra o
// total de leads perdidos aqui, o que importa é o ranking relativo entre
// os motivos.

function SecaoMotivosPerda({ motivos }: { motivos: MotivoPerda[] }) {
  const maiorQuantidade = Math.max(1, ...motivos.map((m) => m.quantidade));

  return (
    <section className="dashboard-section">
      <SectionHeading icon={ThumbsDown}>Motivos de perda</SectionHeading>
      {motivos.length === 0 ? (
        <div className="dashboard-card dashboard-perda-vazio">
          <PartyPopper size={20} />
          <p className="dashboard-perda-vazio-texto">Nenhum lead perdido registrado ainda — ótimo sinal!</p>
        </div>
      ) : (
        <div className="dashboard-card">
          <div className="dashboard-perda-lista">
            {motivos.map((item, indice) => (
              <div key={item.motivo} className="dashboard-perda-item">
                <span className="dashboard-perda-posicao">{indice + 1}</span>
                <div className="dashboard-perda-corpo">
                  <div className="dashboard-perda-topo">
                    <span className="dashboard-perda-motivo">{item.motivo}</span>
                    <span className="dashboard-perda-quantidade">{item.quantidade}</span>
                  </div>
                  <div className="dashboard-perda-barra-track">
                    <div
                      className="dashboard-perda-barra-fill"
                      style={{ width: `${(item.quantidade / maiorQuantidade) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// --- Ações Recomendadas ----------------------------------------------------
// O link "Ver leads →" navega pro Kanban (rota "/", não existe uma rota
// "/kanban" separada nesta app -- ver App.tsx) SEM aplicar filtro real
// ainda -- simplificação aceita explicitamente pra esta fase.
// `kanban_status_filtro`/`filtro_chave` já vêm da API prontos pra quando
// uma versão futura ligar o link a um filtro de verdade no Kanban (ver
// README "O que falta").

function SecaoAcoesRecomendadas({ acoes }: { acoes: AcaoRecomendada[] }) {
  return (
    <section className="dashboard-section">
      <SectionHeading icon={ListChecks}>Ações recomendadas</SectionHeading>
      {acoes.length === 0 ? (
        <div className="dashboard-card">
          <p className="dashboard-muted">Nenhuma ação recomendada no momento -- tudo em dia por aqui.</p>
        </div>
      ) : (
        <div className="dashboard-acoes-lista">
          {acoes.map((acao) => {
            const Icon = ACAO_ICONES[acao.filtro_chave] ?? ACAO_ICONE_PADRAO;
            return (
              <div key={acao.filtro_chave} className="dashboard-acao-card">
                <div className="dashboard-acao-icone">
                  <Icon size={18} />
                </div>
                <div className="dashboard-acao-corpo">
                  <div className="dashboard-acao-titulo">{acao.titulo}</div>
                  <div className="dashboard-acao-quantidade">
                    {acao.quantidade} lead{acao.quantidade === 1 ? "" : "s"}
                  </div>
                </div>
                <Link to="/" className="dashboard-acao-link">
                  <span>Ver leads</span>
                  <ArrowRight size={14} />
                </Link>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// --- Helpers compartilhados -------------------------------------------------

function SectionHeading({ icon: Icon, children }: { icon: LucideIcon; children: string }) {
  return (
    <h2 className="dashboard-section-heading">
      <Icon size={16} />
      <span>{children}</span>
    </h2>
  );
}
