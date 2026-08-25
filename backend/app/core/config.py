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

    # --- Fontes pagas -----------------------------------------------------
    #: Token da API Full (bureau privado, pré-pago) — resolve nome e telefone
    #: de CPF, que é o que a BrasilAPI não faz. Vazio = etapa pulada com
    #: motivo, nunca uma chamada que toma 401 em silêncio (guarda de
    #: configuração da seção 3).
    API_FULL_TOKEN: str = ""
    API_FULL_BASE_URL: str = "https://api.apifull.com.br"
    #: BrasilAPI — espelho gratuito dos Dados Abertos do CNPJ. Só CNPJ.
    BRASIL_API_BASE_URL: str = "https://brasilapi.com.br"

    #: Evolution API (WhatsApp) — self-hosted, instância própria por cliente,
    #: serviço compartilhado entre clientes.
    EVOLUTION_URL: str = ""
    EVOLUTION_KEY: str = ""
    EVOLUTION_INSTANCE: str = ""
    #: Firecrawl — scrape de site em markdown.
    FIRECRAWL_API_KEY: str = ""
    #: Hunter.io — descoberta de e-mail do decisor.
    HUNTER_API_KEY: str = ""
    #: ⚠️ DOBRA o consumo de crédito do Hunter (§5). Só ligar depois de
    #: confirmar o plano contratado.
    HUNTER_DOMAIN_SEARCH_FALLBACK: bool = False
    #: ZeroBounce — validação de entregabilidade.
    ZEROBOUNCE_API_KEY: str = ""
    #: Anthropic — leitura do site por IA (presenca_digital).
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"

    @property
    def evolution_configurada(self) -> bool:
        return all(
            (self.EVOLUTION_URL.strip(), self.EVOLUTION_KEY.strip(),
             self.EVOLUTION_INSTANCE.strip())
        )

    @property
    def firecrawl_configurada(self) -> bool:
        return bool(self.FIRECRAWL_API_KEY.strip())

    @property
    def hunter_configurada(self) -> bool:
        return bool(self.HUNTER_API_KEY.strip())

    @property
    def zerobounce_configurada(self) -> bool:
        return bool(self.ZEROBOUNCE_API_KEY.strip())

    @property
    def anthropic_configurada(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY.strip())

    @property
    def api_full_configurada(self) -> bool:
        return bool(self.API_FULL_TOKEN.strip())

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
