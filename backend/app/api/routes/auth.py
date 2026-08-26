"""Autenticação — login enxuto (e-mail + senha → JWT).

Porte de ``app/api/routes/auth.py`` do Minotto, **com as duas proteções que
lá foram adicionadas depois, numa auditoria**: tempo de resposta constante e
limite de tentativas. Aqui elas entram junto com o login — o problema já é
conhecido, não precisa ser redescoberto em produção.

Sem registro público: usuários são criados por admin via
``scripts/create_user.py``.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import (
    esta_bloqueado,
    get_redis,
    limpar_tentativas,
    registrar_falha,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()

# Hash descartável usado quando o e-mail não existe — ver `_autenticar`.
#
# Gerado no import a partir de senha aleatória, não colado como constante:
# assim nasce com EXATAMENTE o mesmo cost factor que `hash_password` usa
# agora. Um hash escrito à mão ficaria com o custo da época em que foi
# escrito, e a igualdade de tempo — que é o ponto — se perderia em silêncio
# no dia em que o parâmetro do bcrypt mudasse.
#
# Custo: um bcrypt (~0,25 s) por processo, uma vez, na subida.
_HASH_DUMMY = hash_password(secrets.token_urlsafe(32))


def _autenticar(db: Session, email: str, senha: str) -> User | None:
    """Devolve o usuário se as credenciais conferem, ou ``None``.

    ⚠️ **Roda bcrypt SEMPRE, inclusive quando o e-mail não existe** — contra
    um hash descartável. Isso é o ponto da função, não detalhe.

    O jeito ingênuo (``if usuario is None or not verify_password(...)``)
    curto-circuita: e-mail inexistente retorna ANTES do bcrypt. No Minotto
    isso foi medido — **7,7 ms contra 459,4 ms**, 59x — e dava pra enumerar
    quais e-mails têm cadastro só cronometrando a resposta, anulando na
    prática a mensagem de erro genérica que existe justamente pra não vazar
    isso.

    Também não retorna cedo pra usuário inativo, pelo mesmo motivo.
    """
    usuario = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # Um `verify_password` sempre, com o mesmo custo, nos dois caminhos.
    hash_para_conferir = usuario.senha_hash if usuario is not None else _HASH_DUMMY
    senha_confere = verify_password(senha, hash_para_conferir)

    if usuario is None or not usuario.ativo or not senha_confere:
        return None
    return usuario


@router.post("/login", response_model=TokenResponse)
def login(
    dados: LoginRequest,
    db: Session = Depends(get_db),
    redis_client=Depends(get_redis),
) -> TokenResponse:
    """Valida e-mail + senha e devolve um JWT com ``user_id`` e ``role``.

    401 com a **mesma mensagem** pra e-mail inexistente, senha errada e
    usuário inativo — não vazar quais e-mails têm cadastro. E com o **mesmo
    tempo** nos três casos (ver ``_autenticar``).

    429 depois de ``LOGIN_MAX_TENTATIVAS`` falhas seguidas no mesmo e-mail
    dentro da janela. O frontend distingue 401 de 429 e mostra mensagens
    diferentes (``erroLogin.ts``) — um catch genérico faria o usuário
    legítimo achar que errou a senha quando na verdade só precisa esperar.

    ⚠️ O bloqueio é checado ANTES de tocar no banco: não adianta gastar um
    bcrypt numa tentativa que já vai ser recusada. Isso não cria oráculo
    novo — pra ver o 429 o atacante precisa ter causado ele mesmo as falhas
    naquele e-mail, o que não diz nada sobre o e-mail existir.
    """
    if esta_bloqueado(dados.email, redis_client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Muitas tentativas de login. Tente novamente em até "
                f"{settings.LOGIN_JANELA_BLOQUEIO_MINUTOS} minutos."
            ),
        )

    usuario = _autenticar(db, dados.email, dados.senha)
    if usuario is None:
        registrar_falha(dados.email, redis_client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha inválidos",
        )

    limpar_tentativas(dados.email, redis_client)
    return TokenResponse(
        access_token=create_access_token({"user_id": usuario.id, "role": usuario.role})
    )
