"""Geração de texto por IA — mensagem de abordagem e insights estratégicos.

⚠️ **Toda chamada aqui é paga** (Anthropic, por geração). O limite por lead
vive em ``app/api/routes/limites_ia.py`` e é checado **antes** de qualquer
chamada — validar depois de gerar não economizaria nada.

## Contrato HTTP

Messages API direto por HTTP, não o SDK oficial — mesmo padrão do resto dos
serviços deste projeto (cliente ``httpx`` injetável, testável com
``MockTransport``, sem framework por cima)::

    POST https://api.anthropic.com/v1/messages
    headers: x-api-key, anthropic-version: 2023-06-01
    body:    {"model", "max_tokens", "messages": [{"role": "user", ...}]}

Modelo: ``settings.ANTHROPIC_MODEL`` (``claude-haiku-4-5-20251001``).
⚠️ **O sufixo de data é parte do ID** — já foi removido por engano uma vez
nesta base e teve de ser revertido.

## ⚠️ Uma chamada por sequência, não uma por mensagem

Desde a Fase 11a, ``gerar_sequencia_abordagem`` pede as 3 mensagens de
WhatsApp (ou as 2 de e-mail) **numa única chamada**. Não é só economia: numa
chamada só, o modelo escreve o follow-up já tendo escrito a abertura, então
"não repita o ângulo da mensagem 1" é uma instrução que ele consegue cumprir.
Três chamadas independentes triplicariam o custo para produzir três aberturas
parecidas, porque nenhuma delas veria as outras.

## Defensividade

Estas funções rodam **dentro de um handler HTTP**, então nunca levantam por
erro de rede/HTTP nem por resposta malformada: devolvem vazio (``[]`` na
sequência, ``{}`` nos insights). Quem decide o que fazer com isso é a rota,
que devolve 502 em vez de persistir texto vazio.

Isso difere de ``ai_site.py``, que é chamado de dentro do pipeline em lote e
tem sua própria política de erro. Os dois módulos coexistem de propósito: um
serve o enriquecimento automático, o outro o botão que o vendedor aperta.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

#: Fonte única. Antes da Fase 11a este módulo redeclarava ``CANAIS_VALIDOS``
#: com os mesmos dois valores do model — duas listas que ninguém garantia
#: iguais. O tamanho da sequência entrou pelo mesmo caminho: é o número que o
#: prompt pede e o que a rota exige de volta, e um número desses não pode
#: existir em dois lugares.
from app.models.lead_message import CANAIS_VALIDOS, TAMANHO_SEQUENCIA

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_PADRAO = 30.0
MAX_TOKENS_RESPOSTA = 1024
#: A sequência inteira num JSON só é bem maior que a mensagem avulsa que este
#: módulo gerava até a Fase 10. Com 1024 o e-mail (2 corpos de 3-4 frases +
#: assuntos) encosta no teto, e resposta truncada não parseia — vira 502 e uma
#: chamada paga jogada fora. O teto não é cobrado, só o que for gerado.
MAX_TOKENS_SEQUENCIA = 3000


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if cliente is not None:
        return cliente, False
    return httpx.Client(timeout=TIMEOUT_PADRAO), True


def extrair_json(texto: str) -> dict | None:
    """Parseia ``texto`` como objeto JSON, tolerando as variações da IA.

    Mesmo instruída a responder só JSON, a IA às vezes embrulha em bloco de
    código markdown ou acrescenta uma frase antes/depois. Tenta as variações
    e devolve ``None`` se nenhuma funcionar — **nunca levanta**.
    """
    candidatos = [texto.strip()]

    bloco = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if bloco:
        candidatos.append(bloco.group(1))

    objeto = re.search(r"\{.*\}", texto, re.DOTALL)
    if objeto:
        candidatos.append(objeto.group(0))

    for candidato in candidatos:
        try:
            resultado = json.loads(candidato)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(resultado, dict):
            return resultado
    return None


def _chamar_ia(
    prompt: str, cliente: httpx.Client | None, max_tokens: int = MAX_TOKENS_RESPOSTA
) -> str:
    """Uma chamada à Messages API. Devolve o texto, ou ``""`` em qualquer
    falha (rede, HTTP, corpo inesperado). Nunca levanta."""
    http, meu = _cliente(cliente)
    try:
        resposta = http.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": settings.ANTHROPIC_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resposta.raise_for_status()
        blocos = resposta.json().get("content") or []
        return next(
            (b.get("text", "") for b in blocos if b.get("type") == "text"), ""
        )
    except httpx.HTTPError as exc:
        logger.error("ia: chamada falhou — %s", erro_redigido(exc))
        return ""
    except (ValueError, AttributeError, TypeError) as exc:
        logger.error("ia: resposta inesperada — %s", erro_redigido(exc))
        return ""
    finally:
        if meu:
            http.close()


# ---------------------------------------------------------------------------
# Contexto do lead — os sinais reais desta base
# ---------------------------------------------------------------------------

def _formatar_reais(valor: Any) -> str | None:
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return None


def montar_contexto_abordagem(lead: dict) -> str:
    """Subconjunto de sinais pro redator de uma mensagem CURTA.

    De propósito menor que ``montar_contexto_insights``: quem escreve duas
    frases de WhatsApp não deve receber o breakdown inteiro do score — dado
    irrelevante no prompt vira menção irrelevante na mensagem.
    """
    nome = lead.get("decisor") or lead.get("nome") or "não informado"
    linhas = [f"- Nome do produtor/responsável: {nome}"]

    if lead.get("municipio"):
        linhas.append(f"- Localização: {lead['municipio']}/{lead.get('uf') or ''}")

    if lead.get("area_ha") is not None:
        linhas.append(f"- Área da propriedade financiada: {lead['area_ha']} hectares")

    culturas = lead.get("culturas") or []
    if culturas:
        linhas.append(f"- Cultura financiada: {', '.join(str(c) for c in culturas)}")

    valor = _formatar_reais(lead.get("valor_financiado"))
    if valor:
        linhas.append(f"- Valor financiado no crédito rural: {valor}")

    anos = lead.get("anos_credito") or []
    if len(anos) > 1:
        linhas.append(
            f"- Tomou crédito rural em {len(anos)} safras ({', '.join(str(a) for a in anos)}) "
            f"— produtor recorrente, não pontual"
        )
    elif anos:
        linhas.append(f"- Tomou crédito rural na safra {anos[0]}")

    if lead.get("eh_cooperativa"):
        linhas.append("- É uma cooperativa (pessoa jurídica), não produtor pessoa física")

    return "\n".join(linhas)


def montar_contexto_insights(lead: dict) -> str:
    """Quadro completo do lead — o analista precisa ver o forte e o fraco.

    Inclui tudo do contexto de abordagem, mais score, breakdown por critério,
    canais confirmados e presença digital.
    """
    linhas = [montar_contexto_abordagem(lead)]

    if lead.get("score") is not None:
        linhas.append(
            f"- Score geral: {lead['score']}/100 "
            f"(prioridade {lead.get('prioridade') or '—'})"
        )

    breakdown = (lead.get("score_detalhes") or {}).get("breakdown")
    if isinstance(breakdown, list) and breakdown:
        linhas.append("- Breakdown do score por critério:")
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            # Critérios de peso 0 ficam de fora, como na tela: pontuam nada e
            # só distraem a análise (ver criteriosExibiveis no frontend).
            if not item.get("weight"):
                continue
            rotulo = item.get("label") or item.get("key") or "?"
            linhas.append(
                f"  - {rotulo}: {item.get('points')}/{item.get('weight')} pontos"
            )

    if lead.get("decisor"):
        fonte = lead.get("fonte_decisor")
        origem = {"api_full": "bureau de dados", "brasil_api": "Receita Federal"}.get(
            fonte, fonte
        )
        linhas.append(
            f"- Decisor identificado{f' (fonte: {origem})' if origem else ''}"
        )
    else:
        linhas.append("- Decisor NÃO identificado — não sabemos com quem falar")

    if lead.get("whatsapp_ativo"):
        linhas.append("- WhatsApp confirmado ativo no telefone principal")
    elif lead.get("telefone"):
        linhas.append(
            "- Tem telefone, mas WhatsApp NÃO confirmado — canal digital incerto"
        )
    else:
        linhas.append("- Nenhum telefone conhecido")

    if lead.get("telefone_secundario"):
        linhas.append("- Há um segundo telefone como contato alternativo")

    if lead.get("email_status") in ("valid", "catch-all"):
        linhas.append("- E-mail validado (entregável)")
    elif lead.get("email"):
        linhas.append("- Tem e-mail, mas NÃO validado")
    else:
        linhas.append("- Nenhum e-mail conhecido")

    presenca = lead.get("presenca_digital")
    if lead.get("site") or lead.get("instagram"):
        canais = [c for c, v in (("site", lead.get("site")), ("Instagram", lead.get("instagram"))) if v]
        linhas.append(f"- Presença digital: {', '.join(canais)}")
    elif presenca in (0, 0.0, None):
        linhas.append("- Presença digital: nenhuma encontrada (sem site nem Instagram)")

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

#: ⚠️ Regras reescritas do zero pro agro. O equivalente do Minotto girava em
#: torno de dívida ativa na PGFN, RQE e convênio médico — nada disso existe
#: aqui, e traduzir palavra a palavra teria produzido menção a sinal
#: inexistente.
#:
#: A regra que sobrevive intacta é a estrutural: **nunca inventar dado**.
#: Foi ela que evitou, no Minotto, que a IA preenchesse lacuna com plausível.
REGRAS_DE_TOM = """Regras de tom, siga à risca:
- Trate crédito rural como sinal de um produtor ATIVO e estruturado, nunca \
como dívida ou fragilidade. Tomar crédito de custeio ou investimento é \
operação normal e saudável no agronegócio. Exemplo do tom certo: "produtores \
com o porte da sua operação costumam ganhar bastante com um planejamento \
tributário dedicado". Exemplo do tom ERRADO, nunca use: "vi que você tem \
financiamentos em aberto" ou qualquer coisa que soe como cobrança.
- Área da propriedade e valor financiado são informação pública do Sicor \
(Banco Central). Pode citá-los, mas com naturalidade — sem dar a entender \
que houve investigação sobre a pessoa.
- Recorrência (tomar crédito em mais de uma safra) pode ser citada como \
reconhecimento de uma operação consolidada.
- NÃO invente NENHUM dado que não esteja explicitamente na lista abaixo. \
Sem nome do produtor, use um tratamento neutro; nunca chute um nome, uma \
cultura ou um número."""

PROMPT_SEQUENCIA_EMAIL = """Você é um redator de prospecção B2B para um escritório de contabilidade \
(Inova Contabilidade) especializado em produtores rurais e agronegócio no Paraná.

