"""Scrape de site via Firecrawl + extração de Instagram e WhatsApp por regex.

Porte de ``app/services/site_scraping.py`` do Minotto — mesmo nome, mesmo
contrato.

```
POST https://api.firecrawl.dev/v1/scrape
Header: Authorization: Bearer {FIRECRAWL_API_KEY}
Body:   {"url": "<url>", "formats": ["markdown"]}
Sucesso: {"success": true, "data": {"markdown": "...", ...}}
Falha reportada (HTTP 200!): {"success": false, ...}
```

⚠️ **v1, não v2, e de propósito.** O repositório oficial só publica spec
v1; a documentação renderizada promove v2 sem spec bruto verificável.
Ficar na v1 é a decisão herdada do Minotto — migrar sem spec confiável
seria inventar schema. Revisitar quando houver spec publicado.

⚠️ **Nunca foi testado contra a API real**, nem lá nem aqui. O contrato
veio de fonte primária (spec oficial), não foi inventado, mas segue
pendente de uma chamada real — mesma categoria de ressalva do sentinela
``-1`` do Sicor e da resposta-sem-match da API Full.

## Extrair Instagram e WhatsApp do markdown não custa chamada

As duas extrações rodam sobre o markdown que o scrape **já** trouxe. É a
mesma economia que o Minotto documentou: não viram etapa própria, não
gastam requisição.

O ``extrair_whatsapp`` nasceu de um problema real lá: os 6 primeiros leads
voltaram "sem WhatsApp" porque o telefone vinha do Google Places (central
telefônica, fixo). O número que a empresa realmente atende costuma estar
no próprio site, num botão flutuante. Aqui vale menos — a API Full já dá
celular direto — mas continua sendo um segundo candidato de graça.

**Custo por lote mensal:** 1 scrape por lead **que tiver site**. Ver a
ressalva de cobertura no docstring de ``app.workers.enriquecimento``: hoje
só o lado CNPJ tem domínio, então isso é uma fração pequena dos 60.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

URL_SCRAPE = "https://api.firecrawl.dev/v1/scrape"
TIMEOUT_PADRAO = 30.0  # scrape demora mais que consulta comum

#: Paths do Instagram que são rotas do site, não perfis de usuário.
INSTAGRAM_PATHS_RESERVADOS = frozenset(
    {"p", "reel", "reels", "stories", "explore", "accounts", "share"}
)
INSTAGRAM_REGEX = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.IGNORECASE)

# ⚠️ Exige DÍGITO logo após a barra: `wa.me/<alfanumérico>` também é link
# de convite de grupo, que não é telefone.
WHATSAPP_REGEXES = (
    re.compile(r"wa\.me/(\+?[\d][\d\s().-]{9,19})", re.IGNORECASE),
    re.compile(
        r"api\.whatsapp\.com/send\?[^\s\"'<>]*?phone=(\+?[\d][\d\s().-]{9,19})",
        re.IGNORECASE,
    ),
    re.compile(
        r"web\.whatsapp\.com/send\?[^\s\"'<>]*?phone=(\+?[\d][\d\s().-]{9,19})",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class ResultadoScrape:
    url: str = ""
    sucesso: bool = False
    markdown: str = ""
    instagram: str | None = None
    whatsapp: str | None = None
    erro: str | None = None

    @property
    def tem_conteudo(self) -> bool:
        return self.sucesso and bool(self.markdown.strip())


def extrair_dominio(url: str | None) -> str | None:
    """Domínio sem ``www.``, tolerando URL sem esquema."""
    if not url or not url.strip():
        return None
    bruto = url.strip()
    analisada = urlparse(bruto if "//" in bruto else f"//{bruto}")
    dominio = (analisada.netloc or "").split("@")[-1].split(":")[0].lower()
    dominio = dominio[4:] if dominio.startswith("www.") else dominio
    return dominio or None


def extrair_instagram(texto: str | None) -> str | None:
    """Primeiro link de PERFIL do Instagram, ignorando rotas do site."""
    for achado in INSTAGRAM_REGEX.finditer(texto or ""):
        candidato = achado.group(1).rstrip(".")
        if candidato.lower() not in INSTAGRAM_PATHS_RESERVADOS:
            return candidato
    return None


def extrair_whatsapp(texto: str | None) -> str | None:
    """Primeiro número de WhatsApp divulgado no site, já normalizado.

    Import local de ``formatar_numero`` de propósito: evita ciclo entre
    ``site_scraping`` e ``whatsapp``, e mantém a normalização com uma dona
    só — reimplementar a heurística de código do país aqui seria
    exatamente o risco de transcrição silenciosa que o Minotto documentou.
    """
    from app.services.whatsapp import formatar_numero

    for regex in WHATSAPP_REGEXES:
        for achado in regex.finditer(texto or ""):
            numero = formatar_numero(achado.group(1))
            if numero:
                return numero
    return None


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if cliente is not None:
        return cliente, False
    return httpx.Client(timeout=TIMEOUT_PADRAO), True


def raspar_site(url: str, cliente: httpx.Client | None = None) -> ResultadoScrape:
    """Busca o markdown da URL e já extrai Instagram e WhatsApp. Nunca levanta.

    ``sucesso=False`` quando o Firecrawl reporta falha no campo ``success``
    — isso é **HTTP 200 com corpo dizendo que não deu**, não erro HTTP.
    """
    if not url or not url.strip():
        return ResultadoScrape(erro="sem URL — scrape não feito")
    if not settings.firecrawl_configurada and cliente is None:
        return ResultadoScrape(
            url=url, erro="FIRECRAWL_API_KEY ausente no ambiente — etapa pulada"
        )

    http, meu = _cliente(cliente)
    try:
        resposta = http.post(
            URL_SCRAPE,
            headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
            json={"url": url, "formats": ["markdown"]},
        )
        if resposta.status_code >= 400:
            return ResultadoScrape(
                url=url, erro=f"HTTP {resposta.status_code} do Firecrawl"
            )
        corpo = resposta.json()
        if not isinstance(corpo, dict) or not corpo.get("success"):
            return ResultadoScrape(url=url, erro="Firecrawl reportou success=false")
        markdown = ((corpo.get("data") or {}).get("markdown")) or ""
        return ResultadoScrape(
            url=url,
            sucesso=True,
            markdown=markdown,
            instagram=extrair_instagram(markdown),
            whatsapp=extrair_whatsapp(markdown),
        )
    except Exception as exc:  # noqa: BLE001 — nunca levanta pro chamador
        motivo = erro_redigido(exc)
        logger.warning("site_scraping: falha raspando site — %s", motivo)
        return ResultadoScrape(url=url, erro=motivo)
    finally:
        if meu:
            http.close()
