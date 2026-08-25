"""API Full — nome e telefone a partir de CPF (bureau privado, pago).

## Por que esta fonte existe

A BrasilAPI resolve **CNPJ**, e só. A população que o Sicor entrega é
**97,4% pessoa física**, e não há fonte gratuita de nome/telefone por CPF —
os Dados Abertos da Receita não publicam pessoa física. Sem esta fonte, o
critério ``decisor_identificavel`` (peso 20) fica permanentemente vazio para
a quase totalidade dos leads da Inova.

Testada manualmente contra 4 CPFs reais do Sicor (PR, 150–1.400 ha) em
25/08/2026: os 4 voltaram com nome e telefone. Amostra pequena, 100% de
acerto — as respostas estão em ``tests/dados_teste/api_full_amostra/``.

## ⚠️ É recurso PAGO, pré-pago

- **Nunca chamada em teste automatizado.** A fixture ``autouse`` do
  ``conftest.py`` bloqueia socket na suíte inteira; os testes deste módulo
  leem as respostas gravadas em disco, nunca a rede. Essa disciplina não é
  teórica: no Minotto, um teste que esqueceu de mockar ``enrich_email``
  queimou um crédito real do Hunter.io (§6).
- **Uma chamada por CPF selecionado**, e só **depois** da pré-seleção. Com
  ``LEADS_POR_BUSCA`` no valor contratado pela Inova (50) e a margem de 1,2,
  são **até 60 consultas por busca mensal** — e ~97% delas são CPF, então na
  prática ~58 chamadas/mês a esta API. R$ 5,00 cobriram as 4 consultas
  manuais de teste; a conta de custo por lote sai dessa proporção.
- **Guarda de configuração** (§3): sem ``API_FULL_TOKEN`` no ambiente, a
  etapa é **pulada com motivo visível**, nunca sai e toma 401 em silêncio.
  No Minotto isso custou uma investigação inteira — 6 leads sem e-mail, 6
  falhas silenciosas, nenhuma pista, e a chave estava vazia.

## Contrato REAL, confirmado contra resposta gravada

``POST /api/ic-cpf-completo``, ``Authorization: Bearer <token>``,
corpo ``{"cpf": "<11 dígitos>", "link": "ic-cpf-completo"}``.

O dado útil vem de ``dados.CREDCADASTRAL`` — **bureau privado**, não Receita:
nos 4 casos ``DADOS_RETORNADOS.DADOS_RECEITA_FEDERAL`` veio ``"0"``.

```
dados.CREDCADASTRAL.IDENTIFICACAO_PESSOA_FISICA.NOME     -> nome
dados.CREDCADASTRAL.SOMENTE_TELEFONE.DADOS[]             -> {DDD, NUM_TELEFONE, ...}
dados.CREDCADASTRAL.EMAILS.INFOEMAILS[]                  -> {ENDERECO}
dados.CREDCADASTRAL.SOMENTE_ENDERECO.DADOS[]             -> endereço de
    CORRESPONDÊNCIA da pessoa. **Não é a propriedade rural.** Confundir os
    dois no dossiê daria ao time comercial uma localização errada da
    fazenda. Por isso este módulo NÃO extrai endereço.
```

⚠️ **A flag do HEADER não garante conteúdo.** ``DADOS_RETORNADOS`` traz
``"0"``/``"1"`` por seção e a documentação sugere usá-la como porta de
entrada — mas num dos 4 casos reais ``EMAILS`` veio ``"1"`` com a lista
``INFOEMAILS`` **vazia**. A flag serve como indício; quem lê o corpo tem que
tratar vazio de qualquer jeito.

⚠️ **``TIPO_TELEFONE`` e ``PONTUACAO`` vêm vazios** nos 4 casos — não dá pra
distinguir celular de fixo por eles, nem ordenar por qualidade. O tipo é
inferido pela contagem de dígitos (9 = celular, 8 = fixo), que é o que a
numeração brasileira garante. Importa pra etapa de WhatsApp: fixo é candidato
ruim (§6).

⚠️ **Resposta sem match não foi observada.** Nenhum dos 4 exemplos reais é um
CPF sem dado. Não se sabe se o bloco ``CREDCADASTRAL`` some, vem vazio, ou
vem com ``STATUS_RETORNO`` diferente. O parser trata os três casos, mas isso é
**código defensivo não confirmado contra resposta real** — mesma categoria do
tratamento do sentinela ``-1`` do Sicor. Revisitar quando aparecer o primeiro
CPF sem retorno em produção.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.core.documentos import TAMANHO_CPF, normalizar_documento, validar_cpf
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

CAMINHO_CPF_COMPLETO = "/api/ic-cpf-completo"
LINK_CPF_COMPLETO = "ic-cpf-completo"
TIMEOUT_PADRAO = 30.0

DIGITOS_CELULAR = 9
DIGITOS_FIXO = 8

_NAO_DIGITO = re.compile(r"\D")


@dataclass(frozen=True, slots=True)
class Telefone:
    ddd: str
    numero: str

    @property
    def e164(self) -> str:
        """Formato internacional, que é o que a Evolution API espera."""
        return f"+55{self.ddd}{self.numero}"

    @property
    def eh_celular(self) -> bool:
        """Inferido pela contagem de dígitos — ``TIPO_TELEFONE`` vem vazio.

        Fixo é candidato ruim pra WhatsApp (§6). A etapa de validação deve
        priorizar celular.
        """
        return len(self.numero) == DIGITOS_CELULAR


@dataclass(frozen=True, slots=True)
class ResultadoApiFull:
    """Resultado tipado. Nunca vem de uma exceção vazando pro chamador."""

    cpf: str = ""
    nome: str = ""
    telefones: tuple[Telefone, ...] = ()
    emails: tuple[str, ...] = ()
    #: Motivo de a etapa não ter produzido dado. ``None`` = correu bem.
    #: Já redigido — nunca carrega token.
    erro: str | None = None
    #: Flags do HEADER, pro dossiê distinguir "não tem" de "não perguntamos".
    secoes_retornadas: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.nome) and self.erro is None

    @property
    def celulares(self) -> tuple[Telefone, ...]:
        return tuple(t for t in self.telefones if t.eh_celular)

    @property
    def telefone_preferencial(self) -> Telefone | None:
        """Celular primeiro; fixo só se não houver celular nenhum."""
        return next(iter(self.celulares + self.telefones), None)


def _bloco(raiz: Mapping[str, Any] | None, *caminho: str) -> Any:
    """Desce por um caminho de chaves sem levantar em nenhum degrau ausente."""
    atual: Any = raiz
    for chave in caminho:
        if not isinstance(atual, Mapping):
            return None
        atual = atual.get(chave)
    return atual


def _lista(valor: Any) -> list[Any]:
    return valor if isinstance(valor, list) else []


def interpretar_resposta(payload: Mapping[str, Any] | None) -> ResultadoApiFull:
    """Extrai nome, telefones e e-mails de uma resposta já desserializada.

    Função pura: não faz rede, não levanta. Toda a defensividade contra
    resposta parcial mora aqui, o que também é o que permite testá-la contra
    as respostas reais gravadas em disco.
    """
    if not isinstance(payload, Mapping):
        return ResultadoApiFull(erro="resposta não é um objeto JSON")

    header = _bloco(payload, "dados", "HEADER", "DADOS_RETORNADOS")
    secoes = {str(k): str(v) for k, v in header.items()} if isinstance(header, Mapping) else {}

    cred = _bloco(payload, "dados", "CREDCADASTRAL")
    if not isinstance(cred, Mapping):
        # Cenário NÃO observado em dado real — ver o aviso no docstring.
        return ResultadoApiFull(
            erro="resposta sem bloco CREDCADASTRAL (CPF sem dado no bureau?)",
            secoes_retornadas=secoes,
        )

    identificacao = _bloco(cred, "IDENTIFICACAO_PESSOA_FISICA")
    nome = ""
    cpf = ""
    if isinstance(identificacao, Mapping):
        nome = str(identificacao.get("NOME") or "").strip()
        cpf = _NAO_DIGITO.sub("", str(identificacao.get("CPF_NUMERO") or ""))

    telefones: list[Telefone] = []
    vistos: set[tuple[str, str]] = set()
    for item in _lista(_bloco(cred, "SOMENTE_TELEFONE", "DADOS")):
        if not isinstance(item, Mapping):
            continue
        ddd = _NAO_DIGITO.sub("", str(item.get("DDD") or ""))
        numero = _NAO_DIGITO.sub("", str(item.get("NUM_TELEFONE") or ""))
        if not ddd or not numero or (ddd, numero) in vistos:
            continue
        vistos.add((ddd, numero))
        telefones.append(Telefone(ddd=ddd, numero=numero))

    emails: list[str] = []
    for item in _lista(_bloco(cred, "EMAILS", "INFOEMAILS")):
        if not isinstance(item, Mapping):
            continue
        endereco = str(item.get("ENDERECO") or "").strip().lower()
        if endereco and endereco not in emails:
            emails.append(endereco)

    if not nome:
        return ResultadoApiFull(
            cpf=cpf,
            telefones=tuple(telefones),
            emails=tuple(emails),
            erro="resposta sem NOME em IDENTIFICACAO_PESSOA_FISICA",
            secoes_retornadas=secoes,
        )

    return ResultadoApiFull(
        cpf=cpf,
        nome=nome,
        telefones=tuple(telefones),
        emails=tuple(emails),
        secoes_retornadas=secoes,
    )


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Cliente injetável — o padrão de ``services/`` (§3), pra testar com mock."""
    if cliente is not None:
        return cliente, False
    return (
        httpx.Client(
            base_url=settings.API_FULL_BASE_URL,
            timeout=TIMEOUT_PADRAO,
            # Credencial em HEADER, nunca em query string (§6).
            headers={"Authorization": f"Bearer {settings.API_FULL_TOKEN}"},
        ),
        True,
    )


