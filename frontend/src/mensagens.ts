import type { LeadMessage } from "./api";

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

/**
 * Carrega as mensagens de um lead **sem nunca derrubar a tela**.
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
  buscar: () => Promise<LeadMessage[]>,
  { disponivel = MENSAGENS_DISPONIVEIS }: { disponivel?: boolean } = {},
): Promise<LeadMessage[]> {
  if (!disponivel) return [];
  try {
    const mensagens = await buscar();
    return Array.isArray(mensagens) ? mensagens : [];
  } catch {
    // Silencioso de propósito: a ausência de mensagens não é um erro que o
    // vendedor precise ver. O que ele precisa ver é o lead — e vê.
    return [];
  }
}
