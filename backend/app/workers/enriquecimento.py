"""Enriquecimento pago dos candidatos pré-selecionados — **só o decisor**.

Mora em módulo próprio pelo mesmo motivo de ``app.workers.busca``: manter
``celery_app.py`` fino e a lógica testável sem broker. ``busca`` delega pra
cá, e nada aqui roda antes da pré-seleção ter cortado o volume.

## ⛔ Fronteira desta fase

Só ``decisor_identificavel`` (peso 20) é resolvido agora. As demais etapas
pagas da §3 continuam fora, cada uma com seu TODO abaixo — decidir e
implementar uma por vez, medindo custo, é o que evitou surpresa no Minotto.

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
from app.scoring.pre_selecao import Candidato
from app.services import api_full, brasil_api

logger = logging.getLogger(__name__)

ETAPA_DECISOR = "enrich_decisor"


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
        """
        return {"decisor_identificavel": self.decisor or False}


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
