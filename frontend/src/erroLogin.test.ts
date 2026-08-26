import { describe, expect, it } from "vitest";

import { RateLimitError, UnauthorizedError } from "./api";
import {
  MENSAGEM_CREDENCIAIS_INVALIDAS,
  MENSAGEM_MUITAS_TENTATIVAS,
  mensagemDeErroDeLogin,
} from "./erroLogin";

// Texto real que o backend devolve no 429 (verificado contra a resposta
// de `POST /api/auth/login` em 24/08/2026).
const DETALHE_REAL_DO_BACKEND = "Muitas tentativas de login. Tente novamente em até 15 minutos.";

describe("mensagemDeErroDeLogin", () => {
  it("401 mantém a mensagem de credenciais inválidas", () => {
    expect(mensagemDeErroDeLogin(new Error("E-mail ou senha inválidos"))).toBe(
      MENSAGEM_CREDENCIAIS_INVALIDAS,
    );
  });

  it("429 mostra o texto do backend, com o tempo", () => {
    const msg = mensagemDeErroDeLogin(new RateLimitError(DETALHE_REAL_DO_BACKEND));
    expect(msg).toBe(DETALHE_REAL_DO_BACKEND);
    expect(msg).toContain("15 minutos");
  });

  it("429 e 401 produzem mensagens DIFERENTES", () => {
    // A regressão relatada: as duas eram idênticas, e o usuário ficava
    // tentando de novo achando que era só senha errada.
    const rateLimit = mensagemDeErroDeLogin(new RateLimitError(DETALHE_REAL_DO_BACKEND));
    const credenciais = mensagemDeErroDeLogin(new Error("qualquer"));
    expect(rateLimit).not.toBe(credenciais);
  });

  it("429 sem detail do backend cai num texto genérico de espera, não no de credenciais", () => {
    for (const vazio of ["", "   "]) {
      const msg = mensagemDeErroDeLogin(new RateLimitError(vazio));
      expect(msg).toBe(MENSAGEM_MUITAS_TENTATIVAS);
      expect(msg).not.toBe(MENSAGEM_CREDENCIAIS_INVALIDAS);
    }
  });

  it("a mensagem de espera não afirma um número que pode estar desatualizado", () => {
    // O fallback é usado quando NÃO temos o texto do backend -- então não
    // pode chutar a janela, que é configurável no `.env`.
    expect(MENSAGEM_MUITAS_TENTATIVAS).not.toMatch(/\d/);
  });

  it.each([
    new UnauthorizedError(),
    new Error("Failed to fetch"),
    new TypeError("NetworkError"),
    "string solta",
    null,
    undefined,
    { status: 429 }, // objeto parecido, mas não é RateLimitError
  ])("erro não-429 (%p) cai na mensagem de credenciais", (erro) => {
    expect(mensagemDeErroDeLogin(erro)).toBe(MENSAGEM_CREDENCIAIS_INVALIDAS);
  });
});
