"""Validação de WhatsApp ativo via Evolution API.

Porte de ``app/services/whatsapp.py`` do Minotto — mesmo nome, mesmo
contrato, já validado em produção lá:

```
POST {EVOLUTION_URL}/chat/whatsappNumbers/{EVOLUTION_INSTANCE}
Header: apikey: {EVOLUTION_KEY}
Body:   {"numbers": ["<número formatado>"]}
Resposta: lista de objetos com `exists` (bool) e `jid` (só se existir)
```

## ⚠️ É o canal PRINCIPAL da Inova

No kickoff a Carolina foi explícita: WhatsApp é o canal principal para
produtor rural. Isso inverte a importância em relação ao Minotto — e aqui
a etapa tem um dado de entrada que o Minotto não tinha: a API Full devolve
telefone direto do bureau, sem depender de site nem de Google Places. É a
única etapa paga desta fase que roda para toda a população, inclusive os
97,4% de pessoa física.

**Custo por lote mensal:** 1 consulta por telefone preferencial de cada
lead selecionado — até 60 (``LEADS_POR_BUSCA`` 50 × margem 1,2). A
Evolution é self-hosted e compartilhada entre clientes: o custo é de
infraestrutura, não por chamada, mas a instância é própria por cliente e o
volume combinado dos dois projetos passa pela mesma máquina.

## A heurística de código do país é por TAMANHO, não por prefixo

Copiada literalmente do Minotto, junto com o motivo: "adiciona 55 se não
começar com 55" é ambíguo, porque **DDD 55 é do Rio Grande do Sul** — um
número local de 11 dígitos como ``55991234567`` já começa com "55" sem ter
código de país. A decisão é pelo comprimento dos dígitos limpos: 10 ou 11
= nacional sem código, prefixa 55. O resultado tem que ficar em 12–13
dígitos, senão não bate na API (não se gasta consulta com número
claramente errado).

⚠️ **Fixo raramente tem WhatsApp.** No Minotto, os 6 primeiros leads reais
voltaram todos "sem WhatsApp" — não era bug: os telefones vinham do Google
Places, que devolve a central telefônica, e todos eram fixos de 8 dígitos.
A API Full não preenche ``TIPO_TELEFONE``, então ``api_full.Telefone``
infere celular pela contagem de dígitos e ``telefone_preferencial`` já
prioriza celular. Manter essa prioridade é o que evita repetir o episódio.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.segredos import erro_redigido

logger = logging.getLogger(__name__)

CAMINHO_NUMEROS = "/chat/whatsappNumbers/{instancia}"
TIMEOUT_PADRAO = 15.0

_NAO_DIGITO = re.compile(r"\D")


@dataclass(frozen=True, slots=True)
class ResultadoWhatsapp:
    numero_formatado: str = ""
    numero_valido: bool = False
    tem_whatsapp: bool = False
    jid: str | None = None
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.numero_valido and self.erro is None


def formatar_numero(telefone: str | None) -> str | None:
    """Só dígitos + código do país quando o número tem cara de nacional.

    ``None`` quando não dá pra chegar num formato plausível (12–13 dígitos).
    """
    digitos = _NAO_DIGITO.sub("", telefone or "")
    if not digitos:
        return None
    if len(digitos) in (10, 11):
        digitos = f"55{digitos}"
    return digitos if len(digitos) in (12, 13) else None


def _cliente(cliente: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if cliente is not None:
        return cliente, False
    return httpx.Client(base_url=settings.EVOLUTION_URL, timeout=TIMEOUT_PADRAO), True


def validar_whatsapp(
    telefone: str, cliente: httpx.Client | None = None
) -> ResultadoWhatsapp:
    """Consulta se o número tem WhatsApp ativo. Nunca levanta.

    ⚠️ Diferença deliberada em relação ao Minotto: lá ``check_whatsapp``
    deixa a exceção subir e quem engole é o ``_rodar_etapa`` do pipeline.
    Aqui a captura é local **também** — a etapa continua isolada no
    orquestrador, mas o módulo cumpre sozinho a regra 3 do padrão de
    ``services/`` ("nunca lança exceção pro chamador"), como já fazem
    ``sicor``, ``api_full`` e ``brasil_api`` neste projeto.
    """
    numero = formatar_numero(telefone)
    if numero is None:
        return ResultadoWhatsapp(erro="telefone não normalizável — consulta não feita")

    if not settings.evolution_configurada and cliente is None:
        return ResultadoWhatsapp(
            numero_formatado=numero,
            numero_valido=True,
            erro="EVOLUTION_URL/KEY/INSTANCE ausentes no ambiente — etapa pulada",
        )

    http, meu = _cliente(cliente)
    try:
        resposta = http.post(
            CAMINHO_NUMEROS.format(instancia=settings.EVOLUTION_INSTANCE),
            json={"numbers": [numero]},
            headers={"apikey": settings.EVOLUTION_KEY},
        )
        if resposta.status_code >= 400:
            return ResultadoWhatsapp(
                numero_formatado=numero,
                numero_valido=True,
                erro=f"HTTP {resposta.status_code} da Evolution API",
            )
        corpo = resposta.json()
        primeiro = corpo[0] if isinstance(corpo, list) and corpo else {}
        if not isinstance(primeiro, dict):
            primeiro = {}
        return ResultadoWhatsapp(
            numero_formatado=numero,
            numero_valido=True,
            tem_whatsapp=bool(primeiro.get("exists")),
            jid=primeiro.get("jid"),
        )
    except Exception as exc:  # noqa: BLE001 — nunca levanta pro chamador
        motivo = erro_redigido(exc)
        logger.warning("whatsapp: falha validando número — %s", motivo)
        return ResultadoWhatsapp(
            numero_formatado=numero, numero_valido=True, erro=motivo
        )
    finally:
        if meu:
            http.close()
