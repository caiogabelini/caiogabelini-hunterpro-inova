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


def sequencia_json(n: int, *, com_assunto: bool = False) -> str:
    """Resposta bem formada com ``n`` mensagens, no formato que os prompts
    pedem."""
    itens = []
    for i in range(1, n + 1):
        item = {"ordem": i, "conteudo": f"mensagem {i}"}
        if com_assunto:
            item["assunto"] = f"Assunto {i}"
        itens.append(item)
    return json.dumps({"mensagens": itens})


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
        cliente, cap = transporte(sequencia_json(3))
        ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert cap["body"]["model"] == "claude-haiku-4-5-20251001"

    def test_headers_e_url_do_contrato_oficial(self):
        cliente, cap = transporte(sequencia_json(3))
        ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)
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
        assert ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente) == []
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
        ia.PROMPT_SEQUENCIA_EMAIL, ia.PROMPT_SEQUENCIA_WHATSAPP, ia.PROMPT_INSIGHTS,
    ])
    def test_proibem_inventar_dado(self, prompt):
        """A regra estrutural que sobreviveu do Minotto — foi ela que evitou
        lá que a IA preenchesse lacuna com plausível."""
        assert "NÃO invente" in prompt

    @pytest.mark.parametrize("prompt", [
        ia.PROMPT_SEQUENCIA_EMAIL, ia.PROMPT_SEQUENCIA_WHATSAPP, ia.PROMPT_INSIGHTS,
    ])
    def test_falam_de_agro_e_nao_de_saude(self, prompt):
        assert "produtores rurais" in prompt or "agronegócio" in prompt
        for fantasma in ("clínica", "consultório", "médic", "PGFN", "RQE"):
            assert fantasma not in prompt

    def test_credito_rural_nao_e_tratado_como_dívida(self):
        assert "nunca como dívida" in ia.REGRAS_DE_TOM

    @pytest.mark.parametrize("prompt", [
        ia.PROMPT_SEQUENCIA_EMAIL, ia.PROMPT_SEQUENCIA_WHATSAPP,
    ])
    def test_pedem_a_sequencia_inteira_de_uma_vez(self, prompt):
        """⚠️ O ponto da Fase 11a. Uma chamada por mensagem custaria 2-3x e
        produziria aberturas parecidas, porque nenhuma veria as outras."""
        assert "SEQUÊNCIA DE" in prompt
        assert "mesma cadência de abordagem" in prompt

    @pytest.mark.parametrize("prompt", [
        ia.PROMPT_SEQUENCIA_EMAIL, ia.PROMPT_SEQUENCIA_WHATSAPP,
    ])
    def test_mandam_o_followup_saber_o_que_veio_antes(self, prompt):
        """Sem isto o follow-up repete a abertura — que é o defeito que a
        sequência existe pra não ter."""
        assert "já sabendo exatamente o que" in prompt
        assert "ÂNGULO DE VALOR DIFERENTE" in prompt
        assert "NÃO repita" in prompt

    def test_whatsapp_pede_3_e_email_pede_2(self):
        assert "3 MENSAGENS" in ia.PROMPT_SEQUENCIA_WHATSAPP
        assert "EXATAMENTE 3 itens" in ia.PROMPT_SEQUENCIA_WHATSAPP
        assert "2 E-MAILS" in ia.PROMPT_SEQUENCIA_EMAIL
        assert "EXATAMENTE 2 itens" in ia.PROMPT_SEQUENCIA_EMAIL

    def test_o_ultimo_toque_alivia_a_pressao_em_vez_de_insistir(self):
        """Regra de negócio, não estilo: a 3ª é saída educada, não cobrança."""
        assert "REDUZ a pressão" in ia.PROMPT_SEQUENCIA_WHATSAPP
        assert "saída educada" in ia.PROMPT_SEQUENCIA_WHATSAPP
        # As táticas de pressão aparecem no prompt como proibição explícita.
        proibicoes = ia.PROMPT_SEQUENCIA_WHATSAPP.split("Proibido:")[1]
        for tatica in ("urgência", "escassez", "última"):
            assert tatica in proibicoes


