"""E-mail: Hunter.io (encontrar) → MX (dnspython) → ZeroBounce (validar).

Porte de ``app/services/email_enrichment.py`` do Minotto — **um módulo só
para os dois serviços**, como lá (não existe ``hunter.py`` nem
``zerobounce.py`` separados).

```
Hunter Email Finder : GET https://api.hunter.io/v2/email-finder
                      ?domain&first_name&last_name
                      Header: Authorization: Bearer {HUNTER_API_KEY}
Hunter Domain Search: GET https://api.hunter.io/v2/domain-search  (FALLBACK)
ZeroBounce Validate : GET https://api.zerobounce.net/v2/validate?email&api_key
```

## Ordem e parada antecipada — é onde o custo é contido

``enriquecer_email`` encadeia Hunter → MX → ZeroBounce e **para cedo**: se
o Hunter não achar e-mail, ou se o domínio não tiver MX, o ZeroBounce (que
é consulta paga) nem é chamado.

**Custo por lote mensal:** 1 consulta Hunter por lead **com domínio
utilizável**, mais 1 ZeroBounce só para os que passarem. Não são 60 —
depende da cobertura de domínio, que hoje é baixa (ver
``app.workers.enriquecimento``). O plano Free do Hunter é 50/mês, e no
Minotto um teste esquecido chegou a queimar um crédito real.

⚠️ ``HUNTER_DOMAIN_SEARCH_FALLBACK`` **dobra o consumo** — desligado por
padrão, e só ligar depois de confirmar o plano contratado (§5).

## ⚠️ Credencial no header, e a exceção que continua na query

- **Hunter**: header ``Authorization: Bearer``. No Minotto isso foi
  **verificado contra a API real** com chave válida (401 sem, 200 com).
- **ZeroBounce**: continua na query string porque a API não documenta
  alternativa. É risco aceito e **documentado** (§6), e a proteção é 100%
  a redação no log — por isso todo erro daqui passa por ``erro_redigido``.

## ⚠️ Domínio de PLATAFORMA nunca vira domínio de e-mail

Bug real de produção no Minotto: um lead saiu com
``eladiosouza@instagram.com``. A cadeia foi Google Places devolver o
Instagram como "site" → virar domínio → o Hunter **gerar** um endereço
plausível a partir do padrão do domínio (``source_type: generated``). O
Hunter respondeu certo à pergunta errada; a falha foi nossa, na fronteira.
O ZeroBounce salvou o score (devolveu ``invalid``), mas **não salvou o
dossiê** — o endereço falso aparecia como contato acionável pro vendedor.

⚠️ **Não incluir provedor gratuito nesta lista.** É tentador e errado: no
Brasil o produtor rural usa Gmail/Hotmail como contato REAL — na amostra
da Receita, 110 dos 396 e-mails são Gmail e 67 Hotmail. Bloquear isso
descartaria lead legítimo. A linha é específica: domínio de PLATAFORMA,
onde ninguém tem caixa de entrada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import dns.exception
import dns.resolver
import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

URL_HUNTER_FINDER = "https://api.hunter.io/v2/email-finder"
URL_HUNTER_DOMINIO = "https://api.hunter.io/v2/domain-search"
URL_ZEROBOUNCE = "https://api.zerobounce.net/v2/validate"
TIMEOUT_PADRAO = 15.0

#: O `limit` não muda o custo — o crédito é por REQUISIÇÃO.
HUNTER_LIMITE_DOMINIO = 10

#: Do ZeroBounce: valid | invalid | catch-all | do_not_mail | abuse.
ZEROBOUNCE_STATUS_APROVADOS = frozenset({"valid", "catch-all"})

#: "personal" (joao@agro.com) antes de "generic" (contato@agro.com) — mas
#: genérico ainda vale: pra prospecção é contato utilizável, e às vezes é o
#: único disponível.
_ORDEM_TIPO = {"personal": 0, "generic": 1}

#: Não servem como sobrenome pro Hunter.
PARTICULAS_SOBRENOME = frozenset(
    {"de", "da", "do", "das", "dos", "e", "del", "di", "la", "van", "von"}
)

DOMINIOS_SEM_EMAIL_CORPORATIVO = frozenset(
    {
        "instagram.com", "facebook.com", "fb.com", "twitter.com", "x.com",
        "linkedin.com", "tiktok.com", "youtube.com", "pinterest.com",
        "threads.net",
        "whatsapp.com", "wa.me", "t.me", "telegram.me",
        "linktr.ee", "linktree.com", "bio.link", "beacons.ai",
    }
)


@dataclass(frozen=True, slots=True)
class ResultadoEmail:
    email: str = ""
    origem: str = ""          # "hunter_finder" | "hunter_dominio" | ""
    confianca: int | None = None
    mx_valido: bool = False
    status_zerobounce: str = ""
    aprovado: bool = False
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.email) and self.erro is None


def dominio_utilizavel(dominio: str | None) -> bool:
    """Domínio que pode ter caixa de entrada corporativa."""
    if not dominio or "." not in dominio:
        return False
    return dominio.strip().lower() not in DOMINIOS_SEM_EMAIL_CORPORATIVO


def dividir_nome(nome: str | None) -> tuple[str | None, str | None]:
    """``"JOAO DA SILVA SOUZA"`` → ``("JOAO", "SOUZA")``.

    Pega o ÚLTIMO token como sobrenome, não a string restante inteira —
    nome brasileiro longo quebraria a busca do Hunter (§6). Partículas
    (``da``, ``dos``…) não servem como sobrenome.
    """
    partes = [p for p in (nome or "").split() if p]
    if not partes:
        return None, None
    if len(partes) == 1:
        return partes[0], None
    for candidato in reversed(partes[1:]):
        if candidato.lower() not in PARTICULAS_SOBRENOME:
            return partes[0], candidato
    return partes[0], None


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Sem ``base_url``: Hunter e ZeroBounce são hosts diferentes."""
    if cliente is not None:
        return cliente, False
    return httpx.Client(timeout=TIMEOUT_PADRAO), True


