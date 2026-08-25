"""Leitura do site por IA — alimenta o critério ``presenca_digital``.

## ⚠️ Este módulo NÃO é o ``ai_enrichment.py`` do Minotto

O módulo de IA do Minotto tem três funções: ``inferir_dados_site`` (menção a
RQE e especialidades médicas), ``gerar_mensagem_abordagem`` e
``gerar_insights_estrategicos``. **Só a mecânica da primeira foi portada** —
o cliente HTTP, o modelo, e o ``_extrair_json`` defensivo. As outras duas
ficaram de fora de propósito: geração de mensagem e insights não foram
pedidos, e o padrão de limite-de-geração-por-lead que existe lá para conter
o custo delas também não veio junto. Ver o relatório da sessão.

O *prompt* também não foi copiado: perguntar sobre RQE e especialidade
médica não faz sentido nenhum para produtor de grãos. O que se pergunta aqui
é exatamente o que o critério da Inova define — "Presença digital
(site/Instagram)", camada ``INFERENCIA``.

```
POST https://api.anthropic.com/v1/messages
Headers: x-api-key: {ANTHROPIC_API_KEY}, anthropic-version: 2023-06-01
Body:   {"model", "max_tokens", "messages": [{"role": "user", "content": ...}]}
Resposta: {"content": [{"type": "text", "text": "..."}], ...}
```

⚠️ **HTTP puro, sem SDK** — decisão registrada na §2 do docs_fundacao.md
("HTTP puro — sem SDK, por consistência com o resto do projeto"), e é o que
o Minotto faz. Vale saber que a orientação atual da Anthropic é usar o SDK
oficial; manter HTTP puro aqui é escolha do projeto, não desconhecimento.

**Modelo:** ``claude-haiku-4-5-20251001`` — o snapshot **com** sufixo de
data, igual ao Minotto. Pinar o snapshot é deliberado: o alias sem data
aponta para o snapshot mais recente e pode mudar de comportamento sozinho,
o que numa etapa de classificação mexeria no ``presenca_digital`` sem
ninguém alterar código. Classificação de texto curto não precisa de modelo
caro.

**Custo por lote mensal:** 1 chamada por lead **que teve site lido com
sucesso**. Como quase nenhum lead do lado CPF tem site (ver
``app.workers.enriquecimento``), na prática é uma fração pequena dos 60 — e
alimenta um critério de peso 5 que ainda está marcado como provisório.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

URL_MENSAGENS = "https://api.anthropic.com/v1/messages"
VERSAO_API = "2023-06-01"
TIMEOUT_PADRAO = 30.0
MAX_TOKENS = 512

#: Limite de texto enviado — o site inteiro não cabe nem é necessário.
LIMITE_CARACTERES = 12_000

PROMPT = """Você recebe o conteúdo em markdown do site de uma empresa ou \
produtor do agronegócio brasileiro. Avalie a PRESENÇA DIGITAL dela.

Responda SOMENTE com JSON, sem texto antes ou depois, neste formato:
{{"ativa": true|false, "intensidade": 0.0..1.0, "indicios": ["...", "..."]}}

- "ativa": o site parece de uma operação real e mantida (não é domínio \
parqueado, página em construção, nem erro).
- "intensidade": 0.0 = nenhuma presença útil; 1.0 = site completo, com \
contato, descrição da atividade e sinais de atualização recente.
- "indicios": até 4 observações curtas que justificam a nota.

Conteúdo do site:
---
{conteudo}
---"""


@dataclass(frozen=True, slots=True)
class ResultadoAnaliseSite:
    ativa: bool = False
    intensidade: float = 0.0
    indicios: tuple[str, ...] = ()
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None


def extrair_json(texto: str) -> dict | None:
    """Tenta achar o JSON na resposta, mesmo se a IA embrulhar ou comentar.

    Portado do ``_extrair_json`` do Minotto, junto com o motivo: mesmo
    instruída a responder só JSON, a IA às vezes embrulha em bloco de código
    markdown ou acrescenta texto. Falhar aqui devolve ``None`` e o chamador
    trata — não levanta.
    """
    if not texto:
        return None
    tentativas = [texto.strip()]
    bloco = re.search(r"```(?:json)?\s*(.+?)```", texto, re.DOTALL)
    if bloco:
        tentativas.append(bloco.group(1).strip())
    chaves = re.search(r"\{.*\}", texto, re.DOTALL)
    if chaves:
        tentativas.append(chaves.group(0))
    for candidato in tentativas:
        try:
            valor = json.loads(candidato)
            if isinstance(valor, dict):
                return valor
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if cliente is not None:
        return cliente, False
    return httpx.Client(timeout=TIMEOUT_PADRAO), True


def analisar_site(
    conteudo: str, cliente: httpx.Client | None = None
) -> ResultadoAnaliseSite:
    """Avalia a presença digital a partir do markdown do site. Nunca levanta."""
    if not conteudo or not conteudo.strip():
        return ResultadoAnaliseSite(erro="sem conteúdo de site — IA não consultada")
    if not settings.anthropic_configurada and cliente is None:
        return ResultadoAnaliseSite(
            erro="ANTHROPIC_API_KEY ausente no ambiente — etapa pulada"
        )

    http, meu = _cliente(cliente)
    try:
        resposta = http.post(
            URL_MENSAGENS,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": VERSAO_API,
                "content-type": "application/json",
            },
            json={
                "model": settings.ANTHROPIC_MODEL,
                "max_tokens": MAX_TOKENS,
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT.format(
                            conteudo=conteudo[:LIMITE_CARACTERES]
                        ),
                    }
                ],
            },
        )
        if resposta.status_code >= 400:
            return ResultadoAnaliseSite(
                erro=f"HTTP {resposta.status_code} da API da Anthropic"
            )
        corpo = resposta.json() or {}
        blocos = corpo.get("content") or []
        texto = "".join(
            b.get("text") or ""
            for b in blocos
            if isinstance(b, dict) and b.get("type") == "text"
        )
        dados = extrair_json(texto)
        if dados is None:
            return ResultadoAnaliseSite(erro="resposta da IA não veio em JSON legível")
        try:
            intensidade = float(dados.get("intensidade") or 0.0)
        except (TypeError, ValueError):
            intensidade = 0.0
        indicios = dados.get("indicios")
        return ResultadoAnaliseSite(
            ativa=bool(dados.get("ativa")),
            intensidade=max(0.0, min(1.0, intensidade)),
            indicios=tuple(str(i) for i in indicios[:4]) if isinstance(indicios, list) else (),
        )
    except Exception as exc:  # noqa: BLE001 — nunca levanta pro chamador
        motivo = erro_redigido(exc)
        logger.warning("ai_site: falha analisando site — %s", motivo)
        return ResultadoAnaliseSite(erro=motivo)
    finally:
        if meu:
            http.close()
