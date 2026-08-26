"""``scripts/reprocessar_leads.py`` — reprocessamento por documento explícito.

⚠️ **Nenhuma chamada real.** O enriquecimento é substituído por um dublê que
conta chamadas e registra os candidatos que recebeu; a fixture ``sem_rede``
(autouse) bloqueia socket na suíte inteira. Se alguma mudança futura fizer o
script chamar o pipeline pago de verdade, o contador do dublê fica em zero e
os testes falham.

O que estes testes protegem, além do "monta os 4 candidatos certos": que o
``Candidato`` é **hidratado da linha do banco**, e não montado do zero. Ver o
docstring do script — com ``dados_nicho`` vazio o score real medido cai de 80
pra 25, e o upsert zera município, UF e todos os sinais do Sicor.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.models import Lead
from tests.conftest import CNPJ_VALIDO, CPF_VALIDO, CPF_VALIDO_2

CAMINHO = Path(__file__).resolve().parent.parent / "scripts" / "reprocessar_leads.py"


def carregar_script():
    """Importa o script como módulo — ``scripts/`` não é pacote."""
    spec = importlib.util.spec_from_file_location("reprocessar_leads", CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


script = carregar_script()


NICHO_REAL = {
    "area_ha": 411.25,
    "valor_financiado": 500_000.0,
    "culturas": ["SOJA", "MILHO"],
    "data_operacao": "20260731",
    "recorrente": True,
    "anos_credito": [2025, 2026],
    "codigos_car": ["PR41" + "0" * 37],
    "n_operacoes": 2,
    "whatsapp_ativo": False,
    "decisor": "PRODUTOR ANTIGO",
}


@pytest.fixture()
def banco(db):
    """Dois leads reais já persistidos, como no banco de produção."""
    db.add(Lead(
        documento=CPF_VALIDO, nome="PRODUTOR ALFA", uf="PR", municipio="CASCAVEL",
        telefone="554430266710", score=80, prioridade="ALTA",
        dados_nicho=dict(NICHO_REAL),
    ))
    db.add(Lead(
        documento=CPF_VALIDO_2, nome="PRODUTOR BETA", uf="PR", municipio="TOLEDO",
        telefone="554132224989", score=75, prioridade="ALTA",
        dados_nicho=dict(NICHO_REAL, area_ha=250.0),
    ))
    db.commit()
    return db


class TestMontagemDosCandidatos:
    def test_monta_um_candidato_por_documento_na_ordem_dada(self, banco):
        candidatos, erros = script.carregar_candidatos(
            banco, [CPF_VALIDO_2, CPF_VALIDO]
        )
        assert erros == []
        assert [c.documento for c in candidatos] == [CPF_VALIDO_2, CPF_VALIDO]

    def test_aceita_documento_com_mascara(self, banco):
        mascarado = f"{CPF_VALIDO[:3]}.{CPF_VALIDO[3:6]}.{CPF_VALIDO[6:9]}-{CPF_VALIDO[9:]}"
        candidatos, erros = script.carregar_candidatos(banco, [mascarado])
        assert erros == []
        assert candidatos[0].documento == CPF_VALIDO

    def test_candidato_e_hidratado_do_banco_nao_montado_do_zero(self, banco):
        """⚠️ O teste central. Um candidato mínimo (só documento) zeraria o
        score e apagaria os sinais do Sicor no upsert."""
        candidatos, _ = script.carregar_candidatos(banco, [CPF_VALIDO])
        c = candidatos[0]

        assert c.uf == "PR"
        assert c.municipio == "CASCAVEL"
        assert c.nome == "PRODUTOR ALFA"
        # Os sinais gratuitos que sustentam 55 dos 100 pontos do score.
        assert c.dados_nicho["area_ha"] == 411.25
        assert c.dados_nicho["valor_financiado"] == 500_000.0
        assert c.dados_nicho["culturas"] == ["SOJA", "MILHO"]
        # E o resto do rastro do Sicor, que não entra no score mas é dossiê.
        assert c.dados_nicho["data_operacao"] == "20260731"
        assert c.dados_nicho["recorrente"] is True
        assert c.dados_nicho["codigos_car"]

    def test_pontos_parciais_saem_dos_sinais_gratuitos(self, banco):
        """Não é o `score` gravado (que já inclui enriquecimento) — misturar
        os dois faria o campo significar duas coisas."""
        candidatos, _ = script.carregar_candidatos(banco, [CPF_VALIDO])
        assert 0 < candidatos[0].pontos_parciais <= 55.0

    def test_dados_nicho_do_candidato_nao_e_o_mesmo_objeto_do_lead(self, banco):
        """Mutação acidental no candidato não pode sujar a linha da sessão."""
        candidatos, _ = script.carregar_candidatos(banco, [CPF_VALIDO])
        candidatos[0].dados_nicho["area_ha"] = 1.0
        lead = banco.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.dados_nicho["area_ha"] == 411.25


class TestRecusas:
    def test_documento_ausente_do_banco_vira_erro(self, banco):
        candidatos, erros = script.carregar_candidatos(banco, [CNPJ_VALIDO])
        assert candidatos == []
        assert "não existe no banco" in erros[0]

    def test_documento_invalido_vira_erro(self, banco):
        candidatos, erros = script.carregar_candidatos(banco, ["12345678900"])
        assert candidatos == []
        assert "inválido" in erros[0]

    def test_um_documento_ruim_no_meio_e_reportado_junto(self, banco):
        _, erros = script.carregar_candidatos(
            banco, [CPF_VALIDO, "000", CNPJ_VALIDO]
        )
        assert len(erros) == 2


class EnriquecedorFake:
    """Substitui o pipeline pago. Conta chamadas — é o detector de gasto."""

    def __init__(self) -> None:
        self.chamadas = 0
        self.recebidos: list = []

    def __call__(self, candidatos, **kwargs):
        self.chamadas += 1
        self.recebidos = list(candidatos)
        return [
            _enriquecido(c, telefones=("+5545999887766", "+554430266710"))
            for c in candidatos
        ]


def _enriquecido(candidato, *, telefones: tuple[str, ...]):
    from dataclasses import dataclass, field

    @dataclass
    class Enriquecido:
        candidato: object
        nome: str = "PRODUTOR ALFA"
        instagram: str | None = None
        site_url: str | None = None
        tem_whatsapp: bool = True
        email_status: str | None = "valid"
        presenca_digital: float = 0.0
        fonte_decisor: str | None = "api_full"
        decisor: str | None = "PRODUTOR ALFA"
        whatsapp_numero: str = "5545999887766"
        telefones: tuple[str, ...] = ()
        emails: tuple[str, ...] = ()
        score: int | None = 95
        prioridade: str | None = "ALTA"
        etapas_puladas: tuple[dict, ...] = field(default_factory=tuple)

    return Enriquecido(candidato=candidato, telefones=telefones)


@pytest.fixture()
def rodar(banco, monkeypatch):
    """Roda `main` com sessão de teste e enriquecimento dublê."""
    enriquecedor = EnriquecedorFake()
    monkeypatch.setattr(script, "SessionLocal", lambda: banco)
    monkeypatch.setattr(script, "enriquecer_selecionados", enriquecedor)
    monkeypatch.setattr(banco, "close", lambda: None)

    def _rodar(argv):
        return script.main(argv), enriquecedor

    return _rodar


class TestExecucao:
    DOCS = [CPF_VALIDO, CPF_VALIDO_2]

    def test_simular_nao_gasta_e_nao_escreve(self, rodar, banco):
        codigo, enriquecedor = rodar(["--simular", *self.DOCS])

        assert codigo == 0
        assert enriquecedor.chamadas == 0
        lead = banco.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.telefone == "554430266710"  # intacto
        assert lead.score == 80

    def test_chama_o_enriquecimento_uma_vez_com_todos_os_candidatos(self, rodar):
        codigo, enriquecedor = rodar(self.DOCS)

        assert codigo == 0
        assert enriquecedor.chamadas == 1
        assert [c.documento for c in enriquecedor.recebidos] == self.DOCS

    def test_persiste_o_resultado_com_o_telefone_corrigido(self, rodar, banco):
        rodar(self.DOCS)

        lead = banco.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.telefone == "5545999887766"       # o validado
        assert lead.telefone_secundario == "+554430266710"  # o backup
        assert lead.score == 95

    def test_upsert_nao_apaga_os_sinais_do_sicor(self, rodar, banco):
        """A regressão que o candidato mínimo causaria."""
        rodar(self.DOCS)

        lead = banco.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.uf == "PR"
        assert lead.municipio == "CASCAVEL"
        assert lead.dados_nicho["area_ha"] == 411.25
        assert lead.dados_nicho["culturas"] == ["SOJA", "MILHO"]
        assert lead.dados_nicho["codigos_car"]
        # E os sinais novos do enriquecimento entraram por cima.
        assert lead.dados_nicho["whatsapp_ativo"] is True

    def test_nao_cria_lead_novo(self, rodar, banco):
        antes = banco.query(Lead).count()
        rodar(self.DOCS)
        assert banco.query(Lead).count() == antes

    def test_documento_ruim_aborta_antes_de_gastar(self, rodar, banco):
        codigo, enriquecedor = rodar([CPF_VALIDO, CNPJ_VALIDO])

        assert codigo == 1
        assert enriquecedor.chamadas == 0
        lead = banco.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.score == 80  # nada foi tocado


class TestOsQuatroDaSessao:
    """Os 4 CPFs reais que motivaram o script (mascarados no relatório)."""

    QUATRO = ["05587700968", "07411779946", "06094778979", "06669657900"]

    def test_os_quatro_documentos_sao_validos(self):
        from app.core.documentos import TIPO_CPF, detectar_tipo_documento

        for documento in self.QUATRO:
            assert detectar_tipo_documento(documento) == TIPO_CPF

    def test_monta_os_quatro_candidatos_na_ordem(self, db):
        for i, documento in enumerate(self.QUATRO):
            db.add(Lead(
                documento=documento, nome=f"PRODUTOR {i}", uf="PR",
                municipio="CASCAVEL", score=80,
                dados_nicho=dict(NICHO_REAL),
            ))
        db.commit()

        candidatos, erros = script.carregar_candidatos(db, self.QUATRO)
        assert erros == []
        assert [c.documento for c in candidatos] == self.QUATRO
        assert all(c.dados_nicho["area_ha"] == 411.25 for c in candidatos)