def _cabecalho_hunter() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.HUNTER_API_KEY}"}


def buscar_email_hunter(
    dominio: str, nome_decisor: str | None, cliente: httpx.Client | None = None
) -> ResultadoEmail:
    """Email Finder. Nunca levanta."""
    if not dominio_utilizavel(dominio):
        return ResultadoEmail(erro=f"domínio {dominio!r} não tem e-mail corporativo")
    if not settings.hunter_configurada and cliente is None:
        return ResultadoEmail(erro="HUNTER_API_KEY ausente no ambiente — etapa pulada")

    primeiro, ultimo = dividir_nome(nome_decisor)
    http, meu = _cliente(cliente)
    try:
        params = {"domain": dominio}
        if primeiro:
            params["first_name"] = primeiro
        if ultimo:
            params["last_name"] = ultimo
        resposta = http.get(URL_HUNTER_FINDER, params=params, headers=_cabecalho_hunter())
        if resposta.status_code >= 400:
            return ResultadoEmail(erro=f"HTTP {resposta.status_code} do Hunter")
        dados = (resposta.json() or {}).get("data") or {}
        email = (dados.get("email") or "").strip().lower()
        if not email:
            return ResultadoEmail(erro="Hunter não encontrou e-mail para o domínio")
        return ResultadoEmail(
            email=email, origem="hunter_finder", confianca=dados.get("score")
        )
    except Exception as exc:  # noqa: BLE001
        motivo = erro_redigido(exc)
        logger.warning("email: falha no Hunter finder — %s", motivo)
        return ResultadoEmail(erro=motivo)
    finally:
        if meu:
            http.close()


def buscar_email_dominio(
    dominio: str, cliente: httpx.Client | None = None
) -> ResultadoEmail:
    """Domain Search — **fallback que DOBRA o consumo**. Desligado por padrão."""
    if not settings.HUNTER_DOMAIN_SEARCH_FALLBACK and cliente is None:
        return ResultadoEmail(erro="fallback domain-search desligado (dobra o consumo)")
    if not dominio_utilizavel(dominio):
        return ResultadoEmail(erro=f"domínio {dominio!r} não tem e-mail corporativo")

    http, meu = _cliente(cliente)
    try:
        resposta = http.get(
            URL_HUNTER_DOMINIO,
            params={"domain": dominio, "limit": HUNTER_LIMITE_DOMINIO},
            headers=_cabecalho_hunter(),
        )
        if resposta.status_code >= 400:
            return ResultadoEmail(erro=f"HTTP {resposta.status_code} do Hunter")
        emails = ((resposta.json() or {}).get("data") or {}).get("emails") or []
        candidatos = [e for e in emails if isinstance(e, dict) and e.get("value")]
        if not candidatos:
            return ResultadoEmail(erro="domain-search não devolveu e-mail")
        melhor = min(
            candidatos,
            key=lambda e: (
                _ORDEM_TIPO.get((e.get("type") or "").lower(), 99),
                -(e.get("confidence") or 0),
            ),
        )
        return ResultadoEmail(
            email=str(melhor["value"]).strip().lower(),
            origem="hunter_dominio",
            confianca=melhor.get("confidence"),
        )
    except Exception as exc:  # noqa: BLE001
        motivo = erro_redigido(exc)
        logger.warning("email: falha no Hunter domain-search — %s", motivo)
        return ResultadoEmail(erro=motivo)
    finally:
        if meu:
            http.close()


