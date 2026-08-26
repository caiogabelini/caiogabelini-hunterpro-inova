import type { LeadMessage } from "./api";

/**
 * A rota `GET /api/leads/{id}/mensagens` **existe no backend?**
 *
 * ⚠️ Hoje **não**. Geração de mensagem por IA foi deliberadamente deixada de
 * fora do porte (Fase 6): o `ai_enrichment.py` do Minotto tem
 * `gerar_mensagem_abordagem` e `gerar_insights_estrategicos`, e nenhuma das
 * duas foi trazida — junto com elas ficou de fora o padrão de limite de
 * geração por lead que controla o custo delas lá. Não existe model
 * `LeadMessage` nesta base, nem rota.
 *
 * Enquanto for `false`, `carregarMensagens` devolve lista vazia **sem bater
 * na rede**. É deliberado: chamar uma rota que se sabe inexistente geraria
 * um 404 garantido a cada abertura de dossiê, para sempre — ruído no log e
 * no network tab que parece bug pra quem for investigar outra coisa.
 *
 * Quando a rota existir, trocar para `true` é a única mudança necessária —
 * o tratamento de erro isolado já está pronto embaixo.
 */
export const MENSAGENS_DISPONIVEIS = false;

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
