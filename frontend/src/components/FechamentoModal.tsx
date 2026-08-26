import { useState } from "react";
import type { DadosFechamento } from "../api";
import { SERVICOS_FECHAMENTO_FIXOS } from "../servicosFechamento";
import { Modal } from "./Modal";

/**
 * Modal de dados de fechamento -- aberto quando um card é arrastado pra
 * coluna "Ganho" no Kanban (mesmo gatilho estrutural de
 * `LossReasonModal` pra "Perdido", espelhado do lado positivo). Usa o
 * `Modal` genérico (ver components/Modal.tsx), assim como
 * `LossReasonModal` -- as classes de conteúdo comuns aos dois
 * (subtítulo e barra de ações) ficam no prefixo `kanban-modal-*` em
 * pages/KanbanPage.css; as específicas daqui, em `fechamento-modal-*`.
 */
export function FechamentoModal({
  razaoSocial,
  onConfirm,
  onCancel,
}: {
  razaoSocial: string;
  onConfirm: (dados: DadosFechamento) => void;
  onCancel: () => void;
}) {
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [outroMarcado, setOutroMarcado] = useState(false);
  const [outroTexto, setOutroTexto] = useState("");
  const [tipoContrato, setTipoContrato] = useState<"pontual" | "recorrente" | "">("");
  const [valor, setValor] = useState("");

  function alternarServico(chave: string) {
    setSelecionados((atual) => {
      const novo = new Set(atual);
      if (novo.has(chave)) {
        novo.delete(chave);
      } else {
        novo.add(chave);
      }
      return novo;
    });
  }

  const outroValido = outroMarcado && outroTexto.trim().length > 0;
  const temAoMenosUmServico = selecionados.size > 0 || outroValido;
  const valorNumero = Number(valor);
  const valorValido = valor !== "" && Number.isFinite(valorNumero) && valorNumero > 0;
  const podeConfirmar = temAoMenosUmServico && tipoContrato !== "" && valorValido;

  function confirmar() {
    if (!podeConfirmar) return;
    const servicosVendidos = [...selecionados];
    if (outroValido) servicosVendidos.push(outroTexto.trim());
    onConfirm({
      servicos_vendidos: servicosVendidos,
      tipo_contrato: tipoContrato as "pontual" | "recorrente",
      valor_fechamento: valorNumero,
    });
  }

  return (
    <Modal titulo="Detalhes do fechamento" onClose={onCancel}>
      <p className="kanban-modal-subtitulo">{razaoSocial}</p>

      <div className="fechamento-modal-secao">
        <span className="fechamento-modal-label">Serviços vendidos</span>
        <div className="fechamento-modal-checkboxes">
          {SERVICOS_FECHAMENTO_FIXOS.map((servico) => (
            <label key={servico.chave} className="fechamento-modal-checkbox">
              <input
                type="checkbox"
                checked={selecionados.has(servico.chave)}
                onChange={() => alternarServico(servico.chave)}
              />
              <span>{servico.label}</span>
            </label>
          ))}
          <label className="fechamento-modal-checkbox">
            <input type="checkbox" checked={outroMarcado} onChange={(e) => setOutroMarcado(e.target.checked)} />
            <span>Outro</span>
          </label>
        </div>
        {outroMarcado && (
          <input
            type="text"
            className="fechamento-modal-outro-input"
            placeholder="Descreva o serviço..."
            value={outroTexto}
            onChange={(e) => setOutroTexto(e.target.value)}
            autoFocus
          />
        )}
      </div>

      <div className="fechamento-modal-secao">
        <span className="fechamento-modal-label">Tipo de contrato</span>
        <div className="fechamento-modal-radios">
          <label className="fechamento-modal-radio">
            <input
              type="radio"
              name="fechamento-tipo-contrato"
              checked={tipoContrato === "pontual"}
              onChange={() => setTipoContrato("pontual")}
            />
            <span>Pontual</span>
          </label>
          <label className="fechamento-modal-radio">
            <input
              type="radio"
              name="fechamento-tipo-contrato"
              checked={tipoContrato === "recorrente"}
              onChange={() => setTipoContrato("recorrente")}
            />
            <span>Recorrente</span>
          </label>
        </div>
      </div>

      <div className="fechamento-modal-secao">
        <label className="fechamento-modal-label" htmlFor="fechamento-modal-valor">
          {tipoContrato === "recorrente" ? "Valor mensal (R$)" : "Valor único (R$)"}
        </label>
        <input
          id="fechamento-modal-valor"
          type="number"
          min={0}
          step={0.01}
          className="fechamento-modal-valor-input"
          value={valor}
          onChange={(e) => setValor(e.target.value)}
          placeholder="0,00"
        />
      </div>

      <div className="kanban-modal-acoes">
        <button type="button" className="kanban-modal-btn-cancelar" onClick={onCancel}>
          Cancelar
        </button>
        <button
          type="button"
          className="kanban-modal-btn-confirmar"
          onClick={confirmar}
          disabled={!podeConfirmar}
        >
          Confirmar
        </button>
      </div>
    </Modal>
  );
}
