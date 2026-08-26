"""Prioridade de telefone e contato alternativo.

Nasceu de um bug real achado na primeira busca paga (26/08/2026): a etapa de
WhatsApp validava ``telefones[0]`` — a ordem crua da API Full — em vez do
``telefone_preferencial``, que existia e priorizava celular sobre fixo, mas
que ninguém chamava. Um CPF com fixo em primeiro e celular em segundo era
marcado "sem WhatsApp" tendo WhatsApp.

⚠️ **Nenhuma chamada real.** Todo cliente é dublê; a fixture ``sem_rede``
(autouse) bloqueia socket na suíte inteira.
"""

from __future__ import annotations

import pytest

from app.services.api_full import ResultadoApiFull, Telefone
from app.workers.busca import escolher_telefones
from app.workers.enriquecimento import (
    enriquecer_lead,
    resolver_decisor_cpf,
    telefones_ordenados,
)
from app.scoring.pre_selecao import Candidato
from tests.conftest import CPF_VALIDO

FIXO = Telefone(ddd="45", numero="32251234")
CELULAR = Telefone(ddd="45", numero="999887766")
CELULAR_2 = Telefone(ddd="45", numero="988776655")


def _candidato() -> Candidato:
    return Candidato(
        documento=CPF_VALIDO, origem="sicor", nome="PRODUTOR TESTE",
        uf="PR", municipio="CASCAVEL", pontos_parciais=55.0,
    )


def resultado(*telefones: Telefone) -> ResultadoApiFull:
    return ResultadoApiFull(
        cpf=CPF_VALIDO, nome="PRODUTOR TESTE", telefones=telefones, emails=()
    )


class TestOrdenacao:
    def test_celular_em_segundo_lugar_vira_o_primeiro(self):
        """O caso exato do bug: bureau devolve fixo antes do celular."""
        assert telefones_ordenados(resultado(FIXO, CELULAR)) == (
            CELULAR.e164, FIXO.e164,
        )

    def test_celular_ja_em_primeiro_continua_em_primeiro(self):
        assert telefones_ordenados(resultado(CELULAR, FIXO)) == (
            CELULAR.e164, FIXO.e164,
        )

    def test_um_telefone_so_nao_muda_nada(self):
        """Comportamento idêntico ao de antes da correção."""
        assert telefones_ordenados(resultado(CELULAR)) == (CELULAR.e164,)
        assert telefones_ordenados(resultado(FIXO)) == (FIXO.e164,)

    def test_sem_telefone_devolve_vazio(self):
        assert telefones_ordenados(resultado()) == ()

    def test_so_fixos_mantem_a_ordem_do_bureau(self):
        """Sem celular nenhum, não há preferência a aplicar — e o fixo ainda
        é testado, porque um fixo com WhatsApp Business existe."""
        outro_fixo = Telefone(ddd="45", numero="32259999")
        assert telefones_ordenados(resultado(FIXO, outro_fixo)) == (
            FIXO.e164, outro_fixo.e164,
        )

    def test_numero_repetido_nao_ocupa_duas_vagas(self):
        assert telefones_ordenados(resultado(CELULAR, FIXO, CELULAR)) == (
            CELULAR.e164, FIXO.e164,
        )

    def test_varios_celulares_todos_antes_do_fixo(self):
        ordenados = telefones_ordenados(resultado(FIXO, CELULAR, CELULAR_2))
        # ⚠️ Os DOIS celulares antes do fixo, não só o primeiro. Promover
        # apenas o preferencial deixaria o fixo como contato alternativo,
        # que é o pior número da lista.
        assert ordenados == (CELULAR.e164, CELULAR_2.e164, FIXO.e164)

    def test_concorda_com_telefone_preferencial(self):
        """A ordenação não pode divergir da propriedade que ela usa."""
        for combinacao in [(FIXO, CELULAR), (CELULAR, FIXO), (FIXO,), (CELULAR,)]:
            r = resultado(*combinacao)
            assert telefones_ordenados(r)[0] == r.telefone_preferencial.e164


class ClienteApiFullFake:
    """Devolve uma resposta fixa sem tocar em rede."""

    def __init__(self, telefones: tuple[Telefone, ...]) -> None:
        self._telefones = telefones

    def consultar(self, _cpf: str) -> ResultadoApiFull:  # pragma: no cover
        return resultado(*self._telefones)


class TestValidacaoWhatsappUsaOPreferencial:
    """A prova de que a correção chega até a etapa que gasta."""

    @pytest.fixture()
    def espiao(self, monkeypatch):
        """Captura o número que a etapa de WhatsApp recebeu."""
        from app.workers import enriquecimento

        chamados: list[str] = []

        class ResultadoWppFake:
            tem_whatsapp = True
            numero_formatado = "5545999887766"
            erro = None

        def _validar(telefone, cliente=None):
            chamados.append(telefone)
            return ResultadoWppFake()

        monkeypatch.setattr(
            enriquecimento.whatsapp_service, "validar_whatsapp", _validar
        )
        return chamados

    def _rodar(self, monkeypatch, telefones):
        from app.workers import enriquecimento

        monkeypatch.setattr(
            enriquecimento.api_full, "consultar_cpf",
            lambda _doc, cliente=None: resultado(*telefones),
        )
        return enriquecer_lead(_candidato())

    def test_valida_o_celular_mesmo_vindo_em_segundo(
        self, monkeypatch, espiao
    ):
        """⚠️ Antes da correção este teste receberia o FIXO."""
        self._rodar(monkeypatch, (FIXO, CELULAR))
        assert espiao == [CELULAR.e164]

    def test_com_um_telefone_so_valida_esse(self, monkeypatch, espiao):
        self._rodar(monkeypatch, (FIXO,))
        assert espiao == [FIXO.e164]

    def test_sem_telefone_nao_chama_a_evolution(self, monkeypatch, espiao):
        enriquecido = self._rodar(monkeypatch, ())
        assert espiao == []
        from app.workers.enriquecimento import ETAPA_WHATSAPP

        assert any(
            e["etapa"] == ETAPA_WHATSAPP and "sem telefone" in e["motivo"]
            for e in enriquecido.etapas_puladas
        )


