"""Hash de senha (passlib/bcrypt) e JWT (python-jose).

Porte de ``app/core/security.py`` do Minotto — mesmo esquema, mesmas
bibliotecas, mesmos algoritmos.

⚠️ **Sobre o pin de ``bcrypt==3.2.2``:** passlib 1.7.4 (sem manutenção desde
2020) lê ``bcrypt.__about__.__version__`` pra detectar a versão do backend,
atributo removido no bcrypt 4.x. Sem o pin, ``hash_password`` quebra com um
``ValueError`` enganoso ("password cannot be longer than 72 bytes") mesmo
pra senha curta — o Minotto bateu nesse bug de verdade em 20/08/2026. Não
trocar a versão sem revalidar a combinação.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

JWT_ALGORITHM = "HS256"

_contexto_senha = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(senha: str) -> str:
    """Hash bcrypt de uma senha em texto puro.

    A senha original nunca é armazenada — só o resultado desta função.
    """
    return _contexto_senha.hash(senha)


def verify_password(senha: str, hash_armazenado: str) -> bool:
    """Confere senha em texto puro contra um hash de ``hash_password``."""
    return _contexto_senha.verify(senha, hash_armazenado)


def create_access_token(dados: dict) -> str:
    """JWT assinado (HS256, ``SECRET_KEY``) com expiração.

    O payload leva ``user_id`` e ``role`` — o frontend lê ``role`` pra decidir
    o que mostrar na sidebar, mas isso é UX: a autorização real é do backend.
    """
    conteudo = dados.copy()
    conteudo["exp"] = datetime.now(UTC) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return jwt.encode(conteudo, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decodifica e valida (assinatura + expiração).

    Devolve o payload, ou ``None`` se o token for inválido, expirado ou
    adulterado. Nunca levanta — quem chama só checa ``None``.
    """
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
