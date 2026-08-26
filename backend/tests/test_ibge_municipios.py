"""Resolução de município a partir do CD_CAR.

⚠️ **Nenhuma chamada real ao IBGE.** Todo teste injeta um ``httpx.Client``
com ``MockTransport`` que **conta requisições** — é assim que se prova que a
chamada é por UF e não por lead. A fixture ``sem_rede`` (autouse) é a segunda
camada.
"""

from __future__ import annotations

import httpx
import pytest

from app.scoring.pre_selecao import ORIGEM_SICOR, Candidato
from app.services.ibge_municipios import (
    CacheMunicipios,
    codigo_ibge_do_car,
    municipios_dos_cars,
)
from app.workers.busca import resolver_municipios

#: CARs reais dos 4 leads já persistidos (hash preservado — é público).
CAR_TURVO = "PR4127965BFAF88CF795248C3A41EFA4DDA6A60BE"
CAR_ASSIS = "PR4102000CC39EB05D65B479FBBCB3BDD26B4CBB1"
CAR_DOURADINA = "PR4107256F8148AED25BD4202A70EDF304A4B3805"
CAR_MARIA_HELENA = "PR4114708" + "A" * 32

MUNICIPIOS_PR = [
    {"id": 4127965, "nome": "Turvo"},
    {"id": 4102000, "nome": "Assis Chateaubriand"},
    {"id": 4107256, "nome": "Douradina"},
    {"id": 4114708, "nome": "Maria Helena"},
    {"id": 4104808, "nome": "Cascavel"},
]


def cache_fake(payload=None, status=200):
    """Cache com transporte dublê. Devolve ``(cache, contador)``."""
    contador = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        contador["n"] += 1
        if status != 200:
            return httpx.Response(status, json={"erro": "x"})
        return httpx.Response(200, json=payload if payload is not None else MUNICIPIOS_PR)

    cliente = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://servicodados.ibge.gov.br",
    )
    return CacheMunicipios(cliente=cliente), contador


class TestExtrairCodigoDoCar:
    def test_extrai_o_codigo_ibge(self):
        assert codigo_ibge_do_car(CAR_TURVO) == "4127965"
        assert codigo_ibge_do_car(CAR_DOURADINA) == "4107256"

    def test_recusa_digito_verificador_invalido(self):
        """⚠️ O ponto de validar o DV: um CAR corrompido produziria um código
        de 7 dígitos plausível, que resolveria pro município errado."""
        corrompido = "PR4127966" + CAR_TURVO[9:]  # 4127965 -> 4127966
        assert codigo_ibge_do_car(corrompido) is None

    @pytest.mark.parametrize("entrada", [
        None, "", "PR4127965", "PR4127965TRUNCADO",
        "PRXXXXXXX" + "A" * 32,
        "PR4127965" + "A" * 33,
    ])
    def test_recusa_o_que_nao_e_car(self, entrada):
        assert codigo_ibge_do_car(entrada) is None

    def test_aceita_qualquer_uf_nao_so_pr(self):
        # RN2404705 — amostra real do arquivo, outra UF.
        assert codigo_ibge_do_car("RN2404705A57BC3FC6EDD42799F97803FA2A90806") == "2404705"


class TestCachePorUf:
    def test_uma_chamada_por_uf_nao_por_consulta(self):
        """A razão de o cache existir: sem ele, 60 leads = 60 requisições."""
        cache, contador = cache_fake()
        for _ in range(50):
            cache.nome("4127965", "PR")
        assert contador["n"] == 1

    def test_uf_diferente_gera_chamada_nova(self):
        cache, contador = cache_fake()
        cache.nome("4127965", "PR")
        cache.nome("2404705", "RN")
        assert contador["n"] == 2

    def test_uf_e_normalizada(self):
        cache, contador = cache_fake()
        cache.nome("4127965", "pr")
        cache.nome("4127965", " PR ")
        assert contador["n"] == 1

    def test_falha_da_api_nao_levanta_e_nao_repete_infinito(self):
        """Município é enfeite do dossiê — abortar uma busca paga por causa
        dele seria desproporcional."""
        cache, contador = cache_fake(status=500)
        assert cache.nome("4127965", "PR") is None
        assert cache.nome("4102000", "PR") is None
        assert contador["n"] == 1  # o vazio também é cacheado

    def test_resposta_malformada_nao_quebra(self):
        cache, _ = cache_fake(payload=[{"sem": "id"}, "nem dict"])
        assert cache.nome("4127965", "PR") is None

    def test_codigo_desconhecido_devolve_none(self):
        cache, _ = cache_fake()
        assert cache.nome("4199999", "PR") is None


