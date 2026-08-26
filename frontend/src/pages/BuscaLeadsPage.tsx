import { AlertTriangle, CheckCircle2, History, Loader2, PlayCircle, XCircle, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import {
  ConflictError,
  dispararBusca,
  fetchBusca,
  fetchBuscas,
  UnauthorizedError,
  type BuscaLeadsRegistro,
} from "../api";
import { Modal } from "../components/Modal";
import { useAuth } from "../context/AuthContext";
import "./BuscaLeadsPage.css";

// Intervalo de polling enquanto uma busca está "executando" -- não há
// progresso percentual granular pra mostrar (o backend não expõe
// "estamos no CNPJ 12 de 340"), então o valor aqui só precisa ser
// "poucos segundos", não algo cronometrado com precisão.
const INTERVALO_POLLING_MS = 4000;

const STATUS_LABELS: Record<string, string> = {
  executando: "Executando",
  concluido: "Concluído",
  erro: "Erro",
};

export function BuscaLeadsPage() {
  const { token, logout } = useAuth();

  const [historico, setHistorico] = useState<BuscaLeadsRegistro[]>([]);
  const [carregandoHistorico, setCarregandoHistorico] = useState(true);
  const [erroHistorico, setErroHistorico] = useState<string | null>(null);

  const [buscaId, setBuscaId] = useState<string | null>(null);
  const [buscaAtual, setBuscaAtual] = useState<BuscaLeadsRegistro | null>(null);

  const [disparando, setDisparando] = useState(false);
  const [erroDisparo, setErroDisparo] = useState<string | null>(null);
  // Mensagem informativa (não é um erro) pra quando o disparo esbarra
  // num 409 -- já existe uma busca em andamento, iniciada por este ou
  // outro admin. Fica num estado separado de `erroDisparo` de propósito:
  // visualmente não deve ler como falha, é só "alguém já apertou o
  // botão antes de você".
  const [mensagemInfoDisparo, setMensagemInfoDisparo] = useState<string | null>(null);
  const [modalConfirmacaoAberto, setModalConfirmacaoAberto] = useState(false);

  // Busca o histórico e identifica se a mais recente está "executando"
  // -- só busca/deriva o dado, não mexe em estado nenhum (quem chama
  // decide o que fazer com o resultado). Reaproveitada em dois lugares:
  // ao montar a página (ex.: admin recarregou no meio de uma busca) e
  // depois de um 409 no disparo (ex.: outro admin já tinha começado uma
  // busca) -- os dois lugares têm efeitos colaterais diferentes o
  // bastante (um mexe em `carregandoHistorico`, o outro não) pra não
  // valer a pena forçar os dois a chamar a mesma função com setState
  // embutido.
  async function buscarHistoricoEEmAndamento(tokenAtual: string) {
    const dados = await fetchBuscas(tokenAtual);
    const emAndamento = dados.find((b) => b.status === "executando") ?? null;
    return { dados, emAndamento };
  }

  useEffect(() => {
    if (!token) return;
    buscarHistoricoEEmAndamento(token)
      .then(({ dados, emAndamento }) => {
        setHistorico(dados);
        if (emAndamento) {
          setBuscaId(emAndamento.id);
          setBuscaAtual(emAndamento);
        }
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErroHistorico(String(e));
      })
      .finally(() => setCarregandoHistorico(false));
  }, [token, logout]);

  // Polling encadeado (setTimeout recursivo, não setInterval) -- só
  // agenda o próximo tick depois que o anterior já respondeu, pra nunca
  // empilhar requests se uma resposta demorar mais que o intervalo.
  // Para sozinho assim que `status` deixa de ser "executando".
  useEffect(() => {
    if (!buscaId || !token) return;

    let cancelado = false;
    let timeoutId: number;

    async function poll() {
      try {
        const atualizada = await fetchBusca(token!, buscaId!);
        if (cancelado) return;
        setBuscaAtual(atualizada);
        if (atualizada.status === "executando") {
          timeoutId = window.setTimeout(poll, INTERVALO_POLLING_MS);
        } else {
          setHistorico((atual) => [atualizada, ...atual.filter((b) => b.id !== atualizada.id)]);
          // limpa o aviso de "já existe uma busca em andamento" (se
          // veio de um 409) assim que essa busca termina -- não faz
          // sentido continuar mostrando depois que ela já concluiu.
          setMensagemInfoDisparo(null);
        }
      } catch (e) {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        // erro de rede no polling em si (não no backend) -- tenta de
        // novo no próximo ciclo em vez de desistir de acompanhar pra
        // sempre; o registro final ainda existe no backend mesmo que
        // uma requisição de polling isolada falhe.
        if (!cancelado) timeoutId = window.setTimeout(poll, INTERVALO_POLLING_MS);
      }
    }

    poll();
    return () => {
      cancelado = true;
      window.clearTimeout(timeoutId);
    };
  }, [buscaId, token, logout]);

  async function executarBusca() {
    if (!token) return;
    setModalConfirmacaoAberto(false);
    setDisparando(true);
    setErroDisparo(null);
    setMensagemInfoDisparo(null);
    try {
      const nova = await dispararBusca(token);
      setBuscaId(nova.id);
      setBuscaAtual(nova);
      setHistorico((atual) => [nova, ...atual]);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      if (e instanceof ConflictError) {
        // 409 -- já existe uma busca em andamento (deste ou de outro
        // admin). Mensagem amigável do próprio backend (já vem pronta
        // em e.message, ex.: "Já existe uma busca em andamento,
        // iniciada em 22/08/2026 09:15."), e passa a acompanhar essa
        // busca já em curso em vez de só mostrar um erro parado -- o
        // botão "Executar busca do mês" some sozinho porque `emExecucao`
        // vira true assim que buscaAtual for preenchida.
        setMensagemInfoDisparo(e.message);
        buscarHistoricoEEmAndamento(token)
          .then(({ dados, emAndamento }) => {
            setHistorico(dados);
            if (emAndamento) {
              setBuscaId(emAndamento.id);
              setBuscaAtual(emAndamento);
            }
          })
          .catch(() => {
            // se isso falhar, o usuário ainda vê a mensagem do 409 acima
            // (só não entra em modo de acompanhamento) -- não é crítico,
            // pode tentar de novo.
          });
        return;
      }
      setErroDisparo("Não foi possível iniciar a busca agora. Tente novamente.");
    } finally {
      setDisparando(false);
    }
  }

  const emExecucao = buscaAtual?.status === "executando";

  return (
    <div className="busca-leads-page">
      <header className="busca-leads-header">
        <div className="busca-leads-eyebrow">HunterPro</div>
        <h1 className="busca-leads-title">Busca de Leads</h1>
      </header>

      <section className="busca-leads-card busca-leads-disparo">
        {/* Aviso permanente, sempre visível -- não escondido atrás de um
            clique. A confirmação (modal, abaixo) é a segunda camada de
            cuidado, não a única -- pedido explícito: "seja cuidadoso e
            claro na UI de que clicar o botão realmente vai rodar o
            pipeline de verdade". */}
        <div className="busca-leads-aviso">
          <AlertTriangle size={18} />
          <p>
            Este painel dispara o pipeline de enriquecimento <strong>de verdade</strong> contra as APIs em produção
            (Receita Federal, PGFN, CNES, Google Places, Firecrawl + IA, Evolution/WhatsApp, Hunter.io/ZeroBounce)
            para cada CNPJ novo encontrado. <strong>Não é uma simulação</strong> — gera custo real e cria/atualiza
            leads de verdade no Kanban.
          </p>
        </div>

        {!emExecucao ? (
          <button
            type="button"
            className="busca-leads-btn-executar"
            onClick={() => setModalConfirmacaoAberto(true)}
            disabled={disparando}
            aria-busy={disparando}
          >
            {disparando ? (
              <Loader2 size={18} className="spin" aria-label="Iniciando..." />
            ) : (
              <>
                <PlayCircle size={18} />
                <span>Executar busca do mês</span>
              </>
            )}
          </button>
        ) : (
          <div className="busca-leads-progresso">
            <Loader2 size={22} className="spin" />
            <div>
              <p className="busca-leads-progresso-texto">Buscando leads...</p>
              <p className="busca-leads-progresso-nota">
                {buscaAtual.total_cnpjs_encontrados != null
                  ? `${buscaAtual.total_cnpjs_encontrados} CNPJs encontrados${
                      buscaAtual.total_cnpjs_selecionados != null
                        ? ` — ${buscaAtual.total_cnpjs_selecionados} selecionados, enriquecendo agora.`
                        : " — pré-selecionando os melhores candidatos."
                    }`
                  : "Levantando a lista de CNPJs a processar..."}{" "}
                Sem indicador de progresso percentual disponível — esta tela atualiza sozinha quando a busca
                terminar.
              </p>
            </div>
          </div>
        )}

        {mensagemInfoDisparo && <p className="busca-leads-info-disparo">{mensagemInfoDisparo}</p>}
        {erroDisparo && <p className="busca-leads-erro-disparo">{erroDisparo}</p>}

        {buscaAtual && !emExecucao && <ResumoBusca busca={buscaAtual} />}
      </section>

      <section className="busca-leads-card">
        <SectionHeading icon={History}>Histórico de buscas</SectionHeading>
        {carregandoHistorico && <p className="busca-leads-status">Carregando...</p>}
        {erroHistorico && <p className="busca-leads-status busca-leads-erro-disparo">{erroHistorico}</p>}
        {!carregandoHistorico && historico.length === 0 && (
          <p className="busca-leads-muted">Nenhuma busca disparada ainda.</p>
        )}
        {historico.length > 0 && (
          <ul className="busca-leads-historico-lista">
            {historico.map((busca) => (
              <ItemHistorico key={busca.id} busca={busca} />
            ))}
          </ul>
        )}
      </section>

      {modalConfirmacaoAberto && (
        <Modal titulo="Confirmar execução da busca" onClose={() => setModalConfirmacaoAberto(false)}>
          <p className="busca-leads-modal-texto">
            Isso vai rodar o pipeline completo de enriquecimento contra as APIs reais — custo real, não é
            simulação — para todo CNPJ novo encontrado na semente deste mês.
          </p>
          <div className="busca-leads-modal-acoes">
            <button type="button" className="busca-leads-modal-btn-cancelar" onClick={() => setModalConfirmacaoAberto(false)}>
              Cancelar
            </button>
            <button type="button" className="busca-leads-modal-btn-confirmar" onClick={executarBusca}>
              Sim, executar busca real
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// --- Resumo ao concluir --------------------------------------------------

function ResumoBusca({ busca }: { busca: BuscaLeadsRegistro }) {
  const sucesso = busca.status === "concluido";
  const Icon = sucesso ? CheckCircle2 : XCircle;
  const totalErros = busca.erros?.length ?? 0;

  return (
    <div className={`busca-leads-resumo ${sucesso ? "busca-leads-resumo-sucesso" : "busca-leads-resumo-erro"}`}>
      <Icon size={20} />
      <div>
        <p className="busca-leads-resumo-titulo">
          {sucesso ? "Busca concluída" : "Busca concluída com muitos erros"}
        </p>
        <p className="busca-leads-resumo-texto">
          {busca.total_cnpjs_encontrados ?? 0} CNPJs encontrados
          {busca.total_cnpjs_selecionados != null && ` · ${busca.total_cnpjs_selecionados} selecionados`} ·{" "}
          {busca.total_leads_processados ?? 0} processados
          com sucesso
          {totalErros > 0 && ` · ${totalErros} erro${totalErros === 1 ? "" : "s"}`}
        </p>
        {totalErros > 0 && (
          <details className="busca-leads-resumo-erros">
            <summary>Ver erros ({totalErros})</summary>
            <ul>
              {busca.erros!.map((erro, indice) => (
                <li key={indice}>{erro}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

// --- Histórico -------------------------------------------------------------

function ItemHistorico({ busca }: { busca: BuscaLeadsRegistro }) {
  const totalErros = busca.erros?.length ?? 0;
  return (
    <li className="busca-leads-historico-item">
      <div className="busca-leads-historico-topo">
        <span className="busca-leads-historico-data">{new Date(busca.iniciado_em).toLocaleString("pt-BR")}</span>
        <StatusBadge status={busca.status} />
      </div>
      <p className="busca-leads-historico-totais">
        {busca.total_cnpjs_encontrados ?? "—"} CNPJs
        {busca.total_cnpjs_selecionados != null && ` · ${busca.total_cnpjs_selecionados} selecionados`} ·{" "}
        {busca.total_leads_processados ?? "—"} processados
        {totalErros > 0 && ` · ${totalErros} erro${totalErros === 1 ? "" : "s"}`}
      </p>
    </li>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`busca-leads-status-badge busca-leads-status-badge-${status}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

// --- Helpers compartilhados -------------------------------------------------

function SectionHeading({ icon: Icon, children }: { icon: LucideIcon; children: string }) {
  return (
    <h2 className="busca-leads-section-heading">
      <Icon size={16} />
      <span>{children}</span>
    </h2>
  );
}
