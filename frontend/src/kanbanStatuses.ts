// Espelha o status de funil do Kanban.
//
// ⚠️ No Minotto isto espelha `app/models/lead.py::KanbanStatus`. **Esse enum
// não existe no backend da Inova** — o `Lead` da Fase 1 não tem coluna de
// status de Kanban. A lista abaixo é o porte fiel do funil do Minotto e
// define o contrato que o backend precisa passar a expor; enquanto a coluna
// não existir, a tela de Kanban não tem o que ler.
//
// Ordem de funil, não alfabética. Valor novo aqui exige valor novo lá.
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