Escreva uma SEQUÊNCIA DE 2 E-MAILS para o MESMO lead, na ordem em que serão \
enviados. Eles são partes de uma mesma cadência de abordagem, não duas \
tentativas independentes: escreva o segundo já sabendo exatamente o que o \
primeiro disse.

E-MAIL 1 — primeiro contato (enviado hoje):
- Tom profissional, cordial e consultivo. Corpo com 3 a 4 frases.
- Genuinamente personalizado: mencione pelo menos um dado real da lista \
abaixo, com naturalidade (não faça inventário dos dados).
- Termina convidando para uma conversa breve.

E-MAIL 2 — follow-up (pensado para ~3 a 5 dias depois, sem resposta):
- Mais CURTO que o primeiro: 2 a 3 frases.
- Reconhece o e-mail anterior explicitamente, sem cobrança e sem culpar o \
lead ("sei que essa época do ano é corrida" serve; "você não respondeu meu \
e-mail" não).
- Traz um ÂNGULO DE VALOR DIFERENTE do primeiro: outro benefício, outro \
recorte do que a contabilidade resolve. NÃO repita o argumento, a frase de \
apresentação nem o dado já citado no e-mail 1.
- Termina com um CTA claro e objetivo, diferente do convite do e-mail 1 \
(uma pergunta direta ou uma proposta concreta, fácil de responder).
- O assunto do e-mail 2 é próprio: não repita o assunto do e-mail 1 nem use \
"Re:".

