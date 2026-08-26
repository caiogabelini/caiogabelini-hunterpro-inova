"""Rotas do Dashboard — as 5 que o frontend espera desde a Fase 7, mais o
``PUT /premissas`` que o Simulador de Receita chama.

Mesma infra dos demais testes de API: SQLite em memória com pool estático e
``get_db`` trocado por override.

⚠️ Toda métrica é conferida contra uma base montada com números conhecidos —
nenhum teste afirma "responde 200 e pronto". O que se protege aqui é o
significado de cada número, que é onde um dashboard erra sem avisar.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.rate_limit import get_redis
from app.core.security import create_access_token, hash_password
from app.core.tempo import agora_utc
from app.main import app
from app.models import Base, Lead, User

CAMINHOS = [
    "/api/dashboard/summary",
    "/api/dashboard/acoes-recomendadas",
    "/api/dashboard/premissas",
    "/api/dashboard/funil",
    "/api/dashboard/motivos-perda",
]


def documento(n: int) -> str:
    """``000.000.00N-DD`` — mesmo padrão do ``scripts/seed_leads_teste.py``.

    ⚠️ Dígitos verificadores **corretos** de propósito: o ``@validates`` do
    model rejeita CPF inválido, então um número só "do tamanho certo" não
    entra no banco. O prefixo de zeros mantém o documento obviamente
    sintético, sem risco de coincidir com o CPF de uma pessoa real — mesma
    disciplina já acordada pro seed.
    """
    from app.core.documentos import _digito_verificador

    base = f"{n:09d}"
    d1 = _digito_verificador(base, list(range(10, 1, -1)))
    d2 = _digito_verificador(base + str(d1), list(range(11, 1, -1)))
    return f"{base}{d1}{d2}"


def lead(
    n: int,
    *,
    status: str = "novo_lead",
    score: int | None = 50,
    prioridade: str | None = "MEDIA",
    decisor: str | None = "FULANO",
    whatsapp: bool | None = None,
    telefone: str | None = "5545999990000",
    email: str | None = "x@y.com",
    motivo_perda: str | None = None,
    tipo_contrato: str | None = None,
    valor_fechamento: float | None = None,
) -> Lead:
    nicho: dict = {"area_ha": 300.0, "culturas": ["SOJA"]}
    if decisor is not None:
        nicho["decisor"] = decisor
    if whatsapp is not None:
        nicho["whatsapp_ativo"] = whatsapp
    return Lead(
        documento=documento(n), nome=f"PRODUTOR {n}", uf="PR", municipio="CASCAVEL",
        score=score, prioridade=prioridade, kanban_status=status,
        telefone=telefone, email=email, motivo_perda=motivo_perda,
        tipo_contrato=tipo_contrato, valor_fechamento=valor_fechamento,
        dados_nicho=nicho,
    )


@pytest.fixture()
def cliente():
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, future=True)()
    db.add(User(id="u1", email="a@b.com", senha_hash=hash_password("x"), role="client"))
    db.add(User(id="adm", email="c@d.com", senha_hash=hash_password("x"), role="admin"))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: None
    with TestClient(app) as c:
        c.db = db  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    db.close(); engine.dispose()


@pytest.fixture()
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'user_id': 'u1'})}"}


@pytest.fixture()
def base_conhecida(cliente):
    """9 leads com distribuição conhecida, um por coluna do funil."""
    dados = [
        (1, "novo_lead"), (2, "novo_lead"), (3, "qualificacao"),
        (4, "contatado"), (5, "reuniao"), (6, "proposta_enviada"),
        (7, "negociacao"),
    ]
    for n, status in dados:
        cliente.db.add(lead(n, status=status))
    cliente.db.add(lead(8, status="ganho", tipo_contrato="recorrente",
                        valor_fechamento=2000.0, score=90, prioridade="ALTA"))
    cliente.db.add(lead(9, status="perdido", motivo_perda="Preço alto", score=10))
    cliente.db.commit()
    return cliente


class TestAutorizacao:
    @pytest.mark.parametrize("caminho", CAMINHOS)
    def test_sem_token_devolve_401(self, cliente, caminho):
        assert cliente.get(caminho).status_code == 401

    @pytest.mark.parametrize("caminho", CAMINHOS)
    def test_usuario_client_tem_acesso(self, cliente, auth, caminho):
        """⚠️ Dashboard NÃO é admin-only — confirmado contra o Minotto real,
        que usa `get_current_user` nas 5. É a tela de trabalho de quem vende;
        `require_admin` fica pro /api/admin, onde a ação gasta dinheiro."""
        assert cliente.get(caminho, headers=auth).status_code == 200

    def test_admin_tambem_acessa(self, cliente):
        token = create_access_token({"user_id": "adm"})
        r = cliente.get("/api/dashboard/summary",
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200


class TestSummary:
    def test_base_vazia_nao_divide_por_zero(self, cliente, auth):
        corpo = cliente.get("/api/dashboard/summary", headers=auth).json()
        assert corpo["score_medio"] == 0.0
        assert corpo["taxa_conversao"] == 0.0
        assert corpo["receita_fechada_total"] == 0.0

    def test_metricas_com_base_conhecida(self, base_conhecida, auth):
        corpo = base_conhecida.get("/api/dashboard/summary", headers=auth).json()
        # 9 leads, 1 ganho -> 11.1%
        assert corpo["taxa_conversao"] == 11.1
        # proposta_enviada + negociacao
        assert corpo["leads_em_negociacao"] == 2
        # 7×50 + 90 + 10 = 450 -> /9 = 50.0
        assert corpo["score_medio"] == 50.0

    def test_score_nulo_nao_entra_na_media(self, cliente, auth):
        cliente.db.add(lead(1, score=80))
        cliente.db.add(lead(2, score=None))
        cliente.db.commit()
        assert cliente.get("/api/dashboard/summary",
                           headers=auth).json()["score_medio"] == 80.0

    def test_receita_separa_pontual_de_recorrente(self, cliente, auth):
        cliente.db.add(lead(1, status="ganho", tipo_contrato="pontual",
                            valor_fechamento=5000.0))
        cliente.db.add(lead(2, status="ganho", tipo_contrato="recorrente",
                            valor_fechamento=800.0))
        cliente.db.commit()

        corpo = cliente.get("/api/dashboard/summary", headers=auth).json()
        assert corpo["receita_fechada_pontual"] == 5000.0
        assert corpo["receita_fechada_recorrente_mensal"] == 800.0
        assert corpo["receita_fechada_total"] == 5800.0

    def test_so_conta_receita_de_lead_ganho(self, cliente, auth):
        """Valor de fechamento sobrevive a sair de 'ganho' (assimetria da
        Fase 8b) — mas não pode continuar contando como receita."""
        cliente.db.add(lead(1, status="negociacao", tipo_contrato="pontual",
                            valor_fechamento=9999.0))
        cliente.db.commit()
        assert cliente.get("/api/dashboard/summary",
                           headers=auth).json()["receita_fechada_total"] == 0.0

    def test_limite_do_plano_vem_da_config(self, cliente, auth):
        from app.core.config import settings

        corpo = cliente.get("/api/dashboard/summary", headers=auth).json()
        assert corpo["leads_no_mes_limite"] == settings.LEADS_POR_BUSCA

    def test_geracoes_ia_e_sempre_zero_nesta_base(self, base_conhecida, auth):
        """Geração por IA não foi portada (Fase 6) — não há o que contar."""
        assert base_conhecida.get("/api/dashboard/summary",
                                  headers=auth).json()["total_geracoes_ia_mes"] == 0


class TestAcoesRecomendadas:
    def test_base_vazia_devolve_lista_vazia(self, cliente, auth):
        assert cliente.get("/api/dashboard/acoes-recomendadas",
                           headers=auth).json() == []

    def test_omite_categoria_zerada(self, cliente, auth):
        cliente.db.add(lead(1, status="novo_lead", decisor="FULANO"))
        cliente.db.commit()

        chaves = [a["filtro_chave"] for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()]
        assert "decisor_identificado" in chaves
        assert "revisao_manual" not in chaves

    def test_prioridade_alta_usa_o_rotulo_desta_base(self, cliente, auth):
        """⚠️ No Minotto é 'A'; aqui é 'ALTA'. Copiar o 'A' daria contagem
        zero silenciosa — o card sumiria do dashboard sem erro nenhum."""
        cliente.db.add(lead(1, status="novo_lead", prioridade="ALTA"))
        cliente.db.commit()

        acoes = {a["filtro_chave"]: a for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()}
        assert acoes["prioridade_a"]["quantidade"] == 1

    def test_revisao_manual_exige_os_tres_campos_vazios(self, cliente, auth):
        cliente.db.add(lead(1, decisor=None, telefone=None, email=None))
        cliente.db.add(lead(2, decisor=None, telefone="5545999990000", email=None))
        cliente.db.commit()

        acoes = {a["filtro_chave"]: a for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()}
        assert acoes["revisao_manual"]["quantidade"] == 1

    def test_acao_de_whatsapp_substitui_a_de_pgfn(self, cliente, auth):
        """Dívida ativa PGFN é sinal do nicho de saúde e não tem equivalente
        aqui. A substituição conta WhatsApp confirmado + ainda na entrada do
        funil."""
        cliente.db.add(lead(1, status="novo_lead", whatsapp=True))
        cliente.db.add(lead(2, status="qualificacao", whatsapp=True))
        cliente.db.add(lead(3, status="negociacao", whatsapp=True))   # já abordado
        cliente.db.add(lead(4, status="novo_lead", whatsapp=False))   # sem WhatsApp
        cliente.db.add(lead(5, status="novo_lead", whatsapp=None))    # não medido
        cliente.db.commit()

        acoes = {a["filtro_chave"]: a for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()}
        assert acoes["whatsapp_ativo_nao_abordado"]["quantidade"] == 2
        assert "divida_pgfn_nao_abordada" not in acoes

    def test_ordem_e_fixa_nao_por_contagem(self, base_conhecida, auth):
        cliente = base_conhecida
        cliente.db.add(lead(20, status="novo_lead", whatsapp=True))
        cliente.db.commit()

        chaves = [a["filtro_chave"] for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()]
        assert chaves == sorted(chaves, key=[
            "decisor_identificado", "prioridade_a", "revisao_manual",
            "whatsapp_ativo_nao_abordado",
        ].index)


class TestFunil:
    def test_sempre_as_nove_etapas_mesmo_zeradas(self, cliente, auth):
        etapas = cliente.get("/api/dashboard/funil", headers=auth).json()
        assert len(etapas) == 9
        assert all(e["quantidade"] == 0 for e in etapas)

    def test_ordem_e_rotulos_espelham_o_frontend(self, cliente, auth):
        etapas = cliente.get("/api/dashboard/funil", headers=auth).json()
        assert [e["status"] for e in etapas] == [
            "novo_lead", "qualificacao", "contatado", "respondeu", "reuniao",
            "proposta_enviada", "negociacao", "ganho", "perdido",
        ]
        assert [e["label"] for e in etapas] == [
            "Novo Lead", "Qualificação", "Contatado", "Respondeu", "Reunião",
            "Proposta Enviada", "Negociação", "Ganho", "Perdido",
        ]

    def test_contagem_e_percentual(self, base_conhecida, auth):
        etapas = {e["status"]: e for e in base_conhecida.get(
            "/api/dashboard/funil", headers=auth).json()}
        assert etapas["novo_lead"]["quantidade"] == 2
        assert etapas["novo_lead"]["percentual"] == 22.2  # 2/9
        assert etapas["ganho"]["quantidade"] == 1

    def test_perdido_entra_no_denominador(self, base_conhecida, auth):
        """Sem 'perdido' no denominador, a soma fecharia 100% de um universo
        que não é 'todos os leads', e o total deixaria de bater com a
        taxa_conversao do /summary."""
        etapas = base_conhecida.get("/api/dashboard/funil", headers=auth).json()
        assert sum(e["quantidade"] for e in etapas) == 9
        assert round(sum(e["percentual"] for e in etapas)) == 100

    def test_total_do_funil_bate_com_o_summary(self, base_conhecida, auth):
        etapas = base_conhecida.get("/api/dashboard/funil", headers=auth).json()
        summary = base_conhecida.get("/api/dashboard/summary", headers=auth).json()
        total_funil = sum(e["quantidade"] for e in etapas)
        ganhos = next(e["quantidade"] for e in etapas if e["status"] == "ganho")
        assert summary["taxa_conversao"] == round(ganhos / total_funil * 100, 1)


class TestMotivosPerda:
    def test_sem_perdidos_devolve_lista_vazia(self, cliente, auth):
        assert cliente.get("/api/dashboard/motivos-perda", headers=auth).json() == []

    def test_agrupa_e_ordena_por_frequencia(self, cliente, auth):
        for n in range(1, 4):
            cliente.db.add(lead(n, status="perdido", motivo_perda="Preço alto"))
        cliente.db.add(lead(9, status="perdido", motivo_perda="Já tem contador"))
        cliente.db.commit()

        motivos = cliente.get("/api/dashboard/motivos-perda", headers=auth).json()
        assert motivos == [
            {"motivo": "Preço alto", "quantidade": 3},
            {"motivo": "Já tem contador", "quantidade": 1},
        ]

    def test_ignora_perdido_sem_motivo(self, cliente, auth):
        cliente.db.add(lead(1, status="perdido", motivo_perda=None))
        cliente.db.add(lead(2, status="perdido", motivo_perda=""))
        cliente.db.commit()
        assert cliente.get("/api/dashboard/motivos-perda", headers=auth).json() == []

    def test_nao_conta_motivo_de_lead_que_nao_esta_perdido(self, cliente, auth):
        cliente.db.add(lead(1, status="negociacao", motivo_perda="resquício"))
        cliente.db.commit()
        assert cliente.get("/api/dashboard/motivos-perda", headers=auth).json() == []

    def test_limita_a_dez(self, cliente, auth):
        for n in range(1, 15):
            cliente.db.add(lead(n, status="perdido", motivo_perda=f"motivo {n}"))
        cliente.db.commit()
        assert len(cliente.get("/api/dashboard/motivos-perda",
                               headers=auth).json()) == 10


class TestPremissas:
    def test_padrao_calcula_qualificados_da_base_real(self, base_conhecida, auth):
        """5 leads no meio do funil: qualificacao, contatado, reuniao,
        proposta_enviada, negociacao. Fora: novo_lead, ganho, perdido."""
        corpo = base_conhecida.get("/api/dashboard/premissas", headers=auth).json()
        assert corpo["leads_qualificados"] == 5
        assert corpo["taxa_fechamento"] == 20.0
        assert corpo["ticket_medio"] == 1500.0

    def test_qualificado_e_posicao_no_funil_nao_prioridade(self, cliente, auth):
        """⚠️ A confusão que já mordeu no Minotto. Um lead ALTA que ninguém
        contatou NÃO está qualificado; um BAIXA em negociação está."""
        cliente.db.add(lead(1, status="novo_lead", prioridade="ALTA"))
        cliente.db.add(lead(2, status="negociacao", prioridade="BAIXA"))
        cliente.db.commit()
        assert cliente.get("/api/dashboard/premissas",
                           headers=auth).json()["leads_qualificados"] == 1

    def test_get_nao_persiste_o_padrao(self, base_conhecida, auth):
        base_conhecida.get("/api/dashboard/premissas", headers=auth)
        usuario = base_conhecida.db.get(User, "u1")
        assert usuario.dashboard_premissas is None

    def test_put_salva_e_get_devolve_o_salvo(self, base_conhecida, auth):
        novas = {"leads_qualificados": 42, "taxa_fechamento": 35.5,
                 "ticket_medio": 2800.0}
        r = base_conhecida.put("/api/dashboard/premissas", json=novas, headers=auth)
        assert r.status_code == 200
        assert r.json() == novas
        assert base_conhecida.get("/api/dashboard/premissas",
                                  headers=auth).json() == novas

    def test_premissas_sao_por_usuario(self, base_conhecida, auth):
        base_conhecida.put("/api/dashboard/premissas", headers=auth, json={
            "leads_qualificados": 99, "taxa_fechamento": 10.0, "ticket_medio": 500.0})

        outro = {"Authorization": f"Bearer {create_access_token({'user_id': 'adm'})}"}
        corpo = base_conhecida.get("/api/dashboard/premissas", headers=outro).json()
        assert corpo["leads_qualificados"] == 5  # o padrão, não o do outro usuário

    @pytest.mark.parametrize("corpo", [
        {"leads_qualificados": -1, "taxa_fechamento": 20.0, "ticket_medio": 1500.0},
        {"leads_qualificados": 10, "taxa_fechamento": 150.0, "ticket_medio": 1500.0},
        {"leads_qualificados": 10, "taxa_fechamento": 20.0, "ticket_medio": -5.0},
    ])
    def test_valores_sem_sentido_sao_recusados(self, cliente, auth, corpo):
        """O simulador vira conversa comercial — receita estimada negativa ou
        com 150% de conversão não pode chegar na tela."""
        assert cliente.put("/api/dashboard/premissas", json=corpo,
                           headers=auth).status_code == 422


class TestSinaisDentroDoJson:
    """⚠️ Regressão de um bug real, achado ao escrever estes testes.

    Os sinais de enriquecimento moram em ``dados_nicho`` (JSON). Comparar
    ``dados_nicho['chave']`` com ``NULL`` **sem** ``.as_string()`` mente nas
    duas direções: ``is_not(None)`` casa com todo lead (mesmo os sem a chave)
    e ``is_(None)`` não casa com nenhum. O efeito seria "decisor
    identificado" contando a base inteira e "revisão manual" contando zero —
    números plausíveis, errados, sem erro nenhum aparecendo.

    Estes testes montam a base com a chave PRESENTE e AUSENTE ao mesmo tempo,
    que é o cenário que a versão anterior dos testes não cobria.
    """

    def test_decisor_nao_conta_lead_sem_a_chave(self, cliente, auth):
        cliente.db.add(lead(1, status="novo_lead", decisor="FULANO"))
        cliente.db.add(lead(2, status="novo_lead", decisor=None))
        cliente.db.add(lead(3, status="novo_lead", decisor=None))
        cliente.db.commit()

        acoes = {a["filtro_chave"]: a for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()}
        assert acoes["decisor_identificado"]["quantidade"] == 1

    def test_whatsapp_nao_medido_nao_conta_como_confirmado(self, cliente, auth):
        """`None` = não medimos; `False` = medimos e não achamos. Nenhum dos
        dois pode entrar numa lista de 'tem WhatsApp'."""
        cliente.db.add(lead(1, status="novo_lead", whatsapp=True))
        cliente.db.add(lead(2, status="novo_lead", whatsapp=None))
        cliente.db.add(lead(3, status="novo_lead", whatsapp=False))
        cliente.db.commit()

        acoes = {a["filtro_chave"]: a for a in cliente.get(
            "/api/dashboard/acoes-recomendadas", headers=auth).json()}
        assert acoes["whatsapp_ativo_nao_abordado"]["quantidade"] == 1
