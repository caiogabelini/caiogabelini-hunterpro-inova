// Os 4 serviços fixos oferecidos como checkbox no FechamentoModal --
// mesmas chaves que o backend espera em Lead.servicos_vendidos (ver
// app/models/lead.py). Compartilhado entre FechamentoModal.tsx (que
// monta os checkboxes) e LeadDossierPage.tsx (que precisa traduzir a
// chave de volta pro label legível na seção "Fechamento") pra não
// duplicar a mesma lista em dois lugares.
export const SERVICOS_FECHAMENTO_FIXOS: { chave: string; label: string }[] = [
  { chave: "abertura_legalizacao", label: "Abertura e Legalização de Empresas" },
  { chave: "contabilidade_consultiva", label: "Contabilidade Consultiva" },
  { chave: "consultoria_empresarial", label: "Consultoria Empresarial" },
  { chave: "planejamento_tributario", label: "Planejamento Tributário" },
];

/** Traduz uma entrada de `servicos_vendidos` pro label legível -- texto
 * livre digitado em "Outro" não bate com nenhuma chave fixa e volta
 * como está (é o próprio texto que a pessoa escreveu, não uma chave). */
export function labelServicoFechamento(valor: string): string {
  return SERVICOS_FECHAMENTO_FIXOS.find((s) => s.chave === valor)?.label ?? valor;
}
