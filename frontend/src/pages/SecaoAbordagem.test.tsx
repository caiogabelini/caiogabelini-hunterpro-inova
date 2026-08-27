import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { ReactElement } from "react";
import { AuthProvider } from "../context/AuthContext";
import { SecaoAbordagem } from "./LeadDossierPage";
import { SEM_SEQUENCIAS, normalizarSequencias } from "../mensagens";
import type { MensagensDoLead } from "../api";

/**
 * A aba Mensagens renderizada de verdade — `renderToString` sobre o JSX real.
 *
 * ⚠️ **Por que SSR e não uma biblioteca de teste de componente.** O projeto
 * não tem jsdom nem @testing-library; `react-dom/server` já é dependência e
 * responde a pergunta que importa aqui: *o que a Carolina vê e o que ela
 * consegue clicar*. Não cobre interação (clique, estado após o PATCH) — isso
 * está coberto no nível da função pura (mensagens.test.ts) e no da rede
 * (marcarMensagemEnviada.test.ts).
 *
 * O que estes testes protegem que os puros não protegem: que a tela de fato
 * DELEGA a regra. Um `disabled` escrito à mão com a condição errada passaria
 * nos testes de `situacaoMensagem` e falharia aqui.
 */

function html(no: ReactElement): string {
  return renderToString(<AuthProvider>{no}</AuthProvider>);
}

