/**
 * Base das chamadas à API — HunterPro Inova.
 *
 * ⚠️ **ESTADO DO BACKEND EM 26/08/2026.** Este arquivo é o porte fiel do
 * contrato do Minotto, adaptado ao modelo de dados da Inova. Mas o backend
 * da Inova hoje expõe **apenas `GET /health`** — `app/api/routes/` e
 * `app/schemas/` estão vazios desde a Fase 1, e não existem os models
 * `User`, `BuscaLeads` nem `LeadMessage`.
 *
 * Ou seja: o que está aqui **define o contrato que a Fase 8 precisa
 * implementar**, não descreve algo que já responde. Tudo compila; quase
 * tudo falha em runtime até as rotas existirem. Cada função abaixo marcada
 * com ⛔ depende de endpoint que ainda não existe.
 *
 * Base das chamadas à API.
 *
 * Em PRODUÇÃO o padrão é string vazia, ou seja, URLs RELATIVAS
 * (`/api/leads`). O nginx que serve o frontend faz proxy de `/api/` (e
 * de `/health`) pro App Service do backend dentro da rede interna do
 * EasyPanel -- ver frontend/nginx.conf.template. Como o navegador vê
 * front e API na mesma origem, não há requisição cross-origin e o CORS
 * deixa de ser um problema em produção.
 *
 * Em DESENVOLVIMENTO o padrão continua sendo http://localhost:8000,
 * porque o dev server do Vite não tem proxy configurado e o uvicorn
 * roda numa porta separada.
 *
 * O `import.meta.env.PROD` no meio existe pra que o comportamento certo
 * seja o PADRÃO nos dois ambientes, sem depender de ninguém lembrar de
 * definir a variável. Antes o fallback era localhost em qualquer caso:
 * bastava `VITE_API_URL` não estar definida no build de produção pra
 * app inteira tentar falar com a máquina de quem abriu o navegador.
 *
 * `VITE_API_URL` continua sobrescrevendo os dois casos -- necessário só
 * se o backend for servido em outro domínio. Atenção: variável do Vite
 * é assada no bundle em tempo de BUILD, não lida em runtime; defini-la
 * no painel do EasyPanel depois do build não tem efeito nenhum.
 */
const API_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.PROD ? "" : "http://localhost:8000");

/** Um critério já pontuado pra um lead -- mesmo shape de
 * CriterionScoreDetail em app/scoring/compute_lead_score.py, serializado
 * dentro de Lead.score_detalhes.breakdown pela task compute_lead_score. */
export interface ScoreBreakdownItem {
  key: string;
  label: string;
  weight: number;
  layer: "estruturado" | "inferencia" | "validacao";
  points: number;
}

export interface ScoreDetalhes {
  breakdown: ScoreBreakdownItem[];
}

/** Mesmo shape do dict devolvido por
 * app/services/ai_enrichment.py::gerar_insights_estrategicos e
 * persistido em Lead.insights_ia. `potencial_oportunidade` chega já
 * normalizado (lowercase/trim) pelo backend, mas NÃO é forçado a um dos
 * 3 valores esperados -- um valor fora de "alto"/"médio"/"baixo" só cai
 * num badge neutro no frontend (ver POTENCIAL_LABELS em
 * LeadDossierPage.tsx), nunca quebra a tela. */
export interface InsightsIA {
  resumo_estrategico: string;
  potencial_oportunidade: string;
  recomendacao_abordagem: string[];
  estrategia_comunicacao: string;
  cta_sugerido: string;
}

/** Mesmo shape de LeadMessageRead em app/schemas/lead_message.py --
 * uma mensagem de abordagem gerada, já persistida (uma linha de
 * histórico por canal em LeadMessage). `canal` tipado como união
 * literal porque é o mesmo conjunto fechado de CanalMensagem no
 * backend (app/models/lead_message.py) -- Instagram de propósito fora. */
export type CanalAbordagem = "email" | "whatsapp";

export interface LeadMessage {
  id: string;
  lead_id: string;
  canal: CanalAbordagem;
  conteudo: string;
  assunto?: string | null;
  gerado_em: string;
}

