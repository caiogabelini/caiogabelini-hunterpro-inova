import type { InsightsIA } from "./api";

/**
 * A rota `POST /api/leads/{id}/gerar-insights` **existe no backend?**
 *
 * ⚠️ Hoje **não** — mesma origem de [[MENSAGENS_DISPONIVEIS]] em
 * mensagens.ts: geração por IA ficou deliberadamente fora do porte (Fase
 * 6). O `ai_enrichment.py` do Minotto tem `gerar_mensagem_abordagem` e
 * `gerar_insights_estrategicos`; nenhuma das duas foi trazida, nem o
 * controle de limite de geração por lead que segura o custo delas lá.
 * Não existe rota, nem coluna `insights_ia` no model Lead desta base.
 *
 * Diferença em relação a mensagens: aqui **não há carregamento** a
 * blindar. Insights nunca foram buscados por uma chamada própria — eles
 * vinham embutidos no Lead (`lead.insights_ia`), então nada dessa aba
 * participava do `Promise.all` que quebrou o dossiê. O risco que sobra é
 * só o botão "Gerar Insights com IA", que bateria numa rota inexistente
 * e daria o mesmo 404 — por isso a flag existe, mas gate só de UI.
 *
 * Quando a rota existir, trocar para `true` devolve a aba inteira: o
 * render dos insights e o fluxo de gerar/gerar-novamente continuam
 * escritos embaixo do gate, intactos.
 */
export const INSIGHTS_DISPONIVEIS = false;

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
