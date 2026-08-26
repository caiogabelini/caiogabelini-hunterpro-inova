"""Rotas de ``/api/admin/buscas`` — disparo e histórico de busca (Fase 8b).

## ⚠️ Nenhum teste aqui dispara uma busca de verdade

``get_disparador`` é trocado por um fake via ``app.dependency_overrides``. O
fake só **anota** o ``busca_id`` que receberia; nada do pipeline pago é
importado, chamado ou aproximado. O que se verifica é o contrato da rota:
que ela cria o registro certo, responde na hora e despacha exatamente um id.

Além disso a fixture ``sem_rede`` (autouse, ver conftest) bloqueia socket na
suíte inteira — uma tentativa acidental de falar com Redis, com o broker ou
com qualquer API paga viraria AssertionError, não uma chamada real.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.admin import get_disparador
from app.core.database import get_db
from app.core.rate_limit import get_redis
from app.core.security import create_access_token, hash_password
from app.core.tempo import agora_utc
from app.main import app
from app.models import Base, BuscaLeadsRegistro, User


class DisparadorFake:
    """Substitui o despacho pro Celery. Registra, nunca executa."""

    def __init__(self, explode: bool = False) -> None:
        self.recebidos: list[str] = []
        self.explode = explode

    def __call__(self, busca_id: str) -> None:
        if self.explode:
            raise ConnectionError("broker indisponível")
        self.recebidos.append(busca_id)


@pytest.fixture()
def disparador() -> DisparadorFake:
    return DisparadorFake()


@pytest.fixture()
def cliente(disparador):
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, future=True)()
    db.add(User(id="admin1", email="admin@inova.com",
                senha_hash=hash_password("x"), role="admin"))
    db.add(User(id="cli1", email="vendedor@inova.com",
                senha_hash=hash_password("x"), role="client"))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: None
    app.dependency_overrides[get_disparador] = lambda: disparador
    with TestClient(app) as c:
        c.db = db  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    db.close(); engine.dispose()


@pytest.fixture()
def auth_admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'user_id': 'admin1'})}"}


@pytest.fixture()
def auth_client() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'user_id': 'cli1'})}"}


CAMINHOS = ["/api/admin/buscas", "/api/admin/buscas/qualquer-id"]


class TestAutorizacao:
    @pytest.mark.parametrize("caminho", CAMINHOS)
    def test_sem_token_devolve_401(self, cliente, caminho):
        assert cliente.get(caminho).status_code == 401

    @pytest.mark.parametrize("caminho", CAMINHOS)
    def test_usuario_client_devolve_403_nao_401(self, cliente, auth_client, caminho):
        """403, não 401: o token é válido, o que falta é permissão. O frontend
        distingue os dois — 401 desloga, 403 volta pro Kanban."""
        assert cliente.get(caminho, headers=auth_client).status_code == 403

    def test_client_nao_dispara_busca(self, cliente, auth_client, disparador):
        r = cliente.post("/api/admin/buscas", headers=auth_client)
        assert r.status_code == 403
        assert disparador.recebidos == []
        assert cliente.db.query(BuscaLeadsRegistro).count() == 0


class TestDisparo:
    def test_cria_registro_executando_e_despacha_uma_vez(
        self, cliente, auth_admin, disparador
    ):
        r = cliente.post("/api/admin/buscas", headers=auth_admin)
        assert r.status_code == 201

        corpo = r.json()
        assert corpo["status"] == "executando"
        assert corpo["iniciado_por_id"] == "admin1"
        assert corpo["concluido_em"] is None
        # ⚠️ None, não 0: "ainda não sabemos" ≠ "sabemos que é zero".
        assert corpo["total_cnpjs_encontrados"] is None
        assert corpo["total_cnpjs_selecionados"] is None
        assert corpo["total_leads_processados"] is None
        assert corpo["erros"] is None

        assert disparador.recebidos == [corpo["id"]]

    def test_responde_sem_executar_nada_do_pipeline(
        self, cliente, auth_admin, disparador
    ):
        """A rota devolve 201 na hora; quem gasta é o worker, depois.

        Se o pipeline rodasse dentro do request, este teste falharia por
        timeout ou por rede bloqueada — não por asserção."""
        cliente.post("/api/admin/buscas", headers=auth_admin)
        assert len(disparador.recebidos) == 1

    def test_segunda_busca_com_uma_em_andamento_devolve_409(
        self, cliente, auth_admin, disparador
    ):
        assert cliente.post("/api/admin/buscas", headers=auth_admin).status_code == 201
        r = cliente.post("/api/admin/buscas", headers=auth_admin)
        assert r.status_code == 409
        assert "em andamento" in r.json()["detail"]
        # A segunda não pode ter despachado nem criado registro.
        assert len(disparador.recebidos) == 1
        assert cliente.db.query(BuscaLeadsRegistro).count() == 1

    def test_busca_concluida_nao_bloqueia_a_proxima(
        self, cliente, auth_admin, disparador
    ):
        primeira = cliente.post("/api/admin/buscas", headers=auth_admin).json()
        registro = cliente.db.get(BuscaLeadsRegistro, primeira["id"])
        registro.status = "concluido"
        registro.concluido_em = agora_utc()
        cliente.db.commit()

        assert cliente.post("/api/admin/buscas", headers=auth_admin).status_code == 201
        assert len(disparador.recebidos) == 2

    def test_broker_fora_do_ar_devolve_503_e_nao_deixa_registro_preso(
        self, cliente, auth_admin
    ):
        """Sem isto o registro ficaria "executando" pra sempre e o 409 acima
        travaria o painel por causa de uma busca que nunca começou."""
        app.dependency_overrides[get_disparador] = lambda: DisparadorFake(explode=True)

        r = cliente.post("/api/admin/buscas", headers=auth_admin)
        assert r.status_code == 503
        assert "Nenhum custo foi gerado" in r.json()["detail"]

        registro = cliente.db.query(BuscaLeadsRegistro).one()
        assert registro.status == "erro"
        assert registro.concluido_em is not None
        assert "despachar" in registro.erros[0]

        # E o painel continua liberado pra tentar de novo.
        app.dependency_overrides[get_disparador] = lambda: DisparadorFake()
        assert cliente.post("/api/admin/buscas", headers=auth_admin).status_code == 201


class TestHistorico:
    def test_lista_vem_da_mais_recente_pra_mais_antiga(self, cliente, auth_admin):
        for i, dia in enumerate((1, 3, 2)):
            cliente.db.add(BuscaLeadsRegistro(
                id=f"b{i}", iniciado_por_id="admin1", status="concluido",
                iniciado_em=agora_utc().replace(day=dia),
            ))
        cliente.db.commit()

        r = cliente.get("/api/admin/buscas", headers=auth_admin)
        assert r.status_code == 200
        assert [b["id"] for b in r.json()] == ["b1", "b2", "b0"]

    def test_limit_respeitado_e_limitado(self, cliente, auth_admin):
        for i in range(5):
            cliente.db.add(BuscaLeadsRegistro(
                id=f"b{i}", iniciado_por_id="admin1", status="concluido"))
        cliente.db.commit()

        assert len(cliente.get("/api/admin/buscas?limit=2", headers=auth_admin).json()) == 2
        assert cliente.get("/api/admin/buscas?limit=0", headers=auth_admin).status_code == 422
        assert cliente.get("/api/admin/buscas?limit=500", headers=auth_admin).status_code == 422

    def test_detalhe_devolve_o_estado_para_polling(self, cliente, auth_admin):
        cliente.db.add(BuscaLeadsRegistro(
            id="b1", iniciado_por_id="admin1", status="concluido",
            concluido_em=agora_utc(), total_cnpjs_encontrados=2806,
            total_cnpjs_selecionados=60, total_leads_processados=58,
            erros=["52998224725 — whatsapp: sem chave configurada"],
        ))
        cliente.db.commit()

        corpo = cliente.get("/api/admin/buscas/b1", headers=auth_admin).json()
        assert corpo["status"] == "concluido"
        assert corpo["total_cnpjs_encontrados"] == 2806
        assert corpo["total_cnpjs_selecionados"] == 60
        assert corpo["total_leads_processados"] == 58
        assert corpo["erros"] == ["52998224725 — whatsapp: sem chave configurada"]

    def test_busca_inexistente_devolve_404(self, cliente, auth_admin):
        assert cliente.get("/api/admin/buscas/nao-existe",
                           headers=auth_admin).status_code == 404
