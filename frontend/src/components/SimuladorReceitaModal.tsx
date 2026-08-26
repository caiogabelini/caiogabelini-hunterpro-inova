import { Loader2 } from "lucide-react";
import { useState } from "react";
import { salvarPremissas, UnauthorizedError, type DashboardPremissas } from "../api";
import { useAuth } from "../context/AuthContext";
import { formatCurrencyBRL } from "../format";
import { Modal } from "./Modal";

/**
 * Modal de edição das premissas do Simulador de Receita -- aberto pelo
 * botão "Simular cenário" no card de destaque da seção "Resumo
 * executivo" (`pages/DashboardPage.tsx`).
 *
 * Guarda um rascunho PRÓPRIO (`draft`), separado das premissas já
 * salvas que o card do topo mostra (`premissasAtuais`, só usado pra
 * inicializar o rascunho) -- de propósito: o pedido foi "o valor
 * calculado atualiza em tempo real [dentro do modal] conforme o
 * usuário digita" e "AO SALVAR... atualiza o card do topo", não "o
 * card do topo já reflete cada tecla digitada antes de salvar". Editar
 * e cancelar (fechar sem salvar) não deve mudar o que o card já mostra.
 *
 * Mesmo padrão "self-contained" já estabelecido em LeadDossierPage.tsx
 * (`CanalAbordagemCard`/`AbaInsights`) -- chama `useAuth()` direto e
 * dona do próprio `salvando`/`erro`, em vez de receber tudo como props
 * de um componente pai que faria a chamada à API.
 */
export function SimuladorReceitaModal({
  premissasAtuais,
  onClose,
  onSalvo,
}: {
  premissasAtuais: DashboardPremissas;
  onClose: () => void;
  onSalvo: (novasPremissas: DashboardPremissas) => void;
}) {
  const { token, logout } = useAuth();
  const [draft, setDraft] = useState<DashboardPremissas>(premissasAtuais);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  function atualizarCampo(campo: keyof DashboardPremissas, valor: string) {
    const numero = Number(valor);
    setDraft((atual) => ({ ...atual, [campo]: Number.isFinite(numero) ? numero : 0 }));
  }

  async function salvar() {
    if (!token) return;
    setSalvando(true);
    setErro(null);
    try {
      const salvo = await salvarPremissas(token, draft);
      onSalvo(salvo);
      onClose();
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro("Não foi possível salvar as premissas agora. Tente novamente.");
    } finally {
      setSalvando(false);
    }
  }

  const receitaEstimada = draft.leads_qualificados * (draft.taxa_fechamento / 100) * draft.ticket_medio;

  return (
    <Modal titulo="Simular cenário de receita" onClose={onClose}>
      <div className="dashboard-simulador-grid">
        <label className="dashboard-simulador-campo">
          <span>Leads qualificados/mês</span>
          <input
            type="number"
            min={0}
            step={1}
            value={draft.leads_qualificados}
            onChange={(e) => atualizarCampo("leads_qualificados", e.target.value)}
          />
        </label>
        <label className="dashboard-simulador-campo">
          <span>Taxa de fechamento (%)</span>
          <input
            type="number"
            min={0}
            max={100}
            step={0.1}
            value={draft.taxa_fechamento}
            onChange={(e) => atualizarCampo("taxa_fechamento", e.target.value)}
          />
        </label>
        <label className="dashboard-simulador-campo">
          <span>Ticket médio (R$)</span>
          <input
            type="number"
            min={0}
            step={50}
            value={draft.ticket_medio}
            onChange={(e) => atualizarCampo("ticket_medio", e.target.value)}
          />
        </label>
      </div>

      <div className="dashboard-simulador-resultado">
        <span className="dashboard-simulador-resultado-label">Receita estimada / mês</span>
        <span className="dashboard-simulador-resultado-valor">{formatCurrencyBRL(receitaEstimada)}</span>
      </div>

      <div className="dashboard-simulador-acoes">
        <button
          type="button"
          className="dashboard-simulador-btn"
          onClick={salvar}
          disabled={salvando}
          aria-busy={salvando}
        >
          {salvando ? <Loader2 size={15} className="spin" aria-label="Salvando..." /> : "Salvar premissas"}
        </button>
        {erro && <span className="dashboard-simulador-erro">{erro}</span>}
      </div>
    </Modal>
  );
}
