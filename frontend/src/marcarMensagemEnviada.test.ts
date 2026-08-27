import { afterEach, describe, expect, it, vi } from "vitest";
import { SequenciaOrdemError, UnauthorizedError, marcarMensagemEnviada } from "./api";

/**
 * `PATCH /api/leads/{id}/mensagens/{msgId}/enviada` visto do frontend.
 *
 * ⚠️ O que se protege aqui é a **legibilidade do 422**. O backend recusa
 * pular etapa com um texto que explica o caso concreto ("a próxima mensagem
 * pendente é a 1 de 3"); se este módulo engolisse isso num erro genérico, a
 * Carolina veria "não foi possível" sem saber o que fazer — e a regra de
 * ordem passaria a ter duas redações, uma delas pior.
 */

function respostaFalsa(status: number, corpo: unknown) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => corpo,
  })) as unknown as typeof fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const SEQUENCIA = {
  grupo_id: "g1",
  canal: "whatsapp",
  gerado_em: "2026-08-27T22:00:00",
  total: 3,
  proxima_ordem: 2,
  mensagens: [],
};

describe("marcarMensagemEnviada", () => {
  it("usa PATCH e devolve a sequência já atualizada", async () => {
    const fetchFalso = respostaFalsa(200, SEQUENCIA);
    vi.stubGlobal("fetch", fetchFalso);

    const sequencia = await marcarMensagemEnviada("tok", "1", "m1");

    expect(sequencia.proxima_ordem).toBe(2);
    const [url, opcoes] = (fetchFalso as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(url).toContain("/api/leads/1/mensagens/m1/enviada");
    expect(opcoes.method).toBe("PATCH");
  });

  it("⚠️ 422 fora de ordem preserva o texto do backend, palavra por palavra", async () => {
    const detalhe =
      "Não dá para pular etapa da sequência: a próxima mensagem pendente é a 1 de 3. Marque-a como enviada antes desta.";
    vi.stubGlobal("fetch", respostaFalsa(422, { detail: detalhe }));

    await expect(marcarMensagemEnviada("tok", "1", "m2")).rejects.toThrow(SequenciaOrdemError);
    await expect(marcarMensagemEnviada("tok", "1", "m2")).rejects.toThrow(detalhe);
  });

  it("422 de mensagem já enviada também é SequenciaOrdemError, com o motivo dele", async () => {
    vi.stubGlobal("fetch", respostaFalsa(422, { detail: "Esta mensagem já foi marcada como enviada." }));
    await expect(marcarMensagemEnviada("tok", "1", "m1")).rejects.toThrow("já foi marcada como enviada");
  });

  it("401 continua sendo UnauthorizedError — quem chama desloga, não mostra texto", async () => {
    vi.stubGlobal("fetch", respostaFalsa(401, {}));
    await expect(marcarMensagemEnviada("tok", "1", "m1")).rejects.toThrow(UnauthorizedError);
  });

  it("404 e 500 NÃO viram SequenciaOrdemError", async () => {
    // Erro genérico é genérico: só o 422 carrega explicação acionável, e
    // tratar todo erro como "fora de ordem" mentiria sobre a causa.
    for (const status of [404, 500]) {
      vi.stubGlobal("fetch", respostaFalsa(status, { detail: "x" }));
      await expect(marcarMensagemEnviada("tok", "1", "m1")).rejects.not.toBeInstanceOf(SequenciaOrdemError);
    }
  });

  it("corpo sem JSON não quebra o tratamento de erro", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        json: async () => {
          throw new SyntaxError("Unexpected end of JSON input");
        },
      })) as unknown as typeof fetch,
    );
    await expect(marcarMensagemEnviada("tok", "1", "m1")).rejects.toThrow("Erro 422");
  });
});