function textoDe(no: ReactElement): string {
  return html(no).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

const BASE = {
  leadId: "11",
  email: "alberto@exemplo.com",
  temWhatsapp: true,
  telefone: "5542999640915",
  onGerada: () => {},
  onAtualizada: () => {},
  geracoesIa: { email: 0, whatsapp: 0, insights: 0, limite: 2 },
};

function msg(ordem: number, status: "pendente" | "enviada", enviadaEm: string | null = null) {
  return {
    id: `m${ordem}`,
    lead_id: "11",
    canal: "whatsapp" as const,
    ordem,
    status,
    conteudo: `TEXTO-${ordem}`,
    gerado_em: "2026-08-27T22:00:00",
    enviada_em: enviadaEm,
  };
}

function whatsappCom(proximaOrdem: number | null, ...mensagens: ReturnType<typeof msg>[]): MensagensDoLead {
  return {
    email: null,
    whatsapp: {
      grupo_id: "g1",
      canal: "whatsapp",
      gerado_em: "2026-08-27T22:00:00",
      total: mensagens.length,
      proxima_ordem: proximaOrdem,
      mensagens,
    },
  };
}

/** Resposta REAL de `GET /api/leads/11/mensagens` (Alberto Lemuch Filho),
 * capturada do backend em 27/08/2026 — conteúdo encurtado, estrutura
 * intacta. É o dado LEGADO da Fase 10: o backfill da migration transformou
 * cada mensagem antiga numa sequência de uma, e a tela precisa desenhar isso
 * sem ramo especial. */
const PAYLOAD_REAL = {
  email: {
    grupo_id: "d7b7ff82-4253-4971-9770-5e47617be865",
    canal: "email",
    gerado_em: "2026-08-26T19:14:41.497394",
    total: 1,
    proxima_ordem: 1,
    mensagens: [
      {
        id: "d7b7ff82-4253-4971-9770-5e47617be865",
        lead_id: "11",
        canal: "email",
        ordem: 1,
        status: "pendente",
        conteudo: "Alberto, identificamos que sua operação com soja em mais de 110 hectares já está consolidada.",
        assunto: "Planejamento tributário para sua operação",
        gerado_em: "2026-08-26T19:14:41.497394",
        enviada_em: null,
      },
    ],
  },
  whatsapp: {
    grupo_id: "a1f0c0de-0000-4000-8000-000000000001",
    canal: "whatsapp",
    gerado_em: "2026-08-26T19:15:02.000000",
    total: 1,
    proxima_ordem: 1,
    mensagens: [
      {
        id: "a1f0c0de-0000-4000-8000-000000000001",
        lead_id: "11",
        canal: "whatsapp",
        ordem: 1,
        status: "pendente",
        conteudo: "Alberto, tudo bem? Vi que você tem uma operação sólida em soja aqui no Paraná.",
        assunto: null,
        gerado_em: "2026-08-26T19:15:02.000000",
        enviada_em: null,
      },
    ],
  },
};

describe("SecaoAbordagem — rótulos derivados do total", () => {
  it("WhatsApp (3): inicial → follow-up → follow-up final, nessa ordem na tela", () => {
    const texto = textoDe(
      <SecaoAbordagem {...BASE} sequencias={whatsappCom(1, msg(1, "pendente"), msg(2, "pendente"), msg(3, "pendente"))} />,
    );
    expect(texto.indexOf("Mensagem inicial")).toBeLessThan(texto.indexOf("Follow-up"));
    expect(texto).toContain("Follow-up final");
    expect(texto).toContain("3 mensagens");
  });

  it("⚠️ e-mail (2): a última é 'Follow-up', não 'Follow-up final'", () => {
    const texto = textoDe(
      <SecaoAbordagem
        {...BASE}
        temWhatsapp={false}
        sequencias={{
          email: {
            grupo_id: "g", canal: "email", gerado_em: "2026-08-27T22:00:00", total: 2, proxima_ordem: 1,
            mensagens: [1, 2].map((o) => ({ ...msg(o, "pendente"), canal: "email" as const, assunto: `A${o}` })),
          },
          whatsapp: null,
        }}
      />,
    );
    expect(texto).toContain("Follow-up");
    expect(texto).not.toContain("Follow-up final");
    expect(texto).toContain("2 mensagens");
  });

  it("payload REAL do backend (legado de 1 mensagem) desenha sem ramo especial", () => {
    const texto = textoDe(<SecaoAbordagem {...BASE} sequencias={normalizarSequencias(PAYLOAD_REAL)} />);
    expect(texto).toContain("Mensagem inicial");
    expect(texto).not.toContain("Follow-up");
    expect(texto).toContain("1 mensagem");
  });
});

describe("SecaoAbordagem — a ordem de envio manda no botão", () => {
  const cena = whatsappCom(2, msg(1, "enviada", "2026-08-27T10:00:00"), msg(2, "pendente"), msg(3, "pendente"));

  it("⚠️ só a mensagem de proxima_ordem tem botão habilitado", () => {
    const botoes = (html(<SecaoAbordagem {...BASE} sequencias={cena} />).match(/<button[^>]*>[\s\S]*?<\/button>/g) ?? [])
      .filter((b) => b.includes("Marcar como enviada"));
    // A enviada não ganha botão nenhum; sobram a próxima e a bloqueada.
    expect(botoes).toHaveLength(2);
    expect(botoes.filter((b) => !b.includes("disabled"))).toHaveLength(1);
  });

  it("a bloqueada fica desabilitada e diz por quê", () => {
    const marcado = html(<SecaoAbordagem {...BASE} sequencias={cena} />);
    expect(marcado).toContain("Marque a mensagem anterior como enviada primeiro");
  });

  it("os três badges aparecem, cada um no seu toque", () => {
    const texto = textoDe(<SecaoAbordagem {...BASE} sequencias={cena} />);
    expect(texto).toContain("Enviada em 27/08/2026");
    expect(texto).toContain("Próxima a enviar");
    expect(texto).toContain("Aguardando a anterior");
  });

  it("o link de envio só aparece na próxima — abrir o follow-up antes é o pulo de etapa", () => {
    const marcado = html(<SecaoAbordagem {...BASE} sequencias={cena} />);
    expect(marcado.match(/Abrir no WhatsApp/g) ?? []).toHaveLength(1);
  });

  it("cadência concluída: nada a marcar, e a tela diz isso", () => {
    const concluida = whatsappCom(
      null,
      ...[1, 2, 3].map((o) => msg(o, "enviada", "2026-08-27T10:00:00")),
    );
    const texto = textoDe(<SecaoAbordagem {...BASE} sequencias={concluida} />);
    expect(texto).toContain("Cadência concluída");
    expect(texto).not.toContain("Marcar como enviada");
  });
});

describe("SecaoAbordagem — estados sem sequência", () => {
  it("nada gerado: os dois canais oferecem gerar a SEQUÊNCIA", () => {
    const texto = textoDe(<SecaoAbordagem {...BASE} sequencias={SEM_SEQUENCIAS} />);
    expect(texto).toContain("Gerar sequência de WhatsApp com IA");
    expect(texto).toContain("Gerar sequência de e-mail com IA");
    expect(texto).toContain("Sequência ainda não gerada.");
  });

  it("⚠️ WhatsApp antes de e-mail — é o canal que a maioria dos leads responde", () => {
    const texto = textoDe(<SecaoAbordagem {...BASE} sequencias={SEM_SEQUENCIAS} />);
    expect(texto.indexOf("WhatsApp")).toBeLessThan(texto.indexOf("E-mail"));
  });

  it("já existe sequência: o botão diz que vai TROCAR, não completar", () => {
    const texto = textoDe(<SecaoAbordagem {...BASE} sequencias={whatsappCom(1, msg(1, "pendente"))} />);
    expect(texto).toContain("Gerar nova sequência de WhatsApp");
  });

  it("limite de gerações atingido esconde o botão e explica", () => {
    const texto = textoDe(
      <SecaoAbordagem
        {...BASE}
        sequencias={SEM_SEQUENCIAS}
        geracoesIa={{ email: 2, whatsapp: 2, insights: 0, limite: 2 }}
      />,
    );
    expect(texto).toContain("Limite de gerações atingido");
    expect(texto).not.toContain("Gerar sequência de WhatsApp com IA");
  });

  it("sem e-mail nem WhatsApp confirmado, não oferece gerar nada", () => {
    const texto = textoDe(
      <SecaoAbordagem {...BASE} email={null} temWhatsapp={false} sequencias={SEM_SEQUENCIAS} />,
    );
    expect(texto).toContain("Nenhum canal disponível");
  });
});
