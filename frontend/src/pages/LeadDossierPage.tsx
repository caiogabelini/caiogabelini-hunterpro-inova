import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
  Gauge,
  Globe,
  Lightbulb,
  Loader2,
  Lock,
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
  marcarMensagemEnviada,
  SequenciaOrdemError,
  UnauthorizedError,
  type CanalAbordagem,
  type InsightsIA,
  type Lead,
  type LeadMessage,
  type MensagensDoLead,
  type SequenciaAbordagem,
} from "../api";
import { PriorityBadge } from "../components/PriorityBadge";
import { useAuth } from "../context/AuthContext";
import { formatarNumeroWhatsapp, formatCurrencyBRL, formatDate, formatRelative } from "../format";
import { apenasDigitos } from "../documento";
import { getEmailsSecundarios, labelTipoEmail } from "../emailsSecundarios";
import { getInsights } from "../insights";
import { MENSAGEM_LIMITE_ATINGIDO, statusLimiteIa } from "../limitesIa";
import { deveAvisarSiteNaoLido } from "../siteScrape";
import { KANBAN_COLUMNS, STATUS_GANHO, STATUS_PERDIDO } from "../kanbanStatuses";
import { criteriosExibiveis, getScoreBreakdown } from "../leadScore";
import { SIGNAL_LAYER_LABELS } from "../scoreLayers";
import { labelServicoFechamento } from "../servicosFechamento";
import "./LeadDossierPage.css";
import { formatarDocumento, rotuloDocumento, rotuloEntidade, rotuloNome } from "../documento";
import {
  carregarMensagens,
  CANAIS_EM_ORDEM,
  mensagensEmOrdem,
  rotuloBotaoGerar,
  rotuloMensagem,
  SEM_SEQUENCIAS,
  sequenciaConcluida,
  situacaoMensagem,
  type SituacaoMensagem,
} from "../mensagens";
import { getDadosNicho, type DadosNichoSicor } from "../api";
import { getContatos, temAlgumCanal } from "../contatos";
import { getLocalizacao } from "../localizacao";

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
  const [mensagens, setMensagens] = useState<MensagensDoLead>(SEM_SEQUENCIAS);
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

    // Nunca rejeita — no pior caso devolve os dois canais vazios.
    carregarMensagens(() => fetchMensagens(token, id)).then((m) => {
      if (!cancelado) setMensagens(m);
    });

    return () => {
      cancelado = true;
    };
  }, [token, id, logout]);

  /** Uma sequência que acabou de ser GERADA — substitui a do canal e
   * consome uma geração.
   *
   * ⚠️ Substitui, não mescla: "gerar novamente" cria um grupo novo e o
   * anterior sai da tela (continua no banco). Mesclar deixaria mensagens de
   * duas cadências convivendo, e a "próxima a enviar" passaria a ser
   * ambígua. */
  function registrarSequenciaGerada(sequencia: SequenciaAbordagem) {
    setMensagens((atuais) => ({ ...atuais, [sequencia.canal]: sequencia }));
    // Incrementa a contagem local do canal pra que o botão desabilite
    // sozinho ao bater o limite, sem exigir um refetch do lead.
    //
    // `POST /gerar-abordagem/{canal}` devolve a sequência criada, não o
    // Lead -- diferente de `POST /gerar-insights`, que devolve o Lead
    // inteiro já com `geracoes_ia` atualizado (por isso lá basta
    // `setLead`). O incremento aqui espelha o que o backend acabou de
    // contar; na próxima carga do dossiê o valor vem do servidor de novo.
    //
    // ⚠️ Soma 1 por SEQUÊNCIA, não por mensagem: o backend conta grupos
    // distintos (`COUNT(DISTINCT grupo_id)`), então uma geração de WhatsApp
    // com 3 mensagens gasta uma cota só.
    setLead((atual) => {
      if (!atual) return atual;
      const anteriores =
        typeof atual.geracoes_ia === "object" && atual.geracoes_ia !== null
          ? (atual.geracoes_ia as Record<string, number>)
          : null;
      if (!anteriores) return atual;
      return {
        ...atual,
        geracoes_ia: { ...anteriores, [sequencia.canal]: (anteriores[sequencia.canal] ?? 0) + 1 },
      };
    });
  }

  /** Uma sequência que voltou de `PATCH .../enviada` — mesmo grupo, status
   * novo. Não mexe em `geracoes_ia`: marcar como enviada não é geração e
   * não custa nada. */
  function registrarSequenciaAtualizada(sequencia: SequenciaAbordagem) {
    setMensagens((atuais) => ({ ...atuais, [sequencia.canal]: sequencia }));
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
              sequencias={mensagens}
              onGerada={registrarSequenciaGerada}
              onAtualizada={registrarSequenciaAtualizada}
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
          {getLocalizacao(lead).completo ?? "Localização não informada"} · {NICHO_FIXO}
        </p>

        {/* "Score:" saiu daqui -- o número grande no canto (dossier-header-score,
            à direita) já mostra o mesmo dado com muito mais destaque
            visual; manter os dois seria redundante (julgamento pedido
            explicitamente pelo usuário nesta sessão). */}
        {/* ⚠️ "Classificação: X" saiu daqui em 26/08/2026, pela mesma razão
            que "Score: X" já tinha saído: mostrava o MESMO dado duas vezes.
            No Minotto fazia sentido — lá `prioridade` é letra (A/B/C) e a
            classificação era a tradução humana dela ("A" -> "Excelente").
            Aqui `prioridade` já é palavra (ALTA/MEDIA/BAIXA), então o mapa
            A/B/C nunca era atingido: caía sempre no fallback e a linha
            renderizava "Classificação: ALTA · Prioridade: ALTA". */}
        <div className="dossier-resumo">
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
  const contatos = getContatos(lead);
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
            <Campo label="Município/UF" valor={getLocalizacao(lead).completo} />
            {/* Só faz sentido pro lado CNPJ (semente da Receita Federal). */}
            <Campo label="CNAE" valor={nicho.cnae_descricao ?? nicho.cnae} />
            <Campo label="Situação cadastral" valor={nicho.situacao_cadastral} />
            <Campo label="Cooperativa" valor={boolLabel(nicho.eh_cooperativa)} />
          </dl>
        </section>

        <section className="dossier-card">
          <SectionHeading icon={Phone}>Contato</SectionHeading>
          {/* ⚠️ Mesma fonte que a aba Contatos usa (`getContatos`). As duas
              abas mostravam dados diferentes do mesmo lead até 26/08/2026
              justamente por lerem campos diferentes — ver contatos.ts. */}
          <dl>
            <Campo label="Telefone" valor={contatos.telefone} />
            <CampoWhatsapp telefone={contatos.telefone} ativo={contatos.whatsappAtivo} />
            <CampoTelefoneSecundario numero={contatos.telefoneSecundario} />
            <CampoEmail email={contatos.email} validado={contatos.emailValidado} />
            <Campo label="Decisor" valor={contatos.decisor} />
            <Campo label="Fonte do decisor" valor={contatos.fonteDecisor} />
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
  // ⚠️ **Bug corrigido em 26/08/2026.** Esta aba lia `lead.decisor_nome`, um
  // campo do Minotto que esta API nunca enviou. Como é opcional no tipo,
  // `undefined` passava batido pelo TypeScript e a aba concluía "não tem
  // decisor" — enquanto a aba Dados, lendo `dados_nicho`, exibia nome,
  // telefone, WhatsApp e e-mail validado do MESMO lead. Agora as duas passam
  // por `getContatos`. Ver contatos.ts pro quadro completo.
  const contatos = getContatos(lead);
  const numeroWhatsapp = formatarNumeroWhatsapp(contatos.telefone);
  const mostrarWhatsapp = contatos.whatsappAtivo && !!numeroWhatsapp;

  if (!contatos.decisor && !temAlgumCanal(contatos)) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Users}>Contatos</SectionHeading>
        <p className="dossier-muted">Nenhum contato identificado ainda pra este lead.</p>
        <OutrosEmails lead={lead} />
      </section>
    );
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Users}>Contatos</SectionHeading>
      <ul className="dossier-contatos-lista">
        <li className="dossier-contato-card">
          {/* Sem decisor resolvido, os canais ainda valem: o telefone é do
              produtor, mesmo que a API Full não tenha devolvido o nome. */}
          <div className="dossier-contato-nome">
            {contatos.decisor ?? "Decisor não identificado"}
          </div>
          {contatos.fonteDecisor && (
            <div className="dossier-contato-fonte">via {contatos.fonteDecisor}</div>
          )}
          <div className="dossier-contato-canais">
            {mostrarWhatsapp && (
              <a className="dossier-chip" href={`https://wa.me/${numeroWhatsapp}`} target="_blank" rel="noreferrer">
                <MessageCircle size={13} />
                <span>WhatsApp</span>
              </a>
            )}
            {contatos.telefone && (
              <a className="dossier-chip" href={`tel:+${apenasDigitos(contatos.telefone)}`}>
                <Phone size={13} />
                <span>{contatos.telefone}</span>
              </a>
            )}
            {contatos.telefoneSecundario && (
              // Rotulado como alternativo e SEM chip de WhatsApp: a validação
              // da Evolution roda só no número principal (ver a mesma regra
              // no card Contato da aba Dados).
              <a className="dossier-chip dossier-chip-secundario" href={`tel:+${apenasDigitos(contatos.telefoneSecundario)}`}>
                <Phone size={13} />
                <span>{contatos.telefoneSecundario} (alternativo)</span>
              </a>
            )}
            {contatos.email && (
              <a className="dossier-chip" href={`mailto:${contatos.email}`}>
                <Mail size={13} />
                <span>{contatos.email}</span>
                {contatos.emailValidado && <CheckCircle2 size={12} aria-label="validado" />}
              </a>
            )}
            {!mostrarWhatsapp && !contatos.telefone && !contatos.email && (
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

// --- Aba "Mensagens" (sequência de abordagem) -------------------------
//
// ⚠️ **Uma geração produz uma SEQUÊNCIA, não uma mensagem** (Fase 11a):
// 3 toques no WhatsApp, 2 no e-mail. A tela mostra a cadência inteira em
// ordem, com o status de cada toque, e libera "marcar como enviada" só na
// mensagem que o backend aponta em `proxima_ordem`.
//
// ⚠️ **Nada aqui envia nada.** Não há disparo automático nem agendamento: a
// Carolina manda pelo WhatsApp/e-mail dela e volta pra registrar. O botão
// diz "Marcar como enviada" — não "Enviar" — exatamente pra não prometer um
// comportamento que o produto não tem.

export function SecaoAbordagem({
  leadId,
  email,
  temWhatsapp,
  telefone,
  sequencias,
  onGerada,
  onAtualizada,
  geracoesIa,
}: {
  leadId: string;
  email?: string | null;
  temWhatsapp: boolean;
  telefone?: string | null;
  sequencias: MensagensDoLead;
  onGerada: (sequencia: SequenciaAbordagem) => void;
  onAtualizada: (sequencia: SequenciaAbordagem) => void;
  geracoesIa: unknown;
}) {
  // WhatsApp aqui exige o sinal *confirmado ativo* (whatsapp_ativo),
  // não só ter um telefone cadastrado -- gerar mensagem pra um número
  // que nem confirmamos ter WhatsApp não faz sentido.
  const disponivel: Record<CanalAbordagem, boolean> = {
    whatsapp: temWhatsapp,
    email: !!email,
  };
  // A ordem vem de CANAIS_EM_ORDEM (WhatsApp primeiro) — ver mensagens.ts.
  const canais = CANAIS_EM_ORDEM.filter((canal) => disponivel[canal]);

  if (canais.length === 0) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Sparkles}>Sequência de abordagem</SectionHeading>
        <p className="dossier-muted">Nenhum canal disponível ainda (sem e-mail nem WhatsApp confirmado pra este lead).</p>
      </section>
    );
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Sparkles}>Sequência de abordagem</SectionHeading>
      <p className="dossier-abordagem-explicacao">
        A IA escreve a cadência inteira de uma vez. Você envia cada mensagem pelo seu WhatsApp ou e-mail e marca aqui —
        a próxima só libera depois que a anterior for marcada.
      </p>
      <div className="dossier-abordagem-canais">
        {canais.map((canal) => (
          <CanalSequenciaCard
            key={canal}
            leadId={leadId}
            canal={canal}
            sequencia={sequencias[canal]}
            onGerada={onGerada}
            onAtualizada={onAtualizada}
            email={email}
            telefone={telefone}
            geracoesIa={geracoesIa}
          />
        ))}
      </div>
    </section>
  );
}