/**
 * Um lead. Espelha `app/models/lead.py` da Inova.
 *
 * ⚠️ **Diferença estrutural em relação ao Minotto.** Lá o lead é sempre
 * pessoa jurídica: os campos são `cnpj` e `razao_social`. Aqui a chave de
 * negócio é `documento` (CPF **ou** CNPJ) + `tipo_documento`, e o nome é
 * `nome` — razão social quando PJ, nome do produtor quando PF. 98% da
 * população da Inova é pessoa física.
 *
 * ⚠️ Os campos de nicho do Minotto (dívida PGFN, CNES/RQE, zona franca,
 * anos de mercado, Simples Nacional) **não existem** aqui. O equivalente da
 * Inova vem do Sicor e mora em `dados_nicho`, um JSON livre — ver
 * `DadosNichoSicor`. Foi decisão da Fase 1 deixar genérico até os parsers
 * existirem, e a Fase 4 passou a preenchê-lo.
 *
 * ⚠️ Campos marcados ⛔ NÃO existem no backend hoje. Estão aqui porque a
 * tela portada do Minotto os consome; a Fase 8 precisa criá-los (coluna,
 * schema e rota) ou a tela correspondente precisa ser cortada.
 */
export interface Lead {
  id: string;
  /** Só dígitos. CPF (11) ou CNPJ (14) — chave de negócio, índice único. */
  documento: string;
  tipo_documento: "CPF" | "CNPJ";
  /** Razão social (PJ) ou nome do produtor (PF). */
  nome: string;
  /** Município principal — o da operação de crédito mais recente. */
  municipio?: string | null;
  /** Todos os municípios do produtor, o principal primeiro. Mais de um
   *  quando a operação mais recente cobre propriedades em municípios
   *  diferentes (8,5% dos casos). Renderizar via `getLocalizacao`
   *  (localizacao.ts), nunca direto — o "+N" é regra, não formatação. */
  municipios?: string[];
  uf?: string | null;
  telefone?: string | null;
  /** ✅ Existe desde 26/08/2026 (migration `9b12f4c7d833`). Contato
   *  **alternativo**, quando a fonte trouxe mais de um número. ⚠️ NÃO é o
   *  número do WhatsApp — o validado é sempre `telefone`. Este aqui não
   *  passou por validação nenhuma e não deve ser apresentado como se
   *  tivesse passado. `null` = a fonte trouxe um número só. */
  telefone_secundario?: string | null;
  email?: string | null;
  site?: string | null;
  score?: number | null;
  /** "ALTA" | "MEDIA" | "BAIXA" — ver `prioridade_do_score` no backend.
   *  ⚠️ No Minotto era "A"/"B"/"C". */
  prioridade?: string | null;
  /** Etapas do enriquecimento puladas, com motivo. `null` = pipeline nunca
   *  rodou; `[]` = rodou sem pular nada. A distinção é deliberada (§6). */
  etapas_puladas?: EtapaPulada[] | null;
  /** Dados do nicho (Sicor + enriquecimento). Lido via `getDadosNicho`,
   *  nunca direto — é JSON livre vindo de `fetch().json()`, tipado `any`. */
  dados_nicho?: unknown;
  observacoes?: string | null;
  created_at: string;
  updated_at: string;

  /** ✅ Existe desde a Fase 8b (migration `7a3c9d2b4e10`). `NOT NULL` no
   *  banco com default `"novo_lead"`, então todo lead vem com coluna. Os 9
   *  valores possíveis estão em `kanbanStatuses.ts` e há CHECK no banco. */
  kanban_status?: string;
  /** ✅ Existe (Fase 8b). Só preenchido em `"perdido"`; a rota de status
   *  **limpa** este campo ao sair dessa coluna. */
  motivo_perda?: string | null;
  /** ✅ Existem (Fase 8b). Preenchidos pelo modal de fechamento em
   *  `"ganho"`. Diferente de `motivo_perda`, **não** são limpos ao sair de
   *  "ganho": a venda aconteceu. */
  servicos_vendidos?: string[] | null;
  tipo_contrato?: string | null;
  valor_fechamento?: number | null;
  /** ✅ Existe na resposta desde a Fase 8a, mas **não é coluna**: o backend
   *  persiste só `score` (int) e recalcula o breakdown a cada resposta com
   *  `calcular_score` (~7 µs/lead). Deliberado — ver o docstring de
   *  `app/api/routes/leads.py`. */
  score_detalhes?: ScoreDetalhes | null;
  /** ⛔ Fantasma do Minotto: lá é um campo único sem canal, marcado
   *  deprecated. Aqui nunca existiu — mensagem vive em `LeadMessage`, uma
   *  linha por geração e por canal. */
  mensagem_abordagem?: string | null;
  /** ✅ Existem desde a Fase 10, quando a geração por IA foi portada. */
  insights_ia?: InsightsIA | null;
  insights_gerado_em?: string | null;
  insights_geracoes_count?: number;
  geracoes_ia?: unknown;

