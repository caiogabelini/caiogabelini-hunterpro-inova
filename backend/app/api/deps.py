"""Dependencies de autenticação/autorização do FastAPI. Porte do Minotto."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User, UserRole

# ⚠️ `auto_error=False` de propósito: sem isso o próprio HTTPBearer devolve
# 403 quando o header Authorization vem ausente, misturando "não
# autenticado" (401) com "autenticado mas sem permissão" (403). O frontend
# distingue os dois — 401 desloga, 403 redireciona pro Kanban.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credenciais: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Lê o Bearer, decodifica, busca o usuário e injeta na rota.

    401 se o token estiver ausente, inválido ou expirado, se o usuário não
    existir mais, ou se estiver inativo.
    """
    if credenciais is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado"
        )

    payload = decode_access_token(credenciais.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado"
        )

    usuario = db.execute(
        select(User).where(User.id == payload.get("user_id"))
    ).scalar_one_or_none()
    if usuario is None or not usuario.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado ou inativo",
        )
    return usuario


def require_admin(usuario: User = Depends(get_current_user)) -> User:
    """Só passa se ``role == "admin"``.

    Nenhuma rota usa isto ainda — a tela admin (Busca de Leads) é Fase 8b.
    Está aqui porque o guard tem que existir antes da primeira rota que
    precise dele, não depois.
    """
    if usuario.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores",
        )
    return usuario
