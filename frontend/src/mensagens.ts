import type { CanalAbordagem, LeadMessage, MensagensDoLead, SequenciaAbordagem } from "./api";

/**
 * A rota `GET /api/leads/{id}/mensagens` **existe no backend?**
 *
 * ✅ **Sim, desde a Fase 10.** A geração por IA tinha sido deixada de fora do
 * porte na Fase 6 e voltou: existem o model `LeadMessage`, as rotas de
 * geração por canal e o controle de limite por lead.
 *
 * A flag continua aqui — e continua sendo o único ponto a mexer se a rota um
 * dia sair do ar de novo. O `carregarMensagens` abaixo permanece defensivo
 * pelo mesmo motivo de sempre: a aba Mensagens é opcional, o lead é
 * essencial, e uma falha aqui não pode derrubar o dossiê (ver o histórico do
 * `Promise.all` no docstring da função).
 */
export const MENSAGENS_DISPONIVEIS = true;

/** Nenhum canal gerado ainda. Uma constante só pra que "vazio" tenha uma
 * forma só no app inteiro, em vez de cada `catch` inventar a sua. */
export const SEM_SEQUENCIAS: MensagensDoLead = { email: null, whatsapp: null };

/**
 * Ordem de exibição dos canais na aba.
 *
 * ⚠️ **WhatsApp primeiro, de propósito.** É o canal principal da maioria dos
 * leads da Carolina — produtor rural responde WhatsApp e quase não abre
 * e-mail. A ordem alfabética que sairia de `Object.keys` colocaria e-mail em
 * cima, que é o contrário do que a tela deve sugerir como primeiro passo.
 */
export const CANAIS_EM_ORDEM: readonly CanalAbordagem[] = ["whatsapp", "email"];

/** O que a tela pode fazer com uma mensagem, agora.
 *
 * - `enviada`: a Carolina já mandou e marcou.
 * - `proxima`: a única que o backend aceita marcar como enviada.
 * - `bloqueada`: pendente, mas há uma anterior ainda não enviada. */
export type SituacaoMensagem = "enviada" | "proxima" | "bloqueada";

function ehSequencia(bruto: unknown): bruto is SequenciaAbordagem {
  if (!bruto || typeof bruto !== "object" || Array.isArray(bruto)) return false;
  const dados = bruto as Record<string, unknown>;
  return Array.isArray(dados.mensagens) && typeof dados.grupo_id === "string";
}

/**
 * Normaliza a resposta de `GET /mensagens` no shape `{email, whatsapp}`.
 *
 * ⚠️ Leitura defensiva pela mesma razão de `getInsights`/`getScoreBreakdown`:
 * `fetch().json()` é `any`, e o TypeScript não garante nada em runtime.
 *
 * ⚠️ **Uma lista aqui vira vazio, não erro.** Até a Fase 10 esta rota
 * devolvia `LeadMessage[]`; se um backend antigo ficar no ar por engano, o
 * `ehSequencia` não reconhece nada e a aba mostra "ainda não gerada" — o
 * mesmo estado de quem nunca gerou. É a degradação certa: pedir pra gerar de
 * novo custa uma geração, mas mostrar mensagem sem ordem nem status numa tela
 * que promete uma cadência seria pior.
 */
export function normalizarSequencias(bruto: unknown): MensagensDoLead {
  if (!bruto || typeof bruto !== "object" || Array.isArray(bruto)) return SEM_SEQUENCIAS;
  const dados = bruto as Record<string, unknown>;
  return {
    email: ehSequencia(dados.email) ? dados.email : null,
    whatsapp: ehSequencia(dados.whatsapp) ? dados.whatsapp : null,
  };
}

/**
 * Carrega as sequências de um lead **sem nunca derrubar a tela**.
 *
 * ⚠️ Este módulo existe por causa de um bug real (26/08/2026): o dossiê
 * carregava lead e mensagens num `Promise.all`. Como `Promise.all` rejeita
 * assim que QUALQUER promessa rejeita, o 404 das mensagens derrubava o
 * resultado inteiro — e a tela mostrava "Error: Not Found" para todo lead,
 * inclusive os que o `fetchLead` tinha devolvido com 200.
 *
 * A lição é sobre acoplamento, não sobre a rota que faltava: juntar num
 * `Promise.all` uma chamada **essencial** e uma **opcional** faz a opcional
 * ganhar poder de veto sobre a tela inteira. Chamada opcional carrega o
 * próprio tratamento de erro e degrada sozinha.
 *
 * O fetcher é injetado pra este módulo ser testável sem rede — mesmo padrão
 * de cliente injetável usado no backend.
 */
