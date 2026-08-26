"""``executar_busca_completa`` — a orquestração que fecha o registro (Fase 8b).

## ⚠️ Nenhum teste aqui gasta um centavo

Esta é a função que, em produção, chama o enriquecimento pago. Todo teste
injeta ``buscar``/``enriquecer``/``persistir`` por parâmetro — os defaults
reais nunca são resolvidos. O fake de enriquecimento é o guarda: se alguma
mudança futura fizer a função chamar o pipeline real em vez do injetado, o
contador ``chamadas`` do fake fica em zero e o teste falha.

A fixture ``sem_rede`` (autouse) é a segunda linha de defesa: qualquer socket
vira AssertionError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, BuscaLeadsRegistro, User
from app.workers.execucao_busca import executar_busca_completa
from tests.conftest import CNPJ_VALIDO, CPF_VALIDO


# --- Dublês -----------------------------------------------------------------


@dataclass
class CandidatoFake:
    documento: str
    nome: str = "PRODUTOR TESTE"
    municipio: str | None = "CASCAVEL"
    uf: str | None = "PR"
    dados_nicho: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnriquecidoFake:
    candidato: CandidatoFake
    etapas_puladas: tuple[dict[str, str], ...] = ()


@dataclass
class ResultadoBuscaFake:
    """Mesmo shape que ``ResultadoBusca`` expõe pro chamador."""

    selecionados: tuple[CandidatoFake, ...] = ()
    abortada_por: str | None = None
    erros: tuple[dict[str, str], ...] = ()
    leads_sicor: int = 0
    estabelecimentos_rfb: int = 0


class EnriquecedorFake:
    """Conta chamadas. É o detector de gasto acidental."""

    def __init__(self, resultado=None, explode: bool = False) -> None:
        self.resultado = resultado if resultado is not None else []
        self.explode = explode
        self.chamadas = 0
        self.recebidos: list[Any] = []

    def __call__(self, selecionados, **kwargs):
        self.chamadas += 1
        self.recebidos = list(selecionados)
        if self.explode:
            raise RuntimeError("API Full fora do ar")
        return self.resultado


# --- Infra ------------------------------------------------------------------


@pytest.fixture()
def sessao_factory():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False, future=True)
    sessao = fabrica()
    sessao.add(User(id="admin1", email="a@b.com", senha_hash="x", role="admin"))
    sessao.add(BuscaLeadsRegistro(id="b1", iniciado_por_id="admin1", status="executando"))
    sessao.commit()
    # Mesma sessão pra todo mundo: o teste inspeciona o que a função gravou.
    yield lambda: sessao
    sessao.close(); engine.dispose()


def registro(sessao_factory) -> BuscaLeadsRegistro:
    return sessao_factory().get(BuscaLeadsRegistro, "b1")


def rodar(sessao_factory, **kwargs):
    kwargs.setdefault("persistir", lambda _s, enriquecidos: len(enriquecidos))
    return executar_busca_completa("b1", sessao_factory=sessao_factory, **kwargs)


# --- Testes -----------------------------------------------------------------


class TestCaminhoFeliz:
    def test_conclui_e_preenche_os_contadores(self, sessao_factory):
        candidatos = (CandidatoFake(CPF_VALIDO), CandidatoFake(CNPJ_VALIDO))
        enriquecer = EnriquecedorFake([EnriquecidoFake(c) for c in candidatos])

        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(
                selecionados=candidatos, leads_sicor=2800, estabelecimentos_rfb=6
            ),
            enriquecer=enriquecer,
        )

        assert resumo["status"] == "concluido"
        reg = registro(sessao_factory)
        assert reg.status == "concluido"
        assert reg.concluido_em is not None
        assert reg.total_cnpjs_encontrados == 2806
        assert reg.total_cnpjs_selecionados == 2
        assert reg.total_leads_processados == 2
        assert reg.erros == []  # [] = terminou sem erro; None seria "não terminou"
        assert enriquecer.chamadas == 1

    def test_so_os_selecionados_chegam_no_enriquecimento(self, sessao_factory):
        """A invariante de custo: nada pago roda antes do corte."""
        selecionados = (CandidatoFake(CPF_VALIDO),)
        enriquecer = EnriquecedorFake([EnriquecidoFake(selecionados[0])])

        rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(
                selecionados=selecionados, leads_sicor=99999
            ),
            enriquecer=enriquecer,
        )
        assert [c.documento for c in enriquecer.recebidos] == [CPF_VALIDO]

    def test_avisos_das_sementes_sobem_pro_painel(self, sessao_factory):
        """Etapa pulada tem que chegar à tela, senão o erro fica invisível."""
        enriquecer = EnriquecedorFake([])
        rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(
                selecionados=(),
                erros=({"etapa": "rfb_empresas", "motivo": "fatias incompletas"},),
            ),
            enriquecer=enriquecer,
        )
        assert registro(sessao_factory).erros == [
            "rfb_empresas: fatias incompletas"
        ]


class TestAbortaAntesDeGastar:
    def test_semente_ausente_vira_erro_sem_chamar_enriquecimento(self, sessao_factory):
        enriquecer = EnriquecedorFake()
        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(
                abortada_por="semente Sicor indisponível: nenhum arquivo"
            ),
            enriquecer=enriquecer,
        )

        assert resumo["status"] == "erro"
        reg = registro(sessao_factory)
        assert reg.status == "erro"
        assert "Sicor indisponível" in reg.erros[0]
        assert enriquecer.chamadas == 0  # ⚠️ zero gasto

    def test_leitura_das_sementes_explodindo_nao_gasta(self, sessao_factory):
        enriquecer = EnriquecedorFake()

        def _explode(**_):
            raise OSError("disco cheio")

        resumo = rodar(sessao_factory, buscar=_explode, enriquecer=enriquecer)
        assert resumo["status"] == "erro"
        assert "sementes" in registro(sessao_factory).erros[0]
        assert enriquecer.chamadas == 0

    def test_zero_selecionados_conclui_sem_gastar(self, sessao_factory):
        """Semente vazia não é falha: ninguém falhou e nada foi gasto."""
        enriquecer = EnriquecedorFake()
        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(selecionados=()),
            enriquecer=enriquecer,
        )

        assert resumo["status"] == "concluido"
        reg = registro(sessao_factory)
        assert reg.total_cnpjs_selecionados == 0
        assert reg.total_leads_processados == 0
        assert enriquecer.chamadas == 0


class TestFalhas:
    def test_enriquecimento_explodindo_em_bloco_vira_erro_registrado(
        self, sessao_factory
    ):
        """Exceção não pode vazar: mataria a task e deixaria o registro preso
        em "executando" pra sempre."""
        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(selecionados=(CandidatoFake(CPF_VALIDO),)),
            enriquecer=EnriquecedorFake(explode=True),
        )

        assert resumo["status"] == "erro"
        reg = registro(sessao_factory)
        assert reg.status == "erro"
        assert reg.concluido_em is not None
        assert "enriquecimento" in reg.erros[0]

    def test_persistencia_explodindo_vira_erro_registrado(self, sessao_factory):
        candidato = CandidatoFake(CPF_VALIDO)

        def _explode(_sessao, _enriquecidos):
            raise ValueError("coluna faltando")

        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(selecionados=(candidato,)),
            enriquecer=EnriquecedorFake([EnriquecidoFake(candidato)]),
            persistir=_explode,
        )
        assert resumo["status"] == "erro"
        assert "persistência" in registro(sessao_factory).erros[0]

    def test_poucas_falhas_ainda_conclui(self, sessao_factory):
        """1 de 4 falhou: o lote entregou, a busca concluiu."""
        candidatos = tuple(CandidatoFake(f"doc{i}") for i in range(4))
        enriquecidos = [EnriquecidoFake(c) for c in candidatos[:3]]
        enriquecidos.append(EnriquecidoFake(
            candidatos[3],
            etapas_puladas=({"etapa": "enriquecimento", "motivo": "timeout"},),
        ))

        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(selecionados=candidatos),
            enriquecer=EnriquecedorFake(enriquecidos),
        )
        assert resumo["status"] == "concluido"
        assert any("timeout" in e for e in registro(sessao_factory).erros)

    def test_maioria_falhando_vira_erro(self, sessao_factory):
        """Acima de 50% dos SELECIONADOS falhando, a busca é erro — a taxa é
        sobre o que entrou no enriquecimento, nunca sobre o universo."""
        candidatos = tuple(CandidatoFake(f"doc{i}") for i in range(4))
        enriquecidos = [
            EnriquecidoFake(c, etapas_puladas=(
                {"etapa": "enriquecimento", "motivo": "timeout"},))
            for c in candidatos[:3]
        ]
        enriquecidos.append(EnriquecidoFake(candidatos[3]))

        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(
                selecionados=candidatos, leads_sicor=500_000
            ),
            enriquecer=EnriquecedorFake(enriquecidos),
        )
        assert resumo["status"] == "erro"

    def test_etapa_pulada_isolada_nao_conta_como_lead_perdido(self, sessao_factory):
        """Um lead sem WhatsApp não é um lead que falhou — só uma etapa que
        não rendeu. Se contasse, a busca inteira viraria "erro" num lote
        normal, onde etapa pulada é rotina."""
        candidatos = tuple(CandidatoFake(f"doc{i}") for i in range(4))
        enriquecidos = [
            EnriquecidoFake(c, etapas_puladas=(
                {"etapa": "whatsapp", "motivo": "sem chave configurada"},))
            for c in candidatos
        ]

        resumo = rodar(
            sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(selecionados=candidatos),
            enriquecer=EnriquecedorFake(enriquecidos),
        )
        assert resumo["status"] == "concluido"
        assert len(registro(sessao_factory).erros) == 4


class TestRegistroAusente:
    def test_id_inexistente_nao_gasta_e_nao_levanta(self, sessao_factory):
        enriquecer = EnriquecedorFake()
        resumo = executar_busca_completa(
            "nao-existe", sessao_factory=sessao_factory,
            buscar=lambda **_: ResultadoBuscaFake(selecionados=(CandidatoFake("x"),)),
            enriquecer=enriquecer,
        )
        assert resumo["status"] == "erro"
        assert enriquecer.chamadas == 0


class TestKanbanSobreviveARebusca:
    """A busca mensal roda de novo sobre o mesmo universo, então um lead já
    trabalhado reaparece no lote e é atualizado por ``persistir_leads``.

    ⚠️ Se essa atualização tocasse ``kanban_status``, todo lead em negociação
    voltaria pra "Novo Lead" na busca seguinte — o quadro inteiro do vendedor
    seria zerado uma vez por mês, sem erro nenhum aparecendo. Este teste
    tranca o comportamento contra uma regressão silenciosa.
    """

    def test_rebusca_preserva_status_motivo_e_fechamento(self, sessao_factory):
        from app.models import Lead
        from app.workers.busca import persistir_leads

        sessao = sessao_factory()
        sessao.add(Lead(
            documento=CPF_VALIDO, nome="PRODUTOR ANTIGO", uf="PR",
            kanban_status="negociacao", motivo_perda=None,
            servicos_vendidos=["consultoria"], tipo_contrato="recorrente",
            valor_fechamento=1500.0,
        ))
        sessao.commit()

        @dataclass
        class EnriquecidoCompleto:
            candidato: CandidatoFake
            nome: str = "PRODUTOR ATUALIZADO"
            instagram: str | None = None
            site_url: str | None = None
            tem_whatsapp: bool = True
            email_status: str | None = "valid"
            presenca_digital: float = 0.0
            fonte_decisor: str | None = "api_full"
            decisor: str | None = "FULANO"
            whatsapp_numero: str | None = "5545999990000"
            telefones: tuple[str, ...] = ()
            emails: tuple[str, ...] = ("x@y.com",)
            score: int | None = 77
            prioridade: str | None = "ALTA"
            etapas_puladas: tuple[dict[str, str], ...] = ()

        gravados = persistir_leads(
            sessao,
            [EnriquecidoCompleto(CandidatoFake(CPF_VALIDO, dados_nicho={"area_ha": 500.0}))],
        )
        assert gravados == 1

        lead = sessao.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        # O enriquecimento atualizou o que é dele...
        assert lead.nome == "PRODUTOR ATUALIZADO"
        assert lead.score == 77
        # ...e não encostou no que é do vendedor.
        assert lead.kanban_status == "negociacao"
        assert lead.servicos_vendidos == ["consultoria"]
        assert lead.tipo_contrato == "recorrente"
        assert lead.valor_fechamento == 1500.0

    def test_lead_novo_do_pipeline_nasce_na_primeira_coluna(self, sessao_factory):
        from app.models import Lead
        from app.workers.busca import persistir_leads

        sessao = sessao_factory()

        @dataclass
        class EnriquecidoMinimo:
            candidato: CandidatoFake
            nome: str = "PRODUTOR NOVO"
            instagram: str | None = None
            site_url: str | None = None
            tem_whatsapp: bool | None = None
            email_status: str | None = None
            presenca_digital: float = 0.0
            fonte_decisor: str | None = None
            decisor: str | None = None
            whatsapp_numero: str | None = None
            telefones: tuple[str, ...] = ()
            emails: tuple[str, ...] = ()
            score: int | None = 55
            prioridade: str | None = "MEDIA"
            etapas_puladas: tuple[dict[str, str], ...] = ()

        persistir_leads(sessao, [EnriquecidoMinimo(CandidatoFake(CNPJ_VALIDO))])

        lead = sessao.query(Lead).filter(Lead.documento == CNPJ_VALIDO).one()
        assert lead.kanban_status == "novo_lead"
        assert lead.motivo_perda is None
        assert lead.valor_fechamento is None


class TestWiringReal:
    """Os testes acima injetam ``buscar``. Este NÃO injeta — usa o
    ``executar_busca_mensal`` de verdade, pra provar que o caminho padrão
    resolve e que a trava de segurança dispara antes de qualquer gasto.

    Continua sem custo: a trava só olha se os arquivos existem. Apontamos pra
    um diretório inexistente, então ela aborta antes de ler byte nenhum — e o
    enriquecimento (o único ponto que gasta) segue injetado e conta as
    chamadas, que têm que ser zero.
    """

    def test_fonte_ausente_aborta_com_motivo_legivel_e_sem_gastar(
        self, sessao_factory, tmp_path
    ):
        enriquecer = EnriquecedorFake()

        resumo = executar_busca_completa(
            "b1",
            sessao_factory=sessao_factory,
            enriquecer=enriquecer,
            persistir=lambda _s, e: len(e),
            dir_sicor=tmp_path / "sicor_que_nao_existe",
            dir_rfb=tmp_path / "rfb_que_nao_existe",
            anos=[2026],
        )

        assert resumo["status"] == "erro"
        reg = registro(sessao_factory)
        assert reg.status == "erro"
        assert reg.concluido_em is not None
        # A mensagem tem que dizer O QUE faltou — o modo de falha ruim é
        # "busca concluída com sucesso, 0 leads", sem pista nenhuma.
        assert "Sicor" in reg.erros[0]
        assert enriquecer.chamadas == 0

    def test_config_de_anos_le_csv_e_ignora_lixo(self):
        """`BUSCA_ANOS` vem do ambiente como texto; entrada torta não pode
        explodir dentro do worker, longe de quem configurou."""
        from app.core.config import Settings

        assert Settings(BUSCA_ANOS="2025,2026").busca_anos == (2025, 2026)
        assert Settings(BUSCA_ANOS=" 2026 , 2026 ").busca_anos == (2026,)
        assert Settings(BUSCA_ANOS="").busca_anos == ()
        assert Settings(BUSCA_ANOS="ano-passado").busca_anos == ()
