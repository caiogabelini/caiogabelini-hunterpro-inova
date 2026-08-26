"""
Limite de tentativas de login (anti-força-bruta), com contador no Redis.
================================================================================

Porte de `app/core/rate_limit.py` do Minotto, onde nasceu de uma auditoria
de segurança (24/08/2026) que encontrou o login SEM limite nenhum — senhas
podiam ser testadas indefinidamente. Aqui entra junto com o login, não
depois: o problema é conhecido, não precisa ser redescoberto.

POR QUE REDIS E NÃO UMA BIBLIOTECA (slowapi):
O projeto já tem Redis configurado e em uso (broker do Celery), e o contador aqui é literalmente um `INCR` com `EXPIRE`. Uma
dependência nova traria middleware, storage próprio e uma segunda forma
de configurar limite -- mais peça pra manter do que as ~15 linhas que o
problema pede. Contador em Redis também sobrevive a restart do processo
e funciona com várias réplicas da API, coisa que um dict em memória não
faria.

⚠️ **FALHA ABERTO de propósito.** Se o Redis estiver fora, o login
CONTINUA funcionando (só sem limite), e a falha vai pro log. É uma troca
consciente: fechar significaria que uma instabilidade no Redis tranca
todos os usuários pra fora de um produto que tem dois. Um atacante que
consiga derrubar o Redis contorna o limite -- mas quem consegue isso já
tem problema maior à mão.

⚠️ **LIMITAÇÕES CONHECIDAS, não esquecidas:**
  - A chave é o E-MAIL (foi o pedido). Isso barra força bruta contra uma
    conta, mas NÃO barra *password spraying* -- tentar a mesma senha
    contra muitos e-mails diferentes. Barrar isso pediria uma segunda
    chave por IP.
  - Não há chave por IP porque, com o nginx na frente
    (`proxy_set_header X-Real-IP`), confiar no header sem validar a
    cadeia de proxies deixaria qualquer um forjar o próprio IP e zerar o
    contador. Fazer certo exige decidir em quais proxies confiar --
    decisão de infra, não de código.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

PREFIXO_CHAVE_LOGIN = "login:tentativas:"


def _chave(email: str) -> str:
    """Normaliza o e-mail pra que "A@X.com " e "a@x.com" compartilhem o
    mesmo contador -- senão bastaria variar maiúsculas pra reiniciar a
    contagem."""
    return f"{PREFIXO_CHAVE_LOGIN}{email.strip().lower()}"


def esta_bloqueado(email: str, redis_client) -> bool:
    """
    True se este e-mail já esgotou as tentativas na janela atual.

    Nunca levanta: erro de Redis devolve False (libera) -- ver a ressalva
    de "falha aberto" na docstring do módulo.
    """
    if settings.LOGIN_MAX_TENTATIVAS <= 0:
        return False
    try:
        bruto = redis_client.get(_chave(email))
    except Exception:
        logger.warning("Rate limit de login indisponível (Redis) -- liberando a tentativa.")
        return False
    if bruto is None:
        return False
    try:
        return int(bruto) >= settings.LOGIN_MAX_TENTATIVAS
    except (TypeError, ValueError):
        return False


def registrar_falha(email: str, redis_client) -> None:
    """
    Conta mais uma tentativa falha e (re)arma a expiração da janela.

    O `EXPIRE` é aplicado a cada falha, não só na primeira: assim a
    janela é de inatividade -- 5 erros espaçados em 3 horas não bloqueiam,
    5 erros dentro de 15 minutos bloqueiam, e continuar tentando durante
    o bloqueio o mantém de pé em vez de deixá-lo expirar sozinho.
    """
    try:
        pipe = redis_client.pipeline()
        pipe.incr(_chave(email))
        pipe.expire(_chave(email), settings.LOGIN_JANELA_BLOQUEIO_MINUTOS * 60)
        pipe.execute()
    except Exception:
        logger.warning("Não foi possível registrar tentativa de login falha (Redis indisponível).")


def limpar_tentativas(email: str, redis_client) -> None:
    """Zera o contador após um login bem-sucedido -- senão o usuário que
    errou 4 vezes e acertou na 5ª continuaria a um erro do bloqueio."""
    try:
        redis_client.delete(_chave(email))
    except Exception:
        logger.warning("Não foi possível limpar tentativas de login (Redis indisponível).")


def get_redis():
    """
    Dependency do FastAPI com o cliente Redis.

    Função separada (em vez de um cliente global) pra que os testes
    troquem por um fake via `app.dependency_overrides` -- mesmo padrão de
    client injetável usado nos módulos HTTP deste projeto.
    """
    import redis as redis_lib

    return redis_lib.from_url(settings.REDIS_URL)
