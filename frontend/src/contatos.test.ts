import { describe, expect, it } from "vitest";
import { getContatos, temAlgumCanal } from "./contatos";
import type { Lead } from "./api";

/**
 * Regressão do bug de 26/08/2026: a aba Contatos lia `lead.decisor_nome`
 * (fantasma do Minotto) e mostrava "nenhum decisor" para um lead que a aba
 * Dados exibia completo. Os testes abaixo usam o shape REAL da resposta,
 * conferido contra `GET /api/leads/05587700968` no banco local.
 */

function lead(extra: Partial<Lead> = {}): Lead {
  return {
    id: "11",
    documento: "05587700968",
    tipo_documento: "CPF",
    nome: "ALBERTO LEMUCH FILHO",
    created_at: "2026-08-26T16:24:50",
    updated_at: "2026-08-26T17:37:39",
    ...extra,
  } as Lead;
}

describe("getContatos", () => {
  it("lê o decisor do campo que a API realmente envia", () => {
    const c = getContatos(lead({ decisor: "ALBERTO LEMUCH FILHO" }));
    expect(c.decisor).toBe("ALBERTO LEMUCH FILHO");
  });

  it("IGNORA decisor_nome, o campo fantasma do Minotto", () => {
    // ⚠️ O coração do bug: se alguém voltar a ler `decisor_nome`, este teste
    // não deixa passar despercebido — o campo não é fonte de nada.
    const c = getContatos(lead({ decisor_nome: "NOME FANTASMA" } as Partial<Lead>));
    expect(c.decisor).toBeNull();
  });

  it("cai pro dados_nicho quando o campo de topo não veio", () => {
    const c = getContatos(lead({ dados_nicho: { decisor: "DO NICHO" } }));
    expect(c.decisor).toBe("DO NICHO");
  });

  it("campo de topo tem precedência sobre o dados_nicho", () => {
    const c = getContatos(lead({
      decisor: "DO TOPO",
      dados_nicho: { decisor: "DO NICHO" },
    }));
    expect(c.decisor).toBe("DO TOPO");
  });

  it("lead sem decisor devolve null, não string vazia", () => {
    expect(getContatos(lead()).decisor).toBeNull();
    expect(getContatos(lead({ decisor: "" })).decisor).toBeNull();
    expect(getContatos(lead({ decisor: "   " })).decisor).toBeNull();
  });

  it("fonte_decisor sai do dados_nicho — a API não desempacota essa", () => {
    const c = getContatos(lead({ dados_nicho: { fonte_decisor: "api_full" } }));
    expect(c.fonteDecisor).toBe("api_full");
  });

  it("whatsapp só é ativo quando confirmado", () => {
    expect(getContatos(lead({ whatsapp_ativo: true })).whatsappAtivo).toBe(true);
    expect(getContatos(lead({ whatsapp_ativo: false })).whatsappAtivo).toBe(false);
    // `undefined` = não medimos. Trata como "não tem", nunca como "tem".
    expect(getContatos(lead()).whatsappAtivo).toBe(false);
    expect(getContatos(lead({ whatsapp_ativo: null })).whatsappAtivo).toBe(false);
  });

  it("e-mail validado segue os status aprovados do ZeroBounce", () => {
    expect(getContatos(lead({ email_status: "valid" })).emailValidado).toBe(true);
    expect(getContatos(lead({ email_status: "catch-all" })).emailValidado).toBe(true);
    expect(getContatos(lead({ email_status: "invalid" })).emailValidado).toBe(false);
    expect(getContatos(lead()).emailValidado).toBe(false);
  });

  it("traz os dois telefones separados", () => {
    const c = getContatos(lead({
      telefone: "5542999640915",
      telefone_secundario: "+5542999770194",
    }));
    expect(c.telefone).toBe("5542999640915");
    expect(c.telefoneSecundario).toBe("+5542999770194");
  });

  it("o lead real do bug volta completo", () => {
    // Shape exato de GET /api/leads/05587700968, conferido no banco local.
    const c = getContatos(lead({
      decisor: "ALBERTO LEMUCH FILHO",
      telefone: "5542999640915",
      telefone_secundario: "+5542999770194",
      whatsapp_ativo: true,
      email: "beto1166@hotmail.com",
      email_status: "valid",
      dados_nicho: { fonte_decisor: "api_full" },
    }));
    expect(c).toEqual({
      decisor: "ALBERTO LEMUCH FILHO",
      fonteDecisor: "api_full",
      telefone: "5542999640915",
      telefoneSecundario: "+5542999770194",
      whatsappAtivo: true,
      email: "beto1166@hotmail.com",
      emailValidado: true,
    });
  });

  it("nunca lança com dados_nicho de shape estranho", () => {
    expect(() => getContatos(lead({ dados_nicho: "não é objeto" }))).not.toThrow();
    expect(() => getContatos(lead({ dados_nicho: null }))).not.toThrow();
    expect(getContatos(lead({ dados_nicho: [1, 2] })).decisor).toBeNull();
  });
});

describe("temAlgumCanal", () => {
  it("true com qualquer canal, inclusive só o alternativo", () => {
    expect(temAlgumCanal(getContatos(lead({ telefone: "45999990000" })))).toBe(true);
    expect(temAlgumCanal(getContatos(lead({ email: "a@b.com" })))).toBe(true);
    expect(temAlgumCanal(getContatos(lead({ telefone_secundario: "45999990000" })))).toBe(true);
  });

  it("false só quando não há nenhum", () => {
    expect(temAlgumCanal(getContatos(lead()))).toBe(false);
  });
});
