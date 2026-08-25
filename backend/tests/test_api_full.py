"""Cliente da API Full, contra as 4 respostas REAIS gravadas em disco.

⚠️ **Nenhum teste aqui toca a rede.** A API Full é pré-paga: cada chamada
custa. A fixture ``autouse`` do ``conftest.py`` bloqueia socket na suíte
inteira, e estes testes leem as respostas gravadas em
``tests/dados_teste/api_full_amostra/``.

⚠️ **Os arquivos são capturas de console, não respostas HTTP cruas.** Três
dos quatro perderam a chave ``{`` de abertura e todos perderam o fecho — ver
``carregar_amostra``. O reparo mora AQUI, no teste, e não no cliente: em
produção o corpo vem de ``httpx.Response.json()``, que é JSON válido ou
levanta. Botar reparo de chaves no cliente seria moldar o código de produção
a um artefato de como a evidência foi coletada.

⚠️ **As amostras contêm dado pessoal real** (nome, CPF, telefone, e-mail,
endereço, nascimento de pessoas identificáveis). Os testes daqui **não
duplicam esse dado em asserção**: verificam estrutura, contagem e formato, e
comparam contra o que o próprio arquivo traz. Ver a ressalva no relatório da
sessão sobre manter isso versionado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.api_full import (
    DIGITOS_CELULAR,
    DIGITOS_FIXO,
    ResultadoApiFull,
    Telefone,
    consultar_cpf,
    interpretar_resposta,
)

DIR_AMOSTRA = Path(__file__).resolve().parent / "dados_teste" / "api_full_amostra"

#: CPF -> (telefones esperados, e-mails esperados). Contagens, não conteúdo.
ESPERADO = {
    "00521073960": (1, 0),
    "00628195931": (5, 2),
    "00732510970": (2, 1),
    "00764920952": (5, 2),
}

exige_amostras = pytest.mark.skipif(
    not DIR_AMOSTRA.is_dir() or not any(DIR_AMOSTRA.glob("*.txt")),
    reason=f"respostas reais da API Full ausentes em {DIR_AMOSTRA}",
)


def reparar_captura(bruto: str) -> str:
    """Conserta o dano de copy-paste das capturas. **Só pra teste.**

    Dois danos reais, distintos:

    1. **Falta a ``{`` de abertura** em 3 dos 4 arquivos — a primeira linha é
       só ``\\r\\n``. Quem copiou do console perdeu o primeiro caractere.
    2. **Falta o fecho** em todos os 4 — a captura foi cortada no fim, em
       profundidades diferentes (um deles perde o bloco ``VEICULAR`` inteiro).

    O reparo é mecânico: repõe a chave inicial, tira vírgula pendurada e
    fecha o que ficou aberto, contando delimitadores fora de string.
    """
    s = bruto.strip()
    if not s.startswith("{"):
        s = "{" + s
    s = s.rstrip().rstrip(",")
    pilha: list[str] = []
    dentro_de_string = False
    escapado = False
    for ch in s:
        if escapado:
            escapado = False
            continue
        if ch == "\\":
            escapado = True
            continue
        if ch == '"':
            dentro_de_string = not dentro_de_string
            continue
        if dentro_de_string:
            continue
        if ch in "{[":
            pilha.append(ch)
        elif ch in "}]" and pilha:
            pilha.pop()
    return s + "".join("}" if c == "{" else "]" for c in reversed(pilha))


def carregar_amostra(cpf: str) -> dict:
    bruto = (DIR_AMOSTRA / f"{cpf}.txt").read_text(encoding="utf-8", errors="replace")
    return json.loads(reparar_captura(bruto))


@pytest.fixture(scope="session")
def amostras() -> dict[str, dict]:
    return {cpf: carregar_amostra(cpf) for cpf in ESPERADO}


@exige_amostras
class TestCapturasReais:
    """Os 4 CPFs reais do Sicor que foram testados manualmente."""

    def test_todas_as_capturas_estao_danificadas(self) -> None:
        """Documenta o estado da evidência: nenhuma é JSON válido como está."""
        for cpf in ESPERADO:
            bruto = (DIR_AMOSTRA / f"{cpf}.txt").read_text(
                encoding="utf-8", errors="replace"
            )
            with pytest.raises(json.JSONDecodeError):
                json.loads(bruto)

    def test_tres_perderam_a_chave_de_abertura(self) -> None:
        sem_abertura = [
            cpf
            for cpf in ESPERADO
            if not (DIR_AMOSTRA / f"{cpf}.txt")
            .read_text(encoding="utf-8", errors="replace")
            .strip()
            .startswith("{")
        ]
        assert len(sem_abertura) == 3

    def test_os_quatro_resolvem_nome(self, amostras: dict[str, dict]) -> None:
        """O achado que motivou a fase: 4 de 4 CPFs vieram com nome."""
        for cpf, payload in amostras.items():
            r = interpretar_resposta(payload)
            assert r.ok, cpf
            assert r.nome
            assert len(r.nome.split()) >= 2, "nome completo, não só o primeiro"

    def test_o_nome_extraido_e_o_do_arquivo(self, amostras: dict[str, dict]) -> None:
        for cpf, payload in amostras.items():
            esperado = payload["dados"]["CREDCADASTRAL"][
                "IDENTIFICACAO_PESSOA_FISICA"
            ]["NOME"].strip()
            assert interpretar_resposta(payload).nome == esperado

    def test_os_quatro_resolvem_telefone(self, amostras: dict[str, dict]) -> None:
        for cpf, payload in amostras.items():
            r = interpretar_resposta(payload)
            assert r.telefones, cpf
            assert r.telefone_preferencial is not None

    @pytest.mark.parametrize("cpf", sorted(ESPERADO))
    def test_contagem_de_telefones_e_emails(
        self, cpf: str, amostras: dict[str, dict]
    ) -> None:
        r = interpretar_resposta(amostras[cpf])
        assert (len(r.telefones), len(r.emails)) == ESPERADO[cpf]

    def test_cpf_extraido_bate_com_o_nome_do_arquivo(
        self, amostras: dict[str, dict]
    ) -> None:
        for cpf, payload in amostras.items():
            assert interpretar_resposta(payload).cpf == cpf

    def test_telefones_tem_ddd_e_numero_plausiveis(
        self, amostras: dict[str, dict]
    ) -> None:
        for payload in amostras.values():
            for t in interpretar_resposta(payload).telefones:
                assert len(t.ddd) == 2 and t.ddd.isdigit()
                assert len(t.numero) in (DIGITOS_FIXO, DIGITOS_CELULAR)
                assert t.e164.startswith("+55")

    def test_ddd_e_do_parana(self, amostras: dict[str, dict]) -> None:
        """A busca foi PR — os DDD têm que ser do Paraná (41–46)."""
        ddds = {
            t.ddd for p in amostras.values() for t in interpretar_resposta(p).telefones
        }
        assert ddds <= {"41", "42", "43", "44", "45", "46"}, ddds


@exige_amostras
class TestFlagsDoHeader:
    def test_dados_receita_federal_vem_sempre_zero(
        self, amostras: dict[str, dict]
    ) -> None:
        """O dado é de bureau privado, não da Receita — confirmado nos 4."""
        for payload in amostras.values():
            secoes = interpretar_resposta(payload).secoes_retornadas
            assert secoes["DADOS_RECEITA_FEDERAL"] == "0"

    def test_a_flag_NAO_garante_conteudo(self, amostras: dict[str, dict]) -> None:
        """Achado real: um CPF traz EMAILS="1" com INFOEMAILS vazio.

        Por isso o parser não usa a flag como porta de entrada — lê o corpo e
        trata vazio de qualquer jeito.
        """
        r = interpretar_resposta(amostras["00521073960"])
        assert r.secoes_retornadas["EMAILS"] == "1"
        assert r.emails == ()


class TestTelefone:
    def test_celular_e_inferido_por_9_digitos(self) -> None:
        """TIPO_TELEFONE vem vazio nos 4 casos reais — sobra o comprimento."""
        assert Telefone("44", "999998888").eh_celular
        assert not Telefone("44", "33334444").eh_celular

    def test_e164_para_a_evolution_api(self) -> None:
        assert Telefone("44", "999998888").e164 == "+5544999998888"

    def test_preferencial_prioriza_celular(self) -> None:
        r = ResultadoApiFull(
            nome="X",
            telefones=(Telefone("44", "33334444"), Telefone("44", "999998888")),
        )
        assert r.telefone_preferencial.eh_celular
        assert len(r.celulares) == 1

    def test_preferencial_cai_no_fixo_se_nao_houver_celular(self) -> None:
        r = ResultadoApiFull(nome="X", telefones=(Telefone("44", "33334444"),))
        assert r.telefone_preferencial == Telefone("44", "33334444")

    def test_sem_telefone_nenhum(self) -> None:
        assert ResultadoApiFull(nome="X").telefone_preferencial is None


class TestRespostaDegradada:
    """⚠️ Nenhum destes cenários foi observado em resposta real — é código
    defensivo não confirmado, mesma categoria do sentinela ``-1`` do Sicor."""

    def test_payload_none(self) -> None:
        r = interpretar_resposta(None)
        assert not r.ok and "não é um objeto JSON" in r.erro

    def test_sem_bloco_credcadastral(self) -> None:
        r = interpretar_resposta({"status": "sucesso", "dados": {"HEADER": {}}})
        assert not r.ok and "CREDCADASTRAL" in r.erro

    def test_credcadastral_vazio(self) -> None:
        r = interpretar_resposta({"dados": {"CREDCADASTRAL": {}}})
        assert not r.ok and "NOME" in r.erro

    def test_sem_nome_mas_com_telefone_ainda_reporta_erro(self) -> None:
        r = interpretar_resposta(
            {
                "dados": {
                    "CREDCADASTRAL": {
                        "SOMENTE_TELEFONE": {"DADOS": [{"DDD": "44", "NUM_TELEFONE": "999998888"}]}
                    }
                }
            }
        )
        assert not r.ok
        assert len(r.telefones) == 1, "o que deu pra extrair não é jogado fora"

    @pytest.mark.parametrize(
        "corpo",
        [
            {"dados": {"CREDCADASTRAL": {"SOMENTE_TELEFONE": None}}},
            {"dados": {"CREDCADASTRAL": {"SOMENTE_TELEFONE": {"DADOS": None}}}},
            {"dados": {"CREDCADASTRAL": {"SOMENTE_TELEFONE": {"DADOS": "texto"}}}},
            {"dados": {"CREDCADASTRAL": {"SOMENTE_TELEFONE": {"DADOS": [None, 42]}}}},
            {"dados": None},
            {},
        ],
    )
    def test_estrutura_inesperada_nao_levanta(self, corpo: dict) -> None:
        assert isinstance(interpretar_resposta(corpo), ResultadoApiFull)

    def test_telefone_sem_ddd_e_descartado(self) -> None:
        r = interpretar_resposta(
            {
                "dados": {
                    "CREDCADASTRAL": {
                        "IDENTIFICACAO_PESSOA_FISICA": {"NOME": "FULANO DE TAL"},
                        "SOMENTE_TELEFONE": {
                            "DADOS": [
                                {"DDD": "", "NUM_TELEFONE": "999998888"},
                                {"DDD": "44", "NUM_TELEFONE": ""},
                                {"DDD": "44", "NUM_TELEFONE": "999998888"},
                            ]
                        },
                    }
                }
            }
        )
        assert r.telefones == (Telefone("44", "999998888"),)

    def test_telefone_repetido_e_deduplicado(self) -> None:
        r = interpretar_resposta(
            {
                "dados": {
                    "CREDCADASTRAL": {
                        "IDENTIFICACAO_PESSOA_FISICA": {"NOME": "FULANO DE TAL"},
                        "SOMENTE_TELEFONE": {
                            "DADOS": [
                                {"DDD": "44", "NUM_TELEFONE": "999998888"},
                                {"DDD": "(44)", "NUM_TELEFONE": "99999-8888"},
                            ]
                        },
                    }
                }
            }
        )
        assert len(r.telefones) == 1


class TestGuardaDeCusto:
    """Nada aqui pode sair pra rede — a suíte inteira é blindada, mas o
    cliente também tem que recusar sozinho."""

    def test_cpf_invalido_nao_chega_a_consultar(self) -> None:
        r = consultar_cpf("12345678900")
        assert "CPF inválido" in r.erro

    def test_cpf_vazio_nao_chega_a_consultar(self) -> None:
        assert "inválido" in consultar_cpf("").erro

    def test_sem_token_pula_com_motivo_em_vez_de_tomar_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guarda de configuração da §3 — no Minotto isso custou uma
        investigação inteira de 6 falhas silenciosas.

        O token é forçado a vazio em vez de depender do ambiente: com um
        ``.env`` real na máquina de quem roda a suíte, o teste passaria a
        exercitar outro caminho sem ninguém notar.
        """
        monkeypatch.setattr(settings, "API_FULL_TOKEN", "")
        r = consultar_cpf("00521073960")
        assert r.erro is not None
        assert "API_FULL_TOKEN" in r.erro

    def test_com_token_configurado_a_chamada_e_tentada_e_bloqueada(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Com token, o cliente TENTA a rede — e a fixture barra.

        É a prova de que a guarda de configuração não está mascarando a
        blindagem de rede: são duas defesas distintas.
        """
        monkeypatch.setattr(settings, "API_FULL_TOKEN", "token_de_teste")
        r = consultar_cpf("00521073960")
        assert r.erro is not None
        assert "API_FULL_TOKEN" not in r.erro

    def test_o_token_nunca_aparece_no_erro(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "API_FULL_TOKEN", "tok_live_supersecreto_9f8")
        r = consultar_cpf("00521073960")
        assert "supersecreto" not in (r.erro or "")

    def test_a_fixture_de_rede_realmente_bloqueia(self) -> None:
        import socket

        with pytest.raises(AssertionError, match="rede bloqueada"):
            socket.socket().connect(("api.apifull.com.br", 443))
