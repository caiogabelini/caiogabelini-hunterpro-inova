import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Mail, MessageCircle, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchLeadsLista, UnauthorizedError, type Lead, type LeadListaResposta } from "../api";
import { PriorityBadge } from "../components/PriorityBadge";
import { useAuth } from "../context/AuthContext";
import { formatarDocumento } from "../documento";
import { formatDate } from "../format";
import { KANBAN_COLUMNS, STATUS_PERDIDO } from "../kanbanStatuses";
import "./ListaLeadsPage.css";

const POR_PAGINA = 25;
// Não dispara uma request por tecla digitada -- só busca depois que o
// usuário para de digitar por esse tanto de tempo.
const DEBOUNCE_BUSCA_MS = 400;

type Ordenacao = "score_total" | "created_at";

export function ListaLeadsPage() {
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [prioridade, setPrioridade] = useState("");
  const [kanbanStatus, setKanbanStatus] = useState("");
  const [ordenarPor, setOrdenarPor] = useState<Ordenacao>("score_total");
  const [ordem, setOrdem] = useState<"asc" | "desc">("desc");
  const [pagina, setPagina] = useState(1);

  const [dados, setDados] = useState<LeadListaResposta | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  // O reset de `pagina` pra 1 quando um filtro muda acontece dentro de
  // cada handler que muda um filtro (abaixo), não num efeito reativo
  // separado -- ajustar estado diretamente no evento que causou a
  // mudança, em vez de "reagir" a ela depois num efeito, é o padrão
  // recomendado pra esse caso (evita um re-render em cascata só pra
  // zerar uma página).
  useEffect(() => {
    const id = setTimeout(() => {
      setBusca(buscaInput.trim());
      setPagina(1);
    }, DEBOUNCE_BUSCA_MS);
    return () => clearTimeout(id);
  }, [buscaInput]);

  useEffect(() => {
    if (!token) return;
    setCarregando(true);
    setErro(null);
    fetchLeadsLista(token, {
      busca: busca || undefined,
      prioridade: prioridade || undefined,
      kanban_status: kanbanStatus || undefined,
      ordenar_por: ordenarPor,
      ordem,
      pagina,
      por_pagina: POR_PAGINA,
    })
      .then(setDados)
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => setCarregando(false));
  }, [token, logout, busca, prioridade, kanbanStatus, ordenarPor, ordem, pagina]);

  function alternarOrdenacao(coluna: Ordenacao) {
    if (ordenarPor === coluna) {
      setOrdem((atual) => (atual === "desc" ? "asc" : "desc"));
    } else {
      setOrdenarPor(coluna);
      setOrdem("desc");
    }
    setPagina(1);
  }

  function mudarPrioridade(valor: string) {
    setPrioridade(valor);
    setPagina(1);
  }

  function mudarKanbanStatus(valor: string) {
    setKanbanStatus(valor);
    setPagina(1);
  }

  const totalPaginas = dados ? Math.max(1, Math.ceil(dados.total / dados.por_pagina)) : 1;

  return (
    <div className="lista-leads-page">
      <header className="lista-leads-header">
        <div className="lista-leads-eyebrow">HunterPro</div>
        <h1 className="lista-leads-title">Lista de Leads</h1>
      </header>

      <div className="lista-leads-filtros">
        <div className="lista-leads-campo-busca">
          <Search size={16} />
          <input
            type="text"
            placeholder="Buscar por nome, CPF ou CNPJ..."
            value={buscaInput}
            onChange={(e) => setBuscaInput(e.target.value)}
          />
        </div>

        <select
          className="lista-leads-select"
          value={prioridade}
          onChange={(e) => mudarPrioridade(e.target.value)}
          aria-label="Filtrar por prioridade"
        >
          <option value="">Todas as prioridades</option>
          <option value="A">Prioridade A</option>
          <option value="B">Prioridade B</option>
          <option value="C">Prioridade C</option>
        </select>

        <select
          className="lista-leads-select"
          value={kanbanStatus}
          onChange={(e) => mudarKanbanStatus(e.target.value)}
          aria-label="Filtrar por etapa do Kanban"
        >
          <option value="">Todas as etapas</option>
          {KANBAN_COLUMNS.map((coluna) => (
            <option key={coluna.status} value={coluna.status}>
              {coluna.label}
            </option>
          ))}
        </select>
      </div>

      <div className="lista-leads-card">
        {carregando && <p className="lista-leads-status">Carregando...</p>}
        {erro && <p className="lista-leads-status lista-leads-erro">{erro}</p>}

        {!carregando && !erro && dados && dados.items.length === 0 && (
          <p className="lista-leads-muted">Nenhum lead encontrado com esses filtros.</p>
        )}

        {!carregando && !erro && dados && dados.items.length > 0 && (
          <>
            <div className="lista-leads-tabela-wrap">
              <table className="lista-leads-tabela">
                <thead>
                  <tr>
                    <th>Nome</th>
                    <th>CPF/CNPJ</th>
                    <th>Contato</th>
                    <th>Decisor</th>
                    <th>Município/UF</th>
                    <th>
                      <button
                        type="button"
                        className="lista-leads-th-ordenavel"
                        onClick={() => alternarOrdenacao("score_total")}
                      >
                        <span>Score</span>
                        <IconeOrdenacao ativo={ordenarPor === "score_total"} ordem={ordem} />
                      </button>
                    </th>
                    <th>Etapa</th>
                    <th>
                      <button
                        type="button"
                        className="lista-leads-th-ordenavel"
                        onClick={() => alternarOrdenacao("created_at")}
                      >
                        <span>Encontrado em</span>
                        <IconeOrdenacao ativo={ordenarPor === "created_at"} ordem={ordem} />
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {dados.items.map((lead) => (
                    <LinhaLead key={lead.id} lead={lead} onClick={() => navigate(`/leads/${lead.id}`)} />
                  ))}
                </tbody>
              </table>
            </div>

            <div className="lista-leads-paginacao">
              <span className="lista-leads-paginacao-info">
                {dados.total} lead{dados.total === 1 ? "" : "s"} · página {dados.pagina} de {totalPaginas}
              </span>
              <div className="lista-leads-paginacao-botoes">
                <button
                  type="button"
                  className="lista-leads-paginacao-btn"
                  onClick={() => setPagina((p) => Math.max(1, p - 1))}
                  disabled={pagina <= 1}
                  aria-label="Página anterior"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  type="button"
                  className="lista-leads-paginacao-btn"
                  onClick={() => setPagina((p) => Math.min(totalPaginas, p + 1))}
                  disabled={pagina >= totalPaginas}
                  aria-label="Próxima página"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function IconeOrdenacao({ ativo, ordem }: { ativo: boolean; ordem: "asc" | "desc" }) {
  if (!ativo) return <ArrowUpDown size={12} className="lista-leads-icone-ordenacao-inativo" />;
  return ordem === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />;
}

function LinhaLead({ lead, onClick }: { lead: Lead; onClick: () => void }) {
  const etapa = KANBAN_COLUMNS.find((c) => c.status === lead.kanban_status)?.label ?? lead.kanban_status;
  const perdido = lead.kanban_status === STATUS_PERDIDO;

  return (
    <tr className="lista-leads-linha" onClick={onClick}>
      <td>
        <div className="lista-leads-nome">{lead.nome}</div>
        {lead.nome_fantasia && <div className="lista-leads-nome-fantasia">{lead.nome_fantasia}</div>}
      </td>
      {/* CPF-aware — no Minotto era `{lead.cnpj}` cru. */}
      <td className="lista-leads-documento">
        {formatarDocumento(lead.documento, lead.tipo_documento)}
      </td>
      <td>
        {/* Só ícone de disponibilidade, sem mostrar telefone/e-mail em
            texto -- pedido explícito. "Confirmado" (verde) exige o sinal
            positivo real (whatsapp_ativo/email_validado), não só a
            presença do dado -- mesmo critério já usado em CampoWhatsapp/
            CampoEmail no dossiê. */}
        <div className="lista-leads-contato-icones">
          <MessageCircle
            size={15}
            className={lead.whatsapp_ativo ? "lista-leads-contato-on" : "lista-leads-contato-off"}
            aria-label={lead.whatsapp_ativo ? "WhatsApp confirmado" : "WhatsApp não confirmado"}
          />
          <Mail
            size={15}
            className={lead.email_validado ? "lista-leads-contato-on" : "lista-leads-contato-off"}
            aria-label={lead.email_validado ? "E-mail validado" : "E-mail não validado"}
          />
        </div>
      </td>
      <td>{lead.decisor_nome ?? "—"}</td>
      <td>{lead.municipio ? `${lead.municipio}/${lead.uf ?? "—"}` : "—"}</td>
      <td>
        <div className="lista-leads-score">
          <span className="lista-leads-score-valor">{lead.score ?? "—"}</span>
          <PriorityBadge prioridade={lead.prioridade} />
        </div>
      </td>
      <td>
        <span className={`lista-leads-etapa-badge ${perdido ? "lista-leads-etapa-badge-perdido" : ""}`}>{etapa}</span>
      </td>
      <td className="lista-leads-data">{formatDate(lead.created_at)}</td>
    </tr>
  );
}
