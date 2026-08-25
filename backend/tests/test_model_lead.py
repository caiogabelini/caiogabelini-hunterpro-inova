"""Model do Lead — CPF e CNPJ na mesma base, chave de negócio única."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Lead
from tests.conftest import CNPJ_VALIDO, CNPJ_VALIDO_2, CPF_VALIDO, CPF_VALIDO_2


class TestTipoDocumento:
    def test_cpf_deduz_tipo_pessoa_fisica(self) -> None:
        """Produtor rural PF — o caso que não existe no Minotto."""
        lead = Lead(documento=CPF_VALIDO, nome="João da Silva")
        assert lead.tipo_documento == "CPF"
        assert lead.pessoa_fisica is True

    def test_cnpj_deduz_tipo_pessoa_juridica(self) -> None:
        lead = Lead(documento=CNPJ_VALIDO, nome="Agropecuária Exemplo Ltda")
        assert lead.tipo_documento == "CNPJ"
        assert lead.pessoa_fisica is False

    def test_tipo_declarado_coerente_e_aceito(self) -> None:
        lead = Lead(documento=CPF_VALIDO, tipo_documento="CPF", nome="João")
        assert lead.tipo_documento == "CPF"

    def test_tipo_declarado_incoerente_e_rejeitado(self) -> None:
        """Declarar CNPJ e passar um CPF não pode passar silenciosamente."""
        with pytest.raises(ValueError, match="não é um CNPJ válido"):
            Lead(documento=CPF_VALIDO, tipo_documento="CNPJ", nome="João")

    def test_tipo_declarado_antes_do_documento_tambem_e_checado(self) -> None:
        lead = Lead()
        lead.tipo_documento = "CNPJ"
        with pytest.raises(ValueError, match="declarado é 'CNPJ'"):
            lead.documento = CPF_VALIDO

    def test_tipo_fora_do_dominio(self) -> None:
        with pytest.raises(ValueError, match="tipo_documento deve ser um de"):
            Lead(documento=CPF_VALIDO, tipo_documento="RG", nome="João")

    def test_tipo_e_normalizado_pra_maiuscula(self) -> None:
        lead = Lead(documento=CNPJ_VALIDO, tipo_documento="cnpj", nome="Agro Ltda")
        assert lead.tipo_documento == "CNPJ"


class TestValidacaoDeFormato:
    def test_documento_mascarado_e_normalizado(self) -> None:
        lead = Lead(documento="529.982.247-25", nome="João")
        assert lead.documento == CPF_VALIDO

    def test_cnpj_mascarado_e_normalizado(self) -> None:
        lead = Lead(documento="11.222.333/0001-81", nome="Agro Ltda")
        assert lead.documento == CNPJ_VALIDO

    @pytest.mark.parametrize(
        ("documento", "motivo"),
        [
            ("52998224726", "CPF com DV errado"),
            ("11222333000182", "CNPJ com DV errado"),
            ("00000000000", "CPF de dígitos repetidos"),
            ("00000000000000", "CNPJ de dígitos repetidos"),
            ("123456789012", "12 dígitos — não é CPF nem CNPJ"),
            ("1234567890123", "13 dígitos — não é CPF nem CNPJ"),
            ("123", "curto demais"),
            ("", "vazio"),
        ],
    )
    def test_documento_invalido_e_rejeitado(self, documento: str, motivo: str) -> None:
        with pytest.raises(ValueError):
            Lead(documento=documento, nome="Qualquer")

    def test_nome_vazio_e_rejeitado(self) -> None:
        with pytest.raises(ValueError, match="nome é obrigatório"):
            Lead(documento=CPF_VALIDO, nome="   ")


class TestUF:
    def test_uf_normalizada(self) -> None:
        lead = Lead(documento=CPF_VALIDO, nome="João", uf="pr")
        assert lead.uf == "PR"

    def test_uf_invalida(self) -> None:
        with pytest.raises(ValueError, match="UF inválida"):
            Lead(documento=CPF_VALIDO, nome="João", uf="XX")

    def test_uf_vazia_vira_none(self) -> None:
        assert Lead(documento=CPF_VALIDO, nome="João", uf="").uf is None


class TestChaveDeNegocio:
    def test_chave_e_o_documento_normalizado(self) -> None:
        lead = Lead(documento="529.982.247-25", nome="João")
        assert lead.chave_negocio == CPF_VALIDO

    def test_cpf_duplicado_e_rejeitado(self, db: Session) -> None:
        db.add(Lead(documento=CPF_VALIDO, nome="João da Silva"))
        db.commit()
        db.add(Lead(documento=CPF_VALIDO, nome="João da Silva — outra fonte"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_cnpj_duplicado_e_rejeitado(self, db: Session) -> None:
        db.add(Lead(documento=CNPJ_VALIDO, nome="Agro Ltda"))
        db.commit()
        db.add(Lead(documento=CNPJ_VALIDO, nome="Agro Ltda ME"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_mesmo_documento_com_e_sem_mascara_colide(self, db: Session) -> None:
        """A normalização é o que faz a dedup funcionar entre fontes."""
        db.add(Lead(documento=CNPJ_VALIDO, nome="Agro Ltda"))
        db.commit()
        db.add(Lead(documento="11.222.333/0001-81", nome="Agro Ltda"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_cpf_e_cnpj_diferentes_convivem(self, db: Session) -> None:
        """PF e PJ na mesma tabela, sem colisão: 11 e 14 dígitos não batem."""
        db.add(Lead(documento=CPF_VALIDO, nome="João da Silva"))
        db.add(Lead(documento=CNPJ_VALIDO, nome="Agro Ltda"))
        db.add(Lead(documento=CPF_VALIDO_2, nome="Maria Souza"))
        db.add(Lead(documento=CNPJ_VALIDO_2, nome="Grãos do Paraná S/A"))
        db.commit()
        assert db.query(Lead).count() == 4
        assert db.query(Lead).filter(Lead.tipo_documento == "CPF").count() == 2
        assert db.query(Lead).filter(Lead.tipo_documento == "CNPJ").count() == 2


class TestPersistencia:
    def test_campos_base_ida_e_volta(self, db: Session) -> None:
        db.add(
            Lead(
                documento=CNPJ_VALIDO,
                nome="Grãos do Paraná S/A",
                municipio="Cascavel",
                uf="PR",
                telefone="+554533334444",
                email="contato@exemplo.com.br",
                site="https://exemplo.com.br",
                score=87,
                prioridade="ALTA",
            )
        )
        db.commit()
        lead = db.query(Lead).one()
        assert (lead.municipio, lead.uf) == ("Cascavel", "PR")
        assert lead.score == 87
        assert lead.prioridade == "ALTA"
        assert lead.documento_formatado == "11.222.333/0001-81"
        assert lead.created_at is not None and lead.updated_at is not None

    def test_estado_inicial_do_pipeline_e_nulo(self, db: Session) -> None:
        """NULL distingue "nunca rodou" de "rodou e não achou nada"."""
        db.add(Lead(documento=CPF_VALIDO, nome="João da Silva"))
        db.commit()
        lead = db.query(Lead).one()
        assert lead.score is None
        assert lead.prioridade is None
        assert lead.etapas_puladas is None
        assert lead.dados_nicho is None

    def test_etapas_puladas_guarda_motivo(self, db: Session) -> None:
        db.add(
            Lead(
                documento=CPF_VALIDO,
                nome="João da Silva",
                etapas_puladas=[{"etapa": "enrich_email", "motivo": "sem dominio"}],
            )
        )
        db.commit()
        lead = db.query(Lead).one()
        assert lead.etapas_puladas == [
            {"etapa": "enrich_email", "motivo": "sem dominio"}
        ]

    def test_dados_nicho_aceita_json_livre(self, db: Session) -> None:
        """Placeholder da Fase 3 — sem schema fixo ainda, de propósito."""
        db.add(
            Lead(
                documento=CNPJ_VALIDO,
                nome="Agro Ltda",
                dados_nicho={"fonte": "sicor", "bruto": {"safra": "2025/2026"}},
            )
        )
        db.commit()
        assert db.query(Lead).one().dados_nicho["fonte"] == "sicor"
