/**
 * Leitura defensiva de `Lead.emails_secundarios`.
 *
 * Mesma razão de `leadScore.ts::getScoreBreakdown` e
 * `insights.ts::getInsights`: `fetch().json()` é tipado como `any` pelo
 * DOM lib, então o TypeScript não dá NENHUMA garantia em runtime de que
 * o payload bate com a interface `Lead`. Aqui o risco é maior que nos
 * outros dois: eles degradam pra "vazio" num shape inesperado, enquanto
 * um `.map()` direto sobre algo que não é array DERRUBA a página inteira
 * do dossiê.
 *
 * Qualquer coisa que não seja uma lista de itens com `email` string vira
 * `[]` -- que a aba Contatos trata como "não mostra a seção", o mesmo
 * que "não tem secundários".
 */

export interface EmailSecundario {
  email: string;
  /** "personal" | "generic" -- `null` quando o Hunter não classificou. */
  tipo: string | null;
  confidence: number | null;
}

/** Rótulo em português pro badge. Um tipo desconhecido (se o Hunter
 * criar um terceiro) aparece como veio, em vez de sumir ou virar
 * "genérico" por engano. */
export function labelTipoEmail(tipo: string | null): string {
  if (tipo === "personal") return "pessoal";
  if (tipo === "generic") return "genérico";
  return tipo ?? "—";
}

export function getEmailsSecundarios(valor: unknown): EmailSecundario[] {
  if (!Array.isArray(valor)) return [];

  return valor.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const registro = item as Record<string, unknown>;
    const email = registro.email;
    if (typeof email !== "string" || email.length === 0) return [];

    return [
      {
        email,
        tipo: typeof registro.tipo === "string" ? registro.tipo : null,
        confidence: typeof registro.confidence === "number" ? registro.confidence : null,
      },
    ];
  });
}