{regras_de_tom}

Dados do lead:
{{dados_lead}}

Responda APENAS com um objeto JSON válido, sem texto antes ou depois, sem \
bloco de código markdown, no formato exato abaixo, com EXATAMENTE 2 itens em \
"mensagens", na ordem 1 e 2:

{{{{
  "mensagens": [
    {{{{
      "ordem": 1,
      "assunto": "assunto curto e direto, sem saudação, até 60 caracteres",
      "conteudo": "o corpo do e-mail — sem saudação tipo \\"Prezado(a)\\", sem \
assinatura, sem aspas ao redor do texto"
    }}}},
    {{{{
      "ordem": 2,
      "assunto": "assunto próprio do follow-up, até 60 caracteres",
      "conteudo": "o corpo do follow-up, nas mesmas regras"
    }}}}
  ]
}}}}
""".format(regras_de_tom=REGRAS_DE_TOM)

PROMPT_SEQUENCIA_WHATSAPP = """Você é um redator de prospecção B2B para um escritório de contabilidade \
(Inova Contabilidade) especializado em produtores rurais e agronegócio no Paraná.

Escreva uma SEQUÊNCIA DE 3 MENSAGENS DE WHATSAPP para o MESMO lead, na ordem \
em que serão enviadas. Elas são partes de uma mesma cadência de abordagem, \
não três tentativas independentes: escreva a 2 já sabendo exatamente o que a \
1 disse, e a 3 sabendo o que as duas anteriores disseram.

