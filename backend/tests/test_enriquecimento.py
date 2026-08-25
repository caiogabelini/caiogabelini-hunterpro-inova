"""Enriquecimento do decisor: CPF via API Full, CNPJ via BrasilAPI.

Nenhum teste toca a rede — os clientes são injetados como fake (o padrão de
cliente injetável da §3 existe exatamente pra isso). O fake **levanta** se
for chamado para o documento errado, então trocar as fontes por engano
quebra o teste em vez de gastar dinheiro na fonte paga.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.core.segredos import erro_redigido, redigir
from app.scoring.pre_selecao import ORIGEM_RFB, ORIGEM_SICOR, Candidato
from app.services import brasil_api
from app.services.brasil_api import Socio, identificar_decisor, interpretar_resposta
from app.workers.busca import enriquecer_selecionados
from app.workers.enriquecimento import (
    LeadEnriquecido,
    enriquecer_decisor,
    enriquecer_lote,
)
from tests.test_api_full import DIR_AMOSTRA, ESPERADO, carregar_amostra

CPF_REAL = "00521073960"
CNPJ = "11222333000181"


def candidato(doc: str, origem: str = ORIGEM_SICOR) -> Candidato:
    return Candidato(
        documento=doc, origem=origem, nome="", uf="PR", municipio=None,
        pontos_parciais=40.0,
    )


class ClienteFalso:
    """Devolve uma resposta gravada. Levanta se for chamado indevidamente."""

    def __init__(self, payload=None, status=200, proibido=False):
        self.payload, self.status, self.proibido = payload, status, proibido
        self.chamadas = 0

    def _resp(self):
        self.chamadas += 1
        if self.proibido:
            raise AssertionError("fonte errada foi chamada — isso custaria dinheiro")
        return httpx.Response(
            self.status, json=self.payload, request=httpx.Request("POST", "http://x")
        )

    def post(self, *a, **k):
        return self._resp()

    def get(self, *a, **k):
        return self._resp()


@pytest.fixture(scope="session")
def payload_cpf() -> dict:
    return carregar_amostra(CPF_REAL)


PAYLOAD_CNPJ = {
    "cnpj": CNPJ,
    "razao_social": "AGROPECUARIA EXEMPLO LTDA",
    "nome_fantasia": "AGRO EXEMPLO",
    "municipio": "CASCAVEL",
    "uf": "PR",
    "email": "Contato@Exemplo.com.BR",
    "qsa": [
        {"nome_socio": "MARIA DAS DORES", "qualificacao_socio": "49-Sócio-Administrador"},
        {"nome_socio": "JOAO SEM PODER", "qualificacao_socio": "22-Sócio"},
    ],
}


class TestEscolhaDaFonte:
    def test_cpf_vai_pra_api_full_e_NAO_pra_brasil_api(self, payload_cpf) -> None:
        r = enriquecer_decisor(
            candidato(CPF_REAL),
            cliente_api_full=ClienteFalso(payload_cpf),
            cliente_brasil_api=ClienteFalso(proibido=True),
        )
        assert r.decisor_identificavel
        assert r.fonte_decisor == "api_full"

    def test_cnpj_vai_pra_brasil_api_e_NAO_pra_fonte_paga(self) -> None:
        """Mandar CNPJ pra API Full gastaria crédito à toa."""
        r = enriquecer_decisor(
            candidato(CNPJ, ORIGEM_RFB),
            cliente_api_full=ClienteFalso(proibido=True),
            cliente_brasil_api=ClienteFalso(PAYLOAD_CNPJ),
        )
        assert r.decisor_identificavel
        assert r.fonte_decisor == "brasil_api"

    def test_documento_invalido_nao_chama_fonte_nenhuma(self) -> None:
        r = enriquecer_decisor(
            candidato("123"),
            cliente_api_full=ClienteFalso(proibido=True),
            cliente_brasil_api=ClienteFalso(proibido=True),
        )
        assert not r.decisor_identificavel
        assert "inválido" in r.etapas_puladas[0]["motivo"]

    def test_uma_chamada_por_documento(self, payload_cpf) -> None:
        cliente = ClienteFalso(payload_cpf)
        enriquecer_decisor(candidato(CPF_REAL), cliente_api_full=cliente)
        assert cliente.chamadas == 1


class TestDecisorCPF:
    def test_a_pessoa_fisica_e_o_proprio_decisor(self, payload_cpf) -> None:
        """Não há quadro societário a interpretar — o produtor é o decisor."""
        r = enriquecer_decisor(
            candidato(CPF_REAL), cliente_api_full=ClienteFalso(payload_cpf)
        )
        assert r.nome == r.decisor
        assert r.nome

    def test_telefone_sai_em_e164(self, payload_cpf) -> None:
        r = enriquecer_decisor(
            candidato(CPF_REAL), cliente_api_full=ClienteFalso(payload_cpf)
        )
        assert r.telefones and all(t.startswith("+55") for t in r.telefones)

    @pytest.mark.parametrize("cpf", sorted(ESPERADO))
    def test_os_4_cpfs_reais_resolvem_decisor(self, cpf: str) -> None:
        r = enriquecer_decisor(
            candidato(cpf), cliente_api_full=ClienteFalso(carregar_amostra(cpf))
        )
        assert r.decisor_identificavel, cpf
        assert r.sinais_para_score["decisor_identificavel"] == r.decisor

    def test_erro_da_fonte_vira_etapa_pulada_e_nao_excecao(self) -> None:
        r = enriquecer_decisor(
            candidato(CPF_REAL), cliente_api_full=ClienteFalso(status=500)
        )
        assert not r.decisor_identificavel
        assert r.etapas_puladas[0]["etapa"] == "enrich_decisor"
        assert "500" in r.etapas_puladas[0]["motivo"]


class TestDecisorCNPJ:
    def test_escolhe_o_socio_administrador(self) -> None:
        r = enriquecer_decisor(
            candidato(CNPJ, ORIGEM_RFB), cliente_brasil_api=ClienteFalso(PAYLOAD_CNPJ)
        )
        assert r.decisor == "MARIA DAS DORES"
        assert r.nome == "AGROPECUARIA EXEMPLO LTDA"

    def test_sem_palavra_chave_cai_no_primeiro_socio(self) -> None:
        assert identificar_decisor(
            (Socio("PRIMEIRO", "22-Sócio"), Socio("SEGUNDO", "22-Sócio"))
        ).nome == "PRIMEIRO"

    def test_sem_socio_nenhum_devolve_none(self) -> None:
        assert identificar_decisor(()) is None

    def test_cnpj_sem_qsa_vira_etapa_pulada(self) -> None:
        r = enriquecer_decisor(
            candidato(CNPJ, ORIGEM_RFB),
            cliente_brasil_api=ClienteFalso({**PAYLOAD_CNPJ, "qsa": []}),
        )
        assert not r.decisor_identificavel
        assert "quadro societário" in r.etapas_puladas[0]["motivo"]

    def test_404_vira_etapa_pulada(self) -> None:
        r = enriquecer_decisor(
            candidato(CNPJ, ORIGEM_RFB), cliente_brasil_api=ClienteFalso(status=404)
        )
        assert "não encontrado" in r.etapas_puladas[0]["motivo"]

    def test_email_e_normalizado(self) -> None:
        assert interpretar_resposta(PAYLOAD_CNPJ).email == "contato@exemplo.com.br"

    def test_socio_sem_nome_e_descartado(self) -> None:
        r = interpretar_resposta(
            {"razao_social": "X", "qsa": [{"nome_socio": "", "qualificacao_socio": "Sócio"}]}
        )
        assert r.socios == ()

    @pytest.mark.parametrize("bruto", [None, [], "texto", {"qsa": "nao e lista"}])
    def test_resposta_estranha_nao_levanta(self, bruto) -> None:
        assert interpretar_resposta(bruto) is not None


class TestLoteIsolaFalhas:
    """§6: uma etapa que falha não pode derrubar o lote inteiro."""

    def test_um_lead_que_falha_nao_derruba_os_outros(self, payload_cpf) -> None:
        class SoFalhaNoSegundo(ClienteFalso):
            def post(self, *a, **k):
                self.chamadas += 1
                if self.chamadas == 2:
                    raise RuntimeError("timeout simulado")
                return httpx.Response(
                    200, json=payload_cpf, request=httpx.Request("POST", "http://x")
                )

        candidatos = [candidato(c) for c in sorted(ESPERADO)]
        r = enriquecer_lote(candidatos, cliente_api_full=SoFalhaNoSegundo())
        assert len(r) == len(candidatos), "todo lead volta, mesmo o que falhou"
        assert sum(1 for x in r if x.decisor_identificavel) == len(candidatos) - 1
        falho = [x for x in r if not x.decisor_identificavel][0]
        assert falho.etapas_puladas

    def test_lote_vazio(self) -> None:
        assert enriquecer_lote([]) == []

    def test_o_stub_de_busca_delega_pra_ca(self, payload_cpf) -> None:
        r = enriquecer_selecionados(
            [candidato(CPF_REAL)], cliente_api_full=ClienteFalso(payload_cpf)
        )
        assert len(r) == 1 and isinstance(r[0], LeadEnriquecido)
        assert r[0].decisor_identificavel


class TestSegredoNuncaVaza:
    def test_token_em_atribuicao_e_redigido(self) -> None:
        assert "s3cr3t" not in redigir("API_FULL_TOKEN=s3cr3t")

    def test_bearer_e_redigido(self) -> None:
        assert "abc123" not in redigir("Authorization: Bearer abc123")

    def test_senha_de_connection_string_e_redigida(self) -> None:
        assert "minhasenha" not in redigir("postgresql://u:minhasenha@h/db")

    def test_excecao_com_token_sai_redigida(self) -> None:
        try:
            raise RuntimeError("401 em Bearer tok_live_zzz")
        except RuntimeError as exc:
            assert "tok_live_zzz" not in erro_redigido(exc)

    def test_dado_nao_sensivel_sobrevive(self) -> None:
        assert redigir("cpf=00521073960") == "cpf=00521073960"


class TestSinaisParaOScore:
    def test_decisor_encontrado_vira_sinal_verdadeiro(self, payload_cpf) -> None:
        r = enriquecer_decisor(
            candidato(CPF_REAL), cliente_api_full=ClienteFalso(payload_cpf)
        )
        assert r.sinais_para_score["decisor_identificavel"]

    def test_decisor_ausente_vira_sinal_falso_nao_none(self) -> None:
        """False = "medimos e não achou". None seria "não medimos" (§6)."""
        r = enriquecer_decisor(
            candidato(CPF_REAL), cliente_api_full=ClienteFalso(status=500)
        )
        assert r.sinais_para_score["decisor_identificavel"] is False

    def test_o_sinal_completa_os_gratuitos_da_pre_selecao(self, payload_cpf) -> None:
        """Junta com sinais_gratuitos_sicor e vira o dict do score final."""
        from app.scoring.compute_lead_score import calcular_score

        r = enriquecer_decisor(
            candidato(CPF_REAL), cliente_api_full=ClienteFalso(payload_cpf)
        )
        sinais = {
            "tamanho_propriedade": 800.0,
            "valor_financiado": 2_000_000.0,
            "semente_sicor_cultura": True,
            **r.sinais_para_score,
        }
        resultado = calcular_score(sinais)
        assert "decisor_identificavel" not in resultado.ausentes
        assert resultado.por_key("decisor_identificavel").pontos == 20
