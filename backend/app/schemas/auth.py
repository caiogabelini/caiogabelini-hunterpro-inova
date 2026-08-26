"""Schemas de autenticação. Mesmo shape do Minotto."""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    senha: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
