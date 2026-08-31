"""A guarda de configuração que impede subir inseguro em produção.

## ⚠️ O incidente que originou isto

A auditoria de segurança de 31/08/2026 subiu a aplicação com
``ENVIRONMENT=production`` e **sem** definir ``SECRET_KEY``. Ela subiu
normalmente, com a chave padrão do repositório, e um JWT
``{"user_id": "adm-1", "role": "admin"}`` assinado com essa chave — que
qualquer um lê no GitHub — devolveu **200 em /api/leads**.

O aviso já estava escrito no docstring de ``SECRET_KEY`` desde a Fase 8a e no
checklist de deploy da §5. Não bastou. Estes testes existem pra que a guarda
não vire aviso de novo.
"""

from __future__ import annotations

import pytest

from app.core.config import (
    SECRET_KEY_MIN_CARACTERES,
    SECRET_KEY_PADRAO,
    Settings,
    validar_seguranca_producao,
)

CHAVE_FORTE = "k" * SECRET_KEY_MIN_CARACTERES


def config(**kwargs) -> Settings:
    """Uma ``Settings`` montada só com o que o teste declara.

    ⚠️ ``_env_file=None`` desliga a leitura do ``.env`` — sem isso o teste
    herdaria a configuração real da máquina de quem roda e passaria (ou
    falharia) por motivo errado.
    """
    return Settings(_env_file=None, **kwargs)


class TestRecusaEmProducao:
    def test_chave_padrao_do_repositorio_impede_o_boot(self):
        """⚠️ O caso exato do bypass encontrado na auditoria."""
        with pytest.raises(RuntimeError, match="valor padrao do repositorio"):
            validar_seguranca_producao(
                config(ENVIRONMENT="production", SECRET_KEY=SECRET_KEY_PADRAO)
            )

    def test_chave_curta_impede_o_boot(self):
        with pytest.raises(RuntimeError, match="31 caracteres"):
            validar_seguranca_producao(
                config(ENVIRONMENT="production", SECRET_KEY="a" * 31)
            )

    def test_o_minimo_exato_passa(self):
        """A fronteira, não só os dois lados dela."""
        validar_seguranca_producao(
            config(ENVIRONMENT="production", SECRET_KEY=CHAVE_FORTE)
        )

    def test_chave_so_de_espacos_nao_engana_o_comprimento(self):
        """40 espaços têm 40 caracteres e zero entropia. O ``.strip()`` é o
        que impede a guarda de ser satisfeita com nada."""
        with pytest.raises(RuntimeError):
            validar_seguranca_producao(
                config(ENVIRONMENT="production", SECRET_KEY=" " * 40)
            )

    def test_chave_padrao_com_espacos_em_volta_tambem_e_recusada(self):
        with pytest.raises(RuntimeError, match="valor padrao do repositorio"):
            validar_seguranca_producao(
                config(ENVIRONMENT="production", SECRET_KEY=f"  {SECRET_KEY_PADRAO}  ")
            )

    @pytest.mark.parametrize("valor", ["production", "PRODUCTION", " Production "])
    def test_producao_e_reconhecida_em_qualquer_grafia(self, valor):
        """``em_producao`` normaliza; se um dia deixar de normalizar, um
        ``ENVIRONMENT=Production`` no painel passaria batido."""
        with pytest.raises(RuntimeError):
            validar_seguranca_producao(
                config(ENVIRONMENT=valor, SECRET_KEY=SECRET_KEY_PADRAO)
            )


class TestNaoAtrapalhaForaDeProducao:
    @pytest.mark.parametrize("ambiente", ["development", "staging", "test", ""])
    def test_chave_fraca_passa_fora_de_producao(self, ambiente):
        """A guarda é sobre produção. Travar o dev com a chave de dev só faria
        todo mundo exportar uma variável a mais pra rodar teste."""
        validar_seguranca_producao(
            config(ENVIRONMENT=ambiente, SECRET_KEY=SECRET_KEY_PADRAO)
        )


class TestMensagem:
    """⚠️ A mensagem vai pro log do orquestrador. Não pode carregar segredo.

    Foi por isso que a guarda deixou de ser um ``@model_validator``: o
    ``ValidationError`` do Pydantic ecoa ``input_value={...}`` com o dict de
    configuração junto.
    """

    def _mensagem(self, **kwargs) -> str:
        with pytest.raises(RuntimeError) as erro:
            validar_seguranca_producao(config(ENVIRONMENT="production", **kwargs))
        return str(erro.value)

    def test_nao_contem_o_valor_da_chave(self):
        mensagem = self._mensagem(SECRET_KEY="chave-fraca-mas-secreta-xyz")
        assert "chave-fraca-mas-secreta-xyz" not in mensagem

    def test_nao_contem_nenhum_outro_segredo_da_config(self):
        mensagem = self._mensagem(
            SECRET_KEY="curta",
            ANTHROPIC_API_KEY="sk-ant-nao-pode-vazar",
            DATABASE_URL="postgresql://u:senha-secreta@h/db",
            HUNTER_API_KEY="hunter-nao-pode-vazar",
        )
        for segredo in ("sk-ant-nao-pode-vazar", "senha-secreta", "hunter-nao-pode-vazar"):
            assert segredo not in mensagem

    def test_diz_como_resolver(self):
        """Quem lê isso está no meio de um deploy quebrado. A mensagem tem que
        entregar o comando, não só o diagnóstico."""
        mensagem = self._mensagem(SECRET_KEY=SECRET_KEY_PADRAO)
        assert "secrets.token_urlsafe" in mensagem
        assert "SECRET_KEY" in mensagem
