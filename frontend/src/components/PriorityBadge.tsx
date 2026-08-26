/**
 * Badge de prioridade.
 *
 * ⚠️ **Valores diferentes do Minotto.** Lá a prioridade é "A"/"B"/"C"; aqui
 * o backend grava "ALTA"/"MEDIA"/"BAIXA" (`prioridade_do_score` em
 * app/workers/enriquecimento.py). Portar o mapa de classes sem trocar os
 * valores faria todo lead cair no fallback e ficar vermelho.
 *
 * O fallback continua sendo a classe de menor prioridade — valor inesperado
 * vindo do backend não pode quebrar a tela, e "baixa" é o palpite mais
 * conservador (não promove ninguém por engano).
 */
const CORES: Record<string, string> = {
  ALTA: "priority-alta",
  MEDIA: "priority-media",
  BAIXA: "priority-baixa",
};

export function PriorityBadge({
  prioridade,
  size = "sm",
}: {
  prioridade?: string | null;
  size?: "sm" | "lg";
}) {
  if (!prioridade) return null;
  const classe = CORES[prioridade.toUpperCase()] ?? "priority-baixa";
  return (
    <span
      className={`priority-badge ${classe} ${size === "lg" ? "priority-badge-lg" : ""}`}
    >
      {prioridade.toUpperCase()}
    </span>
  );
}
