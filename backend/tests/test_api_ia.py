"""Rotas de geração por IA e o controle de limite por lead.

## ⚠️ Nenhuma chamada real

``ai_enrichment.gerar_*`` é substituído por dublês que **contam chamadas**. Se
alguma mudança futura fizer a rota chamar a Anthropic de verdade, o contador
do dublê denuncia. A fixture ``sem_rede`` (autouse) é a segunda camada.

O que se protege aqui é a regra de custo: limite checado ANTES de gastar,
falha NÃO consome cota, e reset não apaga histórico.
"""

from __future__ import annotations

import uuid

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
from app.models.lead_message import TAMANHO_SEQUENCIA
from app.services.ai_enrichment import MensagemGerada
from tests.conftest import CPF_VALIDO


class IaFake:
    """Conta chamadas. É o detector de gasto acidental."""

    def __init__(self, *, falha: bool = False) -> None:
        self.falha = falha
        self.chamadas = 0
        self.contextos: list[dict] = []

    def sequencia(self, lead: dict, canal: str, cliente=None) -> list[MensagemGerada]:
        """UMA chamada devolve a sequência inteira — 3 no WhatsApp, 2 no
        e-mail. O contador conta chamadas, não mensagens: é assim que este
        dublê denuncia alguém trocando isto por um laço de N chamadas."""
        self.chamadas += 1
        self.contextos.append(lead)
        if self.falha:
            return []
        return [
            MensagemGerada(
                conteudo=f"mensagem {i} de {canal}",
                assunto=f"Assunto {i}" if canal == "email" else None,
                ordem=i,
            )
            for i in range(1, TAMANHO_SEQUENCIA[canal] + 1)
        ]

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
    monkeypatch.setattr(rotas_leads.ai_enrichment, "gerar_sequencia_abordagem", fake.sequencia)
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
    def test_whatsapp_persiste_a_sequencia_de_3(self, cliente, auth):
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["canal"] == "whatsapp"
        assert corpo["total"] == 3
        assert [m["ordem"] for m in corpo["mensagens"]] == [1, 2, 3]
        assert cliente.db.query(LeadMessage).count() == 3

    def test_email_persiste_a_sequencia_de_2_com_assunto(self, cliente, auth):
        corpo = cliente.post(
            f"/api/leads/{id_do(cliente)}/gerar-abordagem/email", headers=auth
        ).json()
        assert corpo["total"] == 2
        assert [m["assunto"] for m in corpo["mensagens"]] == ["Assunto 1", "Assunto 2"]

    def test_a_sequencia_nasce_toda_pendente(self, cliente, auth):
        corpo = cliente.post(
            f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth
        ).json()
        assert {m["status"] for m in corpo["mensagens"]} == {"pendente"}
        assert all(m["enviada_em"] is None for m in corpo["mensagens"])
        # E a tela já sabe qual botão liberar sem recalcular a regra.
        assert corpo["proxima_ordem"] == 1

    def test_todas_as_mensagens_do_grupo_dividem_o_mesmo_grupo_id(self, cliente, auth):
        corpo = cliente.post(
            f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth
        ).json()
        grupos = {m["id"] for m in corpo["mensagens"]}
        assert len(grupos) == 3  # ids próprios...
        linhas = cliente.db.query(LeadMessage).all()
        assert len({l.grupo_id for l in linhas}) == 1  # ...num grupo só.
        assert all(l.grupo_id == corpo["grupo_id"] for l in linhas)

    def test_uma_geracao_e_UMA_chamada_paga_mesmo_gerando_3(self, cliente, auth, ia):
        """⚠️ O ponto econômico da Fase 11a: o custo não cresce com o tamanho
        da sequência do jeito que cresceria com 3 chamadas."""
        cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth)
        assert ia.chamadas == 1

    def test_gerar_de_novo_cria_grupo_NOVO_e_nao_completa_o_anterior(self, cliente, auth):
        alvo = id_do(cliente)
        primeiro = cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp", headers=auth).json()
        segundo = cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp", headers=auth).json()
        assert primeiro["grupo_id"] != segundo["grupo_id"]
        assert cliente.db.query(LeadMessage).count() == 6  # o anterior fica

    def test_canal_invalido_422_sem_gastar(self, cliente, auth):
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/instagram", headers=auth)
        assert r.status_code == 422
        assert cliente.ia.chamadas == 0

    def test_lead_inexistente_404_sem_gastar(self, cliente, auth):
        assert cliente.post("/api/leads/999999/gerar-abordagem/email",
                            headers=auth).status_code == 404
        assert cliente.ia.chamadas == 0

    def test_ia_sem_conteudo_vira_502_e_nao_persiste_nada(self, cliente, auth, ia):
        """Nem sequência vazia, nem sequência pela metade."""
        ia.falha = True
        r = cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth)
        assert r.status_code == 502
        assert cliente.db.query(LeadMessage).count() == 0

    def test_o_contexto_leva_os_sinais_do_agro(self, cliente, auth, ia):
        cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/whatsapp", headers=auth)
        contexto = ia.contextos[0]
        assert contexto["area_ha"] == 110.99
        assert contexto["culturas"] == ["SOJA"]
        assert contexto["decisor"] == "ALBERTO"


