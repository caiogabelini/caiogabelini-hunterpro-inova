"""Rotas de geração por IA e o controle de limite por lead.

## ⚠️ Nenhuma chamada real

``ai_enrichment.gerar_*`` é substituído por dublês que **contam chamadas**. Se
alguma mudança futura fizer a rota chamar a Anthropic de verdade, o contador
do dublê denuncia. A fixture ``sem_rede`` (autouse) é a segunda camada.

O que se protege aqui é a regra de custo: limite checado ANTES de gastar,
falha NÃO consome cota, e reset não apaga histórico.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import leads as rotas_leads
from app.core.database import get_db
from app.core.rate_limit import get_redis
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Base, Lead, LeadMessage, User
from app.services.ai_enrichment import MensagemGerada
from tests.conftest import CPF_VALIDO


class IaFake:
    """Conta chamadas. É o detector de gasto acidental."""

    def __init__(self, *, falha: bool = False) -> None:
        self.falha = falha
        self.chamadas = 0
        self.contextos: list[dict] = []

    def mensagem(self, lead: dict, canal: str, cliente=None) -> MensagemGerada:
        self.chamadas += 1
        self.contextos.append(lead)
        if self.falha:
            return MensagemGerada(conteudo="")
        return MensagemGerada(
            conteudo=f"mensagem de {canal}",
            assunto="Assunto" if canal == "email" else None,
        )

    def insights(self, lead: dict, cliente=None) -> dict:
        self.chamadas += 1
        self.contextos.append(lead)
        if self.falha:
            return {}
        return {
            "resumo_estrategico": "Produtor recorrente.",
            "potencial_oportunidade": "alto",
            "recomendacao_abordagem": ["ligar"],
            "estrategia_comunicacao": "WhatsApp.",
            "cta_sugerido": "Conversamos?",
        }


@pytest.fixture()
def ia(monkeypatch) -> IaFake:
    fake = IaFake()
    monkeypatch.setattr(rotas_leads.ai_enrichment, "gerar_mensagem_abordagem", fake.mensagem)
    monkeypatch.setattr(rotas_leads.ai_enrichment, "gerar_insights_estrategicos", fake.insights)
    return fake


@pytest.fixture()
def cliente(ia):
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, future=True)()
    db.add(User(id="u1", email="a@b.com", senha_hash=hash_password("x"), role="client"))
    db.add(User(id="adm", email="c@d.com", senha_hash=hash_password("x"), role="admin"))
    db.add(Lead(
        documento=CPF_VALIDO, nome="ALBERTO LEMUCH FILHO", uf="PR",
        municipio="GUARAPUAVA", score=95, prioridade="ALTA",
        telefone="5542999640915", email="x@y.com",
        dados_nicho={"area_ha": 110.99, "culturas": ["SOJA"], "decisor": "ALBERTO",
                     "valor_financiado": 531009.1, "anos_credito": [2025, 2026],
                     "whatsapp_ativo": True, "email_status": "valid"},
    ))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: None
    with TestClient(app) as c:
        c.db = db  # type: ignore[attr-defined]
        c.ia = ia  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    db.close(); engine.dispose()


@pytest.fixture()
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'user_id': 'u1'})}"}


@pytest.fixture()
def auth_admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'user_id': 'adm'})}"}


def id_do(cliente) -> str:
    return str(cliente.db.query(Lead).one().id)


class TestAutorizacao:
    def test_sem_token_401(self, cliente):
        assert cliente.post("/api/leads/1/gerar-insights").status_code == 401

    def test_qualquer_papel_pode_gerar(self, cliente, auth):
        """Gerar mensagem é o trabalho de quem vende, não operação de admin."""
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/email", headers=auth)
        assert r.status_code == 200

    def test_reset_e_so_admin(self, cliente, auth, auth_admin):
        alvo = id_do(cliente)
        assert cliente.post(f"/api/admin/leads/{alvo}/resetar-limite-ia/email",
                            headers=auth).status_code == 403
        assert cliente.post(f"/api/admin/leads/{alvo}/resetar-limite-ia/email",
                            headers=auth_admin).status_code == 200


class TestGerarAbordagem:
    def test_persiste_e_devolve_a_mensagem(self, cliente, auth):
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/email", headers=auth)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["canal"] == "email"
        assert corpo["conteudo"] == "mensagem de email"
        assert corpo["assunto"] == "Assunto"
        assert cliente.db.query(LeadMessage).count() == 1

    def test_cada_geracao_e_uma_linha_nova_nunca_sobrescreve(self, cliente, auth):
        alvo = id_do(cliente)
        cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email", headers=auth)
        cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email", headers=auth)
        assert cliente.db.query(LeadMessage).count() == 2

    def test_canal_invalido_422_sem_gastar(self, cliente, auth):
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/instagram", headers=auth)
        assert r.status_code == 422
        assert cliente.ia.chamadas == 0

    def test_lead_inexistente_404_sem_gastar(self, cliente, auth):
        assert cliente.post("/api/leads/999999/gerar-abordagem/email",
                            headers=auth).status_code == 404
        assert cliente.ia.chamadas == 0

    def test_ia_sem_conteudo_vira_502_e_nao_persiste(self, cliente, auth, ia):
        ia.falha = True
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/email", headers=auth)
        assert r.status_code == 502
        assert cliente.db.query(LeadMessage).count() == 0

    def test_o_contexto_leva_os_sinais_do_agro(self, cliente, auth, ia):
        cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth)
        contexto = ia.contextos[0]
        assert contexto["area_ha"] == 110.99
        assert contexto["culturas"] == ["SOJA"]
        assert contexto["decisor"] == "ALBERTO"


class TestListarMensagens:
    def test_lead_sem_mensagem_devolve_lista_vazia(self, cliente, auth):
        r = cliente.get(f"/api/leads/{id_do(cliente)}/mensagens", headers=auth)
        assert r.status_code == 200
        assert r.json() == []

    def test_devolve_a_mais_recente_de_cada_canal(self, cliente, auth):
        alvo = id_do(cliente)
        for canal in ("email", "email", "whatsapp"):
            cliente.post(f"/api/leads/{alvo}/gerar-abordagem/{canal}", headers=auth)

        corpo = cliente.get(f"/api/leads/{alvo}/mensagens", headers=auth).json()
        assert len(corpo) == 2  # uma por canal, não 3
        assert {m["canal"] for m in corpo} == {"email", "whatsapp"}
        # E o histórico continua no banco.
        assert cliente.db.query(LeadMessage).count() == 3

    def test_lead_inexistente_404(self, cliente, auth):
        assert cliente.get("/api/leads/999999/mensagens", headers=auth).status_code == 404


class TestGerarInsights:
    def test_persiste_e_devolve_o_lead(self, cliente, auth):
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-insights", headers=auth)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["insights_ia"]["resumo_estrategico"] == "Produtor recorrente."
        assert corpo["insights_gerado_em"] is not None
        assert corpo["geracoes_ia"]["insights"] == 1

    def test_regerar_sobrescreve_sem_histórico(self, cliente, auth):
        alvo = id_do(cliente)
        cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth)
        cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth)
        lead = cliente.db.query(Lead).one()
        assert lead.insights_geracoes_count == 2
        assert isinstance(lead.insights_ia, dict)

    def test_falha_da_ia_NAO_consome_cota(self, cliente, auth, ia):
        """A regra que mais importa: uma geração que falhou não pode queimar
        a tentativa do usuário."""
        ia.falha = True
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-insights", headers=auth)
        assert r.status_code == 502
        assert cliente.db.query(Lead).one().insights_geracoes_count == 0


class TestLimite:
    def test_bloqueia_com_429_apos_o_limite(self, cliente, auth):
        alvo = id_do(cliente)
        assert cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth).status_code == 200
        assert cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth).status_code == 200
        r = cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth)
        assert r.status_code == 429
        assert "administrador" in r.json()["detail"]

    def test_o_429_acontece_ANTES_de_chamar_a_ia(self, cliente, auth, ia):
        """O ponto do limite é não gastar. Checar depois não economizaria."""
        alvo = id_do(cliente)
        for _ in range(2):
            cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth)
        antes = ia.chamadas
        cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth)
        assert ia.chamadas == antes

    def test_limite_e_por_tipo_nao_global(self, cliente, auth):
        alvo = id_do(cliente)
        for _ in range(2):
            cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email", headers=auth)
        assert cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email",
                            headers=auth).status_code == 429
        # WhatsApp e insights continuam liberados.
        assert cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp",
                            headers=auth).status_code == 200
        assert cliente.post(f"/api/leads/{alvo}/gerar-insights",
                            headers=auth).status_code == 200

    def test_limite_zerado_libera_em_vez_de_travar(self, cliente, auth, monkeypatch):
        """§5: config zerada por engano solta o produto, não trava todo mundo."""
        from app.api.routes import limites_ia

        monkeypatch.setattr(limites_ia.settings, "LIMITE_GERACOES_IA_POR_LEAD", 0)
        alvo = id_do(cliente)
        for _ in range(5):
            assert cliente.post(f"/api/leads/{alvo}/gerar-insights",
                                headers=auth).status_code == 200

    def test_dossie_informa_quantas_restam(self, cliente, auth):
        alvo = id_do(cliente)
        cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email", headers=auth)
        corpo = cliente.get(f"/api/leads/{alvo}", headers=auth).json()
        assert corpo["geracoes_ia"] == {"email": 1, "whatsapp": 0, "insights": 0, "limite": 2}


class TestReset:
    def test_reset_libera_novas_geracoes(self, cliente, auth, auth_admin):
        alvo = id_do(cliente)
        for _ in range(2):
            cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth)
        assert cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth).status_code == 429

        cliente.post(f"/api/admin/leads/{alvo}/resetar-limite-ia/insights", headers=auth_admin)
        assert cliente.post(f"/api/leads/{alvo}/gerar-insights", headers=auth).status_code == 200

    def test_reset_de_mensagem_NAO_apaga_historico(self, cliente, auth, auth_admin):
        """⚠️ A razão de `ia_limite_resetado_em` existir: zerar deletando
        linhas destruiria o que `lead_messages` guarda."""
        alvo = id_do(cliente)
        for _ in range(2):
            cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email", headers=auth)
        assert cliente.db.query(LeadMessage).count() == 2

        cliente.post(f"/api/admin/leads/{alvo}/resetar-limite-ia/email", headers=auth_admin)

        # Histórico intacto...
        assert cliente.db.query(LeadMessage).count() == 2
        # ...e a contagem zerada mesmo assim.
        corpo = cliente.get(f"/api/leads/{alvo}", headers=auth).json()
        assert corpo["geracoes_ia"]["email"] == 0
        assert cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email",
                            headers=auth).status_code == 200

    def test_tipo_invalido_422(self, cliente, auth_admin):
        assert cliente.post(f"/api/admin/leads/{id_do(cliente)}/resetar-limite-ia/sms",
                            headers=auth_admin).status_code == 422

    def test_lead_inexistente_404(self, cliente, auth_admin):
        assert cliente.post("/api/admin/leads/999999/resetar-limite-ia/email",
                            headers=auth_admin).status_code == 404