  /** ⛔ Campos do nicho do MINOTTO (saúde). Não existem no backend da Inova
   *  e não terão equivalente: dívida PGFN, RQE/CNES, zona franca, Simples
   *  Nacional, anos de mercado. Declarados porque as telas portadas os
   *  consomem — a Fase 8 decide se a seção correspondente do dossiê é
   *  removida (o mais provável) ou se algum ganha equivalente no nicho do
   *  agro. Ver a aba "Dados", onde a seção PGFN já foi TROCADA pela do
   *  Sicor. */
  nome_fantasia?: string | null;
  cnae_principal?: string | null;
  anos_mercado?: number | null;
  simples_nacional?: boolean | null;
  situacao_cadastral?: string | null;
  divida_ativa_qtd_inscricoes?: number | null;
  divida_ativa_valor_total?: number | null;
  divida_ativa_ajuizado?: boolean | null;
  zona_franca?: boolean | null;
  rqe_confirmado?: boolean | null;
  rqe_fonte?: string | null;
  cnes_codigo?: string | null;

  /** ✅ Sinais do enriquecimento que o backend **desempacota de
   *  `dados_nicho` e envia no topo** desde a Fase 8a (ver `montar_lead_read`
   *  em app/api/routes/leads.py). Confirmado contra a resposta real em
   *  26/08/2026 — não presuma, a lista abaixo foi conferida chave a chave.
   *
   *  ⚠️ **Leia por `getContatos` (contatos.ts), não daqui direto.** Ter dois
   *  caminhos de leitura pro mesmo dado foi exatamente o que produziu o bug
   *  da aba Contatos: uma aba lia `dados_nicho`, a outra lia um campo que
   *  não existe, e elas discordaram sobre o mesmo lead. */
  decisor?: string | null;
  whatsapp_ativo?: boolean | null;
  email_status?: string | null;

  /** ⛔ **Fantasmas do Minotto — a API nunca enviou nenhum destes.**
   *
   *  Continuam declarados porque componentes portados ainda os mencionam, e
   *  removê-los do tipo transformaria cada uso num erro de compilação de uma
   *  vez só. Mas o perigo é justamente este: por serem opcionais, lê-los
   *  devolve `undefined` em silêncio, sem erro de tipo — e a tela conclui
   *  "não tem". Foi assim que `decisor_nome` esvaziou a aba Contatos inteira
   *  de um lead que tinha todos os dados. Antes de usar qualquer um destes,
   *  confirme na resposta real da API. */
  decisor_nome?: string | null;
  email_validado?: boolean | null;
  emails_secundarios?: unknown;
  site_scrape_sucesso?: boolean | null;
  servicos?: string | null;

  /** ⛔ Não existem e não estão no roadmap: LinkedIn nunca foi fonte deste
   *  projeto, e Google Places não foi portado (ver Fase 6). */
  linkedin_empresa?: string | null;
  linkedin_decisor?: string | null;
  google_rating?: number | null;
  google_avaliacoes?: number | null;
}

/** Uma etapa pulada, com motivo — mesmo shape que o backend grava. */
export interface EtapaPulada {
  etapa: string;
  motivo: string;
}

/**
 * O que o backend grava em `Lead.dados_nicho`.
 *
 * Fonte real: `candidato_de_lead_sicor` (app/scoring/pre_selecao.py) mais o
 * `dados_nicho.update({...})` de `persistir_leads` (app/workers/busca.py).
 * Todos opcionais — é JSON livre, e um lead da semente da Receita Federal
 * traz um conjunto diferente do que um lead do Sicor.
 */
