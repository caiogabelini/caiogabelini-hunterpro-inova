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
/**
 * Remove da EXIBIÇÃO os critérios de peso máximo 0.
 *
 * Hoje são 2: `radar_exportacao` e `google_rating`. Eles existem no
 * backend de propósito — documentam a decisão consciente da cliente de
 * descartar esses sinais, e apagá-los de SCORING_CRITERIA transformaria
 * "avaliado e descartado" em "nunca considerado". Na tela, porém, uma
 * linha "0/0 pts" sem esse contexto lê como ruído ou como critério
 * quebrado.
 *
 * ⚠️ Filtro **só de apresentação**. `calcular_score` continua devolvendo
 * os 9 critérios e a soma dos pesos continua sendo 100 (há `assert` no
 * import de rules.py). Nada aqui muda pontuação: peso 0 contribui 0.
 *
 * A regra é o peso, não a lista de chaves — um critério novo que entre
 * com peso 0, ou um que seja zerado depois, some da tela sozinho.
 */
export function criteriosExibiveis(itens: ScoreBreakdownItem[]): ScoreBreakdownItem[] {
  return itens.filter((item) => {
    const peso = typeof item?.weight === "number" ? item.weight : Number(item?.weight);
    // Esconde só com evidência positiva de peso zerado. Peso ausente,
    // NaN ou não-numérico CONTINUA aparecendo: sumir com um critério é a
    // ação excepcional aqui, e um payload estranho não deveria conseguir
    // apagar linha da tela em silêncio.
    return !(Number.isFinite(peso) && peso <= 0);
  });
}

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