class TestMunicipiosDosCars:
    def test_preserva_ordem_e_nao_repete(self):
        cache, _ = cache_fake()
        nomes = municipios_dos_cars(
            [CAR_DOURADINA, CAR_MARIA_HELENA, CAR_DOURADINA], "PR", cache
        )
        assert nomes == ["Douradina", "Maria Helena"]

    def test_car_invalido_e_ignorado_sem_derrubar_os_validos(self):
        cache, _ = cache_fake()
        assert municipios_dos_cars(["lixo", CAR_TURVO], "PR", cache) == ["Turvo"]

    def test_lista_vazia(self):
        cache, _ = cache_fake()
        assert municipios_dos_cars([], "PR", cache) == []


def candidato(documento: str, *, recentes=(), todos=(), uf="PR") -> Candidato:
    return Candidato(
        documento=documento, origem=ORIGEM_SICOR, nome="", uf=uf, municipio=None,
        pontos_parciais=55.0,
        dados_nicho={
            "codigos_car_recentes": list(recentes),
            "codigos_car": list(todos or recentes),
        },
    )


class TestResolverMunicipios:
    def test_um_municipio_so_sem_indicador_extra(self):
        """91,5% dos casos medidos."""
        cache, _ = cache_fake()
        [c] = resolver_municipios([candidato("1", recentes=[CAR_TURVO])], cache=cache)
        assert c.municipio == "Turvo"
        assert c.dados_nicho["municipios"] == ["Turvo"]

    def test_varios_municipios_guarda_a_lista_toda(self):
        cache, _ = cache_fake()
        [c] = resolver_municipios(
            [candidato("1", recentes=[CAR_DOURADINA, CAR_MARIA_HELENA])], cache=cache
        )
        assert c.municipio == "Douradina"
        assert c.dados_nicho["municipios"] == ["Douradina", "Maria Helena"]

    def test_prioriza_a_operacao_mais_recente(self):
        """⚠️ A regra que importa: mesma da área e do valor. O CAR antigo
        (Cascavel) NÃO pode vencer o da operação mais recente (Turvo)."""
        cache, _ = cache_fake()
        [c] = resolver_municipios([candidato(
            "1",
            recentes=[CAR_TURVO],
            todos=["PR4104808" + "B" * 32, CAR_TURVO],
        )], cache=cache)
        assert c.municipio == "Turvo"
        assert "Cascavel" not in c.dados_nicho["municipios"]

    def test_sem_car_na_operacao_recente_cai_pro_historico(self):
        """"Prioriza" a mais recente, não "exige" — município antigo é melhor
        que nenhum."""
        cache, _ = cache_fake()
        [c] = resolver_municipios(
            [candidato("1", recentes=[], todos=[CAR_ASSIS])], cache=cache
        )
        assert c.municipio == "Assis Chateaubriand"

    def test_lead_sem_car_nenhum_fica_sem_municipio(self):
        cache, _ = cache_fake()
        [c] = resolver_municipios([candidato("1")], cache=cache)
        assert c.municipio is None
        assert "municipios" not in c.dados_nicho

    def test_lote_inteiro_gasta_uma_chamada_so(self):
        cache, contador = cache_fake()
        lote = [candidato(str(i), recentes=[CAR_TURVO]) for i in range(60)]
        resolvidos = resolver_municipios(lote, cache=cache)
        assert all(c.municipio == "Turvo" for c in resolvidos)
        assert contador["n"] == 1

    def test_nao_muta_o_candidato_original(self):
        cache, _ = cache_fake()
        original = candidato("1", recentes=[CAR_TURVO])
        resolver_municipios([original], cache=cache)
        assert original.municipio is None
        assert "municipios" not in original.dados_nicho

    def test_candidato_sem_uf_e_devolvido_intacto(self):
        cache, contador = cache_fake()
        [c] = resolver_municipios(
            [candidato("1", recentes=[CAR_TURVO], uf=None)], cache=cache
        )
        assert c.municipio is None
        assert contador["n"] == 0  # nem tentou

    def test_falha_do_ibge_devolve_o_lote_sem_municipio(self):
        cache, _ = cache_fake(status=503)
        lote = [candidato(str(i), recentes=[CAR_TURVO]) for i in range(3)]
        assert all(c.municipio is None for c in resolver_municipios(lote, cache=cache))