export interface DadosNichoSicor {
  origem?: string;
  /** Área da propriedade, em hectares — critério de peso 30. */
  area_ha?: number | null;
  /** Valor financiado na operação mais recente (R$) — peso 10. */
  valor_financiado?: number | null;
  /** Culturas financiadas (união de todas as operações do produtor). */
  culturas?: string[];
  /** Códigos do CAR das propriedades. Dado bônus, sem enriquecimento. */
  codigos_car?: string[];
  /** Anos em que tomou crédito. Mais de um = recorrente. */
  anos_credito?: number[];
  /** `AAAAMMDD` da operação que definiu área e valor (a mais recente). */
  data_operacao?: string | null;
  recorrente?: boolean;
  n_operacoes?: number;
  tipo_beneficiario?: string | null;
  refs_bacen?: string[];
  /** Preenchidos pelo enriquecimento pago (Fase 6). */
  decisor?: string | null;
  fonte_decisor?: string | null;
  whatsapp_ativo?: boolean;
  email_status?: string | null;
  presenca_digital?: number;
  instagram?: string | null;
  site_url?: string | null;
  /** Lado Receita Federal (CNPJ agro). */
  cnae?: string | null;
  cnae_descricao?: string | null;
  situacao_cadastral?: string | null;
  eh_cooperativa?: boolean;
}

/** Mesmo shape de DashboardSummary em app/schemas/dashboard.py. */
export interface DashboardSummary {
  leads_no_mes: number;
  leads_no_mes_limite: number;
  score_medio: number;
  leads_em_negociacao: number;
  taxa_conversao: number;
  total_geracoes_ia_mes: number;
  receita_fechada_pontual: number;
  receita_fechada_recorrente_mensal: number;
  receita_fechada_total: number;
}

/** Mesmo shape de AcaoRecomendada em app/schemas/dashboard.py.
 * `kanban_status_filtro`/`filtro_chave` ainda não são usados pra filtrar
 * de verdade o Kanban no frontend (ver DashboardPage.tsx) -- só
 * persistidos aqui pra quando essa ligação for implementada. */
export interface AcaoRecomendada {
  titulo: string;
  quantidade: number;
  kanban_status_filtro?: string | null;
  filtro_chave: string;
}

/** Mesmo shape de DashboardPremissas em app/schemas/dashboard.py --
 * premissas do Simulador de Receita, por usuário. */
export interface DashboardPremissas {
  leads_qualificados: number;
  taxa_fechamento: number;
  ticket_medio: number;
}

/** Mesmo shape de FunilEtapa em app/schemas/dashboard.py.
 * ⚠️ `percentual` é uma FOTOGRAFIA da distribuição atual dos leads por
 * etapa, não uma taxa de conversão real de coorte -- este projeto não
 * tem histórico de transição de status, só o `kanban_status` atual de
 * cada lead. Ver a mesma ressalva em DashboardPage.tsx (nota exibida
 * abaixo do funil) e no docstring de `get_funil` no backend. */
export interface FunilEtapa {
  status: string;
  label: string;
  quantidade: number;
  percentual: number;
}

/** Mesmo shape de MotivoPerda em app/schemas/dashboard.py -- agrupado
 * por texto exato de `motivo_perda`, sem normalização semântica. */
export interface MotivoPerda {
  motivo: string;
  quantidade: number;
}

/** Mesmo shape de BuscaLeadsRead em app/schemas/busca_leads.py -- um
 * registro de execução do painel admin de "busca mensal" (ver
 * pages/BuscaLeadsPage.tsx). `status` é `"executando" | "concluido" |
 * "erro"` (mesmos valores de StatusBusca no backend), tipado como
 * `string` aqui (não união literal) pelo mesmo motivo de `Lead.kanban_status`
 * já ser `string` solto neste arquivo -- simplicidade, sem necessidade
 * de um union type estrito pra um campo só comparado por igualdade. */
export interface BuscaLeadsRegistro {
  id: string;
  iniciado_por_id: string;
  iniciado_em: string;
  concluido_em?: string | null;
  status: string;
  total_cnpjs_encontrados?: number | null;
  /** Quantos CNPJs passaram na pré-seleção e entraram no enriquecimento
   * pago (top N por score preliminar). Fica entre `encontrados` (o
   * universo) e `processados`. `null` em buscas anteriores a 22/08/2026,
   * quando a pré-seleção não existia. */
  total_cnpjs_selecionados?: number | null;
  total_leads_processados?: number | null;
  erros?: string[] | null;
}

