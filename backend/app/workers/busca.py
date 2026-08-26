"""Orquestração da busca mensal — leitura das sementes + pré-seleção.

## Por que num módulo próprio, e não dentro de ``celery_app.py``

O Minotto põe ``executar_busca_mensal`` dentro do ``celery_app.py``, junto com
as tasks. Aqui está separado por dois motivos concretos:

1. **Testabilidade.** Assim a orquestração roda numa chamada de função direta,
   sem broker, sem worker, sem Redis. A suíte exercita a busca inteira contra
   os arquivos reais sem subir nada.
2. **A lição do worker (seção 6, que mordeu 3 vezes no Minotto).** Quanto
   menos coisa nasce dentro do ``celery_app``, menor a chance de o processo do
   worker conhecer um conjunto de módulos diferente do processo do
   ``uvicorn``. ``celery_app.py`` fica sendo só o registro da task, e importa
   este módulo e ``app.models`` explicitamente.

## O que esta fase faz — e onde ela para

Segue os passos da §3 do docs_fundacao.md até o corte de volume, **e para**:

```
1. Trava de segurança: as duas fontes gratuitas têm arquivo? Senão, aborta.
2. Lê a semente Sicor      (produtor rural, PF+PJ, multi-ano)
3. Lê a semente Receita    (CNPJ agro por CNAE, situação ativa)
4. Pré-seleciona em 2 fases, deduplicando por documento
5. ⛔ PARA AQUI — o enriquecimento pago é fase futura
```

Os passos 5 a 10 da §3 (Google Places, Firecrawl, WhatsApp, e-mail, BrasilAPI
pro decisor, persistência e ``compute_lead_score`` final) **não** estão aqui.
Ver ``enriquecer_selecionados`` no fim do módulo.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.scoring.pre_selecao import Candidato, ResultadoPreSelecao, pre_selecionar
from app.services.receita_federal import (
    CNAES_AGRO_TODOS,
    buscar_semente_cnpj,
    encontrar_estabelecimentos,
)
from app.services.sicor import ARQ_OPERACAO, encontrar_arquivo, extrair_leads_sicor

logger = logging.getLogger(__name__)

UF_PADRAO = "PR"  # foco da Inova; parametrizável em toda função pública


@dataclass(frozen=True, slots=True)
class ResultadoBusca:
    """Resultado da busca. Nunca vem de uma exceção vazando pro chamador."""

    pre_selecao: ResultadoPreSelecao | None = None
    #: Motivo do aborto, quando a trava de segurança dispara. ``None`` = correu.
    abortada_por: str | None = None
    #: Avisos agregados, renderizados no painel admin (seção 6: etapa pulada
    #: tem que chegar à tela, senão o erro fica invisível).
    erros: tuple[dict[str, str], ...] = field(default_factory=tuple)
    leads_sicor: int = 0
    estabelecimentos_rfb: int = 0

    @property
    def ok(self) -> bool:
        return self.abortada_por is None and self.pre_selecao is not None

    @property
    def selecionados(self) -> tuple[Candidato, ...]:
        return self.pre_selecao.selecionados if self.pre_selecao else ()


def verificar_fontes(
    dir_sicor: Path, dir_rfb: Path, *, anos: Sequence[int]
) -> str | None:
    """Trava de segurança: confere que as fontes gratuitas existem.

    Devolve o motivo do aborto, ou ``None`` se está tudo no lugar.

    **Roda ANTES de qualquer processamento**, de propósito. Nesta fase não há
    dinheiro em jogo — o enriquecimento pago ainda não existe —, mas a
    disciplina é a mesma da seção 6: uma fonte gratuita não populada tem que
    abortar antes, não depois. Quando o enriquecimento entrar, a trava já vai
    estar no lugar certo, e não é preciso lembrar de adicioná-la.

    Sem isso o modo de falha é o pior possível e já aconteceu no Minotto:
    "busca concluída com sucesso, 0 leads", sem nenhuma pista.
    """
    if not any(
        encontrar_arquivo(dir_sicor, ARQ_OPERACAO.format(ano=ano)) for ano in anos
    ):
        return (
            f"semente Sicor indisponível: nenhum arquivo de operação para os "
            f"anos {list(anos)} em {dir_sicor}"
        )
    if encontrar_arquivo(dir_sicor, "SICOR_MUTUARIOS") is None:
        return (
            f"semente Sicor incompleta: SICOR_MUTUARIOS ausente em {dir_sicor} — "
            f"sem ele nenhum produtor é identificável"
        )
    if not encontrar_estabelecimentos(dir_rfb):
        return (
            f"semente Receita Federal indisponível: nenhum arquivo "
            f"ESTABELECIMENTOS em {dir_rfb}"
        )
    return None


def executar_busca_mensal(
    *,
    dir_sicor: Path | str,
    dir_rfb: Path | str,
    anos: Sequence[int],
    uf: str = UF_PADRAO,
    cnaes: Collection[str] = CNAES_AGRO_TODOS,
    culturas_alvo: Collection[str] | None = None,
    cota: int | None = None,
) -> ResultadoBusca:
    """Lê as duas sementes, pré-seleciona em 2 fases e para antes do custo.

    ``cota`` vem de ``settings.cota_pre_selecao`` quando não informada
    (``LEADS_POR_BUSCA × LEADS_MARGEM_PRE_SELECAO``). Nunca levanta: falha
    vira ``ResultadoBusca`` com ``abortada_por`` ou ``erros`` preenchidos.
    """
    dir_sicor, dir_rfb = Path(dir_sicor), Path(dir_rfb)
    cota = settings.cota_pre_selecao if cota is None else cota

    # --- 1. Trava de segurança, ANTES de qualquer processamento ----------
    motivo = verificar_fontes(dir_sicor, dir_rfb, anos=anos)
    if motivo is not None:
        logger.error("busca abortada na trava de segurança: %s", motivo)
        return ResultadoBusca(abortada_por=motivo)

    erros: list[dict[str, str]] = []

    # --- 2. Semente Sicor -------------------------------------------------
    resultado_sicor = extrair_leads_sicor(
        dir_sicor, uf=uf, anos=anos, culturas_alvo=culturas_alvo
    )
    erros.extend(resultado_sicor.etapas_puladas)
    logger.info(
        "busca: semente Sicor -> %d produtores (%d REF_BACEN no alvo, %d sem mutuário)",
        len(resultado_sicor.leads),
        resultado_sicor.refs_no_alvo,
        resultado_sicor.refs_sem_mutuario,
    )

    # --- 3. Semente Receita Federal ---------------------------------------
    resultado_rfb = buscar_semente_cnpj(dir_rfb, cnaes=cnaes, ufs={uf})
    erros.extend(resultado_rfb.etapas_puladas)
    logger.info(
        "busca: semente Receita Federal -> %d estabelecimentos agro ativos em %s",
        len(resultado_rfb.estabelecimentos),
        uf,
    )

    # --- 4. Pré-seleção em 2 fases ---------------------------------------
    pre = pre_selecionar(
        resultado_sicor.leads,
        resultado_rfb.estabelecimentos,
        cota=cota,
        culturas_alvo=culturas_alvo,
    )
    logger.info(
        "busca: pré-seleção -> %d selecionados (fase 1: %d, fase 2: %d, "
        "dedup descartou %d) para cota de %d",
        len(pre.selecionados),
        pre.selecionados_fase1,
        pre.selecionados_fase2,
        pre.descartados_por_dedup,
        cota,
    )

    # --- 5. ⛔ PARA AQUI --------------------------------------------------
    # Daqui pra frente é tudo custo. Ver `enriquecer_selecionados`.

    return ResultadoBusca(
        pre_selecao=pre,
        erros=tuple(erros),
        leads_sicor=len(resultado_sicor.leads),
        estabelecimentos_rfb=len(resultado_rfb.estabelecimentos),
    )


def enriquecer_selecionados(
    selecionados: Sequence[Candidato],
    *,
    apenas_decisor: bool = False,
    **clientes,
) -> list:
    """Enriquecimento pago dos selecionados — pipeline completo.

    Delega pra ``app.workers.enriquecimento``, onde mora a lógica, a ordem
    das etapas e a documentação de custo. Aqui fica só o ponto de entrada
    que a orquestração referencia.

    ⚠️ **Custa dinheiro em toda chamada**, e por lead: decisor (API Full pra
    CPF, ~97% do lote), WhatsApp (Evolution), e-mail (Hunter + ZeroBounce) e
    IA (Anthropic) quando houver site. Só roda DEPOIS da pré-seleção ter
    cortado o volume — nunca antes.

    ``apenas_decisor=True`` mantém o comportamento da Fase 5 (só a etapa de
    decisor), útil pra rodar o lote mais barato enquanto as demais chaves
    não estão contratadas.
    """
    from app.workers.enriquecimento import enriquecer_lote, enriquecer_lote_completo

    if apenas_decisor:
        return enriquecer_lote(
            selecionados,
            cliente_api_full=clientes.get("cliente_api_full"),
            cliente_brasil_api=clientes.get("cliente_brasil_api"),
        )
    return enriquecer_lote_completo(selecionados, **clientes)


def _chave_telefone(numero: str | None) -> str:
    """Identidade de um telefone, imune a formatação e a código de país.

    ``+5545999887766`` (e164 da API Full), ``5545999887766`` (o que a Evolution
    devolve em ``numero_formatado``) e ``45 99988-7766`` são **o mesmo
    telefone**. Sem normalizar, o número já escolhido como principal
    reapareceria como "alternativo" só por estar escrito diferente — e o
    dossiê mostraria o mesmo contato duas vezes, sugerindo duas formas de
    falar com a pessoa onde só existe uma.

    Reusa ``whatsapp.formatar_numero``, a mesma função que o pipeline usa pra
    montar o número que vai pra Evolution, então as duas pontas concordam por
    construção. Quando ela não consegue normalizar (número truncado, DDD
    estranho), cai nos dígitos crus — comparação pior, mas nunca pior que
    comparar a string formatada.
    """
    from app.services.whatsapp import formatar_numero

    return formatar_numero(numero) or "".join(
        c for c in (numero or "") if c.isdigit()
    )


def escolher_telefones(enriquecido) -> tuple[str | None, str | None]:
    """``(principal, secundário)`` a partir da fila de telefones do lead.

    **Principal**: o número que a Evolution confirmou, se houve validação;
    senão o primeiro da fila — que já vem ordenado por preferência, celular
    antes de fixo (ver ``telefones_ordenados`` em ``enriquecimento.py``).

    **Secundário**: o próximo número que não seja o principal. É contato
    alternativo, nada mais: não passou por validação de WhatsApp e não deve
    ser apresentado como se tivesse passado.

    ⚠️ Guarda **um** alternativo, mesmo quando a fonte traz mais (já vimos um
    CPF real com 5 números). Os demais são descartados. Se um dia valer a pena
    guardar todos, o padrão pronto é o ``emails_secundarios`` do Minotto —
    coluna JSON com a lista inteira, sob o argumento de que o bureau cobra por
    consulta e não por número devolvido, então o resto já está pago.
    """
    telefones = list(getattr(enriquecido, "telefones", ()) or [])
    validado = getattr(enriquecido, "whatsapp_numero", "") or ""

    principal = validado or (telefones[0] if telefones else None)
    if not principal:
        return None, None

    chave_principal = _chave_telefone(principal)
    for numero in telefones:
        if _chave_telefone(numero) != chave_principal:
            return principal, numero
    return principal, None


def persistir_leads(sessao, enriquecidos: Sequence) -> int:
    """Grava os leads enriquecidos, com score e prioridade preenchidos.

    Upsert por ``documento`` — a chave de negócio, e o índice único da Fase
    1. Um lead que já existe é atualizado, não duplicado.

    Devolve quantos leads foram gravados ou atualizados. Nunca levanta por
    lead individual: um documento inválido é pulado com log, não derruba a
    persistência dos outros (§6).
    """
    from app.models import Lead

    gravados = 0
    for enriquecido in enriquecidos:
        candidato = enriquecido.candidato
        try:
            lead = (
                sessao.query(Lead)
                .filter(Lead.documento == candidato.documento)
                .one_or_none()
            )
            dados_nicho = dict(candidato.dados_nicho)
            dados_nicho.update(
                {
                    "instagram": enriquecido.instagram or None,
                    "site_url": enriquecido.site_url or None,
                    "whatsapp_ativo": enriquecido.tem_whatsapp,
                    "email_status": enriquecido.email_status or None,
                    "presenca_digital": enriquecido.presenca_digital,
                    "fonte_decisor": enriquecido.fonte_decisor or None,
                    "decisor": enriquecido.decisor or None,
                }
            )
            principal, secundario = escolher_telefones(enriquecido)
            campos = {
                "nome": enriquecido.nome or candidato.nome or candidato.documento,
                "municipio": candidato.municipio,
                "uf": candidato.uf,
                "telefone": principal,
                "telefone_secundario": secundario,
                "email": enriquecido.emails[0] if enriquecido.emails else None,
                "site": enriquecido.site_url or None,
                "score": enriquecido.score,
                "prioridade": enriquecido.prioridade,
                "etapas_puladas": list(enriquecido.etapas_puladas),
                "dados_nicho": dados_nicho,
            }
            if lead is None:
                sessao.add(Lead(documento=candidato.documento, **campos))
            else:
                for chave, valor in campos.items():
                    setattr(lead, chave, valor)
            gravados += 1
        except Exception as exc:  # noqa: BLE001 — isolamento por lead
            from app.core.segredos import erro_redigido

            logger.error(
                "falha persistindo lead %s — seguindo com os demais: %s",
                candidato.documento, erro_redigido(exc),
            )
    sessao.commit()
    logger.info("busca: %d leads persistidos com score e prioridade", gravados)
    return gravados
