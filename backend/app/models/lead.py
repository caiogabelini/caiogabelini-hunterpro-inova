"""Model do Lead.

**Diferença estrutural em relação ao Minotto.** No Minotto o lead é sempre
pessoa jurídica e a chave de negócio é o CNPJ. Na Inova o nicho é
agronegócio/produtores de grãos (foco Paraná), e boa parte do universo-alvo
é **produtor rural pessoa física** — que não tem CNPJ. A cliente decidiu
prospectar PF e PJ juntos, na mesma base, então:

- ``documento`` guarda CPF **ou** CNPJ, sempre só os dígitos (normalizado);
- ``tipo_documento`` diz qual dos dois é;
- a validação é a do algoritmo de cada formato (ver ``app.core.documentos``),
  não uma regra genérica de comprimento;
- a **chave de negócio é o ``documento`` normalizado**, único na tabela. CPF
  tem 11 dígitos e CNPJ tem 14, então os espaços não colidem e um índice
  único simples já deduplica os dois formatos corretamente.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.types import JSON

from app.core.database import Base
from app.core.documentos import (
    TAMANHO_CNPJ,
    TAMANHO_CPF,
    TIPO_CNPJ,
    TIPO_CPF,
    TIPOS_DOCUMENTO,
    detectar_tipo_documento,
    formatar_documento,
    normalizar_documento,
    validar_documento,
)
from app.core.tempo import agora_utc

# JSONB no Postgres (indexável, é o que roda em produção); JSON genérico nos
# demais dialetos, pra suíte de teste poder rodar sem subir um banco.
JSONPortavel = JSON().with_variant(JSONB(), "postgresql")

UFS_BRASIL = frozenset(
    """AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR
    SC SP SE TO""".split()
)


class KanbanStatus(str, Enum):
    """Colunas do funil comercial, em ordem de funil — não alfabética.

    ⚠️ **Espelha ``frontend/src/kanbanStatuses.ts``**, conferido valor a valor
    na Fase 8b. Os 9 valores são idênticos aos do Minotto: o funil comercial é
    o mesmo, só o nicho dos leads muda. Valor novo aqui exige valor novo lá —
    e, como há CHECK no banco (ver ``__table_args__``), também exige migration.

    ``motivo_perda`` só faz sentido em ``PERDIDO``; os três campos de
    fechamento, só em ``GANHO``. Ambos validados na rota
    (``PATCH /api/leads/{id}/status``), não no banco — as colunas ficam
    nullable porque só se aplicam a um status específico.
    """

    NOVO_LEAD = "novo_lead"
    QUALIFICACAO = "qualificacao"
    CONTATADO = "contatado"
    RESPONDEU = "respondeu"
    REUNIAO = "reuniao"
    PROPOSTA_ENVIADA = "proposta_enviada"
    NEGOCIACAO = "negociacao"
    GANHO = "ganho"
    PERDIDO = "perdido"


#: Ordem de funil preservada — usada no CHECK do banco e na validação da rota.
KANBAN_STATUS_VALIDOS: tuple[str, ...] = tuple(s.value for s in KanbanStatus)

#: ``pontual`` = valor único; ``recorrente`` = valor mensal. Espelha o radio
#: de ``FechamentoModal.tsx``.
TIPOS_CONTRATO_VALIDOS: tuple[str, ...] = ("pontual", "recorrente")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- Chave de negócio -------------------------------------------------
    documento: Mapped[str] = mapped_column(
        String(TAMANHO_CNPJ),
        nullable=False,
        unique=True,
        index=True,
        doc="CPF (11 dígitos) ou CNPJ (14 dígitos), só dígitos. Chave de negócio.",
    )
    tipo_documento: Mapped[str] = mapped_column(
        String(4),
        nullable=False,
        doc="'CPF' (produtor rural pessoa física) ou 'CNPJ' (pessoa jurídica).",
    )

    # --- Identificação ----------------------------------------------------
    nome: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Razão social, quando PJ; nome do produtor, quando PF.",
    )
    municipio: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # --- Contato ----------------------------------------------------------
    telefone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Segundo número, quando a fonte traz mais de um. **Contato alternativo**,
    #: não o do WhatsApp: o principal (``telefone``) é o número validado na
    #: Evolution ou, na falta de validação, o preferencial da fila.
    #:
    #: ⚠️ Coluna própria, e não uma chave em ``dados_nicho``, porque telefone
    #: é dado genérico de contato — ``telefone`` já é coluna, e partir o mesmo
    #: conceito entre uma coluna e um JSON esconderia o segundo número de quem
    #: lê o model. Mesmo tratamento que o Minotto dá a ``emails_secundarios``.
    #:
    #: ``None`` = a fonte trouxe um número só (ou nenhum). Nunca guarda uma
    #: repetição do principal — ver ``persistir_leads``.
    telefone_secundario: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Priorização ------------------------------------------------------
    # NULL = score nunca calculado. Não usar server_default aqui: 0 seria
    # ativamente enganoso, porque "não pontuou" e "não foi avaliado ainda"
    # são coisas diferentes comercialmente (seção 7 do docs_fundacao.md).
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prioridade: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- Rastro do pipeline -----------------------------------------------
    # NULL = pipeline nunca rodou; [] = rodou e não pulou nenhuma etapa.
    # A distinção é deliberada (seção 6): "etapa devolveu None" e "etapa nem
    # rodou" viram o mesmo estado se ninguém separar os dois.
    etapas_puladas: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONPortavel,
        nullable=True,
        doc="Etapas do enriquecimento que falharam ou foram puladas, com motivo.",
    )

    # --- Dados específicos do nicho ---------------------------------------
    # TODO(Fase 3): placeholder genérico. Quando os parsers de Sicor, RADAR e
    # CAR/SICAR existirem, os campos do nicho (área da propriedade, cultura
    # financiada, valor/modalidade do crédito rural, matrícula CAR...) viram
    # colunas próprias — tipadas, indexáveis e pontuáveis pelo score. Até lá
    # este JSON existe pra não travar a ingestão nem forçar um schema que
    # ainda não foi confirmado com a cliente.
    dados_nicho: Mapped[dict[str, Any] | None] = mapped_column(
        JSONPortavel, nullable=True
    )

    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Kanban (pipeline comercial, Fase 8b) -----------------------------
    #: Coluna atual do quadro. ``NOT NULL`` com default na primeira coluna do
    #: funil: um lead recém-persistido pelo pipeline já nasce no Kanban, sem
    #: precisar que alguém o "ative". ``server_default`` além do default do
    #: Python porque as linhas que já existem no banco precisam de valor na
    #: migration, e porque ``persistir_leads`` grava por ``setattr`` em massa.
    kanban_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=KanbanStatus.NOVO_LEAD.value,
        server_default=KanbanStatus.NOVO_LEAD.value,
        index=True,
    )
    #: Texto livre, só relevante quando ``kanban_status == "perdido"``.
    #: Limpo automaticamente ao sair de "perdido" (ver a rota de status).
    motivo_perda: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Fechamento (só quando kanban_status == "ganho") ------------------
    #: Espelha ``motivo_perda`` do lado positivo: o que foi efetivamente
    #: vendido. Lista de strings livres — os serviços fixos que o frontend
    #: oferece como checkbox **não** são enum aqui, e o texto digitado em
    #: "Outro" é entrada válida. Validar as opções é papel da tela.
    servicos_vendidos: Mapped[list[Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    #: "pontual" | "recorrente" — validado na rota, não no banco.
    tipo_contrato: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: R$ — valor único se pontual, mensal se recorrente.
    #:
    #: ⚠️ ``Float`` por paridade com o Minotto, não por ser o tipo certo pra
    #: dinheiro. Aqui só é exibido e somado pro painel; se virar base de
    #: cálculo (comissão, faturamento), trocar por ``Numeric`` antes.
    valor_fechamento: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=agora_utc, onupdate=agora_utc
    )

    __table_args__ = (
        CheckConstraint(
            f"tipo_documento IN ('{TIPO_CPF}', '{TIPO_CNPJ}')",
            name="ck_leads_tipo_documento_valido",
        ),
        # Backstop no banco pro invariante que o model já garante em Python:
        # o comprimento do documento tem que bater com o tipo declarado.
        CheckConstraint(
            "(tipo_documento = '{cpf}' AND length(documento) = {n_cpf})"
            " OR (tipo_documento = '{cnpj}' AND length(documento) = {n_cnpj})".format(
                cpf=TIPO_CPF,
                n_cpf=TAMANHO_CPF,
                cnpj=TIPO_CNPJ,
                n_cnpj=TAMANHO_CNPJ,
            ),
            name="ck_leads_documento_coerente_com_tipo",
        ),
        # Conjunto fechado no banco, como já é feito pra `tipo_documento`.
        # ⚠️ Trade-off assumido: coluna nova no funil passa a exigir migration
        # (o Minotto valida só na rota e não tem esse custo). Escolhido assim
        # porque o funil é estável, `persistir_leads` e os scripts de seed
        # escrevem direto no model sem passar pela rota, e um status inválido
        # gravado por um deles some do quadro sem erro nenhum.
        CheckConstraint(
            "kanban_status IN ({})".format(
                ", ".join(f"'{v}'" for v in KANBAN_STATUS_VALIDOS)
            ),
            name="ck_leads_kanban_status_valido",
        ),
    )

    # --- Validação --------------------------------------------------------

    @validates("documento")
    def _validar_documento(self, _key: str, valor: str) -> str:
        """Normaliza, valida o formato e deduz ``tipo_documento``.

        Deduzir aqui é o que garante que os dois campos nunca divergem: quem
        cria um Lead informando só o documento já sai com o tipo certo.
        """
        normalizado = normalizar_documento(valor)
        tipo = detectar_tipo_documento(normalizado)  # levanta se for inválido

        declarado = self.__dict__.get("tipo_documento")
        if declarado is not None and declarado != tipo:
            raise ValueError(
                f"documento {normalizado!r} é {tipo}, mas tipo_documento "
                f"declarado é {declarado!r}"
            )
        self.tipo_documento = tipo
        return normalizado

    @validates("tipo_documento")
    def _validar_tipo_documento(self, _key: str, valor: str) -> str:
        tipo = str(valor).strip().upper()
        if tipo not in TIPOS_DOCUMENTO:
            raise ValueError(
                f"tipo_documento deve ser um de {TIPOS_DOCUMENTO}, recebido {valor!r}"
            )
        documento = self.__dict__.get("documento")
        if documento is not None and not validar_documento(documento, tipo):
            raise ValueError(
                f"documento {documento!r} não é um {tipo} válido"
            )
        return tipo

    @validates("uf")
    def _validar_uf(self, _key: str, valor: str | None) -> str | None:
        if valor is None or valor == "":
            return None
        uf = str(valor).strip().upper()
        if uf not in UFS_BRASIL:
            raise ValueError(f"UF inválida: {valor!r}")
        return uf

    @validates("nome")
    def _validar_nome(self, _key: str, valor: str) -> str:
        nome = (valor or "").strip()
        if not nome:
            raise ValueError("nome é obrigatório")
        return nome

    # --- Conveniências ----------------------------------------------------

    @property
    def chave_negocio(self) -> str:
        """Chave de deduplicação: o documento normalizado (CPF ou CNPJ).

        Vale pros dois formatos sem ambiguidade — 11 e 14 dígitos não colidem.
        Todo upsert de fonte externa casa por aqui, nunca por nome/município.
        """
        return self.documento

    @property
    def pessoa_fisica(self) -> bool:
        return self.tipo_documento == TIPO_CPF

    @property
    def documento_formatado(self) -> str:
        return formatar_documento(self.documento, self.tipo_documento)

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return (
            f"<Lead id={self.id} {self.tipo_documento}={self.documento} "
            f"nome={self.nome!r}>"
        )
