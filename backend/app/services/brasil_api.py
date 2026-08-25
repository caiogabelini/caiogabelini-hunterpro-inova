"""BrasilAPI — dados de CNPJ, incluindo o quadro societário (decisor).

Porte de ``app/services/receita_federal.py`` do Minotto. ⚠️ Note o nome: lá
esse arquivo é o **cliente HTTP da BrasilAPI**, apesar do nome sugerir o
parser de arquivo em lote. Aqui os dois estão separados por nome honesto —
``receita_federal.py`` é o scanner de arquivo, ``brasil_api.py`` é este.

Fonte: ``https://brasilapi.com.br/api/cnpj/v1/{cnpj}`` — pública, gratuita,
sem autenticação, espelha os Dados Abertos do CNPJ. Consulta pontual por
CNPJ, uma chamada por documento, então roda **depois** da pré-seleção.

⚠️ **Só resolve CNPJ.** Pessoa física não tem equivalente aqui — é por isso
que ``app.services.api_full`` existe.

⚠️ Não trata rate limit: a API pública tem limite por IP, não documentado com
precisão. Com ~2 CNPJs por lote mensal (3% de 60) isso não é problema hoje;
vira problema se o mix mudar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

CAMINHO_CNPJ = "/api/cnpj/v1/{cnpj}"
TIMEOUT_PADRAO = 15.0

#: Qualificações de sócio que indicam poder de decisão. Heurística por
#: palavra-chave no texto, como no Minotto: não há tabela oficial confiável
#: de códigos de qualificação pra usar no lugar.
QUALIFICACOES_DECISOR = (
    "ADMINISTRADOR",
    "PRESIDENTE",
    "DIRETOR",
    "TITULAR",
    "SOCIO-GERENTE",
    "SÓCIO-GERENTE",
)


@dataclass(frozen=True, slots=True)
class Socio:
    nome: str
    qualificacao: str


@dataclass(frozen=True, slots=True)
class ResultadoBrasilApi:
    cnpj: str = ""
    razao_social: str = ""
    nome_fantasia: str = ""
    municipio: str = ""
    uf: str = ""
    telefone: str = ""
    email: str = ""
    socios: tuple[Socio, ...] = ()
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.razao_social) and self.erro is None


def identificar_decisor(socios: tuple[Socio, ...]) -> Socio | None:
    """O sócio mais provável de decidir.

    Primeiro que bate uma palavra-chave de poder de decisão; se nenhum bater,
    o primeiro da lista — melhor um nome do que nenhum, já que o critério
    ``decisor_identificavel`` só precisa de alguém identificável. ``None`` se
    não houver sócio.
    """
    for socio in socios:
        if any(p in socio.qualificacao.upper() for p in QUALIFICACOES_DECISOR):
            return socio
    return socios[0] if socios else None


def interpretar_resposta(bruto: Any) -> ResultadoBrasilApi:
    """Extrai o que interessa. Todo acesso via ``.get()`` — campo renomeado
    na origem deixa aquele dado vazio, não quebra o parser."""
    if not isinstance(bruto, dict):
        return ResultadoBrasilApi(erro="resposta não é um objeto JSON")
    socios = tuple(
        Socio(
            nome=str(s.get("nome_socio") or "").strip(),
            qualificacao=str(s.get("qualificacao_socio") or "").strip(),
        )
        for s in (bruto.get("qsa") or [])
        if isinstance(s, dict)
    )
    ddd = str(bruto.get("ddd_telefone_1") or "").strip()
    return ResultadoBrasilApi(
        cnpj=str(bruto.get("cnpj") or ""),
        razao_social=str(bruto.get("razao_social") or "").strip(),
        nome_fantasia=str(bruto.get("nome_fantasia") or "").strip(),
        municipio=str(bruto.get("municipio") or "").strip(),
        uf=str(bruto.get("uf") or "").strip(),
        telefone=ddd,
        email=str(bruto.get("email") or "").strip().lower(),
        socios=tuple(s for s in socios if s.nome),
    )


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if cliente is not None:
        return cliente, False
    return (
        httpx.Client(base_url=settings.BRASIL_API_BASE_URL, timeout=TIMEOUT_PADRAO),
        True,
    )


def consultar_cnpj(
    cnpj: str, cliente: httpx.Client | None = None
) -> ResultadoBrasilApi:
    """Consulta um CNPJ. Nunca levanta — falha vira ``erro`` preenchido."""
    documento = "".join(c for c in (cnpj or "") if c.isdigit())
    if len(documento) != 14:
        return ResultadoBrasilApi(cnpj=documento, erro="CNPJ inválido — consulta não feita")

    http, meu = _cliente(cliente)
    try:
        resposta = http.get(CAMINHO_CNPJ.format(cnpj=documento))
        if resposta.status_code == 404:
            return ResultadoBrasilApi(cnpj=documento, erro="CNPJ não encontrado")
        if resposta.status_code >= 400:
            return ResultadoBrasilApi(
                cnpj=documento, erro=f"HTTP {resposta.status_code} da BrasilAPI"
            )
        return interpretar_resposta(resposta.json())
    except Exception as exc:  # noqa: BLE001 — nunca levanta pro chamador
        motivo = erro_redigido(exc)
        logger.warning("brasil_api: falha consultando CNPJ — %s", motivo)
        return ResultadoBrasilApi(cnpj=documento, erro=motivo)
    finally:
        if meu:
            http.close()
