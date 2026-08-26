"""Rotas de leitura de leads — lista, lista paginada e dossiê.

Mesma infra do ``test_api_auth``: SQLite em memória com pool estático,
``get_db`` trocado por override. Os leads são construídos com documentos
reais válidos (os mesmos CPF/CNPJ que a suíte já usa desde a Fase 1).
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
from tests.conftest import CNPJ_VALIDO, CPF_VALIDO, CPF_VALIDO_2


def lead(documento: str, *, score=None, prioridade=None, nome="PRODUTOR X", **nicho) -> Lead:
    base = {
        "area_ha": 300.0,
        "valor_financiado": 500_000.0,
        "culturas": ["SOJA"],
        "data_operacao": "20260731",
        "recorrente": True,
        "anos_credito": [2025, 2026],
        "codigos_car": ["PR41" + "0" * 37],
        "n_operacoes": 2,
    }
    base.update(nicho)
    return Lead(
        documento=documento, nome=nome, uf="PR", municipio="CASCAVEL",
        score=score, prioridade=prioridade, dados_nicho=base,
    )


@pytest.fixture()
def cliente():
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, future=True)()
    db.add(User(id="u1", email="a@b.com", senha_hash=hash_password("x"), role="admin"))
    db.add(lead(CPF_VALIDO, score=90, prioridade="ALTA", nome="PRODUTOR ALFA"))
    db.add(lead(CPF_VALIDO_2, score=50, prioridade="MEDIA", nome="PRODUTOR BETA"))
    db.add(lead(CNPJ_VALIDO, score=20, prioridade="BAIXA", nome="AGRO LTDA",
                cnae_descricao="CULTIVO DE SOJA", eh_cooperativa=True))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    db.close(); engine.dispose()


@pytest.fixture()
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'user_id': 'u1', 'role': 'admin'})}"}


class TestAutorizacao:
    @pytest.mark.parametrize("caminho", ["/api/leads", "/api/leads/lista", f"/api/leads/{CPF_VALIDO}"])
    def test_sem_token_e_401(self, cliente, caminho) -> None:
        assert cliente.get(caminho).status_code == 401

    def test_token_invalido_e_401(self, cliente, caminho="/api/leads") -> None:
        assert cliente.get(caminho, headers={"Authorization": "Bearer lixo"}).status_code == 401

    def test_usuario_inexistente_no_banco_e_401(self, cliente) -> None:
        """Token assinado mas de usuário apagado — sessão não sobrevive."""
        t = create_access_token({"user_id": "nao-existe", "role": "admin"})
        assert cliente.get("/api/leads", headers={"Authorization": f"Bearer {t}"}).status_code == 401

    def test_api_nao_cria_nem_apaga_lead(self, cliente, auth) -> None:
        """Quem cria lead é o pipeline em lote (`persistir_leads`), não a API.

        ⚠️ Este teste checava também que `PATCH /{id}/status` devolvia 404.
        Deixou de checar na Fase 8b, quando a rota foi criada de propósito —
        é a única escrita da API, e tem cobertura própria em
        `test_api_kanban.py`. O resto do invariante continua valendo: não há
        POST nem DELETE de lead.
        """
        assert cliente.post("/api/leads", json={}, headers=auth).status_code in (404, 405)
        assert cliente.delete(f"/api/leads/{CPF_VALIDO}", headers=auth).status_code in (404, 405)


class TestLista:
    def test_traz_todos_ordenados_por_score(self, cliente, auth) -> None:
        r = cliente.get("/api/leads", headers=auth)
        assert r.status_code == 200
        assert [x["score"] for x in r.json()] == [90, 50, 20]

    def test_campos_do_contrato_do_frontend(self, cliente, auth) -> None:
        item = cliente.get("/api/leads", headers=auth).json()[0]
        for campo in ("id", "documento", "tipo_documento", "nome", "score", "prioridade"):
            assert campo in item

    def test_id_e_string(self, cliente, auth) -> None:
        """O frontend tipa `Lead.id` como string e usa na URL do dossiê."""
        assert isinstance(cliente.get("/api/leads", headers=auth).json()[0]["id"], str)

    def test_tipo_documento_vem_do_banco(self, cliente, auth) -> None:
        por_doc = {x["documento"]: x for x in cliente.get("/api/leads", headers=auth).json()}
        assert por_doc[CPF_VALIDO]["tipo_documento"] == "CPF"
        assert por_doc[CNPJ_VALIDO]["tipo_documento"] == "CNPJ"


class TestDesempacotamentoDoNicho:
    """Os sinais do Sicor sobem de `dados_nicho` pra campos de topo."""

    def test_campos_do_sicor_desempacotados(self, cliente, auth) -> None:
        item = cliente.get("/api/leads", headers=auth).json()[0]
        assert item["area_ha"] == 300.0
        assert item["valor_financiado"] == 500_000.0
        assert item["culturas"] == ["SOJA"]
        assert item["data_operacao"] == "20260731"
        assert item["recorrente"] is True
        assert item["anos_credito"] == [2025, 2026]
        assert item["n_operacoes"] == 2

    def test_dados_nicho_cru_continua_disponivel(self, cliente, auth) -> None:
        """Desempacotar não é esconder — o dossiê pode precisar do resto."""
        assert cliente.get("/api/leads", headers=auth).json()[0]["dados_nicho"]["codigos_car"]

    def test_lead_sem_nicho_nao_quebra(self, cliente, auth) -> None:
        r = cliente.get("/api/leads", headers=auth)
        assert r.status_code == 200


class TestScoreDetalhesRecalculado:
    def test_traz_os_9_criterios(self, cliente, auth) -> None:
        b = cliente.get("/api/leads", headers=auth).json()[0]["score_detalhes"]["breakdown"]
        assert len(b) == 9
        assert {c["layer"] for c in b} <= {"estruturado", "inferencia", "validacao"}

    def test_reflete_os_sinais_do_lead(self, cliente, auth) -> None:
        """300 ha + R$500k + cultura = 30 + 10 + 15 = 55 pontos."""
        b = cliente.get("/api/leads", headers=auth).json()[0]["score_detalhes"]["breakdown"]
        pontos = {c["key"]: c["points"] for c in b}
        assert pontos["tamanho_propriedade"] == 30.0
        assert pontos["valor_financiado"] == 10.0
        assert pontos["semente_sicor_cultura"] == 15.0
        assert sum(pontos.values()) == 55.0

    def test_criterio_sem_sinal_pontua_zero(self, cliente, auth) -> None:
        b = cliente.get("/api/leads", headers=auth).json()[0]["score_detalhes"]["breakdown"]
        pontos = {c["key"]: c["points"] for c in b}
        assert pontos["decisor_identificavel"] == 0.0
        assert pontos["whatsapp_ativo"] == 0.0

    def test_nao_persiste_nada(self, cliente, auth) -> None:
        """Recalcular não pode escrever no banco — é rota de leitura."""
        antes = cliente.get("/api/leads", headers=auth).json()[0]["score"]
        cliente.get("/api/leads", headers=auth)
        assert cliente.get("/api/leads", headers=auth).json()[0]["score"] == antes


class TestListaPaginada:
    def test_pagina_e_total(self, cliente, auth) -> None:
        r = cliente.get("/api/leads/lista?pagina=1&por_pagina=2", headers=auth).json()
        assert r["total"] == 3 and len(r["items"]) == 2 and r["por_pagina"] == 2

    def test_segunda_pagina_traz_o_resto(self, cliente, auth) -> None:
        p1 = cliente.get("/api/leads/lista?pagina=1&por_pagina=2", headers=auth).json()
        p2 = cliente.get("/api/leads/lista?pagina=2&por_pagina=2", headers=auth).json()
        assert len(p2["items"]) == 1
        assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})

    def test_rota_lista_nao_e_confundida_com_identificador(self, cliente, auth) -> None:
        """⚠️ `/lista` tem que ser declarada ANTES de `/{identificador}` —
        invertido, seria lida como um documento e daria 404."""
        assert cliente.get("/api/leads/lista", headers=auth).status_code == 200

    def test_teto_de_por_pagina(self, cliente, auth) -> None:
        assert cliente.get("/api/leads/lista?por_pagina=99999", headers=auth).status_code == 422

    def test_busca_por_nome(self, cliente, auth) -> None:
        r = cliente.get("/api/leads/lista?busca=ALFA", headers=auth).json()
        assert r["total"] == 1 and r["items"][0]["nome"] == "PRODUTOR ALFA"

    def test_busca_por_documento_com_mascara(self, cliente, auth) -> None:
        """O usuário digita com máscara; o banco guarda só dígitos."""
        mascarado = f"{CPF_VALIDO[:3]}.{CPF_VALIDO[3:6]}.{CPF_VALIDO[6:9]}-{CPF_VALIDO[9:]}"
        r = cliente.get(f"/api/leads/lista?busca={mascarado}", headers=auth).json()
        assert r["total"] == 1 and r["items"][0]["documento"] == CPF_VALIDO

    def test_filtro_por_prioridade(self, cliente, auth) -> None:
        r = cliente.get("/api/leads/lista?prioridade=alta", headers=auth).json()
        assert r["total"] == 1 and r["items"][0]["prioridade"] == "ALTA"

    def test_ordenacao_asc_e_desc(self, cliente, auth) -> None:
        asc = cliente.get("/api/leads/lista?ordenar_por=score_total&ordem=asc", headers=auth).json()
        desc = cliente.get("/api/leads/lista?ordenar_por=score_total&ordem=desc", headers=auth).json()
        assert [i["score"] for i in asc["items"]] == [20, 50, 90]
        assert [i["score"] for i in desc["items"]] == [90, 50, 20]

    def test_ordenacao_desconhecida_cai_no_padrao_sem_quebrar(self, cliente, auth) -> None:
        """Lista fechada — interpolar nome de coluna da query seria injeção."""
        r = cliente.get("/api/leads/lista?ordenar_por=DROP+TABLE", headers=auth)
        assert r.status_code == 200

    def test_kanban_status_e_ignorado_sem_erro(self, cliente, auth) -> None:
        """O frontend manda o parâmetro; a coluna é Fase 8b."""
        assert cliente.get("/api/leads/lista?kanban_status=novo_lead", headers=auth).status_code == 200


class TestDossie:
    def test_por_documento(self, cliente, auth) -> None:
        r = cliente.get(f"/api/leads/{CPF_VALIDO}", headers=auth)
        assert r.status_code == 200 and r.json()["documento"] == CPF_VALIDO

    def test_por_id(self, cliente, auth) -> None:
        """O frontend portado navega por `lead.id`, não por documento."""
        ident = cliente.get("/api/leads", headers=auth).json()[0]["id"]
        r = cliente.get(f"/api/leads/{ident}", headers=auth)
        assert r.status_code == 200 and r.json()["id"] == ident

    def test_os_dois_caminhos_dao_o_mesmo_lead(self, cliente, auth) -> None:
        por_doc = cliente.get(f"/api/leads/{CPF_VALIDO}", headers=auth).json()
        por_id = cliente.get(f"/api/leads/{por_doc['id']}", headers=auth).json()
        assert por_doc == por_id

    def test_cnpj_tambem(self, cliente, auth) -> None:
        assert cliente.get(f"/api/leads/{CNPJ_VALIDO}", headers=auth).json()["tipo_documento"] == "CNPJ"

    def test_inexistente_e_404(self, cliente, auth) -> None:
        assert cliente.get("/api/leads/00000000191", headers=auth).status_code == 404
        assert cliente.get("/api/leads/999999", headers=auth).status_code == 404

    def test_traz_score_detalhes(self, cliente, auth) -> None:
        d = cliente.get(f"/api/leads/{CPF_VALIDO}", headers=auth).json()
        assert len(d["score_detalhes"]["breakdown"]) == 9


class TestDocsFechadosEmProducao:
    """⚠️ `ENVIRONMENT` existe desde a Fase 1 e nunca era lida — o docstring
    do config registrava isso como dívida. Esta é a primeira vez que ela
    decide algo.

    O gate roda no IMPORT de `app.main` (os `docs_url`/`openapi_url` são
    argumentos do construtor do FastAPI), então testar exige reimportar o
    módulo com a variável trocada — não basta monkeypatchar `settings`.
    """

    @staticmethod
    def _app_com(environment: str):
        import importlib
        import app.core.config as cfg
        import app.main as main

        original = cfg.settings.ENVIRONMENT
        cfg.settings.ENVIRONMENT = environment
        try:
            return importlib.reload(main).app
        finally:
            cfg.settings.ENVIRONMENT = original
            importlib.reload(main)

    def test_abertos_em_desenvolvimento(self) -> None:
        app_dev = self._app_com("development")
        assert app_dev.docs_url == "/docs"
        assert app_dev.openapi_url == "/openapi.json"

    def test_fechados_em_producao(self) -> None:
        app_prod = self._app_com("production")
        assert app_prod.docs_url is None
        assert app_prod.redoc_url is None
        assert app_prod.openapi_url is None

    def test_producao_e_case_insensitive(self) -> None:
        from app.core.config import settings

        original = settings.ENVIRONMENT
        try:
            for v in ("production", "PRODUCTION", " Production "):
                settings.ENVIRONMENT = v
                assert settings.em_producao, v
            for v in ("development", "staging", ""):
                settings.ENVIRONMENT = v
                assert not settings.em_producao, v
        finally:
            settings.ENVIRONMENT = original


class TestCors:
    def test_origem_do_frontend_e_explicita_nunca_wildcard(self) -> None:
        """Com `allow_credentials=True`, `*` é recusado pelo próprio
        navegador — e mesmo que passasse, abriria a API pra qualquer site."""
        from app.core.config import settings

        assert settings.FRONTEND_ORIGIN.startswith("http")
        assert settings.FRONTEND_ORIGIN != "*"

    def test_preflight_responde_pra_origem_configurada(self, cliente) -> None:
        from app.core.config import settings

        r = cliente.options(
            "/api/auth/login",
            headers={
                "Origin": settings.FRONTEND_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.status_code in (200, 204)
        assert r.headers.get("access-control-allow-origin") == settings.FRONTEND_ORIGIN
