/**
 * Regra de exibição do aviso "não conseguimos ler o site" no dossiê.
 *
 * Existe como função nomeada, apesar de ser uma comparação só, porque a
 * comparação é FÁCIL DE ERRAR: `site_scrape_sucesso` tem TRÊS estados, e
 * o jeito intuitivo de escrever isso (`!lead.site_scrape_sucesso`)
 * mostraria o aviso também quando o valor é `null` -- ou seja, em todo
 * lead que nunca teve site pra ler, onde o aviso é simplesmente falso.
 *
 *   null  -> a etapa nem rodou (lead sem site). NÃO avisa: os campos
 *            vazios já se explicam pela ausência de site.
 *   true  -> leu com sucesso. NÃO avisa: campo vazio significa "o site
 *            não publica isso".
 *   false -> tentou e não leu. AVISA.
 */
export function deveAvisarSiteNaoLido(siteScrapeSucesso: unknown): boolean {
  return siteScrapeSucesso === false;
}
