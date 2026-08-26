/**
 * Regra de exibição do limite de gerações de IA no dossiê.
 *
 * ⚠️ **Controle de custo.** Cada "Gerar mensagem"/"Gerar Insights" é uma
 * chamada paga à Anthropic. O backend barra com 429
 * (`app/api/routes/limites_ia.py`); aqui a mesma regra desabilita o botão
 * ANTES do clique, pra que o usuário veja o motivo em vez de tomar um
 * erro.
 *
 * O LIMITE vem do backend dentro de `geracoes_ia.limite`, nunca
 * hardcodado: `LIMITE_GERACOES_IA_POR_LEAD` é configurável no `.env`, e
 * um número fixo aqui faria frontend e backend discordarem no dia em que
 * alguém ajustasse a config.
 *
 * Leitura defensiva do payload, mesma razão de `getScoreBreakdown`/
 * `getInsights`/`getEmailsSecundarios`: `fetch().json()` é `any`, então o
 * TypeScript não garante nada em runtime.
 */

export type TipoGeracaoIa = "email" | "whatsapp" | "insights";

export interface StatusLimiteIa {
  /** Quantas gerações deste tipo já foram feitas pro lead. */
  usadas: number;
  /** Limite vigente. `null` = o backend não informou (ou desligou). */
  limite: number | null;
  /** Se `true`, o botão fica desabilitado e a mensagem aparece. */
  atingido: boolean;
}

function numeroOuNull(valor: unknown): number | null {
  return typeof valor === "number" && Number.isFinite(valor) ? valor : null;
}

/**
 * Lê `Lead.geracoes_ia` pro tipo pedido.
 *
 * Sem payload (rotas de lista não calculam `geracoes_ia`, ver o schema
 * do backend) ou com shape inesperado, devolve `atingido: false` — ou
 * seja, **falha liberando, não travando**. É deliberado: o backend é
 * quem de fato impede o gasto (429); travar a tela por não conseguir ler
 * um contador transformaria um problema de exibição num produto
 * quebrado.
 *
 * Limite `<= 0` também libera — espelha
 * `settings.LIMITE_GERACOES_IA_POR_LEAD <= 0` desligando a checagem no
 * backend.
 */
export function statusLimiteIa(geracoesIa: unknown, tipo: TipoGeracaoIa): StatusLimiteIa {
  if (typeof geracoesIa !== "object" || geracoesIa === null) {
    return { usadas: 0, limite: null, atingido: false };
  }

  const registro = geracoesIa as Record<string, unknown>;
  const usadas = numeroOuNull(registro[tipo]) ?? 0;
  const limite = numeroOuNull(registro.limite);

  if (limite === null || limite <= 0) {
    return { usadas, limite, atingido: false };
  }
  return { usadas, limite, atingido: usadas >= limite };
}

export const MENSAGEM_LIMITE_ATINGIDO = "Limite de gerações atingido — contate um administrador";
