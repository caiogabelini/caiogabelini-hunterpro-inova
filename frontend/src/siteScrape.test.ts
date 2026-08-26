import { describe, expect, it } from "vitest";

import { deveAvisarSiteNaoLido } from "./siteScrape";

describe("deveAvisarSiteNaoLido", () => {
  it("avisa quando o scrape falhou", () => {
    expect(deveAvisarSiteNaoLido(false)).toBe(true);
  });

  it("não avisa quando o site foi lido com sucesso", () => {
    expect(deveAvisarSiteNaoLido(true)).toBe(false);
  });

  it("não avisa quando a etapa nunca rodou (null/undefined)", () => {
    // O caso que um `!valor` ingênuo quebraria: lead sem site não tem
    // falha de leitura nenhuma pra reportar.
    expect(deveAvisarSiteNaoLido(null)).toBe(false);
    expect(deveAvisarSiteNaoLido(undefined)).toBe(false);
  });

  it.each([0, "", "false", NaN, [], {}])(
    "não avisa pra valor falsy/inesperado que não seja o boolean false (%p)",
    (valor) => {
      expect(deveAvisarSiteNaoLido(valor)).toBe(false);
    },
  );
});