def consultar_cpf(
    cpf: str, cliente: httpx.Client | None = None
) -> ResultadoApiFull:
    """Consulta um CPF. **Custa dinheiro a cada chamada.**

    Nunca levanta: qualquer falha vira ``ResultadoApiFull`` com ``erro``
    preenchido e já redigido. Nenhum caminho aqui deixa o token chegar ao log.
    """
    documento = normalizar_documento(cpf) if cpf else ""
    if len(documento) != TAMANHO_CPF or not validar_cpf(documento):
        return ResultadoApiFull(cpf=documento, erro="CPF inválido — consulta não feita")

    # Guarda de configuração ANTES da chamada (§3): sem token, pula com
    # motivo. Não sai e toma 401 silencioso.
    if not settings.api_full_configurada and cliente is None:
        return ResultadoApiFull(
            cpf=documento, erro="API_FULL_TOKEN ausente no ambiente — etapa pulada"
        )

    http, meu = _cliente(cliente)
    try:
        resposta = http.post(
            CAMINHO_CPF_COMPLETO,
            json={"cpf": documento, "link": LINK_CPF_COMPLETO},
        )
        if resposta.status_code >= 400:
            # Nunca logar a exceção crua nem o corpo: podem ecoar o header.
            return ResultadoApiFull(
                cpf=documento, erro=f"HTTP {resposta.status_code} da API Full"
            )
        resultado = interpretar_resposta(resposta.json())
    except Exception as exc:  # noqa: BLE001 — nunca levanta pro chamador
        motivo = erro_redigido(exc)
        logger.warning("api_full: falha consultando CPF — %s", motivo)
        return ResultadoApiFull(cpf=documento, erro=motivo)
    finally:
        if meu:
            http.close()

    if resultado.erro:
        logger.info("api_full: CPF sem dado utilizável — %s", resultado.erro)
    return resultado
