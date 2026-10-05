"""Testa o WhatsApp do telefone SECUNDÁRIO de leads sem WhatsApp confirmado.

Por quê existe: até 05/10/2026 a etapa de WhatsApp testava só o primeiro
telefone do lead. Quem tinha um segundo celular ativo ficou "não confirmado"
e perdeu os 15 pontos do critério ``whatsapp_ativo``. Este script conserta os
leads já gravados.

⚠️ **Não chama a API Full** (os telefones já estão no banco), então o custo em
reais é zero — só consulta à Evolution. Escreve no banco, mas só nos leads em
que o secundário **tem** WhatsApp.

Quando o secundário tem WhatsApp:
  * ele vira o telefone principal e o antigo principal vira o secundário;
  * ``dados_nicho["whatsapp_ativo"]`` passa a ser ``True``;
  * o score soma a diferença exata que o motor atribui a esse sinal
    (``calcular_score`` antes e depois) e a prioridade é recalculada.

Não toca em ``kanban_status`` nem em nenhum outro campo.

Uso (dentro do container do worker)::

    python scripts/revalidar_whatsapp.py --simular   # só conta, não consulta nem grava
    python scripts/revalidar_whatsapp.py             # roda de verdade
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.routes.leads import _sinais_do_lead  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import Lead  # noqa: E402
from app.scoring.compute_lead_score import calcular_score  # noqa: E402
from app.services import whatsapp as whatsapp_service  # noqa: E402
from app.workers.enriquecimento import prioridade_do_score  # noqa: E402


def mascarar(documento: str) -> str:
    if len(documento) < 6:
        return documento
    return f"{documento[:3]}***{documento[-2:]}"


@dataclass
class Resumo:
    candidatos: int = 0
    consultados: int = 0
    promovidos: int = 0
    sem_whatsapp: int = 0
    erros: list[str] = field(default_factory=list)
    abortado: bool = False


def candidatos_a_revalidar(sessao) -> list[Lead]:
    """Leads com WhatsApp medido como ``False`` e um secundário a testar."""
    resultado = []
    for lead in sessao.query(Lead).filter(Lead.telefone_secundario.isnot(None)).all():
        nicho = lead.dados_nicho or {}
        if nicho.get("whatsapp_ativo") is False and lead.telefone:
            resultado.append(lead)
    return resultado


def promover_secundario(lead: Lead, numero_confirmado: str) -> None:
    """Troca principal/secundário e recalcula score e prioridade do lead."""
    antes = calcular_score(_sinais_do_lead(lead)).score
    nicho = dict(lead.dados_nicho or {})
    nicho["whatsapp_ativo"] = True
    lead.dados_nicho = nicho  # nova instância: o SQLAlchemy não vê mutação in-place
    depois = calcular_score(_sinais_do_lead(lead)).score

    antigo_principal = lead.telefone
    lead.telefone = numero_confirmado
    lead.telefone_secundario = antigo_principal
    if lead.score is not None:
        lead.score = lead.score + (depois - antes)
        lead.prioridade = prioridade_do_score(lead.score)


def revalidar(
    sessao,
    *,
    validar: Callable[[str], whatsapp_service.ResultadoWhatsapp] = whatsapp_service.validar_whatsapp,
    simular: bool = False,
) -> Resumo:
    resumo = Resumo()
    leads = candidatos_a_revalidar(sessao)
    resumo.candidatos = len(leads)
    if simular:
        return resumo

    for lead in leads:
        resultado = validar(lead.telefone_secundario)
        if resultado.erro:
            resumo.erros.append(f"{mascarar(lead.documento)}: {resultado.erro}")
            if resultado.numero_valido:
                # Evolution com problema: parar em vez de martelar o serviço.
                resumo.abortado = True
                break
            continue
        resumo.consultados += 1
        if resultado.tem_whatsapp:
            promover_secundario(lead, resultado.numero_formatado)
            resumo.promovidos += 1
        else:
            resumo.sem_whatsapp += 1
    sessao.commit()
    return resumo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--simular", action="store_true",
                   help="só conta os leads candidatos; não consulta nem grava")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    sessao = SessionLocal()
    try:
        resumo = revalidar(sessao, simular=args.simular)
    finally:
        sessao.close()

    print(f"leads candidatos (WhatsApp 'não' + secundário): {resumo.candidatos}")
    if args.simular:
        print("--simular: nada foi consultado e nada foi gravado.")
        return 0
    print(f"consultados na Evolution : {resumo.consultados}")
    print(f"secundário COM WhatsApp  : {resumo.promovidos} (promovidos a principal)")
    print(f"secundário sem WhatsApp  : {resumo.sem_whatsapp} (inalterados)")
    for erro in resumo.erros:
        print(f"  ✗ {erro}")
    if resumo.abortado:
        print("ABORTADO: erro da Evolution. Os já processados foram gravados.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
