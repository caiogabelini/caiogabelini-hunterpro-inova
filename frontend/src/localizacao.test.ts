import { describe, expect, it } from "vitest";
import { getLocalizacao } from "./localizacao";
import type { Lead } from "./api";

function lead(extra: Partial<Lead> = {}): Lead {
  return {
    id: "1", documento: "05587700968", tipo_documento: "CPF",
    nome: "ALBERTO LEMUCH FILHO",
    created_at: "2026-08-26T00:00:00", updated_at: "2026-08-26T00:00:00",
    ...extra,
  } as Lead;
}

describe("getLocalizacao", () => {
  it("um município só não ganha indicador — 91,5% dos casos", () => {
    const l = getLocalizacao(lead({ municipio: "Turvo", municipios: ["Turvo"], uf: "PR" }));
    expect(l.municipio).toBe("Turvo");
    expect(l.completo).toBe("Turvo/PR");
    expect(l.extras).toBe(0);
  });

  it("mais de um município vira '+N' — 8,5% dos casos", () => {
    const l = getLocalizacao(lead({
      municipio: "Douradina",
      municipios: ["Douradina", "Maria Helena"],
      uf: "PR",
    }));
    expect(l.municipio).toBe("Douradina (+1)");
    expect(l.completo).toBe("Douradina (+1)/PR");
    expect(l.extras).toBe(1);
  });

  it("três municípios contam dois extras", () => {
    const l = getLocalizacao(lead({
      municipio: "Douradina",
      municipios: ["Douradina", "Maria Helena", "Turvo"],
      uf: "PR",
    }));
    expect(l.municipio).toBe("Douradina (+2)");
  });

  it("o principal vem de `municipio`, não do primeiro da lista", () => {
    // A coluna é a fonte de verdade — a lista serve pro contador.
    const l = getLocalizacao(lead({
      municipio: "Turvo",
      municipios: ["Douradina", "Turvo"],
      uf: "PR",
    }));
    expect(l.municipio).toBe("Turvo (+1)");
  });

  it("sem lista, usa só o município — comportamento de antes do '+N'", () => {
    const l = getLocalizacao(lead({ municipio: "Cascavel", uf: "PR" }));
    expect(l.municipio).toBe("Cascavel");
    expect(l.extras).toBe(0);
  });

  it("sem município mas com UF, mostra a UF em vez de nada", () => {
    // ⚠️ O caso dos leads do Sicor antes da correção: a UF era conhecida e
    // a tela dizia "Localização não informada".
    const l = getLocalizacao(lead({ uf: "PR" }));
    expect(l.municipio).toBeNull();
    expect(l.completo).toBe("PR");
  });

  it("sem nada devolve null, e a tela mostra o texto de vazio", () => {
    const l = getLocalizacao(lead());
    expect(l.municipio).toBeNull();
    expect(l.completo).toBeNull();
  });

  it("município sem UF não inventa barra solta", () => {
    const l = getLocalizacao(lead({ municipio: "Turvo", municipios: ["Turvo"] }));
    expect(l.completo).toBe("Turvo");
  });

  it("nunca lança com payload de shape estranho", () => {
    expect(() => getLocalizacao(lead({ municipios: "não é array" as never }))).not.toThrow();
    expect(() => getLocalizacao(lead({ municipios: null as never }))).not.toThrow();
    expect(getLocalizacao(lead({ municipio: "Turvo", municipios: null as never })).municipio)
      .toBe("Turvo");
  });

  it("ignora entradas vazias na lista ao contar extras", () => {
    const l = getLocalizacao(lead({
      municipio: "Turvo", municipios: ["Turvo", ""], uf: "PR",
    }));
    expect(l.municipio).toBe("Turvo");
  });

  it("os 4 leads reais do banco", () => {
    const casos: [Partial<Lead>, string][] = [
      [{ municipio: "Turvo", municipios: ["Turvo"], uf: "PR" }, "Turvo/PR"],
      [{ municipio: "Assis Chateaubriand", municipios: ["Assis Chateaubriand"], uf: "PR" },
        "Assis Chateaubriand/PR"],
      [{ municipio: "Douradina", municipios: ["Douradina", "Maria Helena"], uf: "PR" },
        "Douradina (+1)/PR"],
      [{ municipio: "Turvo", municipios: ["Turvo"], uf: "PR" }, "Turvo/PR"],
    ];
    for (const [dados, esperado] of casos) {
      expect(getLocalizacao(lead(dados)).completo).toBe(esperado);
    }
  });
});
