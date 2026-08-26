import { useState, type FormEvent } from "react";
import { Modal } from "./Modal";

/**
 * Modal de motivo da perda -- aberto quando um card é arrastado pra
 * coluna "Perdido" no Kanban. `motivo_perda` é obrigatório do lado do
 * backend nesse status (ver `PATCH /api/leads/{id}/status`), então o
 * modal bloqueia o avanço até o campo estar preenchido.
 *
 * Usa o `Modal` genérico (components/Modal.tsx), igual a
 * `FechamentoModal` e `SimuladorReceitaModal`. Antes era uma
 * implementação ad-hoc (overlay/card/título próprios, classes
 * `.modal-*` em pages/KanbanPage.css) -- migrado numa sessão de
 * limpeza técnica, sem mudança de comportamento.
 *
 * O `<form>` foi mantido de propósito ao migrar: o `required` do
 * textarea só dispara a validação nativa do navegador dentro de um
 * form com submit, e essa era a validação que já existia. A checagem
 * em JS (`!motivo.trim()`) continua como segunda linha, cobrindo o
 * caso de texto só com espaços, que o `required` aceitaria.
 */
export function LossReasonModal({
  razaoSocial,
  onConfirm,
  onCancel,
}: {
  razaoSocial: string;
  onConfirm: (motivo: string) => void;
  onCancel: () => void;
}) {
  const [motivo, setMotivo] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!motivo.trim()) return;
    onConfirm(motivo.trim());
  }

  return (
    <Modal titulo="Motivo da perda" onClose={onCancel}>
      <p className="kanban-modal-subtitulo">{razaoSocial}</p>
      <form onSubmit={handleSubmit}>
        <textarea
          className="perda-modal-textarea"
          value={motivo}
          onChange={(e) => setMotivo(e.target.value)}
          placeholder="Por que este lead foi perdido?"
          autoFocus
          required
        />
        <div className="kanban-modal-acoes">
          <button type="button" className="kanban-modal-btn-cancelar" onClick={onCancel}>
            Cancelar
          </button>
          <button type="submit" className="kanban-modal-btn-confirmar">
            Confirmar
          </button>
        </div>
      </form>
    </Modal>
  );
}
