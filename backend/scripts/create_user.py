#!/usr/bin/env python3
"""Cria um usuário (normalmente o primeiro admin).

Não existe endpoint de cadastro público — mesma decisão do Minotto: só a
Inova e a 4Hands usam o sistema, então usuário é criado por quem tem acesso
ao banco, rodando este script à mão.

Uso:
    cd backend
    .venv/bin/python scripts/create_user.py \
        --email carolina@inova.com.br --senha "uma-senha-forte" --role admin

⚠️ A senha entra em texto puro no argumento e fica no histórico do shell.
Pra evitar isso, omita `--senha` e o script pergunta sem ecoar na tela.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: F401, E402  (registra os models — ver app/models/__init__.py)
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--senha",
        help="Senha em texto puro — só usada pra gerar o hash, nunca é salva. "
        "Omita pra digitar sem ecoar.",
    )
    parser.add_argument(
        "--role", choices=[r.value for r in UserRole], default=UserRole.ADMIN.value
    )
    args = parser.parse_args()

    senha = args.senha or getpass.getpass("Senha: ")
    if not senha.strip():
        print("Senha vazia — abortado.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        existente = db.execute(
            select(User).where(User.email == args.email)
        ).scalar_one_or_none()
        if existente is not None:
            print(
                f"Já existe usuário com o e-mail {args.email!r} (id={existente.id}).",
                file=sys.stderr,
            )
            return 1

        usuario = User(
            email=args.email,
            senha_hash=hash_password(senha),
            role=args.role,
            ativo=True,
        )
        db.add(usuario)
        db.commit()
        db.refresh(usuario)
        print(f"Usuário criado: {usuario.email} (id={usuario.id}, role={usuario.role}).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
