"""Configuração da aplicação, lida do ambiente.

Fase 1 declara só o que já é usado de verdade. Chaves de API das fontes
externas entram junto com o módulo que as consome — declarar config que
ninguém lê foi um problema real no Minotto (``SERP_API_KEY``, seção 5).
"""

from __future__ import annotations

import math

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLAlchemy exige o esquema "postgresql://", nunca "postgres://".
    DATABASE_URL: str = "postgresql://hunterpro:hunterpro@localhost:5432/hunterpro_inova"

    PROJETO_NOME: str = "HunterPro — Inova Contabilidade"

    # --- Volume e custo (mesmo padrão do Minotto, seção 5) ---------------
    #: Volume contratado pela cliente. A Inova contratou 50/mês.
    LEADS_POR_BUSCA: int = 50
    #: Margem de segurança da pré-seleção: 50 × 1,2 = 60 candidatos pra
    #: entregar 50. Existe porque etapas de enriquecimento falham e alguns
    #: candidatos se revelam inaproveitáveis depois do corte.
    LEADS_MARGEM_PRE_SELECAO: float = 1.2

    @property
    def cota_pre_selecao(self) -> int:
        """Quantos candidatos a pré-seleção deve entregar (com margem).

        ⚠️ Convenção de "desligar" da seção 5: valor ``0`` ou negativo em
        ``LEADS_POR_BUSCA`` **libera** (sem cota), não bloqueia. Config zerada
        por engano tem que soltar o produto, não trancar todo mundo fora.
        """
        if self.LEADS_POR_BUSCA <= 0:
            return 0  # 0 = sem cota, processa tudo
        return math.ceil(self.LEADS_POR_BUSCA * max(self.LEADS_MARGEM_PRE_SELECAO, 1.0))


settings = Settings()
