/**
 * Cruza `var(--x)` usada contra `--x:` definida em todo o CSS de src/.
 *
 * ⚠️ Existe porque um `var(--nao-definida)` **não dá erro em lugar nenhum**:
 * o build passa, o typecheck passa, o lint passa, e o navegador só ignora a
 * propriedade — o elemento renderiza sem cor. Foi assim que a troca de
 * paleta da Fase 7 deixou 26 referências a `--color-gold-*` mortas em 6
 * arquivos, descobertas só por uma varredura como esta.
 *
 * Uso: node scripts/checar-vars-css.mjs
 * Sai com código 1 se houver referência quebrada.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

function arquivosCss(dir) {
  return readdirSync(dir).flatMap((nome) => {
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) return arquivosCss(caminho);
    return caminho.endsWith(".css") ? [caminho] : [];
  });
}

const arquivos = arquivosCss("src");
const definidas = new Set();
const usadas = new Map(); // nome -> [ "arquivo:linha", ... ]

for (const arquivo of arquivos) {
  // ⚠️ Remove comentários ANTES de varrer, preservando as quebras de linha
  // pra que os números de linha continuem certos. Sem isso, um comentário
  // que MENCIONA `var(--x)` — inclusive um explicando que `--x` foi
  // removida — vira falso positivo. Um verificador que acusa o que não é
  // erro é ignorado na terceira vez, e aí não serve pra nada.
  const bruto = readFileSync(arquivo, "utf8");
  const linhas = bruto
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "))
    .split("\n");
  linhas.forEach((linha, i) => {
    // Definição: `--x:` no início de uma declaração (não dentro de var()).
    for (const m of linha.matchAll(/(?:^|[;{]|\s)(--[\w-]+)\s*:/g)) {
      definidas.add(m[1]);
    }
    for (const m of linha.matchAll(/var\(\s*(--[\w-]+)/g)) {
      if (!usadas.has(m[1])) usadas.set(m[1], []);
      usadas.get(m[1]).push(`${arquivo}:${i + 1}`);
    }
  });
}

const quebradas = [...usadas.keys()].filter((v) => !definidas.has(v)).sort();
// Definidas e nunca usadas não são erro (podem ser API pública de tokens),
// mas valem como aviso: token morto é dívida silenciosa.
const orfas = [...definidas].filter((v) => !usadas.has(v)).sort();

console.log(`${arquivos.length} arquivos CSS · ${definidas.size} variáveis definidas · ${usadas.size} referenciadas`);

if (orfas.length) {
  console.log(`\n${orfas.length} definida(s) e nunca usada(s) (aviso, não erro):`);
  for (const v of orfas) console.log(`   ${v}`);
}

if (quebradas.length) {
  console.log(`\n❌ ${quebradas.length} referência(s) QUEBRADA(S):`);
  for (const v of quebradas) {
    for (const onde of usadas.get(v)) console.log(`   ${v}  <- ${onde}`);
  }
  process.exit(1);
}
console.log("\n✅ nenhuma referência quebrada");