/** Lançada quando o backend devolve 401 -- o token guardado em memória
 * (ver AuthContext) expirou ou é inválido. Quem chama decide o que fazer
 * (tipicamente: deslogar e redirecionar pro /login). */
export class UnauthorizedError extends Error {
  constructor() {
    super("Sessão expirada ou token inválido");
    this.name = "UnauthorizedError";
  }
}

/** Lançada quando o backend devolve 409 -- hoje só `dispararBusca`
 * (POST /api/admin/buscas), quando já existe uma BuscaLeads com
 * status "executando". `message` já vem pronta do `detail` do backend
 * ("Já existe uma busca em andamento, iniciada em ...") -- quem chama
 * mostra direto, não precisa montar uma mensagem própria. */
export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

/** Lançada quando o backend devolve 429 -- limite de gerações de IA
 * atingido pro lead (ver app/api/routes/limites_ia.py). Na prática rara:
 * a tela já desabilita o botão antes. Existe pro caso de corrida (duas
 * abas abertas) e pra não virar um "erro genérico" sem explicação. */
export class LimiteIaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LimiteIaError";
  }
}

/** Lançada quando o backend devolve 429 no LOGIN -- limite de tentativas
 * atingido pra aquele e-mail (ver app/core/rate_limit.py).
 *
 * Separada de `LimiteIaError` (que também é 429) de propósito: são
 * limites de domínios diferentes, com mensagens e ações diferentes, e
 * quem trata um não deve capturar o outro por acidente.
 *
 * `message` carrega o `detail` do backend, que já vem em português e já
 * inclui a janela ("Tente novamente em até 15 minutos") -- a tela mostra
 * esse texto em vez de reescrevê-lo, pra que mudar
 * LOGIN_JANELA_BLOQUEIO_MINUTOS no `.env` não exija deploy do frontend. */
export class RateLimitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RateLimitError";
  }
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const corpo = await res.json();
    if (typeof corpo.detail === "string") return corpo.detail;
    return JSON.stringify(corpo.detail);
  } catch {
    return `Erro ${res.status}`;
  }
}

export async function checkHealth(): Promise<{ status: string } | null> {
  try {
    const res = await fetch(`${API_URL}/health`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function login(email: string, senha: string): Promise<string> {
  const res = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, senha }),
  });
  if (res.status === 429) throw new RateLimitError(await parseErrorDetail(res));
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  const corpo = await res.json();
  return corpo.access_token as string;
}

