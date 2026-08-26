"""Reprocessa leads **que já existem no banco**, por documento explícito.

⚠️ **GASTA DINHEIRO DE VERDADE E ESCREVE NO BANCO.** É a diferença em
relação a ``teste_real_enriquecimento.py``, que só imprime: este aqui faz
upsert em ``leads`` via ``persistir_leads``.

Uso::

    # confere o que faria, SEM gastar e SEM escrever
    .venv/bin/python scripts/reprocessar_leads.py --simular 05587700968 ...

    # roda de verdade
    .venv/bin/python scripts/reprocessar_leads.py 05587700968 ...

## Por que script separado, e não uma flag no teste_real_enriquecimento

Três diferenças de natureza, não de parâmetro:

1. **Aquele script não persiste**, este persiste. Somar escrita no banco a
   uma ferramenta que hoje é só leitura é o tipo de mudança que surpreende
   quem já tem o comando no histórico do shell.
2. **Aquele parte da pré-seleção** (lê Sicor, lê Receita, ranqueia). Aqui
   nada disso roda — a entrada é uma lista de documentos.
3. O perfil de risco é outro: lá o pior caso é gastar; aqui é gastar **e**
   sobrescrever dado bom.

## ⚠️ O Candidato é HIDRATADO do banco, não montado do zero

Esta é a decisão que mais importa neste script, e ela contraria o caminho
"monta um Candidato mínimo (documento + tipo) e deixa o enriquecimento
resolver o resto". Esse caminho **destrói dado**, em duas frentes:

**1. O score despenca.** ``enriquecer_lead`` calcula o score final somando os
sinais do enriquecimento aos sinais GRATUITOS que vêm de
``candidato.dados_nicho`` (ver a seção "6. Score final" lá). Com
``dados_nicho`` vazio, ``tamanho_propriedade`` (peso 30), ``valor_financiado``
(peso 10) e ``semente_sicor_cultura`` (peso 15) ficam sem sinal. Medido nos
dados reais destes 4 leads: **80 pontos viram 25**.

**2. O upsert apaga colunas.** ``persistir_leads`` grava
``municipio``/``uf``/``dados_nicho`` a partir do candidato, sem mesclar com o
que já está lá. Candidato mínimo ⇒ município e UF viram ``NULL``, e
``dados_nicho`` perde área, culturas, data da operação, recorrência, CAR e
anos de crédito — dado do Sicor que custou horas de parsing.

A hidratação a partir da linha existente resolve os dois de uma vez, e é
mais fiel ao pedido de "não ler Sicor/Receita de novo": o dado já está no
banco, basta lê-lo de lá.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.documentos import (  # noqa: E402
    TIPO_CPF,
    detectar_tipo_documento,
    normalizar_documento,
)
from app.models import Lead  # noqa: E402
from app.scoring.compute_lead_score import calcular_score  # noqa: E402
from app.scoring.pre_selecao import ORIGEM_SICOR, Candidato  # noqa: E402
from app.workers.busca import (  # noqa: E402
    enriquecer_selecionados,
    escolher_telefones,
    persistir_leads,
)


def mascarar(documento: str) -> str:
    """CPF/CNPJ parcialmente oculto — log legível sem expor o titular."""
    if len(documento) < 6:
        return documento
    return f"{documento[:3]}***{documento[-2:]}"


def candidato_do_lead(lead: Lead) -> Candidato:
    """Reconstrói o ``Candidato`` a partir da linha já persistida.

    Preserva tudo que o pipeline original resolveu de graça (área, culturas,
    valor financiado, município, UF) — ver o aviso no docstring do módulo
    sobre por que montar um candidato mínimo destruiria esses dados.

    ``pontos_parciais`` é recalculado dos sinais gratuitos, não lido do
    ``score`` gravado: aquele score já inclui enriquecimento, e enfiá-lo aqui
    faria o número significar duas coisas diferentes no mesmo campo. O valor
    só serve pra ranking de pré-seleção, que este script não roda.
    """
    nicho = dict(lead.dados_nicho or {})
    sinais = {
        "tamanho_propriedade": nicho.get("area_ha"),
        "valor_financiado": nicho.get("valor_financiado"),
        "semente_sicor_cultura": bool(nicho.get("culturas")),
    }
    return Candidato(
        documento=lead.documento,
        origem=ORIGEM_SICOR,
        nome=lead.nome or "",
        uf=lead.uf,
        municipio=lead.municipio,
        pontos_parciais=float(calcular_score(sinais).pontos),
        dados_nicho=nicho,
    )


def carregar_candidatos(sessao, documentos: list[str]) -> tuple[list[Candidato], list[str]]:
    """``(candidatos, erros)`` — um candidato por documento, na ordem dada.

    Documento inválido ou ausente do banco vira erro, **não** um lead novo:
    criar lead do zero aqui significaria gravar uma linha sem nenhum sinal do
    Sicor, que é exatamente o estrago que este script existe pra evitar.
    """
    candidatos: list[Candidato] = []
    erros: list[str] = []

    for bruto in documentos:
        try:
            documento = normalizar_documento(bruto)
            detectar_tipo_documento(documento)
        except ValueError as exc:
            erros.append(f"{bruto}: documento inválido ({exc})")
            continue

        lead = sessao.query(Lead).filter(Lead.documento == documento).one_or_none()
        if lead is None:
            erros.append(
                f"{mascarar(documento)}: não existe no banco — este script só "
                f"reprocessa lead já persistido"
            )
            continue
        candidatos.append(candidato_do_lead(lead))

    return candidatos, erros


def estado_atual(sessao, documentos: list[str]) -> dict[str, dict]:
    """Foto do antes, pra comparar com o depois no relatório final."""
    foto: dict[str, dict] = {}
    for documento in documentos:
        lead = sessao.query(Lead).filter(Lead.documento == documento).one_or_none()
        if lead is None:
            continue
        nicho = lead.dados_nicho or {}
        foto[documento] = {
            "telefone": lead.telefone,
            "telefone_secundario": lead.telefone_secundario,
            "whatsapp_ativo": nicho.get("whatsapp_ativo"),
            "score": lead.score,
        }
    return foto


def relatar_lead(i: int, enriquecido, antes: dict | None) -> None:
    candidato = enriquecido.candidato
    principal, secundario = escolher_telefones(enriquecido)
    antes = antes or {}

    def mudou(rotulo: str, de, para) -> str:
        seta = "  →  " if de != para else "  =  "
        marca = " ⬅ MUDOU" if de != para else ""
        return f"  {rotulo:20} {str(de):22}{seta}{str(para)}{marca}"

    print(f"\n{'=' * 68}")
    print(f"LEAD {i} — {mascarar(candidato.documento)} — "
          f"{candidato.dados_nicho.get('area_ha')} ha")
    print(f"{'=' * 68}")
    print(mudou("telefone", antes.get("telefone"), principal))
    print(mudou("tel. secundário", antes.get("telefone_secundario"), secundario))
    print(mudou("whatsapp_ativo", antes.get("whatsapp_ativo"), enriquecido.tem_whatsapp))
    print(mudou("score", antes.get("score"), enriquecido.score))
    print(f"  {'telefones vistos':20} {len(enriquecido.telefones)} "
          f"({', '.join(enriquecido.telefones) or '-'})")
    if enriquecido.etapas_puladas:
        print("  etapas puladas:")
        for e in enriquecido.etapas_puladas:
            print(f"    - {e['etapa']}: {e['motivo']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reprocessa leads já persistidos.")
    p.add_argument("documentos", nargs="+", help="CPF/CNPJ (com ou sem máscara)")
    p.add_argument("--simular", action="store_true",
                   help="monta os candidatos e PARA antes de gastar/escrever")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=== guardas de configuração ===")
    for nome, ok in {
        "API Full (CPF, PAGA)": settings.api_full_configurada,
        "Evolution (WhatsApp)": settings.evolution_configurada,
        "Firecrawl (site)": settings.firecrawl_configurada,
        "Hunter.io (e-mail)": settings.hunter_configurada,
        "ZeroBounce (e-mail)": settings.zerobounce_configurada,
        "Anthropic (IA)": settings.anthropic_configurada,
    }.items():
        print(f"  {nome:24} {'ok' if ok else 'AUSENTE — etapa será pulada'}")

    sessao = SessionLocal()
    try:
        candidatos, erros = carregar_candidatos(sessao, args.documentos)

        if erros:
            print("\n=== PROBLEMAS ===")
            for erro in erros:
                print(f"  ✗ {erro}")
            print("\nabortando sem gastar nada — corrija a lista e rode de novo")
            return 1

        documentos = [c.documento for c in candidatos]
        antes = estado_atual(sessao, documentos)

        print(f"\n=== {len(candidatos)} lead(s) a reprocessar ===")
        for c in candidatos:
            foto = antes.get(c.documento, {})
            print(f"  {mascarar(c.documento)} ({detectar_tipo_documento(c.documento)}) "
                  f"area={c.dados_nicho.get('area_ha')} ha "
                  f"| hoje: tel={foto.get('telefone')} "
                  f"wpp={foto.get('whatsapp_ativo')} score={foto.get('score')}")

        if args.simular:
            print("\n--simular: parando ANTES de qualquer chamada paga e de "
                  "qualquer escrita no banco. Nada foi gasto, nada foi alterado.")
            return 0

        n_cpf = sum(
            1 for c in candidatos
            if detectar_tipo_documento(c.documento) == TIPO_CPF
        )
        print(f"\n⚠️  A PARTIR DAQUI GASTA: até {n_cpf} consulta(s) API Full, "
              f"{len(candidatos)} Evolution, mais Firecrawl/Hunter/ZeroBounce/IA "
              f"conforme cada lead tiver domínio e site.")
        print("⚠️  E ESCREVE NO BANCO (upsert por documento).")
        print("\nEnriquecendo…\n")

        enriquecidos = enriquecer_selecionados(candidatos, apenas_decisor=False)
        for i, enriquecido in enumerate(enriquecidos, 1):
            relatar_lead(i, enriquecido, antes.get(enriquecido.candidato.documento))

        gravados = persistir_leads(sessao, enriquecidos)

        print(f"\n{'=' * 68}")
        print("RESUMO")
        print(f"{'=' * 68}")
        print(f"  leads gravados       : {gravados}/{len(enriquecidos)}")
        print(f"  com WhatsApp ativo   : "
              f"{sum(1 for x in enriquecidos if x.tem_whatsapp)}/{len(enriquecidos)}")
        print(f"  com telefone backup  : "
              f"{sum(1 for x in enriquecidos if escolher_telefones(x)[1])}/{len(enriquecidos)}")
        return 0
    finally:
        sessao.close()


if __name__ == "__main__":
    raise SystemExit(main())
