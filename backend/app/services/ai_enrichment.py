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

## Defensividade

Estas funções rodam **dentro de um handler HTTP**, então nunca levantam por
erro de rede/HTTP nem por resposta malformada: devolvem conteúdo vazio
(``MensagemGerada(conteudo="")`` / ``{}``). Quem decide o que fazer com isso é
a rota, que devolve 502 em vez de persistir texto vazio.

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

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_PADRAO = 30.0
MAX_TOKENS_RESPOSTA = 1024

CANAIS_VALIDOS = ("email", "whatsapp")


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


def _chamar_ia(prompt: str, cliente: httpx.Client | None) -> str:
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
                "max_tokens": MAX_TOKENS_RESPOSTA,
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

PROMPT_ABORDAGEM_EMAIL = """Você é um redator de prospecção B2B para um escritório de contabilidade \
(Inova Contabilidade) especializado em produtores rurais e agronegócio no Paraná.

Escreva um E-MAIL de PRIMEIRO CONTATO — tom profissional, cordial e \
consultivo, corpo com 3 a 4 frases. A mensagem deve soar genuinamente \
personalizada (mencione pelo menos um dado real da lista abaixo) e terminar \
convidando para uma conversa breve.

{regras_de_tom}

Dados do lead:
{{dados_lead}}

Responda APENAS com um objeto JSON válido, sem texto antes ou depois, sem \
bloco de código markdown, no formato exato abaixo:

{{{{
  "assunto": "assunto curto e direto do e-mail, sem saudação, até 60 caracteres",
  "corpo": "o corpo do e-mail em si — sem saudação tipo \\"Prezado(a)\\", sem \
assinatura, sem aspas ao redor do texto"
}}}}
""".format(regras_de_tom=REGRAS_DE_TOM)

PROMPT_ABORDAGEM_WHATSAPP = """Você é um redator de prospecção B2B para um escritório de contabilidade \
(Inova Contabilidade) especializado em produtores rurais e agronegócio no Paraná.

Escreva uma mensagem de WHATSAPP de PRIMEIRO CONTATO — tom direto e próximo \
(mas sempre respeitoso), só 1 a 2 frases curtas. A mensagem deve soar \
genuinamente personalizada (mencione pelo menos um dado real da lista abaixo) \
e terminar com uma pergunta simples que convide a responder.

{regras_de_tom}

Dados do lead:
{{dados_lead}}

Responda APENAS com o texto da mensagem, sem aspas ao redor, sem assunto, \
sem assinatura, sem nenhum comentário seu antes ou depois.
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
    """``assunto`` só vem preenchido no canal "email"."""

    conteudo: str
    assunto: str | None = None


def gerar_mensagem_abordagem(
    lead: dict, canal: str, cliente: httpx.Client | None = None
) -> MensagemGerada:
    """Mensagem de primeiro contato personalizada.

    ``email`` é mais formal, 3-4 frases, e devolve assunto (a IA responde em
    JSON). ``whatsapp`` é curto, 1-2 frases, resposta em texto puro.

    Levanta ``ValueError`` para canal desconhecido — isso é erro de
    programação, não resposta da IA (a rota valida antes). Qualquer outra
    falha vira ``conteudo=""``, que a rota traduz em 502.
    """
    if canal not in CANAIS_VALIDOS:
        raise ValueError(f"Canal desconhecido: {canal!r}. Use um de {CANAIS_VALIDOS}.")

    if not settings.anthropic_configurada:
        logger.error("ia: ANTHROPIC_API_KEY ausente — geração não tentada")
        return MensagemGerada(conteudo="")

    template = PROMPT_ABORDAGEM_EMAIL if canal == "email" else PROMPT_ABORDAGEM_WHATSAPP
    texto = _chamar_ia(template.format(dados_lead=montar_contexto_abordagem(lead)), cliente)
    if not texto:
        return MensagemGerada(conteudo="")

    if canal == "whatsapp":
        return MensagemGerada(conteudo=texto.strip())

    dados = extrair_json(texto)
    if dados is None:
        logger.error("ia: resposta de e-mail não parseou como JSON")
        return MensagemGerada(conteudo="")

    corpo = str(dados.get("corpo") or "").strip()
    assunto_bruto = dados.get("assunto")
    assunto = str(assunto_bruto).strip() or None if assunto_bruto else None
    return MensagemGerada(conteudo=corpo, assunto=assunto)


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
