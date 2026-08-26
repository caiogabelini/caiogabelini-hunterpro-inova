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
descobre o ``site_url`` a partir do NOME da empresa, e todo o resto pende
disso. O Google Places **não foi portado** — não estava no escopo pedido.

⚠️ **A etapa de site está estruturalmente parada.** Até 26/08/2026 o
pipeline derivava o site do **domínio do e-mail** do lead. Um teste real com
produtor pessoa física mostrou que isso não se sustenta: e-mail
``@turbopro.com.br`` virou "site do produtor", e a presença digital de uma
empresa de terceiro foi parar no dossiê dele. A inferência foi removida —
ver ``descobrir_site_url``.

Consequência, e é preciso ser explícito: **sem Google Places, nenhum lead
tem site hoje.** Isso zera, na prática, três etapas:

- ``enrich_site_firecrawl`` — nada a raspar
- ``enrich_presenca_digital`` — sem markdown, a IA não é consultada;
  ``presenca_digital`` fica 0,0 pra todo mundo (peso 5)
- **descoberta** de e-mail no Hunter.io — sem domínio confiável, o Hunter
  não é consultado. A etapa de e-mail continua rodando, mas só pra
  **validar** e-mail que a fonte já entregou (API Full, BrasilAPI), via
  MX + ZeroBounce

Sobram de pé, e cobrindo toda a população: **decisor** (peso 20) e
**WhatsApp** (peso 15) — que é justamente o canal principal da cliente.

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
    #: **Ordenados por preferência de contato**, melhor candidato primeiro.
    #: Quem depende disso: a etapa de WhatsApp (testa ``telefones[0]``) e
    #: ``persistir_leads`` (grava o primeiro como principal e o segundo como
    #: alternativo). Duas coisas mantêm a ordem: ``telefones_ordenados`` põe o
    #: celular na frente na origem (CPF/API Full), e o scrape de site insere
    #: um número de ``wa.me`` na posição 0 — que é melhor ainda, porque é
    #: WhatsApp confirmado e não inferido.
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


def telefones_ordenados(resultado: api_full.ResultadoApiFull) -> tuple[str, ...]:
    """Telefones da API Full em **ordem de preferência**, celular primeiro.

    ⚠️ **Bug real corrigido em 26/08/2026**, achado na primeira busca paga.
    Antes esta conversão era ``tuple(t.e164 for t in resultado.telefones)`` —
    ordem crua do bureau. Como ``ResultadoApiFull.telefone_preferencial``
    existia mas **ninguém chamava**, um CPF que viesse com fixo em primeiro e
    celular em segundo fazia a etapa de WhatsApp testar o fixo, falhar, e
    nunca chegar no celular.

    O efeito não era um erro visível: era um lead marcado "sem WhatsApp" que
    tinha WhatsApp. Isso sub-representa justamente o sinal de peso 15 no
    score, o que a cliente mais valoriza — e some no meio de leads que
    realmente não têm.

    A API Full **não diz o tipo** (``TIPO_TELEFONE`` vem vazio); a inferência
    é por contagem de dígitos, em ``Telefone.eh_celular``. E não há garantia
    de ordem na resposta, então depender da posição era sorte, não contrato.

    Deduplica por número: o mesmo telefone repetido na resposta (já visto em
    dado real) não pode ocupar duas vagas da fila.
    """
    # `celulares + telefones` é exatamente a expressão de
    # `ResultadoApiFull.telefone_preferencial`, generalizada da primeira
    # posição pra fila inteira: todos os celulares na frente (na ordem do
    # bureau), depois o resto, e o dedup remove a segunda aparição dos
    # celulares. Assim `[0]` É o `telefone_preferencial` por construção — há
    # teste amarrando as duas pontas.
    #
    # ⚠️ Generalizar importa por causa do telefone alternativo: promover só o
    # primeiro celular deixaria um FIXO na frente de um SEGUNDO celular, e o
    # contato de backup do dossiê viraria o pior número disponível.
    ordenados = [t.e164 for t in resultado.celulares]
    ordenados.extend(t.e164 for t in resultado.telefones)
    return tuple(dict.fromkeys(ordenados))


def resolver_decisor_cpf(
    documento: str, *, cliente: Any = None
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], dict[str, str] | None]:
    """CPF via API Full. Devolve ``(nome, decisor, telefones, emails, pulada)``.

    Em pessoa física o produtor **é** o decisor — não há quadro societário a
    interpretar, ao contrário do CNPJ.

    ``telefones`` sai **ordenado por preferência** (ver ``telefones_ordenados``),
    não na ordem crua da resposta.
    """
    resultado = api_full.consultar_cpf(documento, cliente=cliente)
    if not resultado.ok:
        return "", "", (), (), _pular(resultado.erro or "API Full sem dado")
    return (
        resultado.nome,
        resultado.nome,
        telefones_ordenados(resultado),
        resultado.emails,
        None,
    )


