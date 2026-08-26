import { getDadosNicho, type Lead } from "./api";

/**
 * Os dados de contato de um lead, numa forma só.
 *
 * ## ⚠️ Por que este módulo existe (bug real, 26/08/2026)
 *
 * O dossiê tinha **três** representações do mesmo decisor circulando:
 *
 * | caminho                    | a API manda? | quem lia            |
 * |----------------------------|--------------|---------------------|
 * | `lead.decisor`             | **sim**      | ninguém             |
 * | `lead.dados_nicho.decisor` | **sim**      | aba Dados           |
 * | `lead.decisor_nome`        | **não**      | aba Contatos        |
 *
 * `decisor_nome` é um fantasma do Minotto — campo que esta API nunca enviou.
 * A aba Contatos fazia `if (!lead.decisor_nome)` e caía sempre no estado
 * vazio, mostrando "Nenhum decisor identificado" para um lead que a aba
 * Dados exibia com nome, telefone, WhatsApp e e-mail validado. O dado estava
 * lá o tempo todo; a aba lia o campo errado.
 *
 * O erro não dava tela vermelha nem erro de tipo: `decisor_nome` é opcional
 * no `Lead`, então `undefined` passa pelo TypeScript e vira "não tem". É o
 * modo de falha mais caro — a tela mente com confiança.
 *
 * A correção é **uma fonte só**: as duas abas passam por aqui. Enquanto
 * houver dois caminhos de leitura pro mesmo dado, eles vão divergir de novo.
 *
 * ## Precedência
 *
 * Campo de topo primeiro, `dados_nicho` como reserva. O topo é o contrato
 * que a API escolheu expor na Fase 8a ("a tela não deveria precisar saber
 * que a origem é um JSON"); o JSON é a origem crua, mantida como rede de
 * segurança caso um lead antigo não tenha passado pelo desempacotamento.
 */
export interface ContatosDoLead {
  /** Nome do decisor. `null` = ninguém identificado ainda. */
  decisor: string | null;
  /** Que fonte resolveu o decisor (`api_full`, `brasil_api`). */
  fonteDecisor: string | null;
  /** Número principal — o validado na Evolution, quando houve validação. */
  telefone: string | null;
  /** Contato alternativo. ⚠️ **Não** passou por validação de WhatsApp. */
  telefoneSecundario: string | null;
  /** WhatsApp **confirmado ativo**, não "tem telefone cadastrado". */
  whatsappAtivo: boolean;
  email: string | null;
  /** `email_status` aprovado pelo ZeroBounce. */
  emailValidado: boolean;
}

/** Status do ZeroBounce que contam como e-mail utilizável. */
const STATUS_EMAIL_APROVADO = ["valid", "catch-all"];

function texto(valor: unknown): string | null {
  return typeof valor === "string" && valor.trim() !== "" ? valor : null;
}

export function getContatos(lead: Lead): ContatosDoLead {
  const nicho = getDadosNicho(lead.dados_nicho);
  const emailStatus = texto(lead.email_status) ?? texto(nicho.email_status);

  return {
    decisor: texto(lead.decisor) ?? texto(nicho.decisor),
    // ⚠️ `fonte_decisor` é o único destes que a API **não** desempacota no
    // topo — só existe dentro de `dados_nicho`. Conferido na resposta real.
    fonteDecisor: texto(nicho.fonte_decisor),
    telefone: texto(lead.telefone),
    telefoneSecundario: texto(lead.telefone_secundario),
    // `?? false` de propósito: `undefined`/`null` é "não medimos", e a tela
    // trata isso igual a "não tem" — mas nunca como "tem". Ver a distinção
    // None/False do backend (§6).
    whatsappAtivo: lead.whatsapp_ativo ?? nicho.whatsapp_ativo ?? false,
    email: texto(lead.email),
    emailValidado: emailStatus !== null && STATUS_EMAIL_APROVADO.includes(emailStatus),
  };
}

/** Há algum canal por onde falar com este lead? */
export function temAlgumCanal(contatos: ContatosDoLead): boolean {
  return Boolean(
    contatos.telefone || contatos.telefoneSecundario || contatos.email,
  );
}
