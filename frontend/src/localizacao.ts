import type { Lead } from "./api";

/**
 * Rótulo de localização de um lead.
 *
 * ## O "+N"
 *
 * 8,5% dos produtores têm propriedades em mais de um município (medido sobre
 * os 2.806 do universo real do Paraná). O município principal é o da
 * **operação de crédito mais recente** — mesma regra que já define área e
 * valor —, e os demais viram um contador discreto: `"Douradina (+1)"`.
 *
 * Mostrar só o primeiro esconderia que o produtor opera em mais lugares;
 * listar todos ocuparia a linha inteira num campo de spec sheet. O "+N" diz
 * que há mais sem gastar espaço, e a lista completa fica no dossiê.
 *
 * ## Por que existe como função, e não inline na tela
 *
 * O mesmo rótulo aparece em dois lugares (cabeçalho fixo e card de Dados
 * Cadastrais). Duas cópias da regra divergiriam — foi exatamente assim que a
 * aba Contatos passou a discordar da aba Dados sobre o mesmo lead (ver
 * `contatos.ts`).
 */
export interface Localizacao {
  /** `"Douradina (+1)"`, ou `null` quando não há município conhecido. */
  municipio: string | null;
  /** `"Douradina (+1)/PR"`, ou `null` se não há município nem UF. */
  completo: string | null;
  /** Quantos municípios além do principal. `0` na maioria dos casos. */
  extras: number;
}

export function getLocalizacao(lead: Lead): Localizacao {
  const lista = Array.isArray(lead.municipios) ? lead.municipios.filter(Boolean) : [];
  // `lead.municipio` é a fonte principal — é a coluna que o backend grava.
  // A lista serve para o contador; se vier vazia, o comportamento é o de
  // antes de existir "+N".
  const principal = lead.municipio || lista[0] || null;
  const extras = Math.max(0, lista.length - 1);

  const municipio = principal ? (extras > 0 ? `${principal} (+${extras})` : principal) : null;

  let completo: string | null = null;
  if (municipio && lead.uf) completo = `${municipio}/${lead.uf}`;
  else if (municipio) completo = municipio;
  else if (lead.uf) completo = lead.uf;

  return { municipio, completo, extras };
}
