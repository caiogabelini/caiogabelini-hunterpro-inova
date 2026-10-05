"""Script ``revalidar_whatsapp``: promove o secundário que tem WhatsApp.

Nenhum teste toca a rede: o validador é injetado.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app.models import Lead  # noqa: E402
from app.services.whatsapp import ResultadoWhatsapp  # noqa: E402
from revalidar_whatsapp import candidatos_a_revalidar, revalidar  # noqa: E402
from tests.conftest import CNPJ_VALIDO, CPF_VALIDO  # noqa: E402

NICHO = {
    "area_ha": 800.0,
    "valor_financiado": 2_000_000.0,
    "culturas": ["SOJA"],
    "decisor": "FULANO",
    "whatsapp_ativo": False,
}


def lead(documento=CPF_VALIDO, *, secundario="5544999000002", nicho=None, status="novo_lead"):
    return Lead(
        documento=documento,
        nome="FULANO",
        telefone="5544999000001",
        telefone_secundario=secundario,
        score=75,
        prioridade="MEDIA",
        kanban_status=status,
        dados_nicho=dict(NICHO if nicho is None else nicho),
    )


def validador(com_whatsapp: set[str], *, erro: bool = False):
    chamadas: list[str] = []

    def _validar(numero: str) -> ResultadoWhatsapp:
        chamadas.append(numero)
        if erro:
            return ResultadoWhatsapp(numero_formatado=numero, numero_valido=True, erro="HTTP 500")
        return ResultadoWhatsapp(
            numero_formatado=numero, numero_valido=True, tem_whatsapp=numero in com_whatsapp
        )

    _validar.chamadas = chamadas  # type: ignore[attr-defined]
    return _validar


def test_promove_secundario_com_whatsapp(db) -> None:
    db.add(lead())
    db.commit()
    r = revalidar(db, validar=validador({"5544999000002"}))
    l = db.query(Lead).one()
    assert (r.promovidos, r.sem_whatsapp) == (1, 0)
    assert l.telefone == "5544999000002"
    assert l.telefone_secundario == "5544999000001"
    assert l.dados_nicho["whatsapp_ativo"] is True
    assert l.score == 90 and l.prioridade == "ALTA"


def test_secundario_sem_whatsapp_nao_altera_nada(db) -> None:
    db.add(lead())
    db.commit()
    r = revalidar(db, validar=validador(set()))
    l = db.query(Lead).one()
    assert (r.promovidos, r.sem_whatsapp) == (0, 1)
    assert l.telefone == "5544999000001" and l.score == 75
    assert l.dados_nicho["whatsapp_ativo"] is False


def test_nao_toca_no_kanban(db) -> None:
    db.add(lead(status="contatado"))
    db.commit()
    revalidar(db, validar=validador({"5544999000002"}))
    assert db.query(Lead).one().kanban_status == "contatado"


def test_so_leads_com_whatsapp_falso_e_secundario(db) -> None:
    db.add_all([
        lead(CPF_VALIDO),
        lead(CNPJ_VALIDO, nicho={**NICHO, "whatsapp_ativo": True}),  # já confirmado
    ])
    db.commit()
    assert [l.documento for l in candidatos_a_revalidar(db)] == [CPF_VALIDO]


def test_lead_sem_secundario_fica_de_fora(db) -> None:
    db.add(lead(secundario=None))
    db.commit()
    assert candidatos_a_revalidar(db) == []


def test_simular_nao_consulta_nem_grava(db) -> None:
    db.add(lead())
    db.commit()
    v = validador({"5544999000002"})
    r = revalidar(db, validar=v, simular=True)
    assert r.candidatos == 1 and v.chamadas == []
    assert db.query(Lead).one().telefone == "5544999000001"


def test_erro_da_evolution_aborta_sem_martelar(db) -> None:
    db.add_all([lead(CPF_VALIDO), lead(CNPJ_VALIDO)])
    db.commit()
    v = validador(set(), erro=True)
    r = revalidar(db, validar=v)
    assert r.abortado and len(v.chamadas) == 1
