import { describe, expect, it } from "vitest";
import { getScoreBreakdown } from "./leadScore";

// Mesmo shape que compute_lead_score persiste em Lead.score_detalhes
// (ver app/workers/celery_app.py:871) -- um objeto {"breakdown": [...]},
// não um array cru nem um dict chaveado por critério.
const ITEM_PGFN = { key: "divida_ativa_pgfn", label: "Dívida ativa PGFN", weight: 20, layer: "estruturado" as const, points: 6.67 };
const ITEM_ZONA_FRANCA = {
  key: "zona_franca_alc",
  label: "Área de Livre Comércio / Zona Franca",
  weight: 20,
  layer: "estruturado" as const,
  points: 0,
};

describe("getScoreBreakdown", () => {
  // Regressão do bug reportado: um lead com score_detalhes preenchido
  // (9 critérios) mostrava "Score ainda não calculado" no dossiê -- este
  // teste é o que teria pegado isso, cobrindo exatamente o caso que
  // faltava (só havia cobertura pro caso SEM score_detalhes).
  it("retorna o breakdown quando score_detalhes vem preenchido no formato canônico", () => {
    const resultado = getScoreBreakdown({ breakdown: [ITEM_PGFN, ITEM_ZONA_FRANCA] });
    expect(resultado).toEqual([ITEM_PGFN, ITEM_ZONA_FRANCA]);
    expect(resultado.length).toBeGreaterThan(0);
  });

  it("retorna lista vazia quando score_detalhes é null ou undefined (lead ainda sem score)", () => {
    expect(getScoreBreakdown(null)).toEqual([]);
    expect(getScoreBreakdown(undefined)).toEqual([]);
  });

  it("tolera um array cru (sem o wrapper 'breakdown') como fallback defensivo", () => {
    expect(getScoreBreakdown([ITEM_PGFN])).toEqual([ITEM_PGFN]);
  });

  it("nunca lança pra um shape inesperado -- vira lista vazia em vez de quebrar a tela", () => {
    expect(getScoreBreakdown("json-invalido")).toEqual([]);
    expect(getScoreBreakdown({ breakdown: "não é um array" })).toEqual([]);
    expect(getScoreBreakdown({ algumOutroCampo: 1 })).toEqual([]);
  });
});
