import { useDroppable } from "@dnd-kit/core";
import type { Lead } from "../api";
import { KanbanCard } from "./KanbanCard";

interface Props {
  status: string;
  label: string;
  leads: Lead[];
}

export function KanbanColumn({ status, label, leads }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id: status });

  return (
    <div className={`kanban-column ${isOver ? "kanban-column-over" : ""}`}>
      <div className="kanban-column-header">
        <span>{label}</span>
        <span className="kanban-column-count">{leads.length}</span>
      </div>
      <div ref={setNodeRef} className="kanban-column-body">
        {leads.length === 0 ? (
          <div className="kanban-column-vazia">Nenhum lead aqui ainda</div>
        ) : (
          leads.map((lead) => <KanbanCard key={lead.id} lead={lead} />)
        )}
      </div>
    </div>
  );
}