const TITULO_CANAL: Record<CanalAbordagem, string> = {
  whatsapp: "WhatsApp",
  email: "E-mail",
};

function CanalSequenciaCard({
  leadId,
  canal,
  sequencia,
  onGerada,
  onAtualizada,
  email,
  telefone,
  geracoesIa,
}: {
  leadId: string;
  canal: CanalAbordagem;
  sequencia: SequenciaAbordagem | null;
  onGerada: (sequencia: SequenciaAbordagem) => void;
  onAtualizada: (sequencia: SequenciaAbordagem) => void;
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
      onGerada(await gerarAbordagemCanal(token, leadId, canal));
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro(
        e instanceof LimiteIaError
          ? e.message
          : "Não foi possível gerar a sequência agora. Tente novamente.",
      );
    } finally {
      setGerando(false);
    }
  }

  const limite = statusLimiteIa(geracoesIa, canal);
  const numeroWhatsapp = formatarNumeroWhatsapp(telefone);

  const botaoGerar = limite.atingido ? (
    <p className="dossier-limite-ia">{MENSAGEM_LIMITE_ATINGIDO}</p>
  ) : (
    <button
      type="button"
      className={sequencia ? "dossier-abordagem-btn-secundario" : "dossier-abordagem-btn"}
      onClick={gerar}
      disabled={gerando}
      aria-busy={gerando}
    >
      {gerando ? <Loader2 size={14} className="spin" aria-label="Gerando..." /> : rotuloBotaoGerar(canal, !!sequencia)}
    </button>
  );

  return (
    <div className="dossier-abordagem-canal">
      <h3 className="dossier-abordagem-canal-titulo">
        {canal === "whatsapp" ? <MessageCircle size={14} /> : <Mail size={14} />}
        <span>{TITULO_CANAL[canal]}</span>
        {sequencia && (
          <span className="dossier-abordagem-canal-contador">
            {sequencia.total} {sequencia.total === 1 ? "mensagem" : "mensagens"}
          </span>
        )}
      </h3>

      {sequencia ? (
        <>
          <ol className="dossier-sequencia">
            {mensagensEmOrdem(sequencia).map((mensagem) => (
              <MensagemDaSequencia
                key={mensagem.id}
                leadId={leadId}
                mensagem={mensagem}
                total={sequencia.total}
                proximaOrdem={sequencia.proxima_ordem}
                onAtualizada={onAtualizada}
                email={email}
                numeroWhatsapp={numeroWhatsapp}
              />
            ))}
          </ol>
          {sequenciaConcluida(sequencia) && (
            <p className="dossier-sequencia-concluida">
              <CheckCircle2 size={14} />
              <span>Cadência concluída — todas as mensagens foram enviadas.</span>
            </p>
          )}
          <div className="dossier-abordagem-acoes">{botaoGerar}</div>
        </>
      ) : (
        <>
          <p className="dossier-muted">Sequência ainda não gerada.</p>
          {botaoGerar}
        </>
      )}
      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </div>
  );
}

