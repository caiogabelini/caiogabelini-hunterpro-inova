import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import "./Modal.css";

/**
 * Modal genérico reutilizável -- overlay escurecido, fecha ao clicar
 * fora (no backdrop) ou no X. Também fecha em Esc: não foi pedido
 * explicitamente, mas é a expectativa padrão de qualquer modal e o
 * custo de adicionar é um `useEffect` de poucas linhas -- baixo risco,
 * comportamento que todo usuário já espera.
 *
 * Nomes de classe prefixados (`hp-modal-*`) porque o build do Vite
 * concatena todo CSS importado num único arquivo -- nomes genéricos
 * como `.modal-card` colidiriam com qualquer outra folha de estilo da
 * aplicação. O prefixo era originalmente também pra não colidir com o
 * `LossReasonModal` ad-hoc, que definia `.modal-*` em
 * `pages/KanbanPage.css`; esse modal foi migrado pra cá desde então e
 * aquelas classes não existem mais, mas o prefixo continua valendo
 * como higiene de nome.
 *
 * Usado por `LossReasonModal`, `FechamentoModal` e
 * `SimuladorReceitaModal` -- é o único padrão de modal do projeto.
 */
export function Modal({
  titulo,
  onClose,
  children,
}: {
  titulo: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    function aoTeclar(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", aoTeclar);
    return () => document.removeEventListener("keydown", aoTeclar);
  }, [onClose]);

  return (
    <div className="hp-modal-backdrop" onClick={onClose}>
      <div className="hp-modal-card" role="dialog" aria-modal="true" aria-label={titulo} onClick={(e) => e.stopPropagation()}>
        <div className="hp-modal-header">
          <h2 className="hp-modal-titulo">{titulo}</h2>
          <button type="button" className="hp-modal-fechar" onClick={onClose} aria-label="Fechar">
            <X size={18} />
          </button>
        </div>
        <div className="hp-modal-corpo">{children}</div>
      </div>
    </div>
  );
}
