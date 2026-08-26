"""Geração de texto por IA — prompts, parsing e defensividade.

## ⚠️ Nenhuma chamada real à Anthropic

Todo teste injeta um ``httpx.Client`` com ``MockTransport``. A fixture
``sem_rede`` (autouse) bloqueia socket na suíte inteira como segunda camada:
se algum caminho escapar do dublê e tentar rede de verdade, vira
``AssertionError``, não uma chamada paga.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import ai_enrichment as ia

LEAD = {
    "nome": "ALBERTO LEMUCH FILHO",
    "decisor": "ALBERTO LEMUCH FILHO",
    "fonte_decisor": "api_full",
    "municipio": "GUARAPUAVA",
    "uf": "PR",
    "area_ha": 110.99,
    "culturas": ["SOJA", "MILHO"],
    "valor_financiado": 531009.1,
    "anos_credito": [2025, 2026],
    "score": 95,
    "prioridade": "ALTA",
    "whatsapp_ativo": True,
    "telefone": "5542999640915",
    "email": "x@y.com",
    "email_status": "valid",
}


def transporte(texto: str, status: int = 200):
    """Responde no formato da Messages API. Captura o corpo enviado."""
    capturado: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["headers"] = dict(request.headers)
        capturado["body"] = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": texto}]})

    return httpx.Client(transport=httpx.MockTransport(handler)), capturado


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    """A guarda de configuração barra tudo sem chave — os testes precisam
    passar dela pra exercitar o resto."""
    monkeypatch.setattr(ia.settings, "ANTHROPIC_API_KEY", "sk-teste")


class TestContratoHttp:
    def test_usa_o_modelo_com_sufixo_de_data(self):
        """⚠️ O sufixo já foi removido por engano nesta base e revertido. O
        ID correto e ativo na API é `claude-haiku-4-5-20251001`."""
        cliente, cap = transporte("oi")
        ia.gerar_mensagem_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert cap["body"]["model"] == "claude-haiku-4-5-20251001"

    def test_headers_e_url_do_contrato_oficial(self):
        cliente, cap = transporte("oi")
        ia.gerar_mensagem_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert cap["url"] == "https://api.anthropic.com/v1/messages"
        assert cap["headers"]["anthropic-version"] == "2023-06-01"
        assert cap["headers"]["x-api-key"] == "sk-teste"

    def test_sem_chave_nao_chama_a_api(self, monkeypatch):
        monkeypatch.setattr(ia.settings, "ANTHROPIC_API_KEY", "")
        chamou = []

        def handler(request):  # pragma: no cover - não deve rodar
            chamou.append(1)
            return httpx.Response(200, json={})

        cliente = httpx.Client(transport=httpx.MockTransport(handler))
        assert ia.gerar_mensagem_abordagem(LEAD, "email", cliente=cliente).conteudo == ""
        assert ia.gerar_insights_estrategicos(LEAD, cliente=cliente) == {}
        assert chamou == []


class TestContextoDoLead:
    def test_abordagem_traz_os_sinais_do_agro(self):
        contexto = ia.montar_contexto_abordagem(LEAD)
        assert "ALBERTO LEMUCH FILHO" in contexto
        assert "110.99 hectares" in contexto
        assert "SOJA, MILHO" in contexto
        assert "R$ 531.009,10" in contexto
        assert "2 safras" in contexto

    def test_abordagem_NAO_traz_sinal_do_nicho_de_saude(self):
        """Os prompts foram reescritos, não traduzidos — nada de PGFN/RQE."""
        contexto = ia.montar_contexto_abordagem(LEAD)
        for fantasma in ("PGFN", "RQE", "CNES", "dívida ativa", "convênio"):
            assert fantasma not in contexto

    def test_abordagem_e_menor_que_insights(self):
        """De propósito: quem escreve 2 frases de WhatsApp não deve receber o
        breakdown inteiro do score."""
        assert len(ia.montar_contexto_abordagem(LEAD)) < len(
            ia.montar_contexto_insights(LEAD)
        )

    def test_insights_diz_o_que_FALTA_nao_so_o_que_tem(self):
        magro = {"nome": "PRODUTOR X", "area_ha": 200.0}
        contexto = ia.montar_contexto_insights(magro)
        assert "Decisor NÃO identificado" in contexto
        assert "Nenhum telefone conhecido" in contexto
        assert "Nenhum e-mail conhecido" in contexto

    def test_insights_omite_criterios_de_peso_zero(self):
        contexto = ia.montar_contexto_insights({
            **LEAD,
            "score_detalhes": {"breakdown": [
                {"label": "Tamanho da propriedade rural", "weight": 30, "points": 30},
                {"label": "Habilitação RADAR", "weight": 0, "points": 0},
            ]},
        })
        assert "Tamanho da propriedade rural" in contexto
        assert "RADAR" not in contexto

    def test_lead_sem_nada_nao_quebra(self):
        assert ia.montar_contexto_abordagem({})
        assert ia.montar_contexto_insights({})

    def test_um_ano_so_nao_vira_recorrente(self):
        contexto = ia.montar_contexto_abordagem({**LEAD, "anos_credito": [2026]})
        assert "recorrente" not in contexto
        assert "safra 2026" in contexto


class TestPromptsTemTomCerto:
    @pytest.mark.parametrize("prompt", [
        ia.PROMPT_ABORDAGEM_EMAIL, ia.PROMPT_ABORDAGEM_WHATSAPP, ia.PROMPT_INSIGHTS,
    ])
    def test_proibem_inventar_dado(self, prompt):
        """A regra estrutural que sobreviveu do Minotto — foi ela que evitou
        lá que a IA preenchesse lacuna com plausível."""
        assert "NÃO invente" in prompt

    @pytest.mark.parametrize("prompt", [
        ia.PROMPT_ABORDAGEM_EMAIL, ia.PROMPT_ABORDAGEM_WHATSAPP, ia.PROMPT_INSIGHTS,
    ])
    def test_falam_de_agro_e_nao_de_saude(self, prompt):
        assert "produtores rurais" in prompt or "agronegócio" in prompt
        for fantasma in ("clínica", "consultório", "médic", "PGFN", "RQE"):
            assert fantasma not in prompt

    def test_credito_rural_nao_e_tratado_como_dívida(self):
        assert "nunca como dívida" in ia.REGRAS_DE_TOM


class TestGerarMensagem:
    def test_whatsapp_devolve_texto_puro(self):
        cliente, _ = transporte("  Olá Alberto, tudo bem?  ")
        r = ia.gerar_mensagem_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert r.conteudo == "Olá Alberto, tudo bem?"
        assert r.assunto is None

    def test_email_parseia_assunto_e_corpo(self):
        cliente, _ = transporte(json.dumps({"assunto": "Planejamento da safra", "corpo": "Corpo aqui."}))
        r = ia.gerar_mensagem_abordagem(LEAD, "email", cliente=cliente)
        assert r.assunto == "Planejamento da safra"
        assert r.conteudo == "Corpo aqui."

    def test_email_tolera_json_em_bloco_markdown(self):
        cliente, _ = transporte('```json\n{"assunto": "A", "corpo": "B"}\n```')
        assert ia.gerar_mensagem_abordagem(LEAD, "email", cliente=cliente).conteudo == "B"

    def test_email_tolera_texto_antes_e_depois(self):
        cliente, _ = transporte('Claro! {"assunto": "A", "corpo": "B"} Espero ajudar.')
        assert ia.gerar_mensagem_abordagem(LEAD, "email", cliente=cliente).conteudo == "B"

    def test_canal_desconhecido_levanta(self):
        """Erro de programação, não resposta da IA — a rota valida antes."""
        with pytest.raises(ValueError):
            ia.gerar_mensagem_abordagem(LEAD, "instagram")

    @pytest.mark.parametrize("resposta", ["", "não é json nenhum", "{}", "[]"])
    def test_resposta_inutil_vira_conteudo_vazio_sem_levantar(self, resposta):
        cliente, _ = transporte(resposta)
        assert ia.gerar_mensagem_abordagem(LEAD, "email", cliente=cliente).conteudo == ""

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    def test_erro_http_nunca_propaga(self, status):
        """Roda dentro de um handler HTTP: exceção aqui viraria 500 genérico
        em vez do 502 com mensagem que a rota devolve."""
        cliente, _ = transporte("", status=status)
        assert ia.gerar_mensagem_abordagem(LEAD, "email", cliente=cliente).conteudo == ""

    def test_erro_de_rede_nunca_propaga(self):
        def handler(request):
            raise httpx.ConnectError("sem rede")

        cliente = httpx.Client(transport=httpx.MockTransport(handler))
        assert ia.gerar_mensagem_abordagem(LEAD, "whatsapp", cliente=cliente).conteudo == ""


class TestGerarInsights:
    RESPOSTA = {
        "resumo_estrategico": "Produtor recorrente com boa área.",
        "potencial_oportunidade": "Alto",
        "recomendacao_abordagem": ["a", "b", "c", "d"],
        "estrategia_comunicacao": "WhatsApp primeiro.",
        "cta_sugerido": "Podemos conversar?",
    }

    def test_parseia_os_cinco_campos(self):
        cliente, _ = transporte(json.dumps(self.RESPOSTA))
        r = ia.gerar_insights_estrategicos(LEAD, cliente=cliente)
        assert r["resumo_estrategico"] == "Produtor recorrente com boa área."
        assert r["estrategia_comunicacao"] == "WhatsApp primeiro."
        assert r["cta_sugerido"] == "Podemos conversar?"

    def test_normaliza_potencial_sem_forcar_enum(self):
        """Valor fora dos 3 esperados só cai num badge neutro no frontend —
        melhor que inventar 'baixo' quando a IA respondeu outra coisa."""
        cliente, _ = transporte(json.dumps({**self.RESPOSTA, "potencial_oportunidade": "  MUITO ALTO "}))
        assert ia.gerar_insights_estrategicos(LEAD, cliente=cliente)["potencial_oportunidade"] == "muito alto"

    def test_recomendacoes_limitadas_a_tres(self):
        cliente, _ = transporte(json.dumps(self.RESPOSTA))
        assert len(ia.gerar_insights_estrategicos(LEAD, cliente=cliente)["recomendacao_abordagem"]) == 3

    def test_json_valido_mas_vazio_conta_como_falha(self):
        """Sem resumo não há análise — persistir isso encheria a aba de campos
        em branco sem explicar por quê."""
        cliente, _ = transporte(json.dumps({"resumo_estrategico": "  "}))
        assert ia.gerar_insights_estrategicos(LEAD, cliente=cliente) == {}

    @pytest.mark.parametrize("status", [401, 500])
    def test_erro_http_vira_dict_vazio(self, status):
        cliente, _ = transporte("", status=status)
        assert ia.gerar_insights_estrategicos(LEAD, cliente=cliente) == {}

    def test_recomendacao_de_tipo_errado_vira_lista_vazia(self):
        cliente, _ = transporte(json.dumps({**self.RESPOSTA, "recomendacao_abordagem": "não é lista"}))
        assert ia.gerar_insights_estrategicos(LEAD, cliente=cliente)["recomendacao_abordagem"] == []