class TestGerarSequencia:
    def test_whatsapp_devolve_3_mensagens_ordenadas(self):
        cliente, _ = transporte(sequencia_json(3))
        r = ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert [m.ordem for m in r] == [1, 2, 3]
        assert [m.conteudo for m in r] == ["mensagem 1", "mensagem 2", "mensagem 3"]

    def test_email_devolve_2_com_assunto_proprio(self):
        cliente, _ = transporte(sequencia_json(2, com_assunto=True))
        r = ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente)
        assert [m.ordem for m in r] == [1, 2]
        assert [m.assunto for m in r] == ["Assunto 1", "Assunto 2"]

    def test_uma_unica_chamada_paga_para_a_sequencia_inteira(self):
        """⚠️ A economia da Fase 11a. Se alguém trocar isto por um laço de 3
        chamadas, o custo triplica em silêncio."""
        chamadas = []

        def handler(request):
            chamadas.append(json.loads(request.content))
            return httpx.Response(200, json={"content": [
                {"type": "text", "text": sequencia_json(3)}
            ]})

        cliente = httpx.Client(transport=httpx.MockTransport(handler))
        assert len(ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)) == 3
        assert len(chamadas) == 1

    def test_teto_de_tokens_cabe_a_sequencia_inteira(self):
        """1024 (o teto da mensagem avulsa da Fase 10) trunca o JSON de 2
        e-mails; resposta truncada não parseia e vira chamada paga perdida."""
        cliente, cap = transporte(sequencia_json(2, com_assunto=True))
        ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente)
        assert cap["body"]["max_tokens"] == ia.MAX_TOKENS_SEQUENCIA
        assert ia.MAX_TOKENS_SEQUENCIA > ia.MAX_TOKENS_RESPOSTA

    def test_whatsapp_ignora_assunto_que_a_ia_mandar(self):
        """O canal não tem onde mostrar isso — descartar aqui evita que cada
        leitor tenha que lembrar de ignorar."""
        cliente, _ = transporte(sequencia_json(3, com_assunto=True))
        r = ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert all(m.assunto is None for m in r)

    def test_ordem_vem_da_POSICAO_nao_do_rotulo_da_ia(self):
        """Confiar no campo "ordem" que a IA escreve abriria a chance de dois
        "2" ou de um salto — e aí a sequência não teria próxima pendente."""
        cliente, _ = transporte(json.dumps({"mensagens": [
            {"ordem": 7, "conteudo": "a"},
            {"ordem": 7, "conteudo": "b"},
            {"ordem": 99, "conteudo": "c"},
        ]}))
        r = ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert [m.ordem for m in r] == [1, 2, 3]

    def test_sequencia_incompleta_e_descartada_inteira(self):
        """⚠️ Tudo ou nada: uma cadência de 3 gravada com 2 quebraria a
        promessa da tela em silêncio. A cota só é gasta se persistir."""
        cliente, _ = transporte(sequencia_json(2))
        assert ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente) == []

    def test_mensagem_a_mais_tambem_e_descartada(self):
        cliente, _ = transporte(sequencia_json(3))
        assert ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente) == []

    def test_item_sem_conteudo_derruba_a_sequencia(self):
        cliente, _ = transporte(json.dumps({"mensagens": [
            {"ordem": 1, "conteudo": "a"},
            {"ordem": 2, "conteudo": "   "},
            {"ordem": 3, "conteudo": "c"},
        ]}))
        assert ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente) == []

    def test_tolera_json_em_bloco_markdown(self):
        cliente, _ = transporte(f"```json\n{sequencia_json(2, com_assunto=True)}\n```")
        assert len(ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente)) == 2

    def test_tolera_texto_antes_e_depois(self):
        cliente, _ = transporte(f"Claro! {sequencia_json(3)} Espero ajudar.")
        assert len(ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)) == 3

    def test_canal_desconhecido_levanta(self):
        """Erro de programação, não resposta da IA — a rota valida antes."""
        with pytest.raises(ValueError):
            ia.gerar_sequencia_abordagem(LEAD, "instagram")

    @pytest.mark.parametrize("resposta", [
        "", "não é json nenhum", "{}", "[]", '{"mensagens": "não é lista"}',
    ])
    def test_resposta_inutil_vira_lista_vazia_sem_levantar(self, resposta):
        cliente, _ = transporte(resposta)
        assert ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente) == []

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    def test_erro_http_nunca_propaga(self, status):
        """Roda dentro de um handler HTTP: exceção aqui viraria 500 genérico
        em vez do 502 com mensagem que a rota devolve."""
        cliente, _ = transporte("", status=status)
        assert ia.gerar_sequencia_abordagem(LEAD, "email", cliente=cliente) == []

    def test_erro_de_rede_nunca_propaga(self):
        def handler(request):
            raise httpx.ConnectError("sem rede")

        cliente = httpx.Client(transport=httpx.MockTransport(handler))
        assert ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente) == []


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


class TestOrdemNuncaTemBuraco:
    """⚠️ Regressão: a ordem sai da posição entre as mensagens ACEITAS.

    Indexando a lista crua, um item descartado no meio produziria a sequência
    de ordens 1, 2, 4 — e "a próxima pendente" passaria a apontar para uma
    posição que a tela não sabe numerar.
    """

    def test_item_invalido_no_meio_nao_abre_buraco_na_ordem(self):
        cliente, _ = transporte(json.dumps({"mensagens": [
            {"ordem": 1, "conteudo": "a"},
            "isto não é um objeto",
            {"ordem": 2, "conteudo": "b"},
            {"ordem": 3, "conteudo": "c"},
        ]}))
        r = ia.gerar_sequencia_abordagem(LEAD, "whatsapp", cliente=cliente)
        assert [m.ordem for m in r] == [1, 2, 3]
        assert [m.conteudo for m in r] == ["a", "b", "c"]
