/**
 * Formatação e leitura de CPF/CNPJ.
 *
 * ⚠️ **Não existe no Minotto — e é a diferença estrutural desta adaptação.**
 * Lá o lead é sempre pessoa jurídica: `Lead.cnpj` é renderizado CRU, sem
 * máscara nenhuma (`{lead.cnpj}` em ListaLeadsPage e LeadDossierPage), e o
 * `format.ts` de lá não tem função de documento. Aqui o lead pode ser CPF
 * (11 dígitos, produtor rural pessoa física — 98% da população) ou CNPJ
 * (14 dígitos), então formatar exige saber qual é.
 *
 * Espelha `app/core/documentos.py` do backend: mesma regra de comprimento,
 * mesmas máscaras. Não reimplementa validação de dígito verificador — o
 * backend já rejeita documento inválido na escrita (índice único +
 * CheckConstraint), e revalidar aqui seria duplicar a regra em duas
 * linguagens sem ganho.
 */

export const TAMANHO_CPF = 11;
export const TAMANHO_CNPJ = 14;

export type TipoDocumento = "CPF" | "CNPJ";

/** Só os dígitos — o backend já persiste normalizado, isto é defesa. */
export function apenasDigitos(documento: string | null | undefined): string {
  return (documento ?? "").replace(/\D/g, "");
}

/**
 * Deduz o tipo pelo comprimento. `null` quando não é nem um nem outro.
 *
 * O backend entrega `tipo_documento` junto do lead — prefira o campo dele.
 * Esta função é o fallback pra quando só se tem o número em mãos (ex.: um
 * filtro digitado pelo usuário).
 */
export function tipoDoDocumento(documento: string | null | undefined): TipoDocumento | null {
  const digitos = apenasDigitos(documento);
  if (digitos.length === TAMANHO_CPF) return "CPF";
  if (digitos.length === TAMANHO_CNPJ) return "CNPJ";
  return null;
}

/**
 * `000.000.000-00` ou `00.000.000/0000-00`, conforme o tipo.
 *
 * Documento com comprimento inesperado volta como veio, sem máscara — não
 * inventa formatação pra dado que não reconhece.
 */
export function formatarDocumento(
  documento: string | null | undefined,
  tipo?: TipoDocumento | null,
): string {
  const d = apenasDigitos(documento);
  if (!d) return "";
  const efetivo = tipo ?? tipoDoDocumento(d);
  if (efetivo === "CPF" && d.length === TAMANHO_CPF) {
    return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`;
  }
  if (efetivo === "CNPJ" && d.length === TAMANHO_CNPJ) {
    return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
  }
  return d;
}

/**
 * Rótulo da entidade: "Produtor" (PF) ou "Empresa" (PJ).
 *
 * ⚠️ O Minotto escreve "Empresa"/"Razão social" fixo em toda tela, porque
 * lá 100% dos leads são PJ. Na Inova 98% são pessoa física — chamar um
 * produtor rural de "Empresa" no dossiê seria errado na maioria dos casos.
 * Toda tela que rotula a entidade usa esta função, nunca string fixa.
 */
export function rotuloEntidade(tipo: TipoDocumento | null | undefined): string {
  return tipo === "CNPJ" ? "Empresa" : "Produtor";
}

/** "Nome do produtor" vs "Razão social" — o rótulo do CAMPO de nome. */
export function rotuloNome(tipo: TipoDocumento | null | undefined): string {
  return tipo === "CNPJ" ? "Razão social" : "Nome do produtor";
}

/** "CPF" ou "CNPJ" — o rótulo do campo de documento. */
export function rotuloDocumento(tipo: TipoDocumento | null | undefined): string {
  return tipo === "CNPJ" ? "CNPJ" : "CPF";
}
