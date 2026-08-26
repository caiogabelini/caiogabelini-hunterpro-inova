// Espelha o status de funil do Kanban.
//
// Espelha `app/models/lead.py::KanbanStatus` — os 9 valores foram conferidos
// um a um contra este arquivo na Fase 8b, e são idênticos aos do Minotto: o
// funil comercial é o mesmo, só o nicho dos leads muda.
//
// Ordem de funil, não alfabética. Valor novo aqui exige valor novo lá — e,
// como o backend tem CHECK no banco (`ck_leads_kanban_status_valido`),
// também exige migration.
export const KANBAN_COLUMNS: { status: string; label: string }[] = [
  { status: "novo_lead", label: "Novo Lead" },
  { status: "qualificacao", label: "Qualificação" },
  { status: "contatado", label: "Contatado" },
  { status: "respondeu", label: "Respondeu" },
  { status: "reuniao", label: "Reunião" },
  { status: "proposta_enviada", label: "Proposta Enviada" },
  { status: "negociacao", label: "Negociação" },
  { status: "ganho", label: "Ganho" },
  { status: "perdido", label: "Perdido" },
];

export const STATUS_PERDIDO = "perdido";
export const STATUS_GANHO = "ganho";
