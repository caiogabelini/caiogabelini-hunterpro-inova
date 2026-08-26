/**
 * Traduz a falha do login na mensagem que o usuário vê.
 *
 * ⚠️ **Problema real relatado em produção (24/08/2026).** A tela mostrava
 * "E-mail ou senha inválidos" para QUALQUER falha, inclusive o 429 do
 * limite de tentativas -- porque `handleSubmit` usava um `catch` sem
 * parâmetro, que descartava o erro e fixava a mensagem de credenciais.
 * O backend mandava o texto certo ("Muitas tentativas de login. Tente
 * novamente em até 15 minutos.") e o frontend jogava fora. Resultado: o
 * usuário ficou tentando de novo achando que era só senha errada, sem
 * saber que precisava esperar -- e cada tentativa renovava a janela de
 * bloqueio, piorando a situação que ele tentava resolver.
 */
import { RateLimitError } from "./api";

export const MENSAGEM_CREDENCIAIS_INVALIDAS = "E-mail ou senha inválidos";

/** Usada só se o backend não mandar `detail` (resposta malformada, ou
 * proxy que engoliu o corpo). O texto do backend é preferido porque
 * carrega a janela configurada; este aqui é deliberadamente vago pra não
 * afirmar um número que pode não ser o vigente. */
export const MENSAGEM_MUITAS_TENTATIVAS =
  "Muitas tentativas de login. Aguarde alguns minutos antes de tentar novamente.";

export function mensagemDeErroDeLogin(erro: unknown): string {
  if (erro instanceof RateLimitError) {
    // O `detail` do backend já vem em português e já diz a janela.
    // Reescrevê-lo aqui duplicaria a informação em dois lugares e faria
    // o texto mentir no dia em que LOGIN_JANELA_BLOQUEIO_MINUTOS mudasse.
    const doBackend = erro.message?.trim();
    return doBackend ? doBackend : MENSAGEM_MUITAS_TENTATIVAS;
  }

  // Todo o resto -- 401, erro de rede, resposta inesperada -- mantém a
  // mensagem de credenciais, que era o comportamento anterior. Trocar
  // isso (ex.: distinguir "servidor fora do ar") não foi pedido e mudaria
  // o texto de casos que hoje ninguém reclamou.
  return MENSAGEM_CREDENCIAIS_INVALIDAS;
}
