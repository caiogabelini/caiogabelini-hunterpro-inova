import { describe, expect, it } from "vitest";
import { formatarNumeroWhatsapp } from "./format";

// Mesmos casos de backend/tests/test_whatsapp.py::formatar_numero_whatsapp
// -- essa função é um espelho intencional daquela heurística (ver
// app/services/whatsapp.py), então os testes espelham os de lá também.
describe("formatarNumeroWhatsapp", () => {
  it("prefixa 55 num celular sem código do país (11 dígitos -> 13)", () => {
    expect(formatarNumeroWhatsapp("11987654321")).toBe("5511987654321");
  });

  it("prefixa 55 num fixo sem código do país (10 dígitos -> 12)", () => {
    expect(formatarNumeroWhatsapp("1133334444")).toBe("551133334444");
  });

  it("mantém igual um número que já tem código do país (13 dígitos)", () => {
    expect(formatarNumeroWhatsapp("5511987654321")).toBe("5511987654321");
  });

  it("remove pontuação antes de formatar", () => {
    expect(formatarNumeroWhatsapp("(11) 98765-4321")).toBe("5511987654321");
  });

  it("não confunde DDD 55 (Rio Grande do Sul) com código do país já presente", () => {
    // 11 dígitos começando com "55" (DDD 55 + celular) -- é NACIONAL,
    // tem que prefixar 55 de novo, não tratar como já tendo o código.
    expect(formatarNumeroWhatsapp("55991234567")).toBe("5555991234567");
  });

  it("retorna null pra número com poucos dígitos", () => {
    expect(formatarNumeroWhatsapp("1234")).toBeNull();
  });

  it("retorna null pra número vazio, nulo ou undefined", () => {
    expect(formatarNumeroWhatsapp("")).toBeNull();
    expect(formatarNumeroWhatsapp(null)).toBeNull();
    expect(formatarNumeroWhatsapp(undefined)).toBeNull();
  });

  it("retorna null pra número com dígitos demais", () => {
    expect(formatarNumeroWhatsapp("551198765432199999")).toBeNull();
  });
});