class TestMarcarEnviada:
    """⚠️ A regra "não dá pra pular etapa" da Fase 11a.

    Marcar o follow-up antes do primeiro contato descreveria uma cadência que
    não aconteceu — e é dela que a tela tira o que oferecer em seguida.
    """

    def gerar(self, cliente, auth, canal="whatsapp") -> dict:
        return cliente.post(
            f"/api/leads/{id_do(cliente)}/gerar-abordagem/{canal}", headers=auth
        ).json()

    def marcar(self, cliente, auth, mensagem_id: str):
        return cliente.patch(
            f"/api/leads/{id_do(cliente)}/mensagens/{mensagem_id}/enviada", headers=auth
        )

    def test_marca_a_primeira_e_avanca_a_sequencia(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        r = self.marcar(cliente, auth, seq["mensagens"][0]["id"])
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["mensagens"][0]["status"] == "enviada"
        assert corpo["mensagens"][0]["enviada_em"] is not None
        assert corpo["mensagens"][1]["status"] == "pendente"
        assert corpo["proxima_ordem"] == 2

    def test_pular_para_a_2_com_a_1_pendente_e_422_com_motivo(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        r = self.marcar(cliente, auth, seq["mensagens"][1]["id"])
        assert r.status_code == 422  # ⚠️ 422, não 500: entrada inválida
        assert "pular etapa" in r.json()["detail"]
        assert "1 de 3" in r.json()["detail"]

    def test_pular_para_a_ultima_tambem_e_422(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        assert self.marcar(cliente, auth, seq["mensagens"][2]["id"]).status_code == 422

    def test_fora_de_ordem_NAO_altera_nada_no_banco(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        self.marcar(cliente, auth, seq["mensagens"][2]["id"])
        assert cliente.db.query(LeadMessage).filter(
            LeadMessage.status == "enviada"
        ).count() == 0

    def test_a_sequencia_inteira_pode_ser_marcada_em_ordem(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        for mensagem in seq["mensagens"]:
            assert self.marcar(cliente, auth, mensagem["id"]).status_code == 200
        corpo = cliente.get(f"/api/leads/{id_do(cliente)}/mensagens", headers=auth).json()
        assert {m["status"] for m in corpo["whatsapp"]["mensagens"]} == {"enviada"}
        # Acabou a cadência: não há próxima.
        assert corpo["whatsapp"]["proxima_ordem"] is None

    def test_remarcar_uma_ja_enviada_e_422(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        primeira = seq["mensagens"][0]["id"]
        assert self.marcar(cliente, auth, primeira).status_code == 200
        r = self.marcar(cliente, auth, primeira)
        assert r.status_code == 422
        assert "já foi marcada" in r.json()["detail"]

    def test_a_ordem_e_por_SEQUENCIA_nao_por_canal(self, cliente, auth):
        """WhatsApp e e-mail são cadências independentes: marcar a 1 do
        WhatsApp não libera a 2 do e-mail."""
        whats = self.gerar(cliente, auth, "whatsapp")
        email = self.gerar(cliente, auth, "email")
        assert self.marcar(cliente, auth, whats["mensagens"][0]["id"]).status_code == 200
        assert self.marcar(cliente, auth, email["mensagens"][1]["id"]).status_code == 422
        assert self.marcar(cliente, auth, email["mensagens"][0]["id"]).status_code == 200

    def test_mensagem_de_outro_lead_e_404(self, cliente, auth):
        """A checagem de dono impede mexer em mensagem alheia por uma URL que
        parece inocente."""
        seq = self.gerar(cliente, auth)
        outro = Lead(documento="11144477735", nome="OUTRO", uf="PR")
        cliente.db.add(outro)
        cliente.db.commit()
        r = cliente.patch(
            f"/api/leads/{outro.id}/mensagens/{seq['mensagens'][0]['id']}/enviada",
            headers=auth,
        )
        assert r.status_code == 404
        assert cliente.db.query(LeadMessage).filter(
            LeadMessage.status == "enviada"
        ).count() == 0

    def test_mensagem_inexistente_404(self, cliente, auth):
        assert self.marcar(cliente, auth, "nao-existe").status_code == 404

    def test_lead_inexistente_404(self, cliente, auth):
        seq = self.gerar(cliente, auth)
        r = cliente.patch(
            f"/api/leads/999999/mensagens/{seq['mensagens'][0]['id']}/enviada",
            headers=auth,
        )
        assert r.status_code == 404

    def test_sem_token_401(self, cliente):
        assert cliente.patch("/api/leads/1/mensagens/x/enviada").status_code == 401


class TestListarMensagens:
    def test_lead_sem_mensagem_devolve_os_dois_canais_nulos(self, cliente, auth):
        r = cliente.get(f"/api/leads/{id_do(cliente)}/mensagens", headers=auth)
        assert r.status_code == 200
        assert r.json() == {"email": None, "whatsapp": None}

    def test_devolve_agrupado_por_canal_e_ordenado(self, cliente, auth):
        alvo = id_do(cliente)
        for canal in ("email", "whatsapp"):
            cliente.post(f"/api/leads/{alvo}/gerar-abordagem/{canal}", headers=auth)

        corpo = cliente.get(f"/api/leads/{alvo}/mensagens", headers=auth).json()
        assert corpo["email"]["total"] == 2
        assert corpo["whatsapp"]["total"] == 3
        assert [m["ordem"] for m in corpo["whatsapp"]["mensagens"]] == [1, 2, 3]

    def test_devolve_a_sequencia_ATIVA_e_guarda_a_anterior(self, cliente, auth):
        """"Gerar novamente" troca a ativa; a anterior fica no banco, fora da
        resposta — a aba mostra a cadência vigente, não duas concorrentes."""
        alvo = id_do(cliente)
        antiga = cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp", headers=auth).json()
        nova = cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp", headers=auth).json()

        corpo = cliente.get(f"/api/leads/{alvo}/mensagens", headers=auth).json()
        assert corpo["whatsapp"]["grupo_id"] == nova["grupo_id"]
        assert corpo["whatsapp"]["grupo_id"] != antiga["grupo_id"]
        assert corpo["whatsapp"]["total"] == 3  # a ativa, não as 6 do histórico
        assert cliente.db.query(LeadMessage).count() == 6

    def test_um_canal_gerado_nao_inventa_o_outro(self, cliente, auth):
        alvo = id_do(cliente)
        cliente.post(f"/api/leads/{alvo}/gerar-abordagem/email", headers=auth)
        corpo = cliente.get(f"/api/leads/{alvo}/mensagens", headers=auth).json()
        assert corpo["email"] is not None
        assert corpo["whatsapp"] is None

    def test_lead_inexistente_404(self, cliente, auth):
        assert cliente.get("/api/leads/999999/mensagens", headers=auth).status_code == 404


class TestHistoricoLegado:
    """⚠️ Linhas geradas ANTES da Fase 11a (as do Alberto Lemuch real).

    A migration deu a cada uma o próprio ``grupo_id``, ``ordem=1`` e
    ``status='pendente'`` — uma sequência de uma mensagem. Estes testes
    reproduzem esse formato e provam que ele **lê e conta** como qualquer
    outro, sem ramo especial em lugar nenhum.
    """

    def legado(self, cliente, canal: str = "email") -> LeadMessage:
        lead = cliente.db.query(Lead).one()
        # É o backfill da migration, literalmente: grupo_id = id da própria
        # linha, ordem 1, pendente.
        identificador = str(uuid.uuid4())
        mensagem = LeadMessage(
            id=identificador, grupo_id=identificador, ordem=1, status="pendente",
            lead_id=lead.id, canal=canal, conteudo="mensagem antiga",
            assunto="Assunto antigo" if canal == "email" else None,
        )
        cliente.db.add(mensagem)
        cliente.db.commit()
        return mensagem

    def test_mensagem_antiga_le_como_sequencia_de_uma(self, cliente, auth):
        self.legado(cliente)
        corpo = cliente.get(f"/api/leads/{id_do(cliente)}/mensagens", headers=auth).json()
        assert corpo["email"]["total"] == 1
        assert corpo["email"]["mensagens"][0]["ordem"] == 1
        assert corpo["email"]["mensagens"][0]["status"] == "pendente"
        assert corpo["email"]["proxima_ordem"] == 1

    def test_mensagem_antiga_pode_ser_marcada_como_enviada(self, cliente, auth):
        antiga = self.legado(cliente)
        r = cliente.patch(
            f"/api/leads/{id_do(cliente)}/mensagens/{antiga.id}/enviada", headers=auth
        )
        assert r.status_code == 200
        assert r.json()["proxima_ordem"] is None

    def test_cada_mensagem_antiga_conta_como_UMA_geracao(self, cliente, auth):
        """O backfill preserva o valor do limite: duas linhas legadas contam
        2, exatamente como o COUNT(*) contava antes."""
        self.legado(cliente)
        self.legado(cliente)
        corpo = cliente.get(f"/api/leads/{id_do(cliente)}", headers=auth).json()
        assert corpo["geracoes_ia"]["email"] == 2
        assert cliente.post(f"/api/leads/{id_do(cliente)}/gerar-abordagem/email",
                            headers=auth).status_code == 429

    def test_geracao_nova_convive_com_o_legado_e_vira_a_ativa(self, cliente, auth):
        antiga = self.legado(cliente)
        nova = cliente.post(
            f"/api/leads/{id_do(cliente)}/gerar-abordagem/email", headers=auth
        ).json()
        corpo = cliente.get(f"/api/leads/{id_do(cliente)}/mensagens", headers=auth).json()
        assert corpo["email"]["grupo_id"] == nova["grupo_id"]
        assert corpo["email"]["total"] == 2
        assert cliente.db.get(LeadMessage, antiga.id) is not None


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

    def test_a_sequencia_INTEIRA_consome_UMA_geracao(self, cliente, auth):
        """⚠️ Contando linhas, a primeira geração de WhatsApp (3 linhas) já
        estouraria o limite de 2 e o vendedor perderia a cota no 1º clique.
        A regra do limite não mudou; mudou o que uma geração produz."""
        alvo = id_do(cliente)
        cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp", headers=auth)
        corpo = cliente.get(f"/api/leads/{alvo}", headers=auth).json()
        assert corpo["geracoes_ia"]["whatsapp"] == 1
        assert cliente.db.query(LeadMessage).count() == 3

        assert cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp",
                            headers=auth).status_code == 200
        assert cliente.post(f"/api/leads/{alvo}/gerar-abordagem/whatsapp",
                            headers=auth).status_code == 429

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
        assert cliente.db.query(LeadMessage).count() == 4  # 2 sequências de 2

        cliente.post(f"/api/admin/leads/{alvo}/resetar-limite-ia/email", headers=auth_admin)

        # Histórico intacto...
        assert cliente.db.query(LeadMessage).count() == 4
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