export async function carregarMensagens(
  buscar: () => Promise<unknown>,
  { disponivel = MENSAGENS_DISPONIVEIS }: { disponivel?: boolean } = {},
): Promise<MensagensDoLead> {
  if (!disponivel) return SEM_SEQUENCIAS;
  try {
    return normalizarSequencias(await buscar());
  } catch {
    // Silencioso de propósito: a ausência de mensagens não é um erro que a
    // vendedora precise ver. O que ela precisa ver é o lead — e vê.
    return SEM_SEQUENCIAS;
  }
}

/**
 * O rótulo de uma mensagem, **derivado do total real da sequência**.
 *
 * ⚠️ Não dá pra mapear por `ordem` fixa. A ordem 2 é o último toque no
 * e-mail (sequência de 2) e o do meio no WhatsApp (sequência de 3) — chamar
 * as duas de "Follow-up final" prometeria à Carolina que o e-mail acabou
 * quando ainda falta um, e o contrário se o rótulo fosse só "Follow-up".
 * Por isso "final" só aparece quando existe MAIS DE UM follow-up e este é o
 * último; numa sequência de 2 o único follow-up é só "Follow-up".
 *
 * Sequências maiores que 3 não existem hoje (`TAMANHO_SEQUENCIA` no backend
 * fixa 3 e 2). Se um dia existirem, os follow-ups do meio repetem o mesmo
 * rótulo — o que resolver isso é numerá-los, não voltar a mapear por ordem.
 */
export function rotuloMensagem(ordem: number, total: number): string {
  if (ordem <= 1) return "Mensagem inicial";
  if (total > 2 && ordem >= total) return "Follow-up final";
  return "Follow-up";
}

/**
 * Em que pé está uma mensagem — a base do badge e do botão.
 *
 * ⚠️ **`proximaOrdem` vem do backend, não é deduzido aqui.** A tentação é
 * calcular "a primeira pendente" no cliente; seria uma segunda implementação
 * da regra que o `PATCH .../enviada` aplica, e as duas divergiriam na
 * primeira exceção — com o sintoma pior possível: um botão habilitado que
 * toma 422 no clique.
 */
export function situacaoMensagem(
  mensagem: Pick<LeadMessage, "ordem" | "status">,
  proximaOrdem: number | null,
): SituacaoMensagem {
  if (mensagem.status === "enviada") return "enviada";
  return proximaOrdem !== null && mensagem.ordem === proximaOrdem ? "proxima" : "bloqueada";
}

/** Mensagens ordenadas pela posição na cadência.
 *
 * O backend já devolve em ordem; ordenar de novo custa nada e tira a tela da
 * dependência de uma garantia que ela não controla. */
export function mensagensEmOrdem(sequencia: SequenciaAbordagem): LeadMessage[] {
  return [...sequencia.mensagens].sort((a, b) => a.ordem - b.ordem);
}

/** `true` quando a cadência inteira já foi enviada — não há próxima.
 *
 * Estado real e comum (a Carolina mandou os 3 toques e ninguém respondeu),
 * diferente de "nunca gerou". A tela precisa distinguir os dois: um oferece
 * gerar, o outro mostra a cadência esgotada. */
export function sequenciaConcluida(sequencia: SequenciaAbordagem): boolean {
  return sequencia.proxima_ordem === null;
}

/** Texto do botão de geração. Explicita que sai uma SEQUÊNCIA, não uma
 * mensagem — senão a Carolina clica esperando um texto e recebe três, e o
 * limite de 2 gerações por canal fica difícil de entender. */
export function rotuloBotaoGerar(canal: CanalAbordagem, jaExiste: boolean): string {
  const nome = canal === "whatsapp" ? "WhatsApp" : "e-mail";
  return jaExiste ? `Gerar nova sequência de ${nome}` : `Gerar sequência de ${nome} com IA`;
}