export async function fetchLeads(token: string): Promise<Lead[]> {
  const res = await fetch(`${API_URL}/api/leads`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(`Erro ao buscar leads: ${res.status}`);
  return res.json();
}

/** Mesmo shape de LeadListaResponse em app/schemas/lead.py -- resposta
 * paginada de GET /api/leads/lista (tela "Lista de Leads"). `items` é a
 * mesma forma de `Lead` já usada em todo o resto do app -- o backend
 * devolve o `LeadRead` completo por item, não um subconjunto próprio
 * pra essa tela. */
export interface LeadListaResposta {
  items: Lead[];
  total: number;
  pagina: number;
  por_pagina: number;
}

export interface LeadListaParametros {
  busca?: string;
  prioridade?: string;
  kanban_status?: string;
  ordenar_por?: "score_total" | "created_at";
  ordem?: "asc" | "desc";
  pagina?: number;
  por_pagina?: number;
}

export async function fetchLeadsLista(token: string, parametros: LeadListaParametros): Promise<LeadListaResposta> {
  const query = new URLSearchParams();
  if (parametros.busca) query.set("busca", parametros.busca);
  if (parametros.prioridade) query.set("prioridade", parametros.prioridade);
  if (parametros.kanban_status) query.set("kanban_status", parametros.kanban_status);
  if (parametros.ordenar_por) query.set("ordenar_por", parametros.ordenar_por);
  if (parametros.ordem) query.set("ordem", parametros.ordem);
  if (parametros.pagina) query.set("pagina", String(parametros.pagina));
  if (parametros.por_pagina) query.set("por_pagina", String(parametros.por_pagina));

  const res = await fetch(`${API_URL}/api/leads/lista?${query.toString()}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchLead(token: string, leadId: string): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchMensagens(token: string, leadId: string): Promise<LeadMessage[]> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/mensagens`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function gerarAbordagemCanal(token: string, leadId: string, canal: CanalAbordagem): Promise<LeadMessage> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/gerar-abordagem/${canal}`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (res.status === 429) throw new LimiteIaError(await parseErrorDetail(res));
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function gerarInsights(token: string, leadId: string): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/gerar-insights`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (res.status === 429) throw new LimiteIaError(await parseErrorDetail(res));
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchDashboardSummary(token: string): Promise<DashboardSummary> {
  const res = await fetch(`${API_URL}/api/dashboard/summary`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchAcoesRecomendadas(token: string): Promise<AcaoRecomendada[]> {
  const res = await fetch(`${API_URL}/api/dashboard/acoes-recomendadas`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchPremissas(token: string): Promise<DashboardPremissas> {
  const res = await fetch(`${API_URL}/api/dashboard/premissas`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function salvarPremissas(token: string, premissas: DashboardPremissas): Promise<DashboardPremissas> {
  const res = await fetch(`${API_URL}/api/dashboard/premissas`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(premissas),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchFunil(token: string): Promise<FunilEtapa[]> {
  const res = await fetch(`${API_URL}/api/dashboard/funil`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchMotivosPerda(token: string): Promise<MotivoPerda[]> {
  const res = await fetch(`${API_URL}/api/dashboard/motivos-perda`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function dispararBusca(token: string): Promise<BuscaLeadsRegistro> {
  const res = await fetch(`${API_URL}/api/admin/buscas`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (res.status === 409) throw new ConflictError(await parseErrorDetail(res));
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchBuscas(token: string): Promise<BuscaLeadsRegistro[]> {
  const res = await fetch(`${API_URL}/api/admin/buscas`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchBusca(token: string, buscaId: string): Promise<BuscaLeadsRegistro> {
  const res = await fetch(`${API_URL}/api/admin/buscas/${buscaId}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

/** Mesmos 3 campos que `PATCH /{lead_id}/status` exige quando
 * `kanban_status == "ganho"` (ver app/schemas/lead.py::LeadStatusUpdate
 * e o docstring da rota) -- espelha `motivo_perda` do lado positivo. */
export interface DadosFechamento {
  servicos_vendidos: string[];
  tipo_contrato: "pontual" | "recorrente";
  valor_fechamento: number;
}

export async function updateLeadStatus(
  token: string,
  leadId: string,
  kanban_status: string,
  motivo_perda?: string,
  dadosFechamento?: DadosFechamento,
): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ kanban_status, motivo_perda, ...dadosFechamento }),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

/**
 * Lê `Lead.dados_nicho` de forma defensiva.
 *
 * `dados_nicho` chega pela rede via `fetch().json()`, que o TypeScript tipa
 * como `any` — o compilador não garante nada em runtime. Mesmo padrão de
 * `getScoreBreakdown` (leadScore.ts) e da lição do §6 sobre parser
 * defensivo: shape inesperado vira objeto vazio, tratado pela UI como
 * "sem dado do Sicor", nunca como erro.
 */
export function getDadosNicho(dadosNicho: unknown): DadosNichoSicor {
  if (dadosNicho && typeof dadosNicho === "object" && !Array.isArray(dadosNicho)) {
    return dadosNicho as DadosNichoSicor;
  }
  return {};
}

/**
 * Lê `Lead.etapas_puladas` de forma defensiva.
 *
 * ⚠️ `null` e `[]` significam coisas diferentes e a UI precisa distinguir:
 * `null` = o pipeline nunca rodou pra esse lead; `[]` = rodou e não pulou
 * nada. Esta função devolve `[]` nos dois casos — quem precisa da distinção
 * checa `lead.etapas_puladas === null` antes.
 */
export function getEtapasPuladas(etapas: unknown): EtapaPulada[] {
  if (!Array.isArray(etapas)) return [];
  return etapas.filter(
    (e): e is EtapaPulada =>
      !!e && typeof e === "object" && typeof (e as EtapaPulada).etapa === "string",
  );
}
