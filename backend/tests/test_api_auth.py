"""Autenticação: login, JWT, oráculo de tempo e rate limit.

Roda contra SQLite em memória, com ``get_db`` e ``get_redis`` trocados via
``app.dependency_overrides`` — mesmo padrão de cliente injetável do resto do
projeto. Nenhum teste toca Postgres nem Redis de verdade.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rate_limit import get_redis
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.main import app
from app.models import Base, User

SENHA = "senha-forte-de-teste"
EMAIL = "carolina@inova.com.br"


class RedisFalso:
    """Contador em memória — o mesmo contrato que `rate_limit` usa."""

    def __init__(self, quebrado: bool = False):
        self.dados: dict[str, int] = {}
        self.quebrado = quebrado

    def _check(self):
        if self.quebrado:
            raise ConnectionError("Redis fora do ar")

    def get(self, k):
        self._check()
        v = self.dados.get(k)
        return None if v is None else str(v).encode()

    def delete(self, k):
        self._check()
        self.dados.pop(k, None)

    def pipeline(self):
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, r: RedisFalso):
        self.r, self.ops = r, []

    def incr(self, k):
        self.ops.append(k)
        return self

    def expire(self, k, s):
        return self

    def execute(self):
        self.r._check()
        for k in self.ops:
            self.r.dados[k] = self.r.dados.get(k, 0) + 1
        self.ops = []


@pytest.fixture()
def redis_falso() -> RedisFalso:
    return RedisFalso()


@pytest.fixture()
def cliente(redis_falso: RedisFalso):
    # ⚠️ `StaticPool` + `check_same_thread=False`: o TestClient roda o app
    # numa thread diferente da do teste, e SQLite em memória com pool padrão
    # cria um banco POR CONEXÃO — o usuário inserido aqui simplesmente não
    # existiria pra rota. Um pool estático mantém a mesma conexão nas duas.
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Sessao = sessionmaker(bind=engine, autoflush=False, future=True)
    db = Sessao()
    db.add(User(email=EMAIL, senha_hash=hash_password(SENHA), role="admin", ativo=True))
    db.add(
        User(
            email="inativo@inova.com.br",
            senha_hash=hash_password(SENHA),
            role="client",
            ativo=False,
        )
    )
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis_falso
    with TestClient(app) as c:
        c.db = db  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def entrar(c, email=EMAIL, senha=SENHA):
    return c.post("/api/auth/login", json={"email": email, "senha": senha})


class TestLogin:
    def test_credenciais_corretas_devolvem_jwt(self, cliente) -> None:
        r = entrar(cliente)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["token_type"] == "bearer"
        payload = decode_access_token(corpo["access_token"])
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_senha_errada(self, cliente) -> None:
        r = entrar(cliente, senha="errada")
        assert r.status_code == 401

    def test_email_inexistente(self, cliente) -> None:
        assert entrar(cliente, email="fantasma@x.com").status_code == 401

    def test_usuario_inativo_nao_loga(self, cliente) -> None:
        assert entrar(cliente, email="inativo@inova.com.br").status_code == 401

    def test_a_mensagem_e_a_MESMA_nos_tres_casos(self, cliente) -> None:
        """Mensagem distinta vazaria quais e-mails têm cadastro."""
        msgs = {
            entrar(cliente, senha="errada").json()["detail"],
            entrar(cliente, email="fantasma@x.com").json()["detail"],
            entrar(cliente, email="inativo@inova.com.br").json()["detail"],
        }
        assert len(msgs) == 1

    def test_nao_existe_rota_de_registro_publico(self, cliente) -> None:
        """Usuário é criado por `scripts/create_user.py`, não pela API."""
        for caminho in ("/api/auth/register", "/api/auth/signup", "/api/users"):
            assert cliente.post(caminho, json={}).status_code == 404


class TestOraculoDeTempo:
    """⚠️ A proteção mais sutil do login — e a que já falhou no Minotto.

    Lá o `or` curto-circuitava e e-mail inexistente respondia 59x mais
    rápido, permitindo enumerar cadastros pelo relógio mesmo com a mensagem
    genérica. Estes testes garantem que o bcrypt roda nos DOIS caminhos.
    """

    def test_bcrypt_roda_mesmo_para_email_inexistente(self, cliente, monkeypatch) -> None:
        chamadas: list[str] = []
        import app.api.routes.auth as rota

        original = rota.verify_password

        def espiao(senha, hash_armazenado):
            chamadas.append(hash_armazenado)
            return original(senha, hash_armazenado)

        monkeypatch.setattr(rota, "verify_password", espiao)
        entrar(cliente, email="fantasma@x.com")
        assert len(chamadas) == 1, "sem verify_password, o tempo entrega o cadastro"
        assert chamadas[0] == rota._HASH_DUMMY

    def test_o_hash_dummy_tem_o_MESMO_custo_do_real(self) -> None:
        """Gerado no import, não colado — senão congelaria o custo da época."""
        import app.api.routes.auth as rota

        def custo(h: str) -> str:
            return h.rsplit("$", 1)[0].rsplit("$", 2)[0]

        assert custo(rota._HASH_DUMMY) == custo(hash_password("qualquer"))

    def test_usuario_inativo_tambem_nao_retorna_cedo(self, cliente, monkeypatch) -> None:
        chamadas: list[str] = []
        import app.api.routes.auth as rota

        original = rota.verify_password
        monkeypatch.setattr(
            rota,
            "verify_password",
            lambda s, h: (chamadas.append(h), original(s, h))[1],
        )
        entrar(cliente, email="inativo@inova.com.br")
        assert len(chamadas) == 1


class TestRateLimit:
    def test_bloqueia_depois_do_limite(self, cliente) -> None:
        from app.core.config import settings

        for _ in range(settings.LOGIN_MAX_TENTATIVAS):
            assert entrar(cliente, senha="errada").status_code == 401
        r = entrar(cliente, senha="errada")
        assert r.status_code == 429
        assert "Muitas tentativas" in r.json()["detail"]

    def test_bloqueio_vale_ate_para_a_senha_CERTA(self, cliente) -> None:
        from app.core.config import settings

        for _ in range(settings.LOGIN_MAX_TENTATIVAS):
            entrar(cliente, senha="errada")
        assert entrar(cliente).status_code == 429

    def test_login_certo_limpa_o_contador(self, cliente) -> None:
        entrar(cliente, senha="errada")
        entrar(cliente, senha="errada")
        assert entrar(cliente).status_code == 200
        # zerado: dá pra errar o limite inteiro de novo sem bloquear
        from app.core.config import settings

        for _ in range(settings.LOGIN_MAX_TENTATIVAS):
            assert entrar(cliente, senha="errada").status_code == 401

    def test_o_contador_ignora_caixa_e_espaco(self, cliente) -> None:
        """Senão bastaria variar maiúsculas pra reiniciar a contagem."""
        from app.core.config import settings

        for i in range(settings.LOGIN_MAX_TENTATIVAS):
            variante = EMAIL.upper() if i % 2 else f"  {EMAIL}  "
            entrar(cliente, email=variante, senha="errada")
        assert entrar(cliente, senha="errada").status_code == 429

    def test_bloqueio_NAO_gasta_bcrypt(self, cliente, monkeypatch) -> None:
        """Checado antes do banco: não adianta gastar 250 ms numa tentativa
        que já vai ser recusada."""
        from app.core.config import settings

        for _ in range(settings.LOGIN_MAX_TENTATIVAS):
            entrar(cliente, senha="errada")
        import app.api.routes.auth as rota

        chamadas = []
        monkeypatch.setattr(rota, "verify_password", lambda s, h: chamadas.append(1))
        assert entrar(cliente, senha="errada").status_code == 429
        assert chamadas == []

    def test_redis_fora_do_ar_FALHA_ABERTO(self, cliente, redis_falso) -> None:
        """Fechar trancaria todos os usuários fora por instabilidade de cache."""
        redis_falso.quebrado = True
        assert entrar(cliente).status_code == 200
        assert entrar(cliente, senha="errada").status_code == 401


class TestSeguranca:
    def test_hash_nunca_e_a_senha(self) -> None:
        h = hash_password(SENHA)
        assert SENHA not in h and h.startswith("$2")

    def test_hashes_da_mesma_senha_sao_diferentes(self) -> None:
        """bcrypt usa salt — hash igual significaria salt ausente."""
        assert hash_password(SENHA) != hash_password(SENHA)
        assert verify_password(SENHA, hash_password(SENHA))

    def test_token_adulterado_nao_decodifica(self) -> None:
        t = create_access_token({"user_id": "x", "role": "admin"})
        assert decode_access_token(t[:-4] + "aaaa") is None

    def test_token_assinado_com_outra_chave_nao_decodifica(self, monkeypatch) -> None:
        from app.core.config import settings

        t = create_access_token({"user_id": "x", "role": "client"})
        monkeypatch.setattr(settings, "SECRET_KEY", "outra-chave-completamente")
        assert decode_access_token(t) is None

    def test_role_do_token_nao_e_fronteira_de_seguranca(self, cliente) -> None:
        """Forjar role=admin num token não assinado não dá acesso nenhum —
        a assinatura é o que vale."""
        import base64
        import json

        falso = base64.urlsafe_b64encode(json.dumps({"role": "admin"}).encode()).decode()
        r = cliente.get("/api/leads", headers={"Authorization": f"Bearer x.{falso}.y"})
        assert r.status_code == 401
