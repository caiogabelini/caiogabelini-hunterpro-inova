import { describe, expect, it } from "vitest";
import { criteriosExibiveis, getScoreBreakdown } from "./leadScore";

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

// Os 9 critérios reais desta base (app/scoring/rules.py) -- 7 com peso,
// 2 zerados de propósito. Fixture com os nomes/pesos de verdade, não
// inventados: se rules.py mudar, este teste vira a documentação do que
// a tela esperava mostrar.
const BREAKDOWN_INOVA = [
  { key: "tamanho_propriedade", label: "Tamanho da propriedade rural", weight: 30, layer: "estruturado" as const, points: 30 },
  { key: "decisor_identificavel", label: "Decisor identificável", weight: 20, layer: "estruturado" as const, points: 20 },
  { key: "semente_sicor_cultura", label: "Semente Sicor + cultura bate", weight: 15, layer: "estruturado" as const, points: 15 },
  { key: "whatsapp_ativo", label: "WhatsApp ativo", weight: 15, layer: "validacao" as const, points: 0 },
  { key: "valor_financiado", label: "Valor financiado (Sicor)", weight: 10, layer: "estruturado" as const, points: 2.5 },
  { key: "email_validado", label: "E-mail validado", weight: 5, layer: "validacao" as const, points: 5 },
  { key: "presenca_digital", label: "Presença digital (site/Instagram)", weight: 5, layer: "inferencia" as const, points: 0 },
  { key: "radar_exportacao", label: "Habilitação RADAR (exportação)", weight: 0, layer: "estruturado" as const, points: 0 },
  { key: "google_rating", label: "Boa nota no Google", weight: 0, layer: "estruturado" as const, points: 0 },
];

describe("criteriosExibiveis", () => {
  it("esconde os critérios de peso 0 e mantém os outros 7", () => {
    const visiveis = criteriosExibiveis(BREAKDOWN_INOVA);

    expect(visiveis).toHaveLength(7);
    expect(visiveis.map((c) => c.key)).toEqual([
      "tamanho_propriedade",
      "decisor_identificavel",
      "semente_sicor_cultura",
      "whatsapp_ativo",
      "valor_financiado",
      "email_validado",
      "presenca_digital",
    ]);
    // As 2 linhas que a cliente não deve ver na tela.
    expect(visiveis.map((c) => c.key)).not.toContain("radar_exportacao");
    expect(visiveis.map((c) => c.key)).not.toContain("google_rating");
  });

  it("mantém critérios que pontuaram 0 mas TÊM peso -- zerar pontos não é zerar peso", () => {
    // whatsapp_ativo e presenca_digital pontuaram 0 neste lead. Sumir com
    // eles esconderia justamente o que falta pro lead subir de score.
    const visiveis = criteriosExibiveis(BREAKDOWN_INOVA);
    expect(visiveis.map((c) => c.key)).toContain("whatsapp_ativo");
    expect(visiveis.map((c) => c.key)).toContain("presenca_digital");
  });

  it("não é filtro por nome de critério: qualquer peso 0 some, qualquer peso > 0 fica", () => {
    const inedito = { key: "criterio_novo", label: "Critério novo zerado", weight: 0, layer: "estruturado" as const, points: 0 };
    expect(criteriosExibiveis([...BREAKDOWN_INOVA, inedito])).toHaveLength(7);
  });

  it("preserva a ordem original e não muta a lista recebida", () => {
    const original = [...BREAKDOWN_INOVA];
    const visiveis = criteriosExibiveis(BREAKDOWN_INOVA);
    expect(BREAKDOWN_INOVA).toEqual(original);
    expect(visiveis[0].key).toBe("tamanho_propriedade");
    expect(visiveis[6].key).toBe("presenca_digital");
  });

  it("peso ausente ou não-numérico continua aparecendo -- payload estranho não apaga linha em silêncio", () => {
    const semPeso = { key: "sem_peso", label: "Sem peso", layer: "estruturado", points: 0 } as never;
    const pesoTexto = { key: "peso_texto", label: "Peso texto", weight: "abc", layer: "estruturado", points: 0 } as never;
    expect(criteriosExibiveis([semPeso, pesoTexto])).toHaveLength(2);
  });

  it("é encadeável com getScoreBreakdown -- o caminho real da tela", () => {
    const visiveis = criteriosExibiveis(getScoreBreakdown({ breakdown: BREAKDOWN_INOVA }));
    expect(visiveis).toHaveLength(7);
    expect(criteriosExibiveis(getScoreBreakdown(null))).toEqual([]);
  });
});