MENSAGEM 1 — primeiro contato (enviada hoje):
- Se apresenta como sendo do escritório de contabilidade.
- Menciona com naturalidade UM sinal público da lista abaixo — um só, sem \
fazer inventário dos dados.
- 1 a 2 frases curtas. Tom direto e próximo, sempre respeitoso.
- Termina com uma pergunta aberta, de baixo compromisso. Não peça reunião \
nem horário nesta primeira mensagem.

MENSAGEM 2 — primeiro follow-up (pensada para ~2 a 3 dias depois, sem resposta):
- Reconhece a ausência de resposta SEM soar cobrança e sem culpar o lead \
("sei que essa época é corrida por aí" serve; "vi que você não respondeu" não).
- Traz um ÂNGULO DE VALOR DIFERENTE do da mensagem 1: outro benefício, outro \
recorte. NÃO repita a abertura, a frase de apresentação nem o dado já citado.
- Termina com uma pergunta MAIS FÁCIL de responder que a da mensagem 1 — \
idealmente algo que se responde com sim ou não.

MENSAGEM 3 — follow-up final (pensada para ~5 a 7 dias depois, ainda sem resposta):
- Mais CURTA que as duas anteriores.
- Dá uma saída educada: deixa claro que não vai insistir e que a porta fica \
aberta quando fizer sentido.
- REDUZ a pressão em vez de aumentar. Proibido: urgência, escassez, "última \
chance", desconto por tempo limitado ou qualquer terceira cobrança.

{regras_de_tom}

Dados do lead:
{{dados_lead}}

Responda APENAS com um objeto JSON válido, sem texto antes ou depois, sem \
bloco de código markdown, no formato exato abaixo, com EXATAMENTE 3 itens em \
"mensagens", na ordem 1, 2 e 3. Sem assunto (WhatsApp não tem), sem aspas ao \
redor do texto, sem assinatura:

{{{{
  "mensagens": [
    {{{{"ordem": 1, "conteudo": "texto da primeira mensagem"}}}},
    {{{{"ordem": 2, "conteudo": "texto do primeiro follow-up"}}}},
    {{{{"ordem": 3, "conteudo": "texto do follow-up final"}}}}
  ]
}}}}
""".format(regras_de_tom=REGRAS_DE_TOM)

PROMPT_INSIGHTS = """Você é um analista de prospecção B2B para um escritório de contabilidade \
(Inova Contabilidade) especializado em produtores rurais e agronegócio no \
Paraná. Analise o lead abaixo e gere uma análise estratégica curta para \
ajudar o time comercial a decidir como abordá-lo.

{regras_de_tom}

Considere o que está mais forte e mais fraco NESTE lead especificamente \
(ex.: área grande e crédito recorrente mas sem WhatsApp confirmado; decisor \
identificado mas sem e-mail validado; score alto sustentado só por área, sem \
nenhum canal de contato validado). A análise precisa refletir os dados reais \
abaixo, não um texto genérico que serviria para qualquer produtor.

Dados do lead:
{{dados_lead}}

Responda APENAS com um objeto JSON válido, sem texto antes ou depois, sem \
bloco de código markdown, no formato exato abaixo:

{{{{
  "resumo_estrategico": "resumo em 2-3 frases da situação estratégica deste lead",
  "potencial_oportunidade": "alto" ou "médio" ou "baixo",
  "recomendacao_abordagem": ["1 a 3 recomendações curtas e objetivas de como abordar este lead"],
  "estrategia_comunicacao": "1-2 frases sobre tom/canal/ângulo recomendado para este lead específico",
  "cta_sugerido": "uma frase de call-to-action pronta para usar no primeiro contato"
}}}}
""".format(regras_de_tom=REGRAS_DE_TOM)


# ---------------------------------------------------------------------------
# Geradores
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MensagemGerada:
    """Uma mensagem de uma sequência. ``assunto`` só vem no canal "email"."""

    conteudo: str
    assunto: str | None = None
    #: Posição na sequência (1 = primeiro contato). Atribuída por POSIÇÃO na
    #: lista que a IA devolveu, não pelo campo "ordem" que ela escreve — o
    #: JSON já é ordenado, e confiar no rótulo abriria a chance de dois "2" ou
    #: de um salto. O campo continua sendo pedido no prompt porque ajuda o
    #: modelo a se situar enquanto escreve, não porque é lido de volta.
    ordem: int = 1


def gerar_sequencia_abordagem(
    lead: dict, canal: str, cliente: httpx.Client | None = None
) -> list[MensagemGerada]:
    """A sequência de abordagem inteira do canal, em UMA chamada paga.

    WhatsApp devolve 3 (inicial + 2 follow-ups), e-mail 2 (inicial + 1
    follow-up) — ver ``TAMANHO_SEQUENCIA``. Os dois canais respondem em JSON;
    o WhatsApp deixou de responder texto puro na Fase 11a porque uma resposta
    com 3 mensagens precisa de fronteira explícita entre elas (quebrar por
    linha em branco erra na primeira mensagem que tiver um parágrafo).

    Levanta ``ValueError`` para canal desconhecido — erro de programação, não
    resposta da IA (a rota valida antes). Qualquer outra falha devolve ``[]``,
    que a rota traduz em 502.

    ⚠️ **Tudo ou nada.** Se vierem menos mensagens que o esperado (truncagem
    por ``max_tokens``, item sem conteúdo), devolve ``[]`` em vez da sequência
    parcial. Uma cadência de 3 gravada com 2 quebraria silenciosamente a
    promessa da tela — e como a cota só é consumida quando algo é persistido,
    o custo do rigor é uma nova tentativa, não uma geração perdida.
    """
    if canal not in CANAIS_VALIDOS:
        raise ValueError(f"Canal desconhecido: {canal!r}. Use um de {CANAIS_VALIDOS}.")

    if not settings.anthropic_configurada:
        logger.error("ia: ANTHROPIC_API_KEY ausente — geração não tentada")
        return []

    esperado = TAMANHO_SEQUENCIA[canal]
    template = (
        PROMPT_SEQUENCIA_EMAIL if canal == "email" else PROMPT_SEQUENCIA_WHATSAPP
    )
    texto = _chamar_ia(
        template.format(dados_lead=montar_contexto_abordagem(lead)),
        cliente,
        max_tokens=MAX_TOKENS_SEQUENCIA,
    )
    if not texto:
        return []

    dados = extrair_json(texto)
    if dados is None:
        logger.error("ia: sequência de %s não parseou como JSON", canal)
        return []

    bruto = dados.get("mensagens")
    if not isinstance(bruto, list):
        logger.error("ia: sequência de %s veio sem a lista 'mensagens'", canal)
        return []

    sequencia: list[MensagemGerada] = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        conteudo = str(item.get("conteudo") or "").strip()
        if not conteudo:
            continue
        # Assunto é do e-mail. Se a IA mandar um no WhatsApp, é descartado
        # aqui e não em quem lê — o canal não tem onde mostrar isso.
        assunto = None
        if canal == "email":
            bruto_assunto = item.get("assunto")
            assunto = str(bruto_assunto).strip() or None if bruto_assunto else None
        # ⚠️ A ordem sai da posição entre as ACEITAS, não do índice na lista
        # crua: com um item descartado no meio, indexar pelo bruto produziria
        # uma sequência de ordens 1, 2, 4 — um buraco que nada mais adiante
        # sabe interpretar.
        sequencia.append(
            MensagemGerada(
                conteudo=conteudo, assunto=assunto, ordem=len(sequencia) + 1
            )
        )

    if len(sequencia) != esperado:
        logger.error(
            "ia: sequência de %s veio com %d mensagens utilizáveis, esperado %d",
            canal, len(sequencia), esperado,
        )
        return []
    return sequencia


def gerar_insights_estrategicos(
    lead: dict, cliente: httpx.Client | None = None
) -> dict:
    """Análise estratégica de 5 campos. ``{}`` em qualquer falha.

    ``potencial_oportunidade`` é normalizado (strip + lowercase) mas **não**
    forçado a um dos 3 valores esperados: um valor fora do esperado só faz o
    frontend cair num badge neutro, o que é melhor que inventar "baixo"
    silenciosamente quando a IA respondeu outra coisa.
    """
    if not settings.anthropic_configurada:
        logger.error("ia: ANTHROPIC_API_KEY ausente — geração não tentada")
        return {}

    texto = _chamar_ia(PROMPT_INSIGHTS.format(dados_lead=montar_contexto_insights(lead)), cliente)
    if not texto:
        return {}

    dados = extrair_json(texto)
    if dados is None:
        logger.error("ia: resposta de insights não parseou como JSON")
        return {}

    bruto = dados.get("recomendacao_abordagem")
    recomendacao = [str(r) for r in bruto][:3] if isinstance(bruto, list) else []

    resultado = {
        "resumo_estrategico": str(dados.get("resumo_estrategico") or "").strip(),
        "potencial_oportunidade": str(dados.get("potencial_oportunidade") or "").strip().lower(),
        "recomendacao_abordagem": recomendacao,
        "estrategia_comunicacao": str(dados.get("estrategia_comunicacao") or "").strip(),
        "cta_sugerido": str(dados.get("cta_sugerido") or "").strip(),
    }
    # Resposta em JSON válido mas sem nenhum conteúdo é falha, não sucesso —
    # persistir isso encheria a aba de campos vazios sem explicar por quê.
    if not resultado["resumo_estrategico"]:
        logger.error("ia: insights vieram sem resumo_estrategico")
        return {}
    return resultado
