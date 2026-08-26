import { describe, expect, it } from "vitest";
import { INSIGHTS_DISPONIVEIS, getInsights } from "./insights";

// Mesmo shape que gerar_insights_estrategicos devolve/persiste em
// Lead.insights_ia (ver app/services/ai_enrichment.py).
const INSIGHTS_COMPLETO = {
  resumo_estrategico: "Lead forte, com RQE confirmado.",
  potencial_oportunidade: "alto",
  recomendacao_abordagem: ["Mencionar o rating no Google", "Focar em WhatsApp"],
  estrategia_comunicacao: "Tom consultivo, direto ao ponto.",
  cta_sugerido: "Podemos conversar 15 minutos essa semana?",
};

describe("getInsights", () => {
  it("retorna os insights quando vêm preenchidos no formato canônico", () => {
    expect(getInsights(INSIGHTS_COMPLETO)).toEqual(INSIGHTS_COMPLETO);
  });

  it("retorna null quando insights_ia é null ou undefined (ainda não gerado)", () => {
    expect(getInsights(null)).toBeNull();
    expect(getInsights(undefined)).toBeNull();
  });

  it("nunca lança pra um shape inesperado -- vira null em vez de quebrar a tela", () => {
    expect(getInsights("json-invalido")).toBeNull();
    expect(getInsights([1, 2, 3])).toBeNull();
  });

  it("normaliza recomendacao_abordagem ausente ou de tipo errado pra lista vazia", () => {
    const resultado = getInsights({ resumo_estrategico: "Resumo", recomendacao_abordagem: "não é array" });
    expect(resultado?.recomendacao_abordagem).toEqual([]);
  });

  it("filtra itens não-string dentro de recomendacao_abordagem", () => {
    const resultado = getInsights({ recomendacao_abordagem: ["ok", 42, null, "outro ok"] });
    expect(resultado?.recomendacao_abordagem).toEqual(["ok", "outro ok"]);
  });

  it("preenche campos de texto ausentes/de tipo errado com string vazia", () => {
    const resultado = getInsights({});
    expect(resultado).toEqual({
      resumo_estrategico: "",
      potencial_oportunidade: "",
      recomendacao_abordagem: [],
      estrategia_comunicacao: "",
      cta_sugerido: "",
    });
  });
});

describe("INSIGHTS_DISPONIVEIS", () => {
  it("reflete o estado real do backend", () => {
    // Mesma documentação executável de MENSAGENS_DISPONIVEIS: trocar esta
    // flag sem a rota `POST /gerar-insights` existir devolve à tela um
    // botão que só sabe dar 404.
    expect(INSIGHTS_DISPONIVEIS).toBe(false);
  });
});
