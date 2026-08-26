import type { InsightsIA } from "./api";

/**
 * Extrai `Lead.insights_ia` de forma defensiva -- mesmo raciocínio de
 * `getScoreBreakdown` em leadScore.ts: o valor chega pela rede via
 * `fetch().json()` (tipado `any` pelo TypeScript, sem garantia em
 * runtime de que bate com `InsightsIA`). Trata a entrada como `unknown`
 * e nunca lança: um shape inesperado vira `null`, tratado pela UI como
 * "insights ainda não gerados" (mesmo estado vazio de antes de gerar),
 * nunca como erro. `recomendacao_abordagem` é normalizada pra sempre
 * ser um array de strings (mesmo se vier ausente/tipo errado).
 */
export function getInsights(insightsIa: unknown): InsightsIA | null {
  if (!insightsIa || typeof insightsIa !== "object" || Array.isArray(insightsIa)) return null;

  const dados = insightsIa as Record<string, unknown>;
  const recomendacao = Array.isArray(dados.recomendacao_abordagem)
    ? dados.recomendacao_abordagem.filter((r): r is string => typeof r === "string")
    : [];

  return {
    resumo_estrategico: typeof dados.resumo_estrategico === "string" ? dados.resumo_estrategico : "",
    potencial_oportunidade: typeof dados.potencial_oportunidade === "string" ? dados.potencial_oportunidade : "",
    recomendacao_abordagem: recomendacao,
    estrategia_comunicacao: typeof dados.estrategia_comunicacao === "string" ? dados.estrategia_comunicacao : "",
    cta_sugerido: typeof dados.cta_sugerido === "string" ? dados.cta_sugerido : "",
  };
}
