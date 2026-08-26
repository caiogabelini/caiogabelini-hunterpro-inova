import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  ExternalLink,
  FileText,
  Gauge,
  Globe,
  Lightbulb,
  Link2,
  Loader2,
  Mail,
  MessageCircle,
  MessageSquare,
  Phone,
  Send,
  Sparkles,
  Target,
  Trophy,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  fetchLead,
  fetchMensagens,
  gerarAbordagemCanal,
  gerarInsights,
  LimiteIaError,
  UnauthorizedError,
  type CanalAbordagem,
  type InsightsIA,
  type Lead,
  type LeadMessage,
} from "../api";
import { PriorityBadge } from "../components/PriorityBadge";
import { useAuth } from "../context/AuthContext";
import { formatarNumeroWhatsapp, formatCurrencyBRL, formatDate, formatRelative } from "../format";
import { getEmailsSecundarios, labelTipoEmail } from "../emailsSecundarios";
import { INSIGHTS_DISPONIVEIS, getInsights } from "../insights";
import { MENSAGEM_LIMITE_ATINGIDO, statusLimiteIa } from "../limitesIa";
import { deveAvisarSiteNaoLido } from "../siteScrape";
import { KANBAN_COLUMNS, STATUS_GANHO, STATUS_PERDIDO } from "../kanbanStatuses";
import { criteriosExibiveis, getScoreBreakdown } from "../leadScore";
import { SIGNAL_LAYER_LABELS } from "../scoreLayers";
import { labelServicoFechamento } from "../servicosFechamento";
import "./LeadDossierPage.css";
import { formatarDocumento, rotuloDocumento, rotuloEntidade, rotuloNome } from "../documento";
import { MENSAGENS_DISPONIVEIS, carregarMensagens } from "../mensagens";
import { getDadosNicho, type DadosNichoSicor } from "../api";

type Aba = "dados" | "contatos" | "analise" | "mensagens" | "insights";

const ABAS: { id: Aba; label: string; icon: LucideIcon }[] = [
  { id: "dados", label: "Dados", icon: FileText },
  { id: "contatos", label: "Contatos", icon: Users },
  { id: "analise", label: "Análise", icon: Gauge },
  { id: "mensagens", label: "Mensagens", icon: MessageSquare },
  { id: "insights", label: "Insights", icon: Lightbulb },
];