/** Badge de status. O texto de `enviada` carrega a data porque "quando
 * mandei?" é a pergunta que a Carolina faz olhando a cadência — sem ela o
 * badge diria só o que o botão ausente já dizia. */
function StatusMensagemBadge({ situacao, enviadaEm }: { situacao: SituacaoMensagem; enviadaEm?: string | null }) {
  if (situacao === "enviada") {
    return (
      <span className="dossier-sequencia-badge badge-enviada">
        <CheckCircle2 size={13} />
        <span>{enviadaEm ? `Enviada em ${formatDate(enviadaEm)}` : "Enviada"}</span>
      </span>
    );
  }
  if (situacao === "proxima") {
    return (
      <span className="dossier-sequencia-badge badge-proxima">
        <Clock size={13} />
        <span>Próxima a enviar</span>
      </span>
    );
  }
  return (
    <span className="dossier-sequencia-badge badge-bloqueada">
      <Lock size={13} />
      <span>Aguardando a anterior</span>
    </span>
  );
}

function MensagemDaSequencia({
  leadId,
  mensagem,
  total,
  proximaOrdem,
  onAtualizada,
  email,
  numeroWhatsapp,
}: {
  leadId: string;
  mensagem: LeadMessage;
  total: number;
  proximaOrdem: number | null;
  onAtualizada: (sequencia: SequenciaAbordagem) => void;
  email?: string | null;
  numeroWhatsapp: string | null;
}) {
  const { token, logout } = useAuth();
  const [marcando, setMarcando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const situacao = situacaoMensagem(mensagem, proximaOrdem);
  const ehProxima = situacao === "proxima";

  async function marcar() {
    if (!token) return;
    setMarcando(true);
    setErro(null);
    try {
      onAtualizada(await marcarMensagemEnviada(token, leadId, mensagem.id));
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      // ⚠️ O 422 do backend já explica o que houve ("a próxima mensagem
      // pendente é a 1 de 3"). Mostrar esse texto, e não um genérico, é o
      // que faz a regra de ordem ter uma redação só — a dele.
      setErro(
        e instanceof SequenciaOrdemError
          ? e.message
          : "Não foi possível marcar como enviada agora. Tente novamente.",
      );
    } finally {
      setMarcando(false);
    }
  }

  return (
    <li className={`dossier-sequencia-item situacao-${situacao}`}>
      <div className="dossier-sequencia-cabecalho">
        <span className="dossier-sequencia-rotulo">{rotuloMensagem(mensagem.ordem, total)}</span>
        <StatusMensagemBadge situacao={situacao} enviadaEm={mensagem.enviada_em} />
      </div>

      {mensagem.canal === "email" && mensagem.assunto && (
        <p className="dossier-abordagem-assunto">
          <strong>Assunto:</strong> {mensagem.assunto}
        </p>
      )}
      <p className="dossier-abordagem-texto">{mensagem.conteudo}</p>

      {situacao !== "enviada" && (
        <div className="dossier-abordagem-acoes">
          {/* O link de envio aparece só na próxima: abrir o WhatsApp com o
              follow-up antes do primeiro contato é exatamente o pulo de etapa
              que o backend recusa. */}
          {ehProxima && mensagem.canal === "email" && email && (
            <a
              className="dossier-abordagem-btn-acao"
              href={`mailto:${email}?subject=${encodeURIComponent(mensagem.assunto ?? "")}&body=${encodeURIComponent(mensagem.conteudo)}`}
            >
              <Mail size={14} />
              <span>Abrir no e-mail</span>
            </a>
          )}
          {ehProxima && mensagem.canal === "whatsapp" && numeroWhatsapp && (
            <a
              className="dossier-abordagem-btn-acao"
              href={`https://wa.me/${numeroWhatsapp}?text=${encodeURIComponent(mensagem.conteudo)}`}
              target="_blank"
              rel="noreferrer"
            >
              <Send size={14} />
              <span>Abrir no WhatsApp</span>
            </a>
          )}
          {/* Desabilitado (não oculto) quando bloqueada: a Carolina vê que a
              ação existe e que só falta marcar a anterior. Mesmo tratamento
              nos dois canais. */}
          <button
            type="button"
            className="dossier-abordagem-btn-secundario"
            onClick={marcar}
            disabled={!ehProxima || marcando}
            aria-busy={marcando}
            title={ehProxima ? undefined : "Marque a mensagem anterior como enviada primeiro"}
          >
            {marcando ? <Loader2 size={14} className="spin" aria-label="Marcando..." /> : "Marcar como enviada"}
          </button>
        </div>
      )}

      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </li>
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

/**
 * Segundo número do lead, quando a fonte trouxe mais de um.
 *
 * ⚠️ Rotulado como **alternativo**, e sem badge de WhatsApp de propósito: a
 * validação da Evolution roda só no número principal, então dizer qualquer
 * coisa sobre WhatsApp aqui seria afirmar o que ninguém verificou. O aviso
 * embaixo existe pra que o vendedor saiba disso antes de ligar, em vez de
 * descobrir tentando.
 *
 * Só renderiza quando existe — lead com um telefone só não ganha linha vazia
 * (mesmo comportamento de `Campo` com valor nulo).
 */
function CampoTelefoneSecundario({ numero }: { numero?: string | null }) {
  if (!numero) return null;
  return (
    <div className="dossier-campo">
      <dt>Telefone alternativo</dt>
      <dd className="dossier-telefone-secundario">
        <span>{numero}</span>
        <span className="dossier-telefone-secundario-nota">não verificado</span>
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