def validar_mx(dominio: str, resolver=dns.resolver.resolve) -> bool:
    """Domínio tem registro MX? Gratuito, e evita gastar ZeroBounce à toa.

    ⚠️ Só falha de DNS vira ``False`` silencioso — que é o resultado
    legítimo de "esse domínio não recebe e-mail". Qualquer outra exceção é
    **logada** antes de virar ``False``: um erro de programação (assinatura
    errada, tipo inesperado) que se disfarçasse de "domínio sem MX" mandaria
    todo lead para o mesmo caminho errado sem nenhum sinal. Foi exatamente o
    que um teste desta sessão pegou.
    """
    try:
        return bool(resolver(dominio, "MX"))
    except dns.exception.DNSException:
        return False
    except Exception as exc:  # noqa: BLE001 — nunca levanta, mas nunca cala
        logger.error(
            "validar_mx falhou por motivo NÃO-DNS em %s — tratando como sem MX: %s",
            dominio, erro_redigido(exc),
        )
        return False


def validar_zerobounce(
    email: str, cliente: httpx.Client | None = None
) -> ResultadoEmail:
    """Valida entregabilidade. Nunca levanta.

    ⚠️ A chave vai na QUERY STRING — a API não documenta alternativa. Risco
    aceito e documentado; a proteção é a redação no log.
    """
    if not email:
        return ResultadoEmail(erro="sem e-mail para validar")
    if not settings.zerobounce_configurada and cliente is None:
        return ResultadoEmail(
            email=email, erro="ZEROBOUNCE_API_KEY ausente no ambiente — etapa pulada"
        )

    http, meu = _cliente(cliente)
    try:
        resposta = http.get(
            URL_ZEROBOUNCE,
            params={"email": email, "api_key": settings.ZEROBOUNCE_API_KEY},
        )
        if resposta.status_code >= 400:
            return ResultadoEmail(
                email=email, erro=f"HTTP {resposta.status_code} do ZeroBounce"
            )
        status = str((resposta.json() or {}).get("status") or "").strip().lower()
        return ResultadoEmail(
            email=email,
            status_zerobounce=status,
            aprovado=status in ZEROBOUNCE_STATUS_APROVADOS,
        )
    except Exception as exc:  # noqa: BLE001
        motivo = erro_redigido(exc)
        logger.warning("email: falha no ZeroBounce — %s", motivo)
        return ResultadoEmail(email=email, erro=motivo)
    finally:
        if meu:
            http.close()


def enriquecer_email(
    dominio: str | None,
    nome_decisor: str | None = None,
    *,
    cliente_hunter: httpx.Client | None = None,
    cliente_zerobounce: httpx.Client | None = None,
    resolver_mx=dns.resolver.resolve,
    email_conhecido: str | None = None,
) -> ResultadoEmail:
    """Hunter → MX → ZeroBounce, parando cedo pra não gastar à toa.

    ``email_conhecido`` é atalho: quando a fonte já entregou um e-mail (a
    Receita publica ``correio_eletronico``, a API Full e a BrasilAPI também
    devolvem), **o Hunter nem é consultado** — só valida. É o corte de
    custo mais direto desta etapa, e não existe no Minotto porque lá
    nenhuma fonte gratuita trazia e-mail.
    """
    if email_conhecido and email_conhecido.strip():
        achado = ResultadoEmail(email=email_conhecido.strip().lower(), origem="fonte_gratuita")
    else:
        if not dominio_utilizavel(dominio):
            return ResultadoEmail(erro=f"sem domínio utilizável ({dominio!r})")
        achado = buscar_email_hunter(dominio, nome_decisor, cliente=cliente_hunter)
        if not achado.ok and settings.HUNTER_DOMAIN_SEARCH_FALLBACK:
            achado = buscar_email_dominio(dominio, cliente=cliente_hunter)
        if not achado.ok:
            return achado

    dominio_do_email = achado.email.split("@")[-1]
    if not validar_mx(dominio_do_email, resolver=resolver_mx):
        return ResultadoEmail(
            email=achado.email, origem=achado.origem, confianca=achado.confianca,
            mx_valido=False, erro="domínio sem MX — ZeroBounce não consultado",
        )

    validacao = validar_zerobounce(achado.email, cliente=cliente_zerobounce)
    return ResultadoEmail(
        email=achado.email,
        origem=achado.origem,
        confianca=achado.confianca,
        mx_valido=True,
        status_zerobounce=validacao.status_zerobounce,
        aprovado=validacao.aprovado,
        erro=validacao.erro,
    )
