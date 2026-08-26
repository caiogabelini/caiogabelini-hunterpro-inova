import { describe, expect, it, vi } from "vitest";
import { MENSAGENS_DISPONIVEIS, carregarMensagens } from "./mensagens";

const UMA = [{ id: "1", lead_id: "9", canal: "email", conteudo: "oi", gerado_em: "x" }] as never;

describe("carregarMensagens", () => {
  it("não bate na rede enquanto a rota não existe", async () => {
    const buscar = vi.fn();
    expect(await carregarMensagens(buscar, { disponivel: false })).toEqual([]);
    expect(buscar).not.toHaveBeenCalled();
  });

  it("quando disponível, devolve o que veio", async () => {
    expect(await carregarMensagens(async () => UMA, { disponivel: true })).toEqual(UMA);
  });

  it("404 vira lista vazia, não exceção", async () => {
    const erro404 = async () => {
      throw new Error("Not Found");
    };
    await expect(
      carregarMensagens(erro404, { disponivel: true }),
    ).resolves.toEqual([]);
  });

  it("qualquer erro degrada — rede caindo, JSON inválido, o que for", async () => {
    for (const falha of [
      async () => {
        throw new TypeError("Failed to fetch");
      },
      async () => {
        throw new SyntaxError("Unexpected token");
      },
    ]) {
      await expect(carregarMensagens(falha, { disponivel: true })).resolves.toEqual([]);
    }
  });

  it("resposta que não é array vira lista vazia", async () => {
    for (const bruto of [null, undefined, {}, "texto", 42]) {
      await expect(
        carregarMensagens(async () => bruto as never, { disponivel: true }),
      ).resolves.toEqual([]);
    }
  });

  it("o padrão do módulo reflete o estado real do backend", () => {
    // Documentação executável: a rota `GET /api/leads/{id}/mensagens` passou
    // a existir na Fase 10. Se ela for desligada, esta flag é o único ponto
    // a mexer — e este teste é quem lembra disso.
    expect(MENSAGENS_DISPONIVEIS).toBe(true);
  });
});
