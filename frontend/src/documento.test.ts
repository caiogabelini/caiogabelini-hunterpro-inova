import { describe, expect, it } from "vitest";
import {
  apenasDigitos,
  formatarDocumento,
  rotuloDocumento,
  rotuloEntidade,
  rotuloNome,
  tipoDoDocumento,
} from "./documento";

describe("tipoDoDocumento", () => {
  it("deduz pelo comprimento", () => {
    expect(tipoDoDocumento("52998224725")).toBe("CPF");
    expect(tipoDoDocumento("11222333000181")).toBe("CNPJ");
  });
  it("tolera máscara", () => {
    expect(tipoDoDocumento("529.982.247-25")).toBe("CPF");
    expect(tipoDoDocumento("11.222.333/0001-81")).toBe("CNPJ");
  });
  it("comprimento inesperado vira null", () => {
    for (const v of ["", "123", "1234567890123", null, undefined]) {
      expect(tipoDoDocumento(v)).toBeNull();
    }
  });
});

describe("formatarDocumento", () => {
  it("máscara de CPF tem 11 dígitos", () => {
    expect(formatarDocumento("52998224725")).toBe("529.982.247-25");
  });
  it("máscara de CNPJ tem 14 dígitos", () => {
    expect(formatarDocumento("11222333000181")).toBe("11.222.333/0001-81");
  });
  it("respeita o tipo informado pelo backend", () => {
    expect(formatarDocumento("52998224725", "CPF")).toBe("529.982.247-25");
  });
  it("comprimento inesperado volta sem máscara, não inventa", () => {
    expect(formatarDocumento("123")).toBe("123");
  });
  it("vazio vira string vazia", () => {
    expect(formatarDocumento(null)).toBe("");
    expect(formatarDocumento(undefined)).toBe("");
  });
  it("zero à esquerda é preservado", () => {
    expect(formatarDocumento("00521073960")).toBe("005.210.739-60");
  });
});

describe("rótulos CPF-aware", () => {
  it("pessoa física é Produtor, não Empresa", () => {
    expect(rotuloEntidade("CPF")).toBe("Produtor");
    expect(rotuloNome("CPF")).toBe("Nome do produtor");
    expect(rotuloDocumento("CPF")).toBe("CPF");
  });
  it("pessoa jurídica mantém o vocabulário do Minotto", () => {
    expect(rotuloEntidade("CNPJ")).toBe("Empresa");
    expect(rotuloNome("CNPJ")).toBe("Razão social");
    expect(rotuloDocumento("CNPJ")).toBe("CNPJ");
  });
  it("tipo desconhecido cai em Produtor (o caso majoritário da Inova)", () => {
    expect(rotuloEntidade(null)).toBe("Produtor");
  });
});

describe("apenasDigitos", () => {
  it("remove máscara", () => {
    expect(apenasDigitos("529.982.247-25")).toBe("52998224725");
  });
});
