#!/usr/bin/env python3
"""Povoa o banco LOCAL com leads fictícios, pra inspeção visual da interface.

⚠️ **DADO DE DEMONSTRAÇÃO. NUNCA RODAR CONTRA PRODUÇÃO.**

Isto não é teste automatizado nem fixture de suíte — é ferramenta manual de
desenvolvimento, pra abrir o navegador e ver Lista, Dossiê e Kanban com
conteúdo. O script se recusa a rodar contra um banco que não pareça local
(ver ``_exigir_banco_local``), e ``--limpar`` só apaga o que ele mesmo criou.

## Sobre os documentos: por que este padrão, e o que ele não garante

Os CPF/CNPJ são a sequência ``000.000.001-91``, ``000.000.002-72``… — o
padrão que qualquer desenvolvedor brasileiro bate o olho e reconhece como
dado de teste. É deliberado: os CPFs reais que a suíte usou nas Fases 5 e 6
são de pessoas de verdade e não devem virar dado de demonstração permanente.

⚠️ **Ressalva honesta, porque não dá pra ter as duas coisas.** O ``Lead``
valida dígito verificador (Fase 1), então todo documento aqui é
matematicamente válido — e **não existe faixa de CPF reservada para teste no
Brasil**, como existe para telefone (99999-xxxx). Ou seja: nenhum CPF
sintaticamente válido pode ser provado não-atribuído. O que este padrão
garante é que o dado é *reconhecivelmente* de teste para quem olha, não que o
número seja inexistente. A alternativa — documento com dígito inválido —
seria rejeitada pelo próprio model, e inseri-la por SQL cru criaria no banco
um estado que a aplicação não consegue produzir, mascarando bug depois.

Os nomes são explicitamente fictícios ("PRODUTOR DEMO 01"), o que remove
qualquer ambiguidade sobre a natureza do registro.

## O que NÃO é preenchido, e por quê

``kanban_status``, ``motivo_perda`` e os campos de fechamento **não existem
no model** — são da Fase 8b. Não invento valor pra eles. Consequência
observável na tela: o Kanban joga todos os leads na primeira coluna, porque
``KanbanPage.tsx`` faz ``lead.kanban_status ?? KANBAN_COLUMNS[0].status``.
Isso é o comportamento correto pro estado atual do backend, não um bug do
seed.

Uso:
    cd backend
    .venv/bin/python scripts/seed_leads_teste.py
    .venv/bin/python scripts/seed_leads_teste.py --limpar   # remove os demo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: F401, E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.documentos import _digito_verificador, formatar_documento  # noqa: E402
from app.models import Lead  # noqa: E402
from app.scoring.compute_lead_score import calcular_score  # noqa: E402
from app.workers.enriquecimento import prioridade_do_score  # noqa: E402

#: Hosts aceitos. Qualquer outro aborta — ver `_exigir_banco_local`.
HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1", "", None}

#: Marca em `dados_nicho` que identifica um lead criado por este script.
#: É o que `--limpar` usa — nunca um `DELETE FROM leads` cego.
MARCA_DEMO = "seed_leads_teste"


def _cpf_teste(n: int) -> str:
    """``000.000.00N-DD`` — sequencial, com dígitos verificadores corretos."""
    base = f"{n:09d}"
    d1 = _digito_verificador(base, list(range(10, 1, -1)))
    d2 = _digito_verificador(base + str(d1), list(range(11, 1, -1)))
    return f"{base}{d1}{d2}"


def _cnpj_teste(n: int) -> str:
    """``00.000.00N/0001-DD`` — mesma ideia, matriz (ordem 0001)."""
    base = f"{n:08d}0001"
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _digito_verificador(base, pesos1)
    d2 = _digito_verificador(base + str(d1), [6] + pesos1)
    return f"{base}{d1}{d2}"


def _exigir_banco_local() -> None:
    """Aborta se a DATABASE_URL não parecer um banco de desenvolvimento.

    Guarda barata contra o pior acidente possível deste script: rodar contra
    o banco do cliente e poluir os leads de verdade com dado de demonstração.
    """
    alvo = urlparse(settings.DATABASE_URL)
    if alvo.hostname not in HOSTS_LOCAIS:
        print(
            f"ABORTADO: DATABASE_URL aponta pra {alvo.hostname!r}, que não é local.\n"
            f"Este script é só pra banco de desenvolvimento.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if settings.em_producao:
        print("ABORTADO: ENVIRONMENT=production.", file=sys.stderr)
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Os leads. Variedade deliberada — o caminho feliz sozinho não exercita a tela.
# ---------------------------------------------------------------------------
# Proporção CPF/CNPJ espelha o universo real medido (98,2% pessoa física):
# 8 CPF + 2 CNPJ = 80% aqui, o mais próximo que 10 registros permitem sem
# esconder o caso PJ, que precisa aparecer pra os rótulos CPF-aware serem
# vistos ("Produtor" vs "Empresa").
DEMO: list[dict] = [
    # --- ALTA: caminho feliz completo -----------------------------------
    {
        "n": 1, "tipo": "cpf", "nome": "PRODUTOR DEMO 01 — SOJA GRANDE",
        "municipio": "CASCAVEL", "telefone": "+5545999990001",
        "email": "demo01@exemplo.invalido", "site": None,
        "nicho": {
            "area_ha": 1250.0, "valor_financiado": 4_200_000.0,
            "culturas": ["SOJA", "MILHO"], "data_operacao": "20260731",
            "recorrente": True, "anos_credito": [2025, 2026], "n_operacoes": 3,
            "codigos_car": ["PR4104808DEMO0000000000000000000000001"],
            "decisor": "PRODUTOR DEMO 01 — SOJA GRANDE", "fonte_decisor": "api_full",
            "whatsapp_ativo": True, "email_status": "valid", "presenca_digital": 0.0,
        },
    },
    {
        "n": 2, "tipo": "cpf", "nome": "PRODUTOR DEMO 02 — RECORRENTE",
        "municipio": "TOLEDO", "telefone": "+5545999990002",
        "email": "demo02@exemplo.invalido", "site": None,
        "nicho": {
            "area_ha": 860.5, "valor_financiado": 2_900_000.0,
            "culturas": ["SOJA"], "data_operacao": "20260715",
            "recorrente": True, "anos_credito": [2025, 2026], "n_operacoes": 2,
            "codigos_car": ["PR4127502DEMO0000000000000000000000002"],
            "decisor": "PRODUTOR DEMO 02 — RECORRENTE", "fonte_decisor": "api_full",
            "whatsapp_ativo": True, "email_status": "catch-all", "presenca_digital": 0.0,
        },
    },
    # --- MEDIA: decisor sim, canais parciais -----------------------------
    {
        "n": 3, "tipo": "cpf", "nome": "PRODUTOR DEMO 03 — SEM WHATSAPP",
        "municipio": "MARINGA", "telefone": "+554530001003",
        "email": "demo03@exemplo.invalido", "site": None,
        "nicho": {
            "area_ha": 430.0, "valor_financiado": 1_100_000.0,
            "culturas": ["MILHO"], "data_operacao": "20260620",
            "recorrente": False, "anos_credito": [2026], "n_operacoes": 1,
            "codigos_car": ["PR4115200DEMO0000000000000000000000003"],
            "decisor": "PRODUTOR DEMO 03 — SEM WHATSAPP", "fonte_decisor": "api_full",
            # Telefone fixo: a validação rodou e deu negativo — é sinal
            # PRESENTE que vale 0, não ausência de medição.
            "whatsapp_ativo": False, "email_status": "valid", "presenca_digital": 0.0,
        },
    },
    {
        "n": 4, "tipo": "cpf", "nome": "PRODUTOR DEMO 04 — EMAIL RECUSADO",
        "municipio": "LONDRINA", "telefone": "+5543999990004",
        "email": "demo04@exemplo.invalido", "site": None,
        "nicho": {
            "area_ha": 320.0, "valor_financiado": 780_000.0,
            "culturas": ["SOJA", "TRIGO"], "data_operacao": "20260518",
            "recorrente": False, "anos_credito": [2026], "n_operacoes": 1,
            "codigos_car": ["PR4113700DEMO0000000000000000000000004"],
            "decisor": "PRODUTOR DEMO 04 — EMAIL RECUSADO", "fonte_decisor": "api_full",
            "whatsapp_ativo": True, "email_status": "invalid", "presenca_digital": 0.0,
        },
    },
    {
        "n": 5, "tipo": "cpf", "nome": "PRODUTOR DEMO 05 — PEQUENA AREA",
        "municipio": "GUARAPUAVA", "telefone": "+5542999990005",
        "email": None, "site": None,
        "nicho": {
            # Abaixo do corte de 100 ha: exercita a fração de 0,25 da régua.
            "area_ha": 72.0, "valor_financiado": 60_000.0,
            "culturas": ["FEIJAO"], "data_operacao": "20260410",
            "recorrente": False, "anos_credito": [2026], "n_operacoes": 1,
            "codigos_car": [],
            "decisor": "PRODUTOR DEMO 05 — PEQUENA AREA", "fonte_decisor": "api_full",
            "whatsapp_ativo": False, "email_status": None, "presenca_digital": 0.0,
        },
    },
    # --- Ausência de dado: o que a tela faz quando NÃO achou -------------
    {
        "n": 6, "tipo": "cpf", "nome": "PRODUTOR DEMO 06 — SEM DECISOR",
        "municipio": "PONTA GROSSA", "telefone": None, "email": None, "site": None,
        "nicho": {
            "area_ha": 540.0, "valor_financiado": 1_500_000.0,
            "culturas": ["SOJA"], "data_operacao": "20260305",
            "recorrente": False, "anos_credito": [2026], "n_operacoes": 1,
            "codigos_car": ["PR4119905DEMO0000000000000000000000006"],
            # Nada de decisor/whatsapp/e-mail: a API Full não achou. Campos
            # AUSENTES (não False) — "não medimos" ≠ "medimos e não achamos".
        },
        "etapas_puladas": [
            {"etapa": "enrich_decisor", "motivo": "API Full sem dado pra este CPF"},
            {"etapa": "validate_whatsapp", "motivo": "lead sem telefone"},
            {"etapa": "enrich_email", "motivo": "sem e-mail conhecido e sem domínio"},
        ],
    },
    {
        "n": 7, "tipo": "cpf", "nome": "PRODUTOR DEMO 07 — SO SICOR",
        "municipio": "CAMPO MOURAO", "telefone": None, "email": None, "site": None,
        "nicho": {
            "area_ha": 210.0, "valor_financiado": 490_000.0,
            "culturas": ["MILHO"], "data_operacao": "20260228",
            "recorrente": False, "anos_credito": [2026], "n_operacoes": 1,
            "codigos_car": ["PR4104204DEMO0000000000000000000000007"],
        },
        "etapas_puladas": [
            {"etapa": "enrich_decisor", "motivo": "API Full sem dado pra este CPF"},
        ],
    },
    {
        "n": 8, "tipo": "cpf", "nome": "PRODUTOR DEMO 08 — BAIXA PRIORIDADE",
        "municipio": "PATO BRANCO", "telefone": "+5546999990008",
        "email": None, "site": None,
        "nicho": {
            "area_ha": 55.0, "valor_financiado": 35_000.0,
            "culturas": [], "data_operacao": "20260115",
            "recorrente": False, "anos_credito": [2026], "n_operacoes": 1,
            "codigos_car": [],
            "whatsapp_ativo": False,
        },
        "etapas_puladas": [
            {"etapa": "enrich_decisor", "motivo": "API Full sem dado pra este CPF"},
            {"etapa": "enrich_site_firecrawl", "motivo": "sem fonte confiável de site"},
        ],
    },
    # --- CNPJ: rótulos "Empresa"/"Razão social" e máscara de 14 dígitos --
    {
        "n": 1, "tipo": "cnpj", "nome": "COOPERATIVA AGRO DEMO LTDA",
        "municipio": "CASCAVEL", "telefone": "+554530001101",
        "email": "contato@cooperativademo.invalido", "site": "https://cooperativademo.invalido",
        "nicho": {
            "origem": "receita_federal", "cnae": "0115600",
            "cnae_descricao": "CULTIVO DE SOJA", "situacao_cadastral": "ATIVA",
            "eh_cooperativa": True, "natureza_juridica": "2143",
            "decisor": "MARIA DEMO DA SILVA", "fonte_decisor": "brasil_api",
            "whatsapp_ativo": False, "email_status": "valid",
            "presenca_digital": 0.7, "instagram": "cooperativademo",
        },
    },
    {
        "n": 2, "tipo": "cnpj", "nome": "AGROINDUSTRIA DEMO S/A",
        "municipio": "MARINGA", "telefone": "+554430001102",
        "email": None, "site": None,
        "nicho": {
            "origem": "receita_federal", "cnae": "4632001",
            "cnae_descricao": "COMÉRCIO ATACADISTA DE CEREAIS E LEGUMINOSAS BENEFICIADOS",
            "situacao_cadastral": "ATIVA", "eh_cooperativa": False,
            "decisor": "JOAO DEMO PEREIRA", "fonte_decisor": "brasil_api",
        },
        "etapas_puladas": [
            {"etapa": "enrich_email", "motivo": "sem e-mail conhecido e sem domínio"},
            {"etapa": "enrich_site_firecrawl", "motivo": "sem fonte confiável de site"},
        ],
    },
]


def _montar(item: dict) -> Lead:
    documento = _cpf_teste(item["n"]) if item["tipo"] == "cpf" else _cnpj_teste(item["n"])
    nicho = dict(item["nicho"])
    nicho["_origem_seed"] = MARCA_DEMO

    # Score pelo MESMO motor da produção — nada de número escolhido à mão.
    sinais: dict = {}
    if nicho.get("area_ha") is not None:
        sinais["tamanho_propriedade"] = nicho["area_ha"]
    if nicho.get("valor_financiado") is not None:
        sinais["valor_financiado"] = nicho["valor_financiado"]
    if "culturas" in nicho:
        sinais["semente_sicor_cultura"] = bool(nicho["culturas"])
    if nicho.get("decisor"):
        sinais["decisor_identificavel"] = nicho["decisor"]
    if nicho.get("whatsapp_ativo") is not None:
        sinais["whatsapp_ativo"] = nicho["whatsapp_ativo"]
    if nicho.get("email_status") is not None:
        sinais["email_validado"] = nicho["email_status"] in ("valid", "catch-all")
    if nicho.get("presenca_digital") is not None:
        sinais["presenca_digital"] = nicho["presenca_digital"]

    resultado = calcular_score(sinais)
    return Lead(
        documento=documento,
        nome=item["nome"],
        municipio=item.get("municipio"),
        uf="PR",
        telefone=item.get("telefone"),
        email=item.get("email"),
        site=item.get("site"),
        score=resultado.score,
        prioridade=prioridade_do_score(resultado.score),
        etapas_puladas=item.get("etapas_puladas"),
        dados_nicho=nicho,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--limpar", action="store_true",
        help="Remove só os leads criados por este script e sai.",
    )
    args = parser.parse_args()

    _exigir_banco_local()
    db = SessionLocal()
    try:
        if args.limpar:
            demo = [
                l for l in db.execute(select(Lead)).scalars()
                if (l.dados_nicho or {}).get("_origem_seed") == MARCA_DEMO
            ]
            for l in demo:
                db.delete(l)
            db.commit()
            print(f"{len(demo)} lead(s) de demonstração removidos.")
            print(f"Total no banco agora: {db.query(Lead).count()}")
            return 0

        criados = atualizados = 0
        for item in DEMO:
            novo = _montar(item)
            existente = db.execute(
                select(Lead).where(Lead.documento == novo.documento)
            ).scalar_one_or_none()
            if existente is None:
                db.add(novo)
                criados += 1
            else:
                # Idempotente: rodar duas vezes atualiza, não duplica nem
                # estoura o índice único de `documento`.
                for campo in (
                    "nome", "municipio", "uf", "telefone", "email", "site",
                    "score", "prioridade", "etapas_puladas", "dados_nicho",
                ):
                    setattr(existente, campo, getattr(novo, campo))
                atualizados += 1
        db.commit()

        print(f"{criados} lead(s) criados, {atualizados} atualizados.")
        print(f"Total no banco agora: {db.query(Lead).count()}\n")
        print(f"{'DOCUMENTO':22} {'TIPO':5} {'SCORE':>5} {'PRIOR.':8} NOME")
        for l in db.execute(select(Lead).order_by(Lead.score.desc())).scalars():
            print(
                f"  {formatar_documento(l.documento, l.tipo_documento):20} "
                f"{l.tipo_documento:5} {l.score if l.score is not None else '—':>5} "
                f"{(l.prioridade or '—'):8} {l.nome}"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
