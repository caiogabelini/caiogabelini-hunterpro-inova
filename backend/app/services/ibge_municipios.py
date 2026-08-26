"""Resolução de município a partir do CD_CAR do Sicor.

## Por que este módulo existe

O Sicor **não publica nome nem código de município** em nenhum dos seus
arquivos — só ``CD_ESTADO`` (a UF). Confirmado lendo o cabeçalho real dos
três: OPERACAO_BASICA (47 colunas), MUTUARIOS (6) e PROPRIEDADES (6).

Mas o ``CD_CAR`` do PROPRIEDADES carrega o código IBGE embutido. Formato
verificado contra os **27,5 milhões de linhas** do arquivo real: dos
15.194.692 CARs preenchidos (55,25%), **todos** têm exatamente 41
caracteres, no formato ``UF(2) + IBGE(7) + hash(32)``::

    PR4115606 2FB8899BDD89A78752E1C2A00289A250
    │ │       └─ hash de 32 caracteres
    │ └─ código IBGE de 7 dígitos (com dígito verificador)
    └─ sigla da UF

Na população-alvo (PR, safras 2025+2026) **100% dos 2.806 produtores** têm
ao menos um CAR — não é coincidência: o filtro de área já depende do
PROPRIEDADES, então quem não tem CAR nunca entra no universo.

## ⚠️ A tabela da Receita Federal NÃO serve aqui

``receita_federal.py`` resolve município por uma tabela de domínio própria,
com **código sequencial de 4 dígitos** (``0001`` = GUAJARA-MIRIM). Não é
IBGE. Testado: nenhum dos códigos extraídos do CAR existe naquela tabela.
Cruzar os dois espaços de código daria nome errado sem erro nenhum.

## Fonte: API de Localidades do IBGE

Oficial, gratuita, sem autenticação. **Uma chamada por UF**, não por lead:
uma requisição traz os 399 municípios do Paraná inteiro.

Não é uma tabela estática por decisão herdada do Minotto, e o motivo vale
repetir: 5.570 códigos copiados à mão têm risco real de erro silencioso — um
dígito trocado não dá erro, só resolve o município errado para sempre.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

CAMINHO_MUNICIPIOS = "/api/v1/localidades/estados/{uf}/municipios"
TIMEOUT_PADRAO = 15.0

#: ``UF(2) + IBGE(7) + hash(32)``. Todos os 15,2 milhões de CARs reais têm
#: exatamente este comprimento — não há variante curta a tolerar.
TAMANHO_CAR = 41
_INICIO_IBGE, _FIM_IBGE = 2, 9


def _digito_verificador_ibge(seis_digitos: str) -> int:
    """DV do código IBGE de município.

    Pesos 1 e 2 alternados sobre os 6 primeiros dígitos, somando os **dígitos**
    de cada produto. Aferido contra códigos conhecidos: Curitiba (4106902),
    Cascavel (4104808) e Londrina (4113700).
    """
    soma = 0
    for digito, peso in zip(seis_digitos, (1, 2, 1, 2, 1, 2)):
        produto = int(digito) * peso
        soma += produto // 10 + produto % 10
    return (10 - soma % 10) % 10


def codigo_ibge_do_car(car: str | None) -> str | None:
    """Código IBGE de 7 dígitos embutido num CD_CAR. ``None`` se não der.

    Valida o **dígito verificador**, não só o formato. Sem isso, um CAR
    truncado ou corrompido produziria um código de 7 dígitos plausível que
    resolveria para o município errado — ou para nenhum, silenciosamente.
    """
    if not car or len(car) != TAMANHO_CAR:
        return None
    codigo = car[_INICIO_IBGE:_FIM_IBGE]
    if not codigo.isdigit():
        return None
    if _digito_verificador_ibge(codigo[:6]) != int(codigo[6]):
        return None
    return codigo


class CacheMunicipios:
    """Índice ``código IBGE -> nome``, uma chamada por UF.

    ⚠️ O cache é o ponto do módulo. Sem ele, uma busca de 60 leads faria 60
    requisições ao IBGE para resolver, na prática, os mesmos poucos
    municípios — e um lote de 2.806 faria 2.806.

    Falha de rede **não derruba a busca**: uma UF que não resolve fica com
    índice vazio e os municípios saem em branco, exatamente como estavam
    antes deste módulo existir. Município é enfeite do dossiê; abortar uma
    busca paga por causa dele seria desproporcional.
    """

    def __init__(self, cliente: httpx.Client | None = None) -> None:
        self._cliente = cliente
        self._por_uf: dict[str, dict[str, str]] = {}

    def _buscar(self, uf: str) -> dict[str, str]:
        http = self._cliente or httpx.Client(
            base_url=settings.IBGE_API_BASE_URL, timeout=TIMEOUT_PADRAO
        )
        meu = self._cliente is None
        try:
            resposta = http.get(CAMINHO_MUNICIPIOS.format(uf=uf))
            resposta.raise_for_status()
            indice = {
                str(item["id"]): str(item["nome"])
                for item in resposta.json()
                if isinstance(item, dict) and item.get("id") and item.get("nome")
            }
            logger.info("ibge: %d municípios carregados para %s", len(indice), uf)
            return indice
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.error(
                "ibge: falha ao carregar municípios de %s — municípios ficarão "
                "em branco: %s",
                uf, erro_redigido(exc),
            )
            return {}
        finally:
            if meu:
                http.close()

    def indice(self, uf: str) -> dict[str, str]:
        """Índice da UF, buscando na primeira vez e reusando depois."""
        chave = (uf or "").strip().upper()
        if not chave:
            return {}
        if chave not in self._por_uf:
            self._por_uf[chave] = self._buscar(chave)
        return self._por_uf[chave]

    @property
    def ufs_carregadas(self) -> tuple[str, ...]:
        """Quais UFs já foram buscadas — usado em teste para provar que a
        chamada é por UF, não por lead."""
        return tuple(sorted(self._por_uf))

    def nome(self, codigo_ibge: str | None, uf: str) -> str | None:
        if not codigo_ibge:
            return None
        return self.indice(uf).get(codigo_ibge) or None


def municipios_dos_cars(
    cars: list[str] | tuple[str, ...], uf: str, cache: CacheMunicipios
) -> list[str]:
    """Nomes de município dos CARs informados, sem repetir, na ordem de entrada.

    Ordem preservada de propósito: o primeiro é o que a tela mostra como
    principal, e "primeiro CAR da operação mais recente" é um critério
    estável — reordenar por alfabeto faria o principal mudar sem motivo.
    """
    nomes: list[str] = []
    for car in cars or ():
        nome = cache.nome(codigo_ibge_do_car(car), uf)
        if nome and nome not in nomes:
            nomes.append(nome)
    return nomes
