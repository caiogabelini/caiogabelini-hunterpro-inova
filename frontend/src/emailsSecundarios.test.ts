import { describe, expect, it } from "vitest";

import { getEmailsSecundarios, labelTipoEmail } from "./emailsSecundarios";

describe("getEmailsSecundarios", () => {
  it("devolve os e-mails válidos no formato canônico do backend", () => {
    const resultado = getEmailsSecundarios([
      { email: "contato@x.com", tipo: "generic", confidence: 80 },
      { email: "maria@x.com", tipo: "personal", confidence: 95 },
    ]);
    expect(resultado).toEqual([
      { email: "contato@x.com", tipo: "generic", confidence: 80 },
      { email: "maria@x.com", tipo: "personal", confidence: 95 },
    ]);
  });

  it("preserva a ordem que o backend mandou (já vem ranqueada)", () => {
    const resultado = getEmailsSecundarios([
      { email: "b@x.com", tipo: "generic", confidence: 10 },
      { email: "a@x.com", tipo: "generic", confidence: 90 },
    ]);
    expect(resultado.map((e) => e.email)).toEqual(["b@x.com", "a@x.com"]);
  });

  it.each([null, undefined, {}, "texto", 42, { emails: [] }])(
    "devolve [] pra shape inesperado (%p) em vez de quebrar a página",
    (valor) => {
      expect(getEmailsSecundarios(valor)).toEqual([]);
    },
  );

  it("descarta itens sem email utilizável, mantendo os bons", () => {
    const resultado = getEmailsSecundarios([
      { tipo: "personal", confidence: 99 },
      { email: "", tipo: "generic", confidence: 50 },
      null,
      "texto",
      { email: "ok@x.com", tipo: "generic", confidence: 60 },
    ]);
    expect(resultado).toEqual([{ email: "ok@x.com", tipo: "generic", confidence: 60 }]);
  });

  it("normaliza tipo/confidence ausentes ou de tipo errado pra null", () => {
    expect(getEmailsSecundarios([{ email: "a@x.com" }])).toEqual([
      { email: "a@x.com", tipo: null, confidence: null },
    ]);
    expect(getEmailsSecundarios([{ email: "a@x.com", tipo: 7, confidence: "alta" }])).toEqual([
      { email: "a@x.com", tipo: null, confidence: null },
    ]);
  });
});

describe("labelTipoEmail", () => {
  it("traduz os dois tipos do Hunter", () => {
    expect(labelTipoEmail("personal")).toBe("pessoal");
    expect(labelTipoEmail("generic")).toBe("genérico");
  });

  it("mostra um tipo desconhecido como veio, sem inventar rótulo", () => {
    expect(labelTipoEmail("role")).toBe("role");
  });

  it("cai pra travessão quando não há tipo", () => {
    expect(labelTipoEmail(null)).toBe("—");
  });
});
