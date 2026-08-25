"""Redação de credencial em log e mensagem de erro.

Seção 6 do docs_fundacao.md: **nunca deixar exceção HTTP crua virar log** —
ela pode carregar a URL completa com a chave, ou o header ``Authorization``.
Ter uma função central de redação (por nome de variável **e** por padrão de
connection string com senha embutida) é o que impede isso de depender da
memória de quem escreveu o ``except``.

A regra irmã, também da §6: **nunca colocar credencial em query string** —
sempre header. Se uma API só aceitar query string, documentar como risco
aceito. A API Full aceita ``Authorization: Bearer``, então não é o caso aqui.
"""

from __future__ import annotations

import re
import traceback

REDIGIDO = "***REDIGIDO***"

#: Nomes de variável/campo cujo VALOR nunca pode aparecer em log.
NOMES_SENSIVEIS = (
    "token",
    "api_key",
    "apikey",
    "secret",
    "senha",
    "password",
    "passwd",
    "authorization",
    "bearer",
    "chave",
)

# ⚠️ Sem `\b` antes do nome: `API_FULL_TOKEN` não casa com `\btoken\b`,
# porque `_` é caractere de palavra e não há fronteira entre `_` e `T`. Foi
# exatamente esse o furo na primeira versão desta função — o nome real da
# variável deste projeto passava intocado.
_NOME = r"[\w.\-]*(?:" + "|".join(NOMES_SENSIVEIS) + r")[\w.\-]*"

#: `chave = valor`, `chave: valor`, `"chave": "valor"`. O `Bearer` opcional
#: no meio é o outro furo da primeira versão: sem ele a redação comia a
#: palavra "Bearer" e deixava o token logo atrás, em claro.
_ATRIBUICAO = re.compile(
    r"(?i)(" + _NOME + r")(\s*[=:]\s*)([\"']?)(?:Bearer\s+)?([^\s\"',&}]+)"
)
#: `Authorization: Bearer <token>` sem nome de variável antes.
_BEARER = re.compile(r"(?i)\bBearer\s+([^\s\"',&}]+)")
#: Senha embutida em connection string: esquema://usuario:SENHA@host
_CONNECTION_STRING = re.compile(r"(?i)\b([a-z0-9+.\-]+://[^:/\s]+:)([^@\s]+)(@)")
#: Token em query string, caso alguma fonte futura só aceite isso.
_QUERY_STRING = re.compile(r"(?i)([?&]" + _NOME + r"=)([^&\s]+)")


def redigir(texto: str | None) -> str:
    """Substitui todo valor sensível reconhecível por ``***REDIGIDO***``."""
    if not texto:
        return ""
    limpo = _CONNECTION_STRING.sub(rf"\1{REDIGIDO}\3", str(texto))
    limpo = _QUERY_STRING.sub(rf"\1{REDIGIDO}", limpo)
    limpo = _ATRIBUICAO.sub(rf"\1\2\3{REDIGIDO}\3", limpo)
    limpo = _BEARER.sub(f"Bearer {REDIGIDO}", limpo)
    return limpo


def erro_redigido(exc: BaseException) -> str:
    """Mensagem curta de uma exceção, já redigida. Use isto, nunca ``str(exc)``."""
    return redigir(f"{type(exc).__name__}: {exc}")


def traceback_redigido() -> str:
    """Traceback atual, já redigido. Use isto, nunca ``traceback.format_exc()``."""
    return redigir(traceback.format_exc())
