import type { ScoreBreakdownItem } from "./api";

/**
 * Extrai a lista de critérios pontuados de `Lead.score_detalhes` de
 * forma defensiva.
 *
 * `score_detalhes` chega pela rede via `fetch().json()`, que o
 * TypeScript tipa como `any` -- o compilador não garante em runtime que
 * o valor bate com o shape declarado em `ScoreDetalhes`. Por isso essa
 * função trata a entrada como `unknown` e nunca lança: aceita o formato
 * canônico persistido por `compute_lead_score`
 * (`{ breakdown: [...] }`, ver app/workers/celery_app.py:871) e, como
 * fallback, um array "cru" sem o wrapper `breakdown` -- shape
 * inesperado vira lista vazia, tratada pela UI como "sem score
 * calculado ainda", nunca como erro.
 */
export function getScoreBreakdown(scoreDetalhes: unknown): ScoreBreakdownItem[] {
  if (Array.isArray(scoreDetalhes)) {
    return scoreDetalhes as ScoreBreakdownItem[];
  }
  if (
    scoreDetalhes &&
    typeof scoreDetalhes === "object" &&
    Array.isArray((scoreDetalhes as Record<string, unknown>).breakdown)
  ) {
    return (scoreDetalhes as { breakdown: ScoreBreakdownItem[] }).breakdown;
  }
  return [];
}
