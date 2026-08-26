import { describe, expect, it } from "vitest";

import { statusLimiteIa } from "./limitesIa";

const PAYLOAD = { email: 2, whatsapp: 0, insights: 1, limite: 2 };

describe("statusLimiteIa", () => {
  it("marca atingido quando as usadas alcançam o limite", () => {
    expect(statusLimiteIa(PAYLOAD, "email")).toEqual({ usadas: 2, limite: 2, atingido: true });
  });

  it("não marca atingido abaixo do limite", () => {
    expect(statusLimiteIa(PAYLOAD, "insights")).toEqual({ usadas: 1, limite: 2, atingido: false });
    expect(statusLimiteIa(PAYLOAD, "whatsapp")).toEqual({ usadas: 0, limite: 2, atingido: false });
  });

  it("marca atingido se ultrapassou o limite (limite reduzido depois)", () => {
    expect(statusLimiteIa({ email: 5, limite: 2 }, "email").atingido).toBe(true);
  });

  it("usa o limite que vem do backend, não um número fixo", () => {
    expect(statusLimiteIa({ email: 2, limite: 5 }, "email").atingido).toBe(false);
    expect(statusLimiteIa({ email: 2, limite: 1 }, "email").atingido).toBe(true);
  });

  it("limite <= 0 libera (espelha a config que desliga a checagem)", () => {
    expect(statusLimiteIa({ email: 99, limite: 0 }, "email").atingido).toBe(false);
    expect(statusLimiteIa({ email: 99, limite: -1 }, "email").atingido).toBe(false);
  });

  it.each([null, undefined, "texto", 42, []])(
    "shape inesperado (%p) libera em vez de travar a tela",
    (valor) => {
      // O backend é quem impede o gasto (429). Travar por não conseguir
      // ler um contador transformaria erro de exibição em produto quebrado.
      expect(statusLimiteIa(valor, "email")).toEqual({ usadas: 0, limite: null, atingido: false });
    },
  );

  it("tipo ausente no payload conta como zero usadas", () => {
    expect(statusLimiteIa({ limite: 2 }, "whatsapp")).toEqual({ usadas: 0, limite: 2, atingido: false });
  });

  it("valores de tipo errado não quebram", () => {
    expect(statusLimiteIa({ email: "dois", limite: "dois" }, "email")).toEqual({
      usadas: 0,
      limite: null,
      atingido: false,
    });
  });
});
