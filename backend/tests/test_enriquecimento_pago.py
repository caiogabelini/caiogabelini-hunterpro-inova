"""Módulos pagos portados do Minotto + pipeline completo.

⚠️ **Nenhum teste toca a rede.** Evolution, Firecrawl, Hunter, ZeroBounce e
Anthropic custam dinheiro (ou infraestrutura). A fixture ``autouse`` do
``conftest.py`` bloqueia socket na suíte inteira; aqui os clientes são
injetados como fake, e o fake **levanta** se for chamado quando não devia.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.scoring.pre_selecao import ORIGEM_SICOR, Candidato
from app.services import ai_site, email_enrichment, site_scraping
from app.services import whatsapp as whatsapp_service
from app.workers.busca import enriquecer_selecionados, persistir_leads
from app.workers.enriquecimento import (
    DOMINIOS_SEM_SITE_PROPRIO,
    _dominio_do_site,
    descobrir_site_url,
    LeadEnriquecido,
    dominio_raspavel,
    enriquecer_lead,
    enriquecer_lote_completo,
    prioridade_do_score,
)
from tests.test_api_full import carregar_amostra

CPF_REAL = "00521073960"
MARKDOWN = "Fazenda Boa Vista. Soja e milho. https://instagram.com/fazenda_bv"


class Fake:
    """Cliente HTTP falso. Levanta se for chamado indevidamente."""

    def __init__(self, payload=None, status=200, proibido=False):
        self.payload, self.status, self.proibido = payload, status, proibido
        self.chamadas = 0

    def _r(self):
        self.chamadas += 1
        if self.proibido:
            raise AssertionError("serviço pago chamado quando não devia")
        return httpx.Response(
            self.status, json=self.payload, request=httpx.Request("POST", "http://x")
        )

    def post(self, *a, **k):
        return self._r()

    def get(self, *a, **k):
        return self._r()


def candidato(doc=CPF_REAL, **nicho) -> Candidato:
    base = {"area_ha": 800.0, "valor_financiado": 2_000_000.0, "culturas": ["SOJA"]}
    base.update(nicho)
    return Candidato(
        documento=doc, origem=ORIGEM_SICOR, nome="", uf="PR", municipio=None,
        pontos_parciais=45.0, dados_nicho=base,
    )


RESP_FIRECRAWL = {"success": True, "data": {"markdown": MARKDOWN}}
RESP_EVOLUTION = [{"exists": True, "jid": "5544999998888@s.whatsapp.net"}]
RESP_ZEROBOUNCE = {"status": "valid"}
RESP_IA = {"content": [{"type": "text", "text": '{"ativa": true, "intensidade": 0.8}'}]}


class TestWhatsapp:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("44999998888", "5544999998888"),
            ("4433334444", "554433334444"),
            ("+55 44 99999-8888", "5544999998888"),
            ("5544999998888", "5544999998888"),
            ("123", None),
            ("", None),
            (None, None),
        ],
    )
    def test_formatacao(self, entrada, esperado) -> None:
        assert whatsapp_service.formatar_numero(entrada) == esperado

    def test_ddd_55_do_rio_grande_do_sul_nao_e_codigo_de_pais(self) -> None:
        """A armadilha que o Minotto documentou: `55991234567` é DDD 55 + 9
        dígitos, número nacional — não um número já com código de país."""
        assert whatsapp_service.formatar_numero("55991234567") == "5555991234567"

    def test_consulta_bem_sucedida(self) -> None:
        r = whatsapp_service.validar_whatsapp("44999998888", cliente=Fake(RESP_EVOLUTION))
        assert r.tem_whatsapp and r.jid and r.ok

    def test_numero_sem_whatsapp(self) -> None:
        r = whatsapp_service.validar_whatsapp("4433334444", cliente=Fake([{"exists": False}]))
        assert r.numero_valido and not r.tem_whatsapp

    def test_numero_invalido_nao_gasta_consulta(self) -> None:
        fake = Fake(proibido=True)
        assert not whatsapp_service.validar_whatsapp("123", cliente=fake).numero_valido
        assert fake.chamadas == 0

    def test_sem_config_pula_com_motivo(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "EVOLUTION_URL", "")
        assert "EVOLUTION" in whatsapp_service.validar_whatsapp("44999998888").erro

    @pytest.mark.parametrize("corpo", [[], [None], "texto", {}, [{"outro": 1}]])
    def test_resposta_estranha_nao_levanta(self, corpo) -> None:
        assert not whatsapp_service.validar_whatsapp("44999998888", cliente=Fake(corpo)).tem_whatsapp

    def test_erro_http_vira_erro_no_resultado(self) -> None:
        assert "500" in whatsapp_service.validar_whatsapp("44999998888", cliente=Fake(status=500)).erro


class TestSiteScraping:
    def test_scrape_extrai_instagram_e_whatsapp(self) -> None:
        md = "contato https://wa.me/5544999998888 e https://instagram.com/fazenda_bv"
        r = site_scraping.raspar_site(
            "https://x.com.br", cliente=Fake({"success": True, "data": {"markdown": md}})
        )
        assert r.tem_conteudo
        assert r.instagram == "fazenda_bv"
        assert r.whatsapp == "5544999998888"

    def test_success_false_e_HTTP_200_nao_erro(self) -> None:
        """O Firecrawl reporta falha no corpo, com status 200."""
        r = site_scraping.raspar_site("https://x.com.br", cliente=Fake({"success": False}))
        assert not r.sucesso and "success=false" in r.erro

    def test_instagram_ignora_rota_do_site(self) -> None:
        assert site_scraping.extrair_instagram("instagram.com/reel/XYZ") is None
        assert site_scraping.extrair_instagram("instagram.com/p/XYZ") is None

    def test_whatsapp_ignora_convite_de_grupo(self) -> None:
        assert site_scraping.extrair_whatsapp("wa.me/KabcDEF123") is None

    def test_whatsapp_aceita_os_tres_formatos(self) -> None:
        for texto in (
            "wa.me/5544999998888",
            "api.whatsapp.com/send?phone=5544999998888",
            "web.whatsapp.com/send?phone=5544999998888&text=oi",
        ):
            assert site_scraping.extrair_whatsapp(texto) == "5544999998888"

    @pytest.mark.parametrize(
        ("url", "esperado"),
        [
            ("https://www.Cocamar.com.br/x", "cocamar.com.br"),
            ("coamo.com.br", "coamo.com.br"),
            ("", None),
            (None, None),
        ],
    )
    def test_extrair_dominio(self, url, esperado) -> None:
        assert site_scraping.extrair_dominio(url) == esperado

    def test_sem_chave_pula_sem_chamar(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "FIRECRAWL_API_KEY", "")
        assert "FIRECRAWL_API_KEY" in site_scraping.raspar_site("https://x.com").erro


class TestEmail:
    def test_divide_nome_brasileiro_longo(self) -> None:
        assert email_enrichment.dividir_nome("JOAO DA SILVA SOUZA") == ("JOAO", "SOUZA")

    def test_dominio_de_plataforma_e_bloqueado(self) -> None:
        """O bug real: `eladiosouza@instagram.com` gerado pelo Hunter."""
        assert not email_enrichment.dominio_utilizavel("instagram.com")
        assert not email_enrichment.dominio_utilizavel("linktr.ee")

    def test_provedor_gratuito_NAO_e_bloqueado(self) -> None:
        """Produtor rural usa Gmail como contato real — bloquear perderia lead."""
        assert email_enrichment.dominio_utilizavel("gmail.com")
        assert email_enrichment.dominio_utilizavel("hotmail.com")

    def test_email_conhecido_pula_o_hunter(self) -> None:
        """Corte de custo: a Receita já publica e-mail — não paga descoberta."""
        hunter = Fake(proibido=True)
        r = email_enrichment.enriquecer_email(
            "coamo.com.br", "JOAO SILVA",
            cliente_hunter=hunter, cliente_zerobounce=Fake(RESP_ZEROBOUNCE),
            resolver_mx=lambda d, *a, **k: True, email_conhecido="contato@coamo.com.br",
        )
        assert hunter.chamadas == 0
        assert r.origem == "fonte_gratuita" and r.aprovado

    def test_sem_mx_nao_gasta_zerobounce(self) -> None:
        zb = Fake(proibido=True)
        r = email_enrichment.enriquecer_email(
            "coamo.com.br", None, cliente_zerobounce=zb,
            resolver_mx=lambda d, *a, **k: False, email_conhecido="x@coamo.com.br",
        )
        assert zb.chamadas == 0 and not r.mx_valido and "MX" in r.erro

    def test_hunter_sem_achado_nao_gasta_zerobounce(self) -> None:
        zb = Fake(proibido=True)
        r = email_enrichment.enriquecer_email(
            "coamo.com.br", "JOAO SILVA",
            cliente_hunter=Fake({"data": {"email": None}}), cliente_zerobounce=zb,
        )
        assert zb.chamadas == 0 and not r.ok

    @pytest.mark.parametrize(
        ("status", "aprovado"),
        [("valid", True), ("catch-all", True), ("invalid", False),
         ("do_not_mail", False), ("abuse", False)],
    )
    def test_status_do_zerobounce(self, status, aprovado) -> None:
        r = email_enrichment.validar_zerobounce("x@y.com", cliente=Fake({"status": status}))
        assert r.aprovado is aprovado

    def test_fallback_domain_search_desligado_por_padrao(self) -> None:
        """Ligar DOBRA o consumo de crédito do Hunter (§5)."""
        assert settings.HUNTER_DOMAIN_SEARCH_FALLBACK is False
        assert "dobra o consumo" in email_enrichment.buscar_email_dominio("coamo.com.br").erro


class TestAnaliseSite:
    def test_json_puro(self) -> None:
        r = ai_site.analisar_site(MARKDOWN, cliente=Fake(RESP_IA))
        assert r.ok and r.ativa and r.intensidade == pytest.approx(0.8)

    def test_json_embrulhado_em_markdown(self) -> None:
        corpo = {"content": [{"type": "text", "text": '```json\n{"intensidade": 0.4}\n```'}]}
        assert ai_site.analisar_site(MARKDOWN, cliente=Fake(corpo)).intensidade == pytest.approx(0.4)

    def test_resposta_nao_json_nao_levanta(self) -> None:
        corpo = {"content": [{"type": "text", "text": "não consegui avaliar"}]}
        assert "JSON" in ai_site.analisar_site(MARKDOWN, cliente=Fake(corpo)).erro

    def test_intensidade_e_presa_entre_0_e_1(self) -> None:
        for bruto, esperado in ((5, 1.0), (-3, 0.0), ("x", 0.0)):
            corpo = {"content": [{"type": "text", "text": f'{{"intensidade": {bruto!r}}}'}]}
            assert ai_site.analisar_site(MARKDOWN, cliente=Fake(corpo)).intensidade == esperado

    def test_sem_conteudo_nao_gasta_chamada(self) -> None:
        fake = Fake(proibido=True)
        assert "sem conteúdo" in ai_site.analisar_site("", cliente=fake).erro
        assert fake.chamadas == 0

    def test_modelo_e_o_snapshot_com_data(self) -> None:
        """O snapshot pinado é o COM data, igual ao Minotto em produção."""
        assert settings.ANTHROPIC_MODEL == "claude-haiku-4-5-20251001"


class TestDominioRaspavel:
    def test_provedor_gratuito_nao_vira_site(self) -> None:
        """Não existe site em gmail.com — e-mail real, domínio não raspável."""
        assert not dominio_raspavel("gmail.com")
        assert "hotmail.com" in DOMINIOS_SEM_SITE_PROPRIO

    def test_dominio_proprio_e_raspavel(self) -> None:
        assert dominio_raspavel("cocamar.com.br")

    def test_plataforma_tambem_nao(self) -> None:
        assert not dominio_raspavel("instagram.com")


class TestPipelineCompleto:
    def _clientes(self, **over):
        base = dict(
            cliente_api_full=Fake(carregar_amostra(CPF_REAL)),
            cliente_firecrawl=Fake(RESP_FIRECRAWL),
            cliente_evolution=Fake(RESP_EVOLUTION),
            cliente_hunter=Fake({"data": {"email": "j@coamo.com.br", "score": 90}}),
            cliente_zerobounce=Fake(RESP_ZEROBOUNCE),
            cliente_ia=Fake(RESP_IA),
            resolver_mx=lambda d, *a, **k: True,
        )
        base.update(over)
        return base

    def test_nenhum_lead_tem_site_hoje(self) -> None:
        """A etapa de site está estruturalmente parada — e isso é correto.

        Até 26/08/2026 o pipeline derivava o site do domínio do e-mail. Um
        teste real mostrou o estrago: e-mail ``@turbopro.com.br`` virou "site
        do produtor" e a presença digital de terceiro foi pro dossiê dele.
        Sem Google Places, a resposta honesta é pular com motivo.
        """
        firecrawl = Fake(proibido=True)
        r = enriquecer_lead(candidato(), **self._clientes(cliente_firecrawl=firecrawl))
        assert firecrawl.chamadas == 0, "não pode gastar Firecrawl sem fonte de site"
        assert r.site_url == ""
        assert r.presenca_digital == 0.0
        pulada = next(e for e in r.etapas_puladas if e["etapa"] == "enrich_site_firecrawl")
        assert "sem fonte confiável de site" in pulada["motivo"]

    def test_email_corporativo_NAO_vira_mais_site(self) -> None:
        """O caso real que motivou a remoção, travado como teste.

        Um lead cujo e-mail é ``@turbopro.com.br`` não pode fazer o pipeline
        concluir que ``https://turbopro.com.br`` é o site dele.
        """
        ia = Fake(proibido=True)
        firecrawl = Fake(proibido=True)
        r = enriquecer_lead(
            candidato(),
            **self._clientes(cliente_firecrawl=firecrawl, cliente_ia=ia),
        )
        assert r.site_url == ""
        assert firecrawl.chamadas == 0 and ia.chamadas == 0

    def test_descoberta_de_site_nao_tem_fonte_hoje(self) -> None:
        url, motivo = descobrir_site_url(candidato())
        assert url is None
        assert "Google Places" in motivo

    def test_dominio_so_sai_de_site_confirmado_nunca_de_email(self) -> None:
        """A direção correta é site → domínio, não e-mail → site."""
        assert _dominio_do_site("https://cocamar.com.br") == "cocamar.com.br"
        assert _dominio_do_site(None) is None
        assert _dominio_do_site("https://gmail.com") is None, "provedor não é site"

    def test_whatsapp_roda_mesmo_sem_site(self) -> None:
        """WhatsApp é o canal principal e não depende de site nenhum."""
        r = enriquecer_lead(candidato(), **self._clientes())
        assert r.tem_whatsapp and r.whatsapp_numero.startswith("55")

    def test_falha_do_firecrawl_nao_derruba_o_whatsapp(self) -> None:
        """A decisão de resiliência mais importante do enriquecimento (§6)."""
        class Explode(Fake):
            def post(self, *a, **k):
                raise RuntimeError("timeout do Firecrawl")

        r = enriquecer_lead(
            candidato(),
            **self._clientes(cliente_firecrawl=Explode()),
        )
        assert r.tem_whatsapp, "o WhatsApp tem que sobreviver à queda do site"

    def test_score_final_usa_o_motor_calibrado(self) -> None:
        r = enriquecer_lead(candidato(), **self._clientes())
        assert r.score is not None and 0 <= r.score <= 100
        assert r.prioridade in ("ALTA", "MEDIA", "BAIXA")

    def test_decisor_soma_os_20_pontos_no_score(self) -> None:
        com = enriquecer_lead(candidato(), **self._clientes())
        sem = enriquecer_lead(
            candidato(), **self._clientes(cliente_api_full=Fake(status=500))
        )
        assert com.score > sem.score

    def test_etapas_puladas_chegam_ao_resultado(self) -> None:
        r = enriquecer_lead(candidato(), **self._clientes())
        assert r.etapas_puladas, "etapa pulada tem que chegar à tela (§6)"

    def test_lote_isola_falha_por_lead(self) -> None:
        leads = enriquecer_lote_completo(
            [candidato(), candidato("00628195931")], **self._clientes()
        )
        assert len(leads) == 2

    def test_stub_apenas_decisor_nao_chama_os_pagos_novos(self) -> None:
        r = enriquecer_selecionados(
            [candidato()],
            apenas_decisor=True,
            cliente_api_full=Fake(carregar_amostra(CPF_REAL)),
        )
        assert len(r) == 1 and isinstance(r[0], LeadEnriquecido)
        assert r[0].decisor_identificavel and not r[0].tem_whatsapp

    def test_sinais_para_score_usam_False_nao_None(self) -> None:
        """False = medimos e não achamos. None seria "não medimos" (§6)."""
        r = enriquecer_lead(candidato(), **self._clientes())
        assert r.sinais_para_score["email_validado"] in (True, False)
        assert r.sinais_para_score["whatsapp_ativo"] in (True, False)


class TestPersistencia:
    def test_grava_com_score_e_prioridade(self, db) -> None:
        from app.models import Lead

        leads = enriquecer_lote_completo(
            [candidato()],
            cliente_api_full=Fake(carregar_amostra(CPF_REAL)),
            cliente_evolution=Fake(RESP_EVOLUTION),
            resolver_mx=lambda d, *a, **k: True,
        )
        assert persistir_leads(db, leads) == 1
        gravado = db.query(Lead).one()
        assert gravado.documento == CPF_REAL
        assert gravado.score is not None and gravado.prioridade is not None
        assert gravado.etapas_puladas is not None

    def test_upsert_nao_duplica_documento(self, db) -> None:
        from app.models import Lead

        leads = enriquecer_lote_completo(
            [candidato()], cliente_api_full=Fake(carregar_amostra(CPF_REAL))
        )
        persistir_leads(db, leads)
        persistir_leads(db, leads)
        assert db.query(Lead).filter(Lead.documento == CPF_REAL).count() == 1


class TestPrioridade:
    @pytest.mark.parametrize(
        ("score", "esperado"),
        [(100, "ALTA"), (70, "ALTA"), (69, "MEDIA"), (40, "MEDIA"), (39, "BAIXA"),
         (0, "BAIXA"), (None, None)],
    )
    def test_faixas(self, score, esperado) -> None:
        assert prioridade_do_score(score) == esperado


class TestSelecaoDoScriptDeTesteReal:
    """O teste que teria evitado o crédito gasto em 26/08/2026.

    O script manual tinha uma guarda de composição (``if cnpjs and quantos
    >= 2``) que fazia ``--leads 1`` **nunca** trazer o CNPJ — mesmo sendo ele
    o primeiro do ranking. Como CNPJ vai pra BrasilAPI (gratuita) e CPF vai
    pra API Full (paga), o comando recomendado pra "testar barato" era
    exatamente o que garantia o caminho pago.

    Estes testes rodam contra lista mock, sem tocar em rede nem em arquivo.
    """

    RANKING = ["CNPJ_784", "CPF_055", "CPF_060", "CPF_066"]

    @staticmethod
    def _selecionar(ranking, quantos):
        import sys
        from pathlib import Path

        raiz = Path(__file__).resolve().parent.parent
        if str(raiz / "scripts") not in sys.path:
            sys.path.insert(0, str(raiz / "scripts"))
        from teste_real_enriquecimento import selecionar_do_ranking

        return selecionar_do_ranking(ranking, quantos)

    def test_leads_1_traz_o_primeiro_do_ranking_mesmo_sendo_cnpj(self) -> None:
        """O bug exato: com 1 vaga, o CNPJ do topo era descartado."""
        assert self._selecionar(self.RANKING, 1) == ["CNPJ_784"]

    def test_leads_2(self) -> None:
        assert self._selecionar(self.RANKING, 2) == ["CNPJ_784", "CPF_055"]

    def test_leads_3(self) -> None:
        assert self._selecionar(self.RANKING, 3) == ["CNPJ_784", "CPF_055", "CPF_060"]

    def test_respeita_a_ordem_do_ranking_sem_reordenar_por_tipo(self) -> None:
        ranking = ["CPF_a", "CNPJ_b", "CPF_c"]
        assert self._selecionar(ranking, 2) == ["CPF_a", "CNPJ_b"]

    def test_pedir_mais_que_o_disponivel_nao_estoura(self) -> None:
        assert self._selecionar(["CNPJ_784"], 5) == ["CNPJ_784"]

    def test_ranking_vazio(self) -> None:
        assert self._selecionar([], 3) == []
