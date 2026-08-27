import { describe, expect, it, vi } from "vitest";
import type { LeadMessage, SequenciaAbordagem } from "./api";
import {
  CANAIS_EM_ORDEM,
  MENSAGENS_DISPONIVEIS,
  SEM_SEQUENCIAS,
  carregarMensagens,
  mensagensEmOrdem,
  normalizarSequencias,
  rotuloBotaoGerar,
  rotuloMensagem,
  sequenciaConcluida,
  situacaoMensagem,
} from "./mensagens";

function mensagem(ordem: number, extra: Partial<LeadMessage> = {}): LeadMessage {
  return {
    id: `m${ordem}`,
    lead_id: "9",
    canal: "whatsapp",
    ordem,
    status: "pendente",
    conteudo: `texto ${ordem}`,
    gerado_em: "2026-08-27T22:00:00",
    enviada_em: null,
    ...extra,
  };
}

/** Uma sequência no formato exato de SequenciaAbordagemRead. */
function sequencia(total: number, extra: Partial<SequenciaAbordagem> = {}): SequenciaAbordagem {
  return {
    grupo_id: "g1",
    canal: "whatsapp",
    gerado_em: "2026-08-27T22:00:00",
    total,
    proxima_ordem: 1,
    mensagens: Array.from({ length: total }, (_, i) => mensagem(i + 1)),
    ...extra,
  };
}

describe("carregarMensagens", () => {
  it("não bate na rede enquanto a rota não existe", async () => {
    const buscar = vi.fn();
    expect(await carregarMensagens(buscar, { disponivel: false })).toEqual(SEM_SEQUENCIAS);
    expect(buscar).not.toHaveBeenCalled();
  });

  it("quando disponível, devolve as sequências dos dois canais", async () => {
    const payload = { email: sequencia(2, { canal: "email" }), whatsapp: sequencia(3) };
    const carregado = await carregarMensagens(async () => payload, { disponivel: true });
    expect(carregado.whatsapp?.total).toBe(3);
    expect(carregado.email?.total).toBe(2);
  });

  it("404 vira estado vazio, não exceção", async () => {
    const erro404 = async () => {
      throw new Error("Not Found");
    };
    await expect(carregarMensagens(erro404, { disponivel: true })).resolves.toEqual(SEM_SEQUENCIAS);
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
      await expect(carregarMensagens(falha, { disponivel: true })).resolves.toEqual(SEM_SEQUENCIAS);
    }
  });

  it("o padrão do módulo reflete o estado real do backend", () => {
    // Documentação executável: a rota `GET /api/leads/{id}/mensagens` passou
    // a existir na Fase 10. Se ela for desligada, esta flag é o único ponto
    // a mexer — e este teste é quem lembra disso.
    expect(MENSAGENS_DISPONIVEIS).toBe(true);
  });
});

describe("normalizarSequencias", () => {
  it("shape inesperado vira estado vazio", () => {
    for (const bruto of [null, undefined, "texto", 42, []]) {
      expect(normalizarSequencias(bruto)).toEqual(SEM_SEQUENCIAS);
    }
  });

  it("canal sem geração fica null, e isso NÃO é erro", () => {
    const so = normalizarSequencias({ email: null, whatsapp: sequencia(3) });
    expect(so.email).toBeNull();
    expect(so.whatsapp).not.toBeNull();
  });

  it("⚠️ a lista da Fase 10 vira vazio em vez de renderizar meia tela", () => {
    // Se um backend anterior à Fase 11a ficar no ar por engano, ele devolve
    // `LeadMessage[]`. Sem ordem nem status não dá pra desenhar uma cadência,
    // e o estado honesto é "ainda não gerada".
    expect(normalizarSequencias([{ id: "1", canal: "email", conteudo: "oi" }])).toEqual(SEM_SEQUENCIAS);
  });

  it("canal com objeto sem mensagens[] é descartado, não quebra", () => {
    expect(normalizarSequencias({ email: { grupo_id: "g" }, whatsapp: 42 })).toEqual(SEM_SEQUENCIAS);
  });
});

