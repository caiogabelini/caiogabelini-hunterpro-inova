"""Enriquecimento pago dos candidatos pré-selecionados — **só o decisor**.

Mora em módulo próprio pelo mesmo motivo de ``app.workers.busca``: manter
``celery_app.py`` fino e a lógica testável sem broker. ``busca`` delega pra
cá, e nada aqui roda antes da pré-seleção ter cortado o volume.

## Ordem das etapas, e de onde vem cada dado

```
decisor      CPF → API Full (paga) | CNPJ → BrasilAPI (grátis)
   │             ...também trazem TELEFONE e às vezes E-MAIL
   ├── site      Firecrawl, se houver domínio  ──> markdown
   │      ├── instagram   regex sobre o markdown  (custo zero)
   │      └── whatsapp    regex sobre o markdown  (custo zero)
   ├── whatsapp  Evolution API sobre o melhor telefone disponível
   ├── email     Hunter → MX → ZeroBounce (pula o Hunter se já houver e-mail)
   └── ia        analisa o markdown ──> presenca_digital
```

⚠️ **A cadeia do Minotto começa antes: no Google Places.** Lá é ele que
descobre o ``site_url`` a partir do nome da empresa, e todo o resto pende
disso. O Google Places **não foi portado** — não estava no escopo pedido, e
trazê-lo por conta própria seria justamente o tipo de decisão que precisa
passar pelo Caio. Ver o relatório da sessão.

Sem ele, o ``site_url`` daqui sai do **domínio do e-mail** que as fontes já
entregam de graça: ``correio_eletronico`` no arquivo da Receita (preenchido
em 67,3% dos CNPJ agro ativos do PR), ``email`` da BrasilAPI, e os e-mails da
API Full. Domínio de provedor gratuito (gmail, hotmail) **não vira site** —
não existe site em ``gmail.com``.

⚠️ **Consequência medida, não suposta:** o lado CPF praticamente não tem
domínio próprio, então site/Instagram/IA quase não rodam para ele. É
coerente com o que a Carolina disse no kickoff — presença digital é fraca
nesse perfil — mas aqui a causa é mais dura que "o produtor não tem site":
**não temos por onde procurar**. WhatsApp é a exceção e roda para todo
mundo, porque o telefone vem direto do bureau.

## ⛔ O que continua fora

Geração de mensagem por IA e insights estratégicos (existem no
``ai_enrichment.py`` do Minotto) **não** foram portados — não foram pedidos.
Junto com eles fica de fora o padrão de limite-de-geração-por-lead que
controla o custo deles lá.

## Duas fontes, escolhidas pelo tipo de documento

```
CNPJ  -> BrasilAPI   (gratuita, pública)          ~3% dos leads
CPF   -> API Full    (bureau privado, PAGA)       ~97% dos leads
```

Essa proporção não é detalhe: a fonte paga é o caminho de quase todo lead.
Com ``LEADS_POR_BUSCA`` = 50 e margem 1,2 são até 60 candidatos por busca,
dos quais ~58 CPF — logo ~58 chamadas pagas por lote mensal.

## Cada etapa é isolada (§6)

Uma consulta que falha vira ``None`` e um registro em ``etapas_puladas``; o
lead segue sem decisor em vez de derrubar o lote. Sem isso, um timeout no 12º
de 60 leads perderia os 11 já processados — a decisão de resiliência mais
importante do enriquecimento.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.core.documentos import TIPO_CNPJ, TIPO_CPF, detectar_tipo_documento
from app.core.segredos import erro_redigido
from app.scoring.compute_lead_score import calcular_score
from app.scoring.pre_selecao import Candidato, sinais_gratuitos_sicor
from app.services import ai_site, api_full, brasil_api, email_enrichment, site_scraping
from app.services import whatsapp as whatsapp_service
from app.services.email_enrichment import dominio_utilizavel

logger = logging.getLogger(__name__)

ETAPA_DECISOR = "enrich_decisor"
ETAPA_SITE = "enrich_site_firecrawl"
ETAPA_WHATSAPP = "validate_whatsapp"
ETAPA_EMAIL = "enrich_email"
ETAPA_IA = "enrich_presenca_digital"

#: Provedores gratuitos: e-mail real, mas o domínio NÃO é um site da empresa.
#: Diferente de `DOMINIOS_SEM_EMAIL_CORPORATIVO`, que é sobre caixa de
#: entrada. Aqui a pergunta é outra: "dá pra raspar um site nesse domínio?".
# ⚠️ Inclui os provedores de ISP BRASILEIROS, não só os globais. A primeira
# versão desta lista só tinha gmail/hotmail/outlook e companhia, e a medição
# contra os 4 CPFs reais marcou `oi.com.br` como "domínio próprio" — ou seja,
# teria mandado o Firecrawl raspar o site da operadora de telefonia achando
# que era o site do produtor, e ainda contado isso como presença digital.
DOMINIOS_SEM_SITE_PROPRIO = frozenset(
    {
        # Globais
        "gmail.com", "googlemail.com", "hotmail.com", "hotmail.com.br",
        "outlook.com", "outlook.com.br", "live.com", "msn.com",
        "yahoo.com", "yahoo.com.br", "icloud.com", "me.com", "aol.com",
        "protonmail.com", "proton.me", "gmx.com", "zoho.com",
        # Provedores e ISPs brasileiros
        "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br",
        "oi.com.br", "vivo.com.br", "globo.com", "globomail.com", "r7.com",
        "zipmail.com.br", "superig.com.br", "brturbo.com.br",
        "itelefonica.com.br", "pop.com.br", "click21.com.br", "veloxmail.com.br",
        "yahoo.com.mx", "onda.com.br", "netsite.com.br",
    }
)


def _rodar_etapa(nome: str, funcao, puladas: list[dict[str, str]]):
    """Isola uma etapa: exceção vira etapa pulada, não derruba o lead.

    A decisão de resiliência mais importante do enriquecimento (§6): sem ela,
    um timeout do Firecrawl no 12º de 60 leads derrubaria os 11 já
    processados. Toda mensagem passa por ``erro_redigido`` — é por aqui que
    TODA falha vira log, e exceção de HTTP carrega URL com credencial.
    """
    try:
        return funcao()
    except Exception as exc:  # noqa: BLE001
        motivo = erro_redigido(exc)
        logger.error("etapa %s falhou — seguindo sem esse sinal. Erro: %s", nome, motivo)
        puladas.append({"etapa": nome, "motivo": motivo})
        return None


def dominio_raspavel(dominio: str | None) -> bool:
    """Domínio que pode ter site próprio (exclui provedor gratuito)."""
    if not dominio_utilizavel(dominio):
        return False
    return dominio.strip().lower() not in DOMINIOS_SEM_SITE_PROPRIO


@dataclass(frozen=True, slots=True)
class LeadEnriquecido:
    """Candidato + o que o enriquecimento conseguiu resolver."""

    candidato: Candidato
    nome: str = ""
    decisor: str = ""
    telefones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    #: Fonte que resolveu o decisor: "api_full", "brasil_api" ou "" (nenhuma).
    fonte_decisor: str = ""
    site_url: str = ""
    instagram: str = ""
    tem_whatsapp: bool = False
    whatsapp_numero: str = ""
    email_aprovado: bool = False
    email_status: str = ""
    presenca_digital: float = 0.0
    #: Score final (0–100) e prioridade, já com TODOS os sinais coletados.
    score: int | None = None
    prioridade: str | None = None
    etapas_puladas: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def decisor_identificavel(self) -> bool:
        """O sinal que alimenta o critério de peso 20 no score final."""
        return bool(self.decisor)

    @property
    def sinais_para_score(self) -> dict[str, Any]:
        """O que o enriquecimento acrescenta ao dict de sinais do score.

        Junta-se aos sinais gratuitos da pré-seleção
        (``sinais_gratuitos_sicor``) pra formar o dict que
        ``compute_lead_score.calcular_score`` consome.

        ⚠️ ``False`` e não ``None``: ``False`` é "medimos e não achamos",
        ``None`` seria "não medimos" (§6). Só um sinal cuja etapa foi PULADA
        deve ficar ausente do dict.
        """
        sinais: dict[str, Any] = {
            "decisor_identificavel": self.decisor or False,
            "whatsapp_ativo": self.tem_whatsapp,
            "email_validado": self.email_aprovado,
            "presenca_digital": self.presenca_digital,
        }
        return sinais


def _pular(motivo: str, etapa: str = ETAPA_DECISOR) -> dict[str, str]:
    logger.info("enriquecimento: etapa '%s' pulada — %s", etapa, motivo)
    return {"etapa": etapa, "motivo": motivo}


def resolver_decisor_cpf(
    documento: str, *, cliente: Any = None
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], dict[str, str] | None]:
    """CPF via API Full. Devolve ``(nome, decisor, telefones, emails, pulada)``.

    Em pessoa física o produtor **é** o decisor — não há quadro societário a
    interpretar, ao contrário do CNPJ.
    """
    resultado = api_full.consultar_cpf(documento, cliente=cliente)
    if not resultado.ok:
        return "", "", (), (), _pular(resultado.erro or "API Full sem dado")
    telefones = tuple(t.e164 for t in resultado.telefones)
    return resultado.nome, resultado.nome, telefones, resultado.emails, None


def resolver_decisor_cnpj(
    documento: str, *, cliente: Any = None
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], dict[str, str] | None]:
    """CNPJ via BrasilAPI. O decisor sai do quadro societário (QSA)."""
    resultado = brasil_api.consultar_cnpj(documento, cliente=cliente)
    if not resultado.ok:
        return "", "", (), (), _pular(resultado.erro or "BrasilAPI sem dado")
    socio = brasil_api.identificar_decisor(resultado.socios)
    telefones = (resultado.telefone,) if resultado.telefone else ()
    emails = (resultado.email,) if resultado.email else ()
    pulada = None if socio else _pular("CNPJ sem quadro societário publicado")
    return (
        resultado.razao_social,
        socio.nome if socio else "",
        telefones,
        emails,
        pulada,
    )


def enriquecer_decisor(
    candidato: Candidato,
    *,
    cliente_api_full: Any = None,
    cliente_brasil_api: Any = None,
) -> LeadEnriquecido:
    """Resolve o decisor de UM candidato, escolhendo a fonte pelo documento.

    Nunca levanta: falha de fonte vira etapa pulada com motivo.
    """
    try:
        tipo = detectar_tipo_documento(candidato.documento)
    except ValueError as exc:
        return LeadEnriquecido(
            candidato=candidato,
            etapas_puladas=(_pular(f"documento inválido: {exc}"),),
        )

    if tipo == TIPO_CPF:
        nome, decisor, telefones, emails, pulada = resolver_decisor_cpf(
            candidato.documento, cliente=cliente_api_full
        )
        fonte = "api_full"
    elif tipo == TIPO_CNPJ:
        nome, decisor, telefones, emails, pulada = resolver_decisor_cnpj(
            candidato.documento, cliente=cliente_brasil_api
        )
        fonte = "brasil_api"
    else:  # pragma: no cover — detectar_tipo_documento só devolve CPF/CNPJ
        return LeadEnriquecido(
            candidato=candidato, etapas_puladas=(_pular(f"tipo {tipo} sem fonte"),)
        )

    # TODO(fase futura): as etapas pagas que ainda NÃO entram aqui, na ordem
    # de dependência da §3. Cada uma exige sua própria decisão de custo:
    #   - search_google_places      (site, rating, telefone) — depende do nome
    #   - enrich_site_firecrawl     (scrape + wa.me) — depende do site
    #   - validate_whatsapp         (Evolution API) — prioriza CELULAR; a
    #     API Full não diz o tipo, então usar Telefone.eh_celular
    #   - enrich_email              (Hunter.io + ZeroBounce) — a API Full já
    #     devolve e-mail em parte dos casos; medir quanto isso reduz o
    #     consumo do Hunter ANTES de contratar volume lá
    #   - presenca_digital          (IA sobre o scrape)

    return LeadEnriquecido(
        candidato=candidato,
        nome=nome,
        decisor=decisor,
        telefones=telefones,
        emails=emails,
        fonte_decisor=fonte if decisor else "",
        etapas_puladas=(pulada,) if pulada else (),
    )


def enriquecer_lote(
    selecionados: Sequence[Candidato],
    *,
    cliente_api_full: Any = None,
    cliente_brasil_api: Any = None,
) -> list[LeadEnriquecido]:
    """Enriquece o lote inteiro, isolando a falha de cada lead (§6).

    Um lead que falha não interrompe os seguintes — é a diferença entre
    perder 1 e perder os 11 já processados.
    """
    enriquecidos: list[LeadEnriquecido] = []
    for candidato in selecionados:
        try:
            enriquecidos.append(
                enriquecer_decisor(
                    candidato,
                    cliente_api_full=cliente_api_full,
                    cliente_brasil_api=cliente_brasil_api,
                )
            )
        except Exception as exc:  # noqa: BLE001 — isolamento por lead
            from app.core.segredos import erro_redigido

            enriquecidos.append(
                LeadEnriquecido(
                    candidato=candidato,
                    etapas_puladas=(_pular(f"erro inesperado: {erro_redigido(exc)}"),),
                )
            )
    com_decisor = sum(1 for e in enriquecidos if e.decisor_identificavel)
    logger.info(
        "enriquecimento: %d de %d leads com decisor identificado",
        com_decisor, len(enriquecidos),
    )
    return enriquecidos


# ---------------------------------------------------------------------------
# Pipeline completo — decisor + site + WhatsApp + e-mail + IA
# ---------------------------------------------------------------------------

#: Faixas de prioridade a partir do score final. Rótulo comercial, não peso —
#: os pesos vivem em `scoring/rules.py`.
#: ⚠️ Os cortes NÃO foram confirmados com a Carolina; são convenção do
#: pipeline até ela decidir onde termina "quente".
CORTE_ALTA = 70
CORTE_MEDIA = 40


def prioridade_do_score(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= CORTE_ALTA:
        return "ALTA"
    if score >= CORTE_MEDIA:
        return "MEDIA"
    return "BAIXA"


def _melhor_dominio(candidato: Candidato, emails: Sequence[str]) -> str | None:
    """Domínio raspável a partir dos e-mails já conhecidos do lead."""
    for email in emails:
        if "@" not in email:
            continue
        dominio = email.split("@")[-1].strip().lower()
        if dominio_raspavel(dominio):
            return dominio
    return None


def enriquecer_lead(
    candidato: Candidato,
    *,
    cliente_api_full: Any = None,
    cliente_brasil_api: Any = None,
    cliente_firecrawl: Any = None,
    cliente_evolution: Any = None,
    cliente_hunter: Any = None,
    cliente_zerobounce: Any = None,
    cliente_ia: Any = None,
    resolver_mx=None,
) -> LeadEnriquecido:
    """Roda o pipeline pago inteiro num lead. Nunca levanta.

    Cada etapa é isolada por ``_rodar_etapa``: uma falha do Firecrawl não
    pode custar o WhatsApp de ninguém.
    """
    puladas: list[dict[str, str]] = []

    # --- 1. Decisor (Fase 5) — também traz telefone e, às vezes, e-mail ---
    base = enriquecer_decisor(
        candidato,
        cliente_api_full=cliente_api_full,
        cliente_brasil_api=cliente_brasil_api,
    )
    puladas.extend(base.etapas_puladas)
    telefones = list(base.telefones)
    emails = list(base.emails)

    # --- 2. Site (Firecrawl) — só se houver domínio raspável --------------
    site_url = ""
    instagram = ""
    markdown = ""
    dominio = _melhor_dominio(candidato, emails)
    if dominio is None:
        puladas.append(
            {
                "etapa": ETAPA_SITE,
                "motivo": "sem domínio próprio conhecido — nada a raspar "
                "(Google Places não está no pipeline)",
            }
        )
    else:
        site_url = f"https://{dominio}"
        scrape = _rodar_etapa(
            ETAPA_SITE,
            lambda: site_scraping.raspar_site(site_url, cliente=cliente_firecrawl),
            puladas,
        )
        if scrape is not None and scrape.tem_conteudo:
            markdown = scrape.markdown
            instagram = scrape.instagram or ""
            # WhatsApp do site é candidato melhor que fixo (§6) — entra na
            # frente da fila, sem custar chamada nenhuma.
            if scrape.whatsapp:
                telefones.insert(0, scrape.whatsapp)
        elif scrape is not None and scrape.erro:
            puladas.append({"etapa": ETAPA_SITE, "motivo": scrape.erro})

    # --- 3. WhatsApp (Evolution) — roda pra todo lead com telefone --------
    tem_whatsapp = False
    numero_whatsapp = ""
    if not telefones:
        puladas.append({"etapa": ETAPA_WHATSAPP, "motivo": "lead sem telefone"})
    else:
        resultado_wpp = _rodar_etapa(
            ETAPA_WHATSAPP,
            lambda: whatsapp_service.validar_whatsapp(
                telefones[0], cliente=cliente_evolution
            ),
            puladas,
        )
        if resultado_wpp is not None:
            tem_whatsapp = resultado_wpp.tem_whatsapp
            numero_whatsapp = resultado_wpp.numero_formatado
            if resultado_wpp.erro:
                puladas.append({"etapa": ETAPA_WHATSAPP, "motivo": resultado_wpp.erro})

    # --- 4. E-mail (Hunter → MX → ZeroBounce) -----------------------------
    email_aprovado = False
    email_status = ""
    if not emails and dominio is None:
        puladas.append(
            {"etapa": ETAPA_EMAIL, "motivo": "sem e-mail conhecido e sem domínio"}
        )
    else:
        extras = {"resolver_mx": resolver_mx} if resolver_mx is not None else {}
        resultado_email = _rodar_etapa(
            ETAPA_EMAIL,
            lambda: email_enrichment.enriquecer_email(
                dominio,
                base.decisor or None,
                cliente_hunter=cliente_hunter,
                cliente_zerobounce=cliente_zerobounce,
                email_conhecido=emails[0] if emails else None,
                **extras,
            ),
            puladas,
        )
        if resultado_email is not None:
            email_aprovado = resultado_email.aprovado
            email_status = resultado_email.status_zerobounce
            if resultado_email.email and resultado_email.email not in emails:
                emails.append(resultado_email.email)
            if resultado_email.erro:
                puladas.append({"etapa": ETAPA_EMAIL, "motivo": resultado_email.erro})

    # --- 5. IA sobre o site -> presenca_digital ---------------------------
    presenca = 0.0
    if not markdown:
        puladas.append(
            {"etapa": ETAPA_IA, "motivo": "sem conteúdo de site — IA não consultada"}
        )
        # Instagram achado sem site legível ainda é presença digital parcial.
        presenca = 0.3 if instagram else 0.0
    else:
        analise = _rodar_etapa(
            ETAPA_IA,
            lambda: ai_site.analisar_site(markdown, cliente=cliente_ia),
            puladas,
        )
        if analise is not None and analise.ok:
            presenca = analise.intensidade
            # Instagram confirmado soma ao que a IA viu, sem estourar 1.0.
            if instagram:
                presenca = min(1.0, presenca + 0.2)
        elif analise is not None and analise.erro:
            puladas.append({"etapa": ETAPA_IA, "motivo": analise.erro})
            presenca = 0.5 if instagram else 0.3  # site leu, IA não opinou

    parcial = LeadEnriquecido(
        candidato=candidato,
        nome=base.nome,
        decisor=base.decisor,
        telefones=tuple(telefones),
        emails=tuple(emails),
        fonte_decisor=base.fonte_decisor,
        site_url=site_url if markdown else "",
        instagram=instagram,
        tem_whatsapp=tem_whatsapp,
        whatsapp_numero=numero_whatsapp,
        email_aprovado=email_aprovado,
        email_status=email_status,
        presenca_digital=presenca,
    )

    # --- 6. Score final ---------------------------------------------------
    # Sinais gratuitos da pré-seleção + sinais do enriquecimento. É o mesmo
    # motor calibrado da Fase 2 — nenhum peso é somado à mão aqui.
    sinais: dict[str, Any] = dict(candidato.dados_nicho.get("sinais_gratuitos") or {})
    if not sinais:
        sinais = {
            "tamanho_propriedade": candidato.dados_nicho.get("area_ha"),
            "valor_financiado": candidato.dados_nicho.get("valor_financiado"),
            "semente_sicor_cultura": bool(candidato.dados_nicho.get("culturas")),
        }
    sinais.update(parcial.sinais_para_score)
    resultado_score = calcular_score(sinais)

    return LeadEnriquecido(
        candidato=parcial.candidato,
        nome=parcial.nome,
        decisor=parcial.decisor,
        telefones=parcial.telefones,
        emails=parcial.emails,
        fonte_decisor=parcial.fonte_decisor,
        site_url=parcial.site_url,
        instagram=parcial.instagram,
        tem_whatsapp=parcial.tem_whatsapp,
        whatsapp_numero=parcial.whatsapp_numero,
        email_aprovado=parcial.email_aprovado,
        email_status=parcial.email_status,
        presenca_digital=parcial.presenca_digital,
        score=resultado_score.score,
        prioridade=prioridade_do_score(resultado_score.score),
        etapas_puladas=tuple(puladas),
    )


def enriquecer_lote_completo(
    selecionados: Sequence[Candidato], **clientes: Any
) -> list[LeadEnriquecido]:
    """Pipeline completo no lote, isolando a falha de cada lead (§6)."""
    resultado: list[LeadEnriquecido] = []
    for candidato in selecionados:
        try:
            resultado.append(enriquecer_lead(candidato, **clientes))
        except Exception as exc:  # noqa: BLE001 — isolamento por lead
            resultado.append(
                LeadEnriquecido(
                    candidato=candidato,
                    etapas_puladas=(
                        {"etapa": "enriquecimento", "motivo": erro_redigido(exc)},
                    ),
                )
            )
    logger.info(
        "enriquecimento: %d leads — %d com decisor, %d com WhatsApp, "
        "%d com e-mail aprovado, %d com presença digital > 0",
        len(resultado),
        sum(1 for x in resultado if x.decisor_identificavel),
        sum(1 for x in resultado if x.tem_whatsapp),
        sum(1 for x in resultado if x.email_aprovado),
        sum(1 for x in resultado if x.presenca_digital > 0),
    )
    return resultado
