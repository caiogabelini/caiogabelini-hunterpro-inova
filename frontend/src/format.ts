export function formatCurrencyBRL(value: number): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(iso));
}

/** "há 2 dias", "há 3 horas", etc. Cai pra data absoluta (formatDate)
 * depois de 30 dias -- uma relativa tipo "há 4 meses" fica vaga demais
 * pra decidir prioridade de contato. */
export function formatRelative(iso: string): string {
  const entao = new Date(iso).getTime();
  const diffMs = Math.max(0, Date.now() - entao);
  const MINUTO = 60_000;
  const HORA = 3_600_000;
  const DIA = 86_400_000;

  if (diffMs < MINUTO) return "agora mesmo";
  if (diffMs < HORA) {
    const m = Math.floor(diffMs / MINUTO);
    return `há ${m} minuto${m === 1 ? "" : "s"}`;
  }
  if (diffMs < DIA) {
    const h = Math.floor(diffMs / HORA);
    return `há ${h} hora${h === 1 ? "" : "s"}`;
  }
  const d = Math.floor(diffMs / DIA);
  if (d < 30) return `há ${d} dia${d === 1 ? "" : "s"}`;
  return formatDate(iso);
}

/**
 * Mesma heurística de `app/services/whatsapp.py::formatar_numero_whatsapp`
 * -- decide prefixar o código do país (55) pelo TAMANHO do número já
 * limpo (10/11 dígitos = nacional, sempre prefixa), não pelo prefixo:
 * DDD 55 é do Rio Grande do Sul, um número local de 11 dígitos pode
 * começar com "55" sem ter o código do país. Resultado final precisa
 * ficar em 12-13 dígitos pra ser considerado um link plausível --
 * espelha a mesma validação do backend, não reinventa a regra.
 */
export function formatarNumeroWhatsapp(telefone: string | null | undefined): string | null {
  const digitos = (telefone ?? "").replace(/\D/g, "");
  if (!digitos) return null;

  const comCodigoPais = digitos.length === 10 || digitos.length === 11 ? `55${digitos}` : digitos;
  if (comCodigoPais.length !== 12 && comCodigoPais.length !== 13) return null;
  return comCodigoPais;
}