describe("rotuloMensagem", () => {
  it("WhatsApp (3): inicial, follow-up, follow-up final", () => {
    expect([1, 2, 3].map((o) => rotuloMensagem(o, 3))).toEqual([
      "Mensagem inicial",
      "Follow-up",
      "Follow-up final",
    ]);
  });

  it("⚠️ e-mail (2): a ordem 2 é 'Follow-up', NUNCA 'Follow-up final'", () => {
    // O rótulo é derivado do total, não mapeado por ordem. Com um mapa fixo
    // por ordem, a 2 do e-mail herdaria o rótulo da 2 do WhatsApp — ou o do
    // último toque, dependendo do mapa. Os dois estariam errados num canal.
    expect([1, 2].map((o) => rotuloMensagem(o, 2))).toEqual(["Mensagem inicial", "Follow-up"]);
  });

  it("o ÚLTIMO toque só vira 'final' quando há mais de um follow-up", () => {
    // Mesma posição relativa (a última da cadência), rótulos diferentes: com
    // um follow-up só, "final" não informa nada e sugere que houve outros.
    expect(rotuloMensagem(2, 2)).toBe("Follow-up");
    expect(rotuloMensagem(3, 3)).toBe("Follow-up final");
  });

  it("sequência legada de 1 mensagem só tem a inicial", () => {
    // A Fase 11a fez backfill das mensagens antigas como sequência de 1.
    expect(rotuloMensagem(1, 1)).toBe("Mensagem inicial");
  });
});

describe("situacaoMensagem", () => {
  it("enviada é enviada, mesmo que fosse a próxima", () => {
    expect(situacaoMensagem({ ordem: 1, status: "enviada" }, 2)).toBe("enviada");
  });

  it("só a ordem que bate com proxima_ordem é a próxima", () => {
    expect(situacaoMensagem({ ordem: 2, status: "pendente" }, 2)).toBe("proxima");
    expect(situacaoMensagem({ ordem: 3, status: "pendente" }, 2)).toBe("bloqueada");
    expect(situacaoMensagem({ ordem: 1, status: "pendente" }, 2)).toBe("bloqueada");
  });

  it("⚠️ proxima_ordem null não libera ninguém", () => {
    // Cadência concluída: nenhuma mensagem pendente pode ser marcada. Se isto
    // caísse em "proxima", a tela ofereceria um botão que toma 422.
    expect(situacaoMensagem({ ordem: 1, status: "pendente" }, null)).toBe("bloqueada");
  });

  it("numa sequência inteira, exatamente UMA é a próxima", () => {
    const seq = sequencia(3, {
      proxima_ordem: 2,
      mensagens: [mensagem(1, { status: "enviada", enviada_em: "2026-08-27T10:00:00" }), mensagem(2), mensagem(3)],
    });
    const situacoes = seq.mensagens.map((m) => situacaoMensagem(m, seq.proxima_ordem));
    expect(situacoes).toEqual(["enviada", "proxima", "bloqueada"]);
    expect(situacoes.filter((s) => s === "proxima")).toHaveLength(1);
  });
});

describe("sequenciaConcluida", () => {
  it("proxima_ordem null = cadência esgotada", () => {
    const enviadas = sequencia(3, {
      proxima_ordem: null,
      mensagens: [1, 2, 3].map((o) => mensagem(o, { status: "enviada", enviada_em: "2026-08-27T10:00:00" })),
    });
    expect(sequenciaConcluida(enviadas)).toBe(true);
    expect(enviadas.mensagens.every((m) => situacaoMensagem(m, null) === "enviada")).toBe(true);
  });

  it("com uma pendente, não está concluída", () => {
    expect(sequenciaConcluida(sequencia(3))).toBe(false);
  });
});

describe("mensagensEmOrdem", () => {
  it("ordena pela posição na cadência, venha o array como vier", () => {
    const fora = sequencia(3, { mensagens: [mensagem(3), mensagem(1), mensagem(2)] });
    expect(mensagensEmOrdem(fora).map((m) => m.ordem)).toEqual([1, 2, 3]);
  });

  it("não muta a sequência recebida", () => {
    const seq = sequencia(3, { mensagens: [mensagem(3), mensagem(1), mensagem(2)] });
    mensagensEmOrdem(seq);
    expect(seq.mensagens.map((m) => m.ordem)).toEqual([3, 1, 2]);
  });
});

describe("ordem dos canais e rótulo do botão", () => {
  it("⚠️ WhatsApp vem antes de e-mail", () => {
    // Canal principal da maioria dos leads da Carolina. A ordem alfabética
    // (que sairia de Object.keys) inverteria isso.
    expect(CANAIS_EM_ORDEM).toEqual(["whatsapp", "email"]);
  });

  it("o botão diz SEQUÊNCIA, não mensagem", () => {
    // Uma geração produz 3 (ou 2) mensagens e consome 1 das 2 permitidas.
    // "Gerar mensagem" faria a vendedora esperar um texto e receber três.
    expect(rotuloBotaoGerar("whatsapp", false)).toBe("Gerar sequência de WhatsApp com IA");
    expect(rotuloBotaoGerar("email", false)).toBe("Gerar sequência de e-mail com IA");
  });

  it("regerar deixa claro que troca a sequência inteira", () => {
    expect(rotuloBotaoGerar("whatsapp", true)).toBe("Gerar nova sequência de WhatsApp");
    expect(rotuloBotaoGerar("email", true)).toBe("Gerar nova sequência de e-mail");
  });
});
