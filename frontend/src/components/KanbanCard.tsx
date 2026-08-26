import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { useNavigate } from "react-router-dom";
import type { Lead } from "../api";
import { PriorityBadge } from "./PriorityBadge";

export function KanbanCard({ lead }: { lead: Lead }) {
  const navigate = useNavigate();
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: lead.id,
  });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.4 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className="kanban-card"
      onClick={() => navigate(`/leads/${lead.id}`)}
      title="Ver dossiê do lead"
    >
      <div className="kanban-card-top">
        <PriorityBadge prioridade={lead.prioridade} />
        <span className="kanban-card-score">{lead.score ?? "—"}</span>
      </div>
      <div className="kanban-card-nome">{lead.nome}</div>
    </div>
  );
}