export function LeadDossierPage() {
  const { id } = useParams<{ id: string }>();
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [lead, setLead] = useState<Lead | null>(null);
  const [mensagens, setMensagens] = useState<LeadMessage[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [abaAtiva, setAbaAtiva] = useState<Aba>("dados");

  useEffect(() => {
    if (!token || !id) return;

    // ⚠️ **Duas cargas SEPARADAS, de propósito.**
    //
    // Isto era um `Promise.all([fetchLead, fetchMensagens])`. Como
    // `Promise.all` rejeita assim que qualquer promessa rejeita, o 404 de
    // `GET /api/leads/{id}/mensagens` — rota que não existe, ver
    // `mensagens.ts` — derrubava o resultado inteiro: a tela mostrava
    // "Error: Not Found" para TODO lead, inclusive os que o `fetchLead`
    // tinha devolvido com 200.
    //
    // O lead é ESSENCIAL: sem ele não há dossiê, e o erro é real. As
    // mensagens são OPCIONAIS e carregam o próprio tratamento de erro, sem
    // poder de veto sobre a tela.
    let cancelado = false;

    fetchLead(token, id)
      .then((leadData) => {
        if (!cancelado) setLead(leadData);
      })
      .catch((e) => {
        if (cancelado) return;
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => {
        if (!cancelado) setCarregando(false);
      });

    // Nunca rejeita — no pior caso devolve [].
    carregarMensagens(() => fetchMensagens(token, id)).then((m) => {
      if (!cancelado) setMensagens(m);
    });

    return () => {
      cancelado = true;
    };
  }, [token, id, logout]);

  function registrarNovaMensagem(mensagem: LeadMessage) {
    setMensagens((atuais) => [mensagem, ...atuais.filter((m) => m.canal !== mensagem.canal)]);
    // Incrementa a contagem local do canal pra que o botão desabilite
    // sozinho ao bater o limite, sem exigir um refetch do lead.
    //
    // `POST /gerar-abordagem/{canal}` devolve o LeadMessage criado, não o
    // Lead -- diferente de `POST /gerar-insights`, que devolve o Lead
    // inteiro já com `geracoes_ia` atualizado (por isso lá basta
    // `setLead`). O incremento aqui espelha o que o backend acabou de
    // contar; na próxima carga do dossiê o valor vem do servidor de novo.
    setLead((atual) => {
      if (!atual) return atual;
      const anteriores =
        typeof atual.geracoes_ia === "object" && atual.geracoes_ia !== null
          ? (atual.geracoes_ia as Record<string, number>)
          : null;
      if (!anteriores) return atual;
      return {
        ...atual,
        geracoes_ia: { ...anteriores, [mensagem.canal]: (anteriores[mensagem.canal] ?? 0) + 1 },
      };
    });
  }

  return (
    <div className="dossier-page">
      <button className="dossier-voltar" onClick={() => navigate("/")}>
        <ArrowLeft size={15} />
        <span>Voltar pro Kanban</span>
      </button>

      {carregando && <p className="dossier-status">Carregando...</p>}
      {erro && <p className="dossier-status dossier-erro">{erro}</p>}

      {lead && (
        <>
          <DossierHeader lead={lead} />

          <AbaNav ativa={abaAtiva} onChange={setAbaAtiva} />

          {abaAtiva === "dados" && <AbaDados lead={lead} />}

          {abaAtiva === "contatos" && <AbaContatos lead={lead} />}

          {abaAtiva === "analise" && (
            <AbaAnalise scoreDetalhes={lead.score_detalhes} />
          )}

          {abaAtiva === "mensagens" && (
            <SecaoAbordagem
              leadId={lead.id}
              email={lead.email}
              temWhatsapp={!!lead.whatsapp_ativo}
              telefone={lead.telefone}
              mensagens={mensagens}
              onGerada={registrarNovaMensagem}
              geracoesIa={lead.geracoes_ia}
            />
          )}

          {abaAtiva === "insights" && (
            <AbaInsights
              leadId={lead.id}
              insights={getInsights(lead.insights_ia)}
              geradoEm={lead.insights_gerado_em}
              onGerado={setLead}
              geracoesIa={lead.geracoes_ia}
            />
          )}
        </>
      )}
    </div>
  );
}

// --- Cabeçalho (fixo, fora das abas) ---------------------------------------

// Mapeamento simples A/B/C -> rótulo de classificação, derivado da
// `prioridade` que já existe no lead (nenhum campo novo). "Excelente"/
// "Bom"/"Regular" são só um rótulo mais legível pro mesmo valor que o
// badge de prioridade já mostra.
const CLASSIFICACAO_LABELS: Record<string, string> = {
  A: "Excelente",
  B: "Bom",
  C: "Regular",
};

// Nicho é fixo pra todo lead deste projeto (produtores de grãos do PR,
// ver docs_fundacao.md). Rótulo genérico de propósito: o mesmo texto
// serve pro CPF (produtor pessoa física, ~98% do universo) e pro CNPJ
// (cooperativa/empresa rural). Cultura específica (soja/milho) NÃO entra
// aqui -- ela varia por lead e já aparece em SecaoSicor, lida de
// `dados_nicho`; repetir no cabeçalho seria inventar um resumo que pode
// contradizer a seção logo abaixo.
const NICHO_FIXO = "Agronegócio";

/** WhatsApp antes de e-mail -- mesma preferência já assumida no resto
 * do dossiê (a ação rápida de WhatsApp aparece antes da de e-mail na
 * seção de Contato). Só considera canal *confirmado* (whatsapp_ativo/
 * email_validado), não a mera presença do dado. */
function canalPreferido(lead: Lead): string {
  if (lead.whatsapp_ativo) return "WhatsApp";
  if (lead.email_validado) return "E-mail";
  return "—";
}

function DossierHeader({ lead }: { lead: Lead }) {
  const classificacao = lead.prioridade ? CLASSIFICACAO_LABELS[lead.prioridade.toUpperCase()] ?? lead.prioridade : "—";

  return (
    <header className="dossier-header">
      <div className="dossier-header-principal">
        <div className="dossier-eyebrow">Dossiê do lead</div>
        <h1 className="dossier-razao">{lead.nome}</h1>
        {lead.nome_fantasia && <p className="dossier-fantasia">{lead.nome_fantasia}</p>}
        {/* CPF-aware: o rótulo e a máscara mudam conforme tipo_documento.
            No Minotto era `{lead.cnpj}` cru, sem máscara e sempre CNPJ. */}
        <p className="dossier-documento">
          <span className="dossier-documento-rotulo">{rotuloDocumento(lead.tipo_documento)}</span>{" "}
          {formatarDocumento(lead.documento, lead.tipo_documento)}
        </p>

        <p className="dossier-secundaria">
          {lead.municipio ? `${lead.municipio}/${lead.uf ?? "—"}` : "Localização não informada"} · {NICHO_FIXO}
        </p>

        {/* "Score:" saiu daqui -- o número grande no canto (dossier-header-score,
            à direita) já mostra o mesmo dado com muito mais destaque
            visual; manter os dois seria redundante (julgamento pedido
            explicitamente pelo usuário nesta sessão). */}
        <div className="dossier-resumo">
          <span>
            Classificação: <strong>{classificacao}</strong>
          </span>
          <span className="dossier-resumo-sep">·</span>
          <span>Prioridade: {lead.prioridade ? <PriorityBadge prioridade={lead.prioridade} /> : <strong>—</strong>}</span>
          <span className="dossier-resumo-sep">·</span>
          <span>
            Canal: <strong>{canalPreferido(lead)}</strong>
          </span>
        </div>

        <div className="dossier-header-footer">
          <KanbanStatusBadge status={lead.kanban_status ?? "novo_lead"} motivoPerda={lead.motivo_perda} />
          <p className="dossier-datas">
            Encontrado em {formatDate(lead.created_at)} · Atualizado {formatRelative(lead.updated_at)}
          </p>
        </div>
      </div>

      {/* Número de score "hero" -- de volta no canto superior direito
          do cabeçalho, do jeito que já existia antes da reestruturação
          em abas (badge de prioridade grande acima, número gigante com
          brilho dourado, label embaixo). A aba "Análise" não repete
          mais esse bloco (ver AbaAnalise) -- repetir seria redundante,
          já que agora ele fica visível o tempo todo aqui no cabeçalho,
          em toda aba, não só na Análise. */}
      <div className="dossier-header-score">
        <PriorityBadge prioridade={lead.prioridade} size="lg" />
        <div className="dossier-score-total">{lead.score ?? "—"}</div>
        <div className="dossier-score-label">pontos de score</div>
      </div>
    </header>
  );
}

function KanbanStatusBadge({ status, motivoPerda }: { status: string; motivoPerda?: string | null }) {
  const label = KANBAN_COLUMNS.find((c) => c.status === status)?.label ?? status;
  const perdido = status === STATUS_PERDIDO;
  return (
    <div className="dossier-kanban-status">
      <span className={`dossier-kanban-pill ${perdido ? "dossier-kanban-pill-perdido" : ""}`}>{label}</span>
      {perdido && motivoPerda && <span className="dossier-motivo-perda">Motivo: {motivoPerda}</span>}
    </div>
  );
}

// --- Navegação por abas ------------------------------------------------

function AbaNav({ ativa, onChange }: { ativa: Aba; onChange: (aba: Aba) => void }) {
  return (
    <div className="dossier-tabs" role="tablist">
      {ABAS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={ativa === id}
          className={`dossier-tab ${ativa === id ? "dossier-tab-ativa" : ""}`}
          onClick={() => onChange(id)}
        >
          <Icon size={15} />
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

// --- Aba "Dados" ---------------------------------------------------------
// Layout, de cima pra baixo:
//   1. SecaoSicor  -- crédito rural (área, cultura, valor, recorrência).
//      Abre a aba por ser o sinal mais crítico DESTE nicho, mesmo lugar
//      que a dívida ativa PGFN ocupa no Minotto (ver a nota dentro de
//      AbaDados sobre a troca).
//   2. dossier-grid -- 3 cards: Dados cadastrais / Contato / Presença
//      digital. Campos inalterados em relação ao Minotto, tirando os
//      rótulos CPF-aware (rotuloNome/rotuloDocumento/rotuloEntidade).
//   3. SecaoFechamento -- só renderiza com kanban_status = ganho, que
//      hoje nenhuma rota preenche (Fase 8b).
// O 4º card "RQE / Especialidade" do Minotto não existe aqui: RQE é
// sinal do nicho de saúde, sem equivalente no agro.

function AbaDados({ lead }: { lead: Lead }) {
  const nicho = getDadosNicho(lead.dados_nicho);
  return (
    <>
      {/* ⚠️ Aqui ficava a SecaoPgfn (dívida ativa) do Minotto — o sinal
          mais crítico do nicho de saúde. Não existe na Inova: o critério
          #1 da Carolina é tamanho de propriedade, e a fonte é o Sicor.
          A seção foi TROCADA, não adaptada — os campos abaixo vêm de
          `dados_nicho`, gravado por `persistir_leads` (app/workers/busca.py)
          a partir de `candidato_de_lead_sicor`. */}
      <SecaoSicor nicho={nicho} />

      <div className="dossier-grid">
        <section className="dossier-card">
          <SectionHeading icon={Building2}>Dados cadastrais</SectionHeading>
          <dl>
            {/* CPF-aware: "Nome do produtor" (PF) vs "Razão social" (PJ). */}
            <Campo label={rotuloNome(lead.tipo_documento)} valor={lead.nome} />
            <Campo
              label={rotuloDocumento(lead.tipo_documento)}
              valor={formatarDocumento(lead.documento, lead.tipo_documento)}
            />
            <Campo label="Tipo" valor={rotuloEntidade(lead.tipo_documento)} />
            <Campo label="Município/UF" valor={lead.municipio ? `${lead.municipio}/${lead.uf ?? "—"}` : null} />
            {/* Só faz sentido pro lado CNPJ (semente da Receita Federal). */}
            <Campo label="CNAE" valor={nicho.cnae_descricao ?? nicho.cnae} />
            <Campo label="Situação cadastral" valor={nicho.situacao_cadastral} />
            <Campo label="Cooperativa" valor={boolLabel(nicho.eh_cooperativa)} />
          </dl>
        </section>

        <section className="dossier-card">
          <SectionHeading icon={Phone}>Contato</SectionHeading>
          <dl>
            <Campo label="Telefone" valor={lead.telefone} />
            <CampoWhatsapp telefone={lead.telefone} ativo={nicho.whatsapp_ativo} />
            <CampoEmail email={lead.email} validado={nicho.email_status === "valid" || nicho.email_status === "catch-all"} />
            <Campo label="Decisor" valor={nicho.decisor} />
            <Campo label="Fonte do decisor" valor={nicho.fonte_decisor} />
          </dl>
        </section>

        <section className="dossier-card">
          <SectionHeading icon={Globe}>Presença digital</SectionHeading>
          {/* Regra em `siteScrape.ts` (com testes): só avisa quando o
              scrape de fato FALHOU. `null` = a etapa nem rodou, e aí os
              campos vazios já se explicam pela ausência de site. */}
          {deveAvisarSiteNaoLido(lead.site_scrape_sucesso) && (
            <p className="dossier-aviso-scrape">
              <AlertTriangle size={14} aria-hidden="true" />
              <span>
                Não conseguimos ler o site desta empresa — os dados de presença digital
                podem estar incompletos.
              </span>
            </p>
          )}
          <dl>
            <Campo label="Site" valor={lead.site} link={lead.site} />
            <Campo label="Instagram" valor={nicho.instagram} />
            {/* Intensidade 0–1 classificada pela IA sobre o markdown do site.
                ⚠️ Hoje é 0,0 pra todo lead: sem Google Places não há fonte de
                site, então a IA nunca é consultada (ver Fase 6). */}
            <Campo
              label="Presença digital"
              valor={nicho.presenca_digital != null ? `${(nicho.presenca_digital * 100).toFixed(0)}%` : null}
            />
            {/* ⛔ LinkedIn e Google Places não são fontes deste projeto. */}
          </dl>
        </section>
      </div>

      <SecaoFechamento lead={lead} />
    </>
  );
}

// ⚠️ `SecaoPgfn` do Minotto foi REMOVIDA nesta adaptação — dívida ativa
// da PGFN é o sinal do nicho de saúde e não existe na Inova. O lugar dela
// na aba "Dados" é ocupado por `SecaoSicor` (crédito rural).

function SecaoFechamento({ lead }: { lead: Lead }) {
  if (lead.kanban_status !== STATUS_GANHO) return null;
  if (!lead.servicos_vendidos?.length || !lead.tipo_contrato || lead.valor_fechamento == null) return null;

  const rotuloValor = lead.tipo_contrato === "recorrente" ? "Valor mensal" : "Valor único";

  return (
    <section className="dossier-card dossier-fechamento">
      <SectionHeading icon={Trophy}>Fechamento</SectionHeading>
      <dl>
        <Campo label="Serviços vendidos" valor={lead.servicos_vendidos.map(labelServicoFechamento).join(", ")} />
        <Campo label="Tipo de contrato" valor={lead.tipo_contrato === "recorrente" ? "Recorrente" : "Pontual"} />
        <Campo label={rotuloValor} valor={formatCurrencyBRL(lead.valor_fechamento)} />
      </dl>
    </section>
  );
}

// ⚠️ `RqeResumoBadge` do Minotto foi REMOVIDO nesta adaptação, mesma
// razão de `SecaoPgfn`: RQE (Registro de Qualificação de Especialista,
// confirmado via CNES) é sinal do nicho de saúde e não tem equivalente
// no agro — os campos `rqe_confirmado`/`rqe_fonte` nunca chegam pela API
// desta base. Não foi trocado por outro badge: inventar um sinal novo na
// linha de resumo seria decisão de produto, não correção de texto.

// --- Aba "Contatos" --------------------------------------------------------
// Mesmo dado do decisor (decisor_nome, email, whatsapp_ativo,
// linkedin_decisor) que já aparece na aba "Dados", só apresentado de um
// jeito diferente: como um card de pessoa/contato com os canais dele em
// chips clicáveis, em vez de uma linha de spec sheet. Estruturado como
// lista (<ul>) mesmo só tendo hoje um único contato possível
// (decisor_nome é um campo único no Lead) -- comporta mais contatos no
// futuro sem mudar a estrutura, só o array de origem.

// Lista dos e-mails que o `domain-search` do Hunter achou no domínio
// além do escolhido como principal. Complemento opcional: quando não há
// nenhum, não renderiza NADA (nem título, nem estado vazio) -- não é uma
// informação que o vendedor esteja esperando encontrar, então um "nenhum
// e-mail secundário" só ocuparia espaço.
//
// Só exibição por ora: sem botão de copiar, sem mailto. O principal já
// tem esses recursos na aba Dados; estes aqui são pistas de contato, não
// o canal escolhido.
function OutrosEmails({ lead }: { lead: Lead }) {
  const emails = getEmailsSecundarios(lead.emails_secundarios);
  if (emails.length === 0) return null;

  return (
    <div className="dossier-outros-emails">
      <h3 className="dossier-outros-emails-titulo">Outros e-mails encontrados</h3>
      <ul className="dossier-outros-emails-lista">
        {emails.map((item) => (
          <li key={item.email} className="dossier-outros-emails-item">
            <span className="dossier-outros-emails-endereco">{item.email}</span>
            <span
              className={
                item.tipo === "personal"
                  ? "dossier-email-tipo dossier-email-tipo-pessoal"
                  : "dossier-email-tipo"
              }
            >
              {labelTipoEmail(item.tipo)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AbaContatos({ lead }: { lead: Lead }) {
  const numeroWhatsapp = formatarNumeroWhatsapp(lead.telefone);
  const temWhatsapp = !!lead.whatsapp_ativo && !!numeroWhatsapp;
  const temEmail = !!lead.email;
  const temLinkedin = !!lead.linkedin_decisor;

  if (!lead.decisor_nome) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Users}>Contatos</SectionHeading>
        <p className="dossier-muted">Nenhum decisor identificado ainda pra este lead.</p>
        {/* Renderizado TAMBÉM aqui de propósito: os e-mails secundários
            são do DOMÍNIO, não do decisor. Um lead sem decisor
            identificado mas com contato@clinica.com.br é justamente onde
            eles mais valem -- escondê-los junto com o resto da aba seria
            perder o único contato disponível. */}
        <OutrosEmails lead={lead} />
      </section>
    );
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Users}>Contatos</SectionHeading>
      <ul className="dossier-contatos-lista">
        <li className="dossier-contato-card">
          <div className="dossier-contato-nome">{lead.decisor_nome}</div>
          <div className="dossier-contato-canais">
            {temWhatsapp && (
              <a className="dossier-chip" href={`https://wa.me/${numeroWhatsapp}`} target="_blank" rel="noreferrer">
                <MessageCircle size={13} />
                <span>WhatsApp</span>
              </a>
            )}
            {temEmail && (
              <a className="dossier-chip" href={`mailto:${lead.email}`}>
                <Mail size={13} />
                <span>E-mail</span>
              </a>
            )}
            {lead.linkedin_decisor && (
              // Link2 (ícone genérico), não um logo da LinkedIn -- essa
              // versão do lucide-react (1.33) não exporta mais ícones de
              // marca (removidos por questão de trademark); o texto "LinkedIn"
              // ao lado do ícone já deixa claro o canal.
              <a className="dossier-chip" href={lead.linkedin_decisor} target="_blank" rel="noreferrer">
                <Link2 size={13} />
                <span>LinkedIn</span>
              </a>
            )}
            {!temWhatsapp && !temEmail && !temLinkedin && (
              <span className="dossier-muted">Nenhum canal confirmado ainda.</span>
            )}
          </div>
        </li>
      </ul>
      <OutrosEmails lead={lead} />
    </section>
  );
}

// --- Aba "Análise" --------------------------------------------------
// O bloco "hero" de score (badge de prioridade grande + número gigante
// + brilho dourado) voltou pro cabeçalho fixo (ver DossierHeader) nesta
// sessão -- não repetido aqui pra não duplicar o mesmo elemento visual
// em todo lugar. Esta aba foca no que só ela tem: o breakdown dos
// critérios e os "Sinais positivos" derivados dele. São 7 linhas, não 9:
// os 2 critérios de peso 0 ficam de fora da EXIBIÇÃO (ver
// criteriosExibiveis em leadScore.ts) -- o backend continua devolvendo
// os 9. "Sinais positivos"
// é reorganização do próprio breakdown, não é uma chamada de IA nova:
// qualquer critério que bateu o peso máximo dele vira um chip.

function AbaAnalise({ scoreDetalhes }: { scoreDetalhes: unknown }) {
  // `todos` guarda o que o backend mandou; `breakdown` é o que a tela
  // mostra. A distinção importa pro estado vazio mais abaixo: "nenhum
  // critério exibível" e "score nem calculado ainda" são coisas
  // diferentes e não devem virar a mesma mensagem.
  const todos = getScoreBreakdown(scoreDetalhes);
  const breakdown = criteriosExibiveis(todos);
  // Critérios de peso 0 já saíram no filtro acima -- nunca deveriam
  // aparecer como "sinal positivo" de nada, mesmo pontuando 0/0.
  // points >= weight - 0.01 tolera arredondamento de ponto flutuante
  // vindo do backend (ex.: 6 × 20/6 pode chegar como 19.999999999998,
  // não exatamente 20.0).
  const sinaisPositivos = breakdown.filter((item) => item.points >= item.weight - 0.01);

  return (
    <section className="dossier-card dossier-analise">
      {sinaisPositivos.length > 0 && (
        <div className="dossier-sinais-positivos">
          <h3 className="dossier-subheading">
            <CheckCircle2 size={15} />
            <span>Sinais positivos</span>
          </h3>
          <ul className="dossier-sinais-lista">
            {sinaisPositivos.map((item) => (
              <li key={item.key} className="dossier-sinal-chip">
                {item.label}
              </li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="dossier-subheading">
        <Gauge size={15} />
        <span>Breakdown do score</span>
      </h3>
      {breakdown.length ? (
        <ul className="dossier-breakdown-list">
          {breakdown.map((item) => (
            <li key={item.key} className={`dossier-breakdown-item layer-${item.layer}`}>
              <span className="dossier-breakdown-layer-tag">{SIGNAL_LAYER_LABELS[item.layer] ?? item.layer}</span>
              <span className="dossier-breakdown-label">{item.label}</span>
              <span className="dossier-breakdown-pontos">
                {formatPontos(item.points)}/{item.weight} pts
              </span>
            </li>
          ))}
        </ul>
      ) : todos.length ? (
        <p className="dossier-muted">Nenhum critério com peso nesta versão do score.</p>
      ) : (
        <p className="dossier-muted">Score ainda não calculado pra este lead.</p>
      )}
    </section>
  );
}

// --- Aba "Mensagens" (abordagem sugerida) -----------------------------

function SecaoAbordagem({
  leadId,
  email,
  temWhatsapp,
  telefone,
  mensagens,
  onGerada,
  geracoesIa,
}: {
  leadId: string;
  email?: string | null;
  temWhatsapp: boolean;
  telefone?: string | null;
  mensagens: LeadMessage[];
  onGerada: (mensagem: LeadMessage) => void;
  geracoesIa: unknown;
}) {
  // ⚠️ Enquanto a geração de mensagem por IA não existir no backend, os
  // botões abaixo bateriam em `POST /gerar-abordagem/{canal}` — que também
  // não existe — e dariam o mesmo 404 que acabou de ser corrigido no
  // carregamento. Melhor dizer que a funcionalidade não faz parte desta
  // versão do que oferecer um botão que só sabe falhar.
  if (!MENSAGENS_DISPONIVEIS) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Sparkles}>Abordagem sugerida</SectionHeading>
        <p className="dossier-muted">
          A geração de mensagem por IA não faz parte desta versão. Os contatos
          do lead estão na aba <strong>Contatos</strong>.
        </p>
      </section>
    );
  }

  const mostrarEmail = !!email;
  // WhatsApp aqui exige o sinal *confirmado ativo* (whatsapp_ativo),
  // não só ter um telefone cadastrado -- gerar mensagem pra um número
  // que nem confirmamos ter WhatsApp não faz sentido.
  const mostrarWhatsapp = temWhatsapp;

  if (!mostrarEmail && !mostrarWhatsapp) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Sparkles}>Abordagem sugerida</SectionHeading>
        <p className="dossier-muted">Nenhum canal disponível ainda (sem e-mail nem WhatsApp confirmado pra este lead).</p>
      </section>
    );
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Sparkles}>Abordagem sugerida</SectionHeading>
      <div className="dossier-abordagem-canais">
        {mostrarEmail && (
          <CanalAbordagemCard
            leadId={leadId}
            canal="email"
            titulo="E-mail"
            mensagem={mensagens.find((m) => m.canal === "email") ?? null}
            onGerada={onGerada}
            email={email}
            geracoesIa={geracoesIa}
          />
        )}
        {mostrarWhatsapp && (
          <CanalAbordagemCard
            leadId={leadId}
            canal="whatsapp"
            titulo="WhatsApp"
            mensagem={mensagens.find((m) => m.canal === "whatsapp") ?? null}
            onGerada={onGerada}
            telefone={telefone}
            geracoesIa={geracoesIa}
          />
        )}
      </div>
    </section>
  );
}

function CanalAbordagemCard({
  leadId,
  canal,
  titulo,
  mensagem,
  onGerada,
  email,
  telefone,
  geracoesIa,
}: {
  leadId: string;
  canal: CanalAbordagem;
  titulo: string;
  mensagem: LeadMessage | null;
  onGerada: (mensagem: LeadMessage) => void;
  email?: string | null;
  telefone?: string | null;
  geracoesIa: unknown;
}) {
  const { token, logout } = useAuth();
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function gerar() {
    if (!token) return;
    setGerando(true);
    setErro(null);
    try {
      const nova = await gerarAbordagemCanal(token, leadId, canal);
      onGerada(nova);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro(
        e instanceof LimiteIaError
          ? e.message
          : "Não foi possível gerar a mensagem agora. Tente novamente.",
      );
    } finally {
      setGerando(false);
    }
  }

  const limite = statusLimiteIa(geracoesIa, canal);

  const numeroWhatsapp = formatarNumeroWhatsapp(telefone);

  return (
    <div className="dossier-abordagem-canal">
      <h3 className="dossier-abordagem-canal-titulo">{titulo}</h3>

      {mensagem ? (
        <>
          {canal === "email" && mensagem.assunto && (
            <p className="dossier-abordagem-assunto">
              <strong>Assunto:</strong> {mensagem.assunto}
            </p>
          )}
          <p className="dossier-abordagem-texto">{mensagem.conteudo}</p>
          <div className="dossier-abordagem-acoes">
            {canal === "email" && email && (
              <a
                className="dossier-abordagem-btn-acao"
                href={`mailto:${email}?subject=${encodeURIComponent(mensagem.assunto ?? "")}&body=${encodeURIComponent(mensagem.conteudo)}`}
              >
                <Mail size={14} />
                <span>Abrir no e-mail</span>
              </a>
            )}
            {canal === "whatsapp" && numeroWhatsapp && (
              <a
                className="dossier-abordagem-btn-acao"
                href={`https://wa.me/${numeroWhatsapp}?text=${encodeURIComponent(mensagem.conteudo)}`}
                target="_blank"
                rel="noreferrer"
              >
                <Send size={14} />
                <span>Enviar mensagem gerada</span>
              </a>
            )}
            {limite.atingido ? (
              <p className="dossier-limite-ia">{MENSAGEM_LIMITE_ATINGIDO}</p>
            ) : (
              <button
                type="button"
                className="dossier-abordagem-btn-secundario"
                onClick={gerar}
                disabled={gerando}
                aria-busy={gerando}
              >
                {gerando ? <Loader2 size={14} className="spin" aria-label="Gerando..." /> : "Gerar novamente"}
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <p className="dossier-muted">Mensagem ainda não gerada.</p>
          {limite.atingido ? (
            <p className="dossier-limite-ia">{MENSAGEM_LIMITE_ATINGIDO}</p>
          ) : (
            <button type="button" className="dossier-abordagem-btn" onClick={gerar} disabled={gerando} aria-busy={gerando}>
              {gerando ? <Loader2 size={15} className="spin" aria-label="Gerando..." /> : "Gerar mensagem com IA"}
            </button>
          )}
        </>
      )}
      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </div>
  );
}

// --- Aba "Insights" (análise estratégica via IA) ------------------------
// Mesmo padrão de UX de "Gerar"/"Gerando..."/"Gerar novamente" já usado em
// CanalAbordagemCard (Abordagem sugerida). Diferença: a rota
// `POST /gerar-insights` devolve o Lead INTEIRO já atualizado (não um
// objeto isolado como LeadMessage), então `onGerado` recebe o Lead
// completo -- quem chama (LeadDossierPage) só faz `setLead` direto, sem
// precisar mesclar `insights_ia`/`insights_gerado_em` manualmente.

const POTENCIAL_LABELS: Record<string, string> = {
  alto: "Alto potencial",
  médio: "Médio potencial",
  baixo: "Baixo potencial",
};

/** Verde=alto, dourado=médio, cinza=baixo -- mesma linguagem de cor do
 * resto do dossiê (verde/dourado/cinza já usados em indicador-on/
 * rqe-alta-confianca/layer-estruturado etc.). Um valor fora dos 3
 * esperados (a IA não é forçada a um enum, ver InsightsIA em api.ts) cai
 * no badge neutro/cinza "desconhecido" em vez de quebrar a tela. */
function PotencialBadge({ potencial }: { potencial: string }) {
  const chave = potencial.toLowerCase();
  const classe =
    chave === "alto" ? "potencial-alto" : chave === "médio" ? "potencial-medio" : chave === "baixo" ? "potencial-baixo" : "potencial-desconhecido";
  return <span className={`dossier-potencial-badge ${classe}`}>{POTENCIAL_LABELS[chave] ?? potencial}</span>;
}

function AbaInsights({
  leadId,
  insights,
  geradoEm,
  onGerado,
  geracoesIa,
}: {
  leadId: string;
  insights: InsightsIA | null;
  geradoEm?: string | null;
  onGerado: (lead: Lead) => void;
  geracoesIa: unknown;
}) {
  const { token, logout } = useAuth();
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function gerar() {
    if (!token) return;
    setGerando(true);
    setErro(null);
    try {
      const leadAtualizado = await gerarInsights(token, leadId);
      onGerado(leadAtualizado);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro(
        e instanceof LimiteIaError
          ? e.message
          : "Não foi possível gerar os insights agora. Tente novamente.",
      );
    } finally {
      setGerando(false);
    }
  }

  // ⚠️ Mesmo tratamento dado à aba Mensagens, pelo mesmo motivo: o botão
  // abaixo bate em `POST /gerar-insights`, que não existe nesta base, e o
  // clique só sabe virar 404. Melhor dizer que a funcionalidade não faz
  // parte desta versão do que oferecer um botão que só sabe falhar.
  //
  // O gate fica DEPOIS dos hooks de propósito -- `useAuth`/`useState` acima
  // precisam rodar em toda renderização (regras dos hooks). Em
  // SecaoAbordagem o equivalente pôde ficar na primeira linha porque lá o
  // componente não tem hook próprio.
  if (!INSIGHTS_DISPONIVEIS) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Lightbulb}>Insights estratégicos</SectionHeading>
        <p className="dossier-muted">
          A geração de insights por IA não faz parte desta versão. A análise
          do score do lead está na aba <strong>Análise</strong>.
        </p>
      </section>
    );
  }

  const limite = statusLimiteIa(geracoesIa, "insights");

  if (!insights) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Lightbulb}>Insights estratégicos</SectionHeading>
        <p className="dossier-muted">
          Ainda não geramos uma análise estratégica pra este lead. A IA olha pro score, área financiada,
          culturas, valor do crédito rural, presença digital e outros sinais já coletados e sugere como
          priorizar a abordagem.
        </p>
        {limite.atingido ? (
          <p className="dossier-limite-ia">{MENSAGEM_LIMITE_ATINGIDO}</p>
        ) : (
          <button type="button" className="dossier-abordagem-btn" onClick={gerar} disabled={gerando} aria-busy={gerando}>
            {gerando ? <Loader2 size={15} className="spin" aria-label="Gerando..." /> : "Gerar Insights com IA"}
          </button>
        )}
        {erro && <p className="dossier-abordagem-erro">{erro}</p>}
      </section>
    );
  }

  return (
    <section className="dossier-card dossier-insights">
      <div className="dossier-insights-header">
        <SectionHeading icon={Lightbulb}>Insights estratégicos</SectionHeading>
        {limite.atingido ? (
          <p className="dossier-limite-ia">{MENSAGEM_LIMITE_ATINGIDO}</p>
        ) : (
          <button
            type="button"
            className="dossier-abordagem-btn-secundario"
            onClick={gerar}
            disabled={gerando}
            aria-busy={gerando}
          >
            {gerando ? <Loader2 size={14} className="spin" aria-label="Gerando..." /> : "Gerar novamente"}
          </button>
        )}
      </div>

      {geradoEm && <p className="dossier-insights-data">Gerado {formatRelative(geradoEm)}</p>}

      {insights.resumo_estrategico && <p className="dossier-insights-resumo">{insights.resumo_estrategico}</p>}

      {insights.potencial_oportunidade && (
        <div className="dossier-insights-potencial">
          <span className="dossier-insights-potencial-label">Potencial de oportunidade</span>
          <PotencialBadge potencial={insights.potencial_oportunidade} />
        </div>
      )}

      {insights.recomendacao_abordagem.length > 0 && (
        <div className="dossier-insights-bloco">
          <h3 className="dossier-subheading">
            <Target size={15} />
            <span>Recomendação de abordagem</span>
          </h3>
          <ul className="dossier-insights-recomendacao-lista">
            {insights.recomendacao_abordagem.map((item) => (
              <li key={item}>
                <CheckCircle2 size={14} />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {insights.estrategia_comunicacao && (
        <div className="dossier-insights-bloco">
          <h3 className="dossier-subheading">
            <MessageSquare size={15} />
            <span>Estratégia de comunicação</span>
          </h3>
          <p className="dossier-insights-texto">{insights.estrategia_comunicacao}</p>
        </div>
      )}

      {insights.cta_sugerido && (
        <div className="dossier-insights-cta">
          <span className="dossier-insights-cta-label">CTA sugerido</span>
          <p className="dossier-insights-cta-texto">{insights.cta_sugerido}</p>
        </div>
      )}

      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </section>
  );
}

// --- Helpers compartilhados ---------------------------------------------

/** Título de seção com ícone -- usado nos cards das abas "Dados",
 * "Contatos" e "Mensagens" (exceto os subtítulos da aba
 * "Análise", que usam `.dossier-subheading` -- um nível abaixo, já
 * que a aba inteira é um card só, não vários). `icon` recebe o
 * componente do lucide-react direto (não um elemento já instanciado),
 * pra poder controlar `size` num lugar só. */
function SectionHeading({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <h2 className="dossier-section-heading">
      <Icon size={16} />
      <span>{children}</span>
    </h2>
  );
}

function boolLabel(v?: boolean | null): string | null {
  if (v === null || v === undefined) return null;
  return v ? "Sim" : "Não";
}

function formatPontos(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function Campo({ label, valor, link }: { label: string; valor?: string | number | null; link?: string | null }) {
  const vazio = valor === null || valor === undefined || valor === "";
  return (
    <div className="dossier-campo">
      <dt>{label}</dt>
      <dd className={vazio ? "dossier-campo-vazio" : undefined}>
        {vazio ? "—" : link ? (
          <a href={link} target="_blank" rel="noreferrer">
            {valor}
          </a>
        ) : (
          valor
        )}
      </dd>
    </div>
  );
}

function Indicador({
  ativo,
  textoAtivo,
  textoInativo,
}: {
  ativo?: boolean | null;
  textoAtivo: ReactNode;
  textoInativo: ReactNode;
}) {
  const conhecido = ativo !== null && ativo !== undefined;
  return (
    <span className={`dossier-indicador ${conhecido ? (ativo ? "indicador-on" : "indicador-off") : "indicador-desconhecido"}`}>
      {conhecido ? (ativo ? textoAtivo : textoInativo) : "—"}
    </span>
  );
}

function CampoIndicador({
  label,
  ativo,
  textoAtivo,
  textoInativo,
}: {
  label: string;
  ativo?: boolean | null;
  textoAtivo: ReactNode;
  textoInativo: ReactNode;
}) {
  return (
    <div className="dossier-campo">
      <dt>{label}</dt>
      <dd>
        <Indicador ativo={ativo} textoAtivo={textoAtivo} textoInativo={textoInativo} />
      </dd>
    </div>
  );
}

function CampoWhatsapp({ telefone, ativo }: { telefone?: string | null; ativo?: boolean | null }) {
  const numero = formatarNumeroWhatsapp(telefone);
  return (
    <div className="dossier-campo">
      <dt>WhatsApp</dt>
      <dd className="dossier-campo-acao">
        <Indicador ativo={ativo} textoAtivo="Ativo" textoInativo="Não confirmado" />
        {numero && (
          <a className="dossier-whatsapp-link" href={`https://wa.me/${numero}`} target="_blank" rel="noreferrer">
            <span>Abrir WhatsApp</span>
            <ExternalLink size={12} />
          </a>
        )}
      </dd>
    </div>
  );
}

function CampoEmail({ email, validado }: { email?: string | null; validado?: boolean | null }) {
  const [copiado, setCopiado] = useState(false);

  async function copiar() {
    if (!email) return;
    try {
      await navigator.clipboard.writeText(email);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1500);
    } catch {
      // clipboard indisponível (ex.: contexto não-seguro) -- silencioso,
      // não é crítico o suficiente pra mostrar erro na tela
    }
  }

  return (
    <>
      <div className="dossier-campo">
        <dt>E-mail</dt>
        <dd className="dossier-campo-acao">
          {email ?? <span className="dossier-campo-vazio">—</span>}
          {email && (
            <button type="button" className="dossier-copy-btn" onClick={copiar}>
              {copiado ? "Copiado!" : "Copiar"}
            </button>
          )}
        </dd>
      </div>
      <CampoIndicador label="E-mail validado" ativo={validado} textoAtivo="Validado" textoInativo="Não validado" />
    </>
  );
}

/**
 * Seção de crédito rural (Sicor) — substitui a `SecaoPgfn` do Minotto.
 *
 * No Minotto a dívida ativa da PGFN abre a aba "Dados" por ser o sinal mais
 * crítico daquele nicho. Aqui o equivalente é o crédito rural: área da
 * propriedade (critério de peso 30, o #1 da cliente), valor financiado,
 * cultura e recorrência.
 *
 * Campos lidos de `dados_nicho` — nomes conferidos contra
 * `candidato_de_lead_sicor` (app/scoring/pre_selecao.py) e `persistir_leads`
 * (app/workers/busca.py), não adivinhados.
 */
function SecaoSicor({ nicho }: { nicho: DadosNichoSicor }) {
  const temDado = nicho.area_ha != null || nicho.valor_financiado != null;
  if (!temDado) {
    return (
      <section className="dossier-card dossier-sicor dossier-sicor-vazio">
        <SectionHeading icon={Building2}>Crédito rural (Sicor)</SectionHeading>
        <p className="dossier-sicor-sem-dado">
          Sem operação de crédito rural registrada para este documento — o lead veio
          da semente da Receita Federal, não do Sicor.
        </p>
      </section>
    );
  }

  const anos = nicho.anos_credito ?? [];
  return (
    <section className="dossier-card dossier-sicor">
      <SectionHeading icon={Building2}>Crédito rural (Sicor)</SectionHeading>
      <div className="dossier-sicor-destaques">
        <div className="dossier-sicor-destaque">
          <span className="dossier-sicor-rotulo">Área da propriedade</span>
          <strong className="dossier-sicor-valor">
            {nicho.area_ha != null ? `${nicho.area_ha.toLocaleString("pt-BR")} ha` : "—"}
          </strong>
        </div>
        <div className="dossier-sicor-destaque">
          <span className="dossier-sicor-rotulo">Valor financiado</span>
          <strong className="dossier-sicor-valor">
            {nicho.valor_financiado != null ? formatCurrencyBRL(nicho.valor_financiado) : "—"}
          </strong>
        </div>
        <div className="dossier-sicor-destaque">
          <span className="dossier-sicor-rotulo">Operação mais recente</span>
          <strong className="dossier-sicor-valor">{formatarDataOperacao(nicho.data_operacao)}</strong>
        </div>
      </div>
      <dl>
        <Campo label="Cultura financiada" valor={(nicho.culturas ?? []).join(", ") || null} />
        <Campo
          label="Anos com crédito"
          valor={anos.length ? anos.join(", ") : null}
        />
        {/* Recorrente = tomou crédito em mais de um ano. É o desempate da
            pré-seleção e um sinal de operação continuada. */}
        <Campo label="Produtor recorrente" valor={boolLabel(nicho.recorrente)} />
        <Campo label="Operações no período" valor={nicho.n_operacoes} />
        {/* Dado bônus: o CAR identifica o imóvel. Vários produtores podem
            dividir o mesmo CAR (26,9% da população) — são leads separados. */}
        <Campo
          label="Código do CAR"
          valor={(nicho.codigos_car ?? []).join(", ") || null}
        />
      </dl>
    </section>
  );
}

/** `AAAAMMDD` → `DD/MM/AAAA`. Formato cru do Sicor, não ISO. */
function formatarDataOperacao(data: string | null | undefined): string {
  const d = (data ?? "").trim();
  if (!/^\d{8}$/.test(d)) return "—";
  return `${d.slice(6, 8)}/${d.slice(4, 6)}/${d.slice(0, 4)}`;
}