class TestEscolhaDoSecundario:
    def test_dois_numeros_o_segundo_vira_alternativo(self):
        enriquecido = _fake(telefones=(CELULAR.e164, FIXO.e164))
        assert escolher_telefones(enriquecido) == (CELULAR.e164, FIXO.e164)

    def test_um_numero_so_nao_gera_alternativo(self):
        enriquecido = _fake(telefones=(CELULAR.e164,))
        assert escolher_telefones(enriquecido) == (CELULAR.e164, None)

    def test_sem_telefone_nenhum(self):
        assert escolher_telefones(_fake(telefones=())) == (None, None)

    def test_numero_validado_vira_o_principal(self):
        enriquecido = _fake(
            telefones=(CELULAR.e164, FIXO.e164), whatsapp_numero="5545999887766"
        )
        principal, secundario = escolher_telefones(enriquecido)
        assert principal == "5545999887766"
        assert secundario == FIXO.e164

    @pytest.mark.parametrize(
        "validado", ["5545999887766", "+5545999887766", "45 99988-7766", "45999887766"]
    )
    def test_o_principal_nunca_se_repete_como_alternativo(self, validado):
        """Formatação diferente é o mesmo telefone. Sem normalizar, o dossiê
        mostraria o mesmo contato duas vezes."""
        enriquecido = _fake(
            telefones=(CELULAR.e164, FIXO.e164), whatsapp_numero=validado
        )
        _, secundario = escolher_telefones(enriquecido)
        assert secundario == FIXO.e164

    def test_cinco_numeros_guarda_o_segundo_e_descarta_o_resto(self):
        """Caso real observado na primeira busca paga. Guardar mais exigiria
        coluna JSON (ver o docstring de `escolher_telefones`)."""
        numeros = (CELULAR.e164, FIXO.e164, "+5545911112222",
                   "+5545933334444", "+5545955556666")
        assert escolher_telefones(_fake(telefones=numeros)) == (
            CELULAR.e164, FIXO.e164,
        )


def _fake(*, telefones: tuple[str, ...], whatsapp_numero: str = ""):
    class Enriquecido:
        pass

    e = Enriquecido()
    e.telefones = telefones
    e.whatsapp_numero = whatsapp_numero
    return e


class TestPersistencia:
    def test_secundario_e_gravado_e_lido(self, db):
        from app.models import Lead
        from app.workers.busca import persistir_leads

        persistir_leads(db, [_enriquecido_completo((CELULAR.e164, FIXO.e164))])

        lead = db.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.telefone == CELULAR.e164
        assert lead.telefone_secundario == FIXO.e164

    def test_um_numero_so_deixa_o_secundario_nulo(self, db):
        from app.models import Lead
        from app.workers.busca import persistir_leads

        persistir_leads(db, [_enriquecido_completo((CELULAR.e164,))])

        lead = db.query(Lead).filter(Lead.documento == CPF_VALIDO).one()
        assert lead.telefone == CELULAR.e164
        assert lead.telefone_secundario is None


def _enriquecido_completo(telefones: tuple[str, ...]):
    from dataclasses import dataclass, field

    @dataclass
    class CandidatoFake:
        documento: str = CPF_VALIDO
        nome: str = "PRODUTOR TESTE"
        municipio: str | None = "CASCAVEL"
        uf: str | None = "PR"
        dados_nicho: dict = field(default_factory=dict)

    @dataclass
    class Enriquecido:
        candidato: CandidatoFake = field(default_factory=CandidatoFake)
        nome: str = "PRODUTOR TESTE"
        instagram: str | None = None
        site_url: str | None = None
        tem_whatsapp: bool = True
        email_status: str | None = None
        presenca_digital: float = 0.0
        fonte_decisor: str | None = "api_full"
        decisor: str | None = "PRODUTOR TESTE"
        whatsapp_numero: str = ""
        telefones: tuple[str, ...] = ()
        emails: tuple[str, ...] = ()
        score: int | None = 60
        prioridade: str | None = "MEDIA"
        etapas_puladas: tuple[dict, ...] = ()

    return Enriquecido(telefones=telefones)


class TestResolverDecisorCpf:
    def test_devolve_telefones_ja_ordenados(self, monkeypatch):
        from app.workers import enriquecimento

        monkeypatch.setattr(
            enriquecimento.api_full, "consultar_cpf",
            lambda _doc, cliente=None: resultado(FIXO, CELULAR),
        )
        _, _, telefones, _, pulada = resolver_decisor_cpf(CPF_VALIDO)
        assert telefones == (CELULAR.e164, FIXO.e164)
        assert pulada is None
