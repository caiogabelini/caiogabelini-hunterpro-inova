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
    // Se alguém implementar a rota e esquecer de trocar a flag, este teste
    // não pega — mas se alguém trocar a flag SEM a rota existir, o dossiê
    // volta a fazer um 404 por abertura. O valor é documentação executável.
    expect(MENSAGENS_DISPONIVEIS).toBe(false);
  });
});