def resolver_decisor_cnpj(
    documento: str, *, cliente: Any = None
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], dict[str, str] | None]:
    """CNPJ via BrasilAPI. O decisor sai do quadro societário (QSA).

    ⚠️ Diferente do CPF, aqui **não há o que priorizar**: o cliente da
    BrasilAPI lê um único campo (``ddd_telefone_1``), então a tupla tem no
    máximo um número e não existe ambiguidade de ordem. Verificado em
    26/08/2026, junto com a correção de ordenação do lado do CPF.
    """
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
    #   - validate_whatsapp         (Evolution API) — FEITO: a priorização de
    #     celular vive em `telefones_ordenados` desde 26/08/2026
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


def descobrir_site_url(candidato: Candidato) -> tuple[str | None, str]:
    """Site do lead, **quando houver fonte confiável**. Hoje nunca há.

    Devolve ``(site_url, motivo)`` — com ``site_url=None`` e o motivo pra
    registrar em ``etapas_puladas``.

    ⚠️ **Por que isto sempre devolve None hoje, e por que não é omissão.**

    Até 26/08/2026 esta função não existia: o pipeline derivava o site do
    **domínio do e-mail** do lead. Um teste real com produtor pessoa física
    mostrou por que isso não se sustenta — o lead voltou com e-mail
    ``@turbopro.com.br``, e o pipeline concluiu que
    ``https://turbopro.com.br`` era o site *dele*, raspou aquele site e
    atribuiu a presença digital daquela empresa ao produtor.

    **Domínio de e-mail corporativo não prova propriedade de site.** Pode ser
    o empregador, a cooperativa, a revenda de insumos, o escritório de
    contabilidade — qualquer um que tenha criado o e-mail pra ele. É a mesma
    família do ``eladiosouza@instagram.com`` do Minotto (§6), invertida: lá o
    "site" errado gerou e-mail falso; aqui o e-mail gerou um "site" que pode
    não ser do lead.

    **A fonte legítima é o Google Places** (``websiteUri``), que descobre o
    site pelo NOME do estabelecimento — não foi portado, e é aqui que entra
    quando for. Enquanto não entrar, pular com motivo é a resposta honesta:
    "não sei" é melhor que um palpite que contamina o dossiê e ainda gasta
    Firecrawl e IA no site de terceiro.
    """
    return None, (
        "sem fonte confiável de site para este lead — a descoberta por "
        "Google Places não está no pipeline, e domínio de e-mail não prova "
        "propriedade de site"
    )


def _dominio_do_site(site_url: str | None) -> str | None:
    """Domínio de um site **já confirmado** do lead.

    Mantida pro dia em que ``descobrir_site_url`` tiver fonte de verdade: a
    direção correta é site → domínio (é o que o Minotto faz com o
    ``websiteUri`` do Places). A direção inversa — e-mail → site — foi
    removida, ver ``descobrir_site_url``.
    """
    if not site_url:
        return None
    dominio = site_scraping.extrair_dominio(site_url)
    return dominio if dominio_raspavel(dominio) else None


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

    # --- 2. Site (Firecrawl) — só se houver fonte confiável de site -------
    # ⚠️ Hoje nunca há: `descobrir_site_url` devolve None com motivo. Ver o
    # docstring dela sobre a inferência por e-mail que foi REMOVIDA.
    site_url = ""
    instagram = ""
    markdown = ""
    url_descoberta, motivo_sem_site = descobrir_site_url(candidato)
    dominio = _dominio_do_site(url_descoberta)
    if url_descoberta is None:
        puladas.append({"etapa": ETAPA_SITE, "motivo": motivo_sem_site})
    else:
        site_url = url_descoberta
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
        # `telefones` chega ordenado por preferência (ver LeadEnriquecido),
        # então [0] é o melhor candidato: WhatsApp do site > celular > fixo.
        # ⚠️ Não trocar por outro índice sem revisitar `telefones_ordenados`.
        numero_alvo = telefones[0]
        resultado_wpp = _rodar_etapa(
            ETAPA_WHATSAPP,
            lambda: whatsapp_service.validar_whatsapp(
                numero_alvo, cliente=cliente_evolution
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
