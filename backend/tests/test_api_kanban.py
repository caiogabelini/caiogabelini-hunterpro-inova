"""``PATCH /api/leads/{id}/status`` — movimentação no Kanban (Fase 8b).

Mesma infra dos demais testes de API: SQLite em memória com pool estático e
``get_db`` trocado por override.

O contrato exercitado aqui é o de ``updateLeadStatus`` em
``frontend/src/api.ts``: os campos de fechamento chegam **achatados** no mesmo
objeto que ``kanban_status``, não aninhados.
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
from app.main import app
from app.models import Base, Lead, User
from tests.conftest import CNPJ_VALIDO, CPF_VALIDO


@pytest.fixture()
def cliente():
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, future=True)()
    db.add(User(id="u1", email="a@b.com", senha_hash=hash_password("x"), role="admin"))
    db.add(Lead(documento=CPF_VALIDO, nome="PRODUTOR ALFA", uf="PR", score=90,
                dados_nicho={"area_ha": 300.0}))
    db.add(Lead(documento=CNPJ_VALIDO, nome="AGRO LTDA", uf="PR", score=40,
                dados_nicho={"area_ha": 800.0}))
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


def _id_do(cliente, documento: str) -> str:
    return cliente.get(f"/api/leads/{documento}", headers={
        "Authorization": f"Bearer {create_access_token({'user_id': 'u1'})}"
    }).json()["id"]


class TestAutorizacao:
    def test_sem_token_devolve_401(self, cliente):
        r = cliente.patch("/api/leads/1/status", json={"kanban_status": "contatado"})
        assert r.status_code == 401

    def test_qualquer_papel_autenticado_pode_mover(self, cliente):
        """Mover card é trabalho diário do vendedor, não operação de admin."""
        cliente.db.add(User(id="u2", email="c@d.com",
                            senha_hash=hash_password("x"), role="client"))
        cliente.db.commit()
        token = create_access_token({"user_id": "u2"})
        alvo = _id_do(cliente, CPF_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status",
                          json={"kanban_status": "contatado"},
                          headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200


class TestValidacao:
    def test_status_desconhecido_devolve_422_com_a_lista(self, cliente, auth):
        alvo = _id_do(cliente, CPF_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status",
                          json={"kanban_status": "coluna_inventada"}, headers=auth)
        assert r.status_code == 422
        assert "novo_lead" in r.json()["detail"]

    def test_lead_inexistente_devolve_404(self, cliente, auth):
        r = cliente.patch("/api/leads/999999/status",
                          json={"kanban_status": "contatado"}, headers=auth)
        assert r.status_code == 404

    def test_perdido_exige_motivo(self, cliente, auth):
        alvo = _id_do(cliente, CPF_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status",
                          json={"kanban_status": "perdido"}, headers=auth)
        assert r.status_code == 422
        assert "motivo_perda" in r.json()["detail"]

    def test_perdido_com_motivo_so_de_espaco_tambem_falha(self, cliente, auth):
        alvo = _id_do(cliente, CPF_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status",
                          json={"kanban_status": "perdido", "motivo_perda": "   "},
                          headers=auth)
        assert r.status_code == 422

    @pytest.mark.parametrize(
        ("corpo", "campo"),
        [
            ({"kanban_status": "ganho"}, "servicos_vendidos"),
            ({"kanban_status": "ganho", "servicos_vendidos": []}, "servicos_vendidos"),
            ({"kanban_status": "ganho", "servicos_vendidos": ["x"]}, "tipo_contrato"),
            ({"kanban_status": "ganho", "servicos_vendidos": ["x"],
              "tipo_contrato": "mensal"}, "tipo_contrato"),
            ({"kanban_status": "ganho", "servicos_vendidos": ["x"],
              "tipo_contrato": "pontual"}, "valor_fechamento"),
            ({"kanban_status": "ganho", "servicos_vendidos": ["x"],
              "tipo_contrato": "pontual", "valor_fechamento": 0}, "valor_fechamento"),
        ],
    )
    def test_ganho_exige_os_tres_campos_do_modal(self, cliente, auth, corpo, campo):
        alvo = _id_do(cliente, CPF_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status", json=corpo, headers=auth)
        assert r.status_code == 422
        assert campo in r.json()["detail"]

    def test_etapa_intermediaria_nao_exige_nada(self, cliente, auth):
        alvo = _id_do(cliente, CPF_VALIDO)
        for etapa in ("qualificacao", "contatado", "respondeu", "reuniao",
                      "proposta_enviada", "negociacao"):
            r = cliente.patch(f"/api/leads/{alvo}/status",
                              json={"kanban_status": etapa}, headers=auth)
            assert r.status_code == 200, etapa
            assert r.json()["kanban_status"] == etapa


class TestPersistencia:
    def test_lead_novo_nasce_na_primeira_coluna(self, cliente, auth):
        r = cliente.get(f"/api/leads/{CPF_VALIDO}", headers=auth)
        assert r.json()["kanban_status"] == "novo_lead"

    def test_ganho_grava_os_dados_do_modal(self, cliente, auth):
        alvo = _id_do(cliente, CNPJ_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status", headers=auth, json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["contabilidade_consultiva", "Consultoria de safra"],
            "tipo_contrato": "recorrente",
            "valor_fechamento": 2500.0,
        })
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["kanban_status"] == "ganho"
        assert corpo["servicos_vendidos"] == [
            "contabilidade_consultiva", "Consultoria de safra"
        ]
        assert corpo["tipo_contrato"] == "recorrente"
        assert corpo["valor_fechamento"] == 2500.0
        # E sobrevive a uma nova leitura, não só na resposta do PATCH.
        assert cliente.get(f"/api/leads/{CNPJ_VALIDO}",
                           headers=auth).json()["valor_fechamento"] == 2500.0

    def test_servico_livre_do_campo_outro_e_aceito(self, cliente, auth):
        """`servicos_vendidos` não é enum: o texto digitado em "Outro" no
        FechamentoModal entra como item normal da lista."""
        alvo = _id_do(cliente, CPF_VALIDO)
        r = cliente.patch(f"/api/leads/{alvo}/status", headers=auth, json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["Assessoria pra financiamento do Plano Safra"],
            "tipo_contrato": "pontual", "valor_fechamento": 900.0,
        })
        assert r.status_code == 200
        assert r.json()["servicos_vendidos"] == [
            "Assessoria pra financiamento do Plano Safra"
        ]

    def test_sair_de_perdido_limpa_o_motivo(self, cliente, auth):
        alvo = _id_do(cliente, CPF_VALIDO)
        cliente.patch(f"/api/leads/{alvo}/status", headers=auth,
                      json={"kanban_status": "perdido", "motivo_perda": "sem interesse"})
        assert cliente.get(f"/api/leads/{CPF_VALIDO}",
                           headers=auth).json()["motivo_perda"] == "sem interesse"

        cliente.patch(f"/api/leads/{alvo}/status", headers=auth,
                      json={"kanban_status": "negociacao"})
        assert cliente.get(f"/api/leads/{CPF_VALIDO}",
                           headers=auth).json()["motivo_perda"] is None

    def test_sair_de_ganho_NAO_limpa_o_fechamento(self, cliente, auth):
        """Assimetria deliberada: uma venda que aconteceu continua tendo
        acontecido, mesmo que o card seja movido de volta por engano."""
        alvo = _id_do(cliente, CPF_VALIDO)
        cliente.patch(f"/api/leads/{alvo}/status", headers=auth, json={
            "kanban_status": "ganho", "servicos_vendidos": ["x"],
            "tipo_contrato": "pontual", "valor_fechamento": 1000.0,
        })
        cliente.patch(f"/api/leads/{alvo}/status", headers=auth,
                      json={"kanban_status": "negociacao"})

        corpo = cliente.get(f"/api/leads/{CPF_VALIDO}", headers=auth).json()
        assert corpo["kanban_status"] == "negociacao"
        assert corpo["valor_fechamento"] == 1000.0
        assert corpo["servicos_vendidos"] == ["x"]

    def test_aceita_documento_alem_do_id(self, cliente, auth):
        """O Kanban manda `lead.id`, mas a rota resolve os dois — mesma
        resolução do dossiê (`_resolver_lead`)."""
        r = cliente.patch(f"/api/leads/{CPF_VALIDO}/status",
                          json={"kanban_status": "contatado"}, headers=auth)
        assert r.status_code == 200


class TestFiltroDaLista:
    def test_lista_filtra_por_kanban_status(self, cliente, auth):
        alvo = _id_do(cliente, CPF_VALIDO)
        cliente.patch(f"/api/leads/{alvo}/status",
                      json={"kanban_status": "reuniao"}, headers=auth)

        r = cliente.get("/api/leads/lista?kanban_status=reuniao", headers=auth)
        assert r.status_code == 200
        assert [i["documento"] for i in r.json()["items"]] == [CPF_VALIDO]

    def test_status_invalido_no_filtro_e_ignorado_nao_da_422(self, cliente, auth):
        r = cliente.get("/api/leads/lista?kanban_status=inventado", headers=auth)
        assert r.status_code == 200
        assert r.json()["total"] == 2
