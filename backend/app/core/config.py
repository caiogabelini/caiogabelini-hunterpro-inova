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

    #: development | staging | production.
    #: ⚠️ Declarada desde a Fase 1 e nunca lida — o docstring de então
    #: registrava isso como dívida ("se o próximo projeto quiser desabilitar
    #: /docs em produção, é aqui que se usa"). A partir da Fase 8a ela é
    #: lida de verdade, em `app/main.py`, pra fechar /docs e /openapi.json
    #: fora de desenvolvimento.
    ENVIRONMENT: str = "development"

    # --- Autenticação -----------------------------------------------------
    #: ⚠️ NUNCA usar este valor em produção. É o padrão de desenvolvimento;
    #: produção recebe uma chave forte e diferente via ambiente (§7).
    SECRET_KEY: str = "changeme-in-env"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12h

    #: Anti-força-bruta no login (contador por e-mail no Redis).
    #: ⚠️ Convenção de "desligar" da §5: 0 ou negativo LIBERA, não bloqueia
    #: — config zerada por engano solta o produto, não tranca todo mundo.
    LOGIN_MAX_TENTATIVAS: int = 5
    LOGIN_JANELA_BLOQUEIO_MINUTOS: int = 15

    REDIS_URL: str = "redis://localhost:6379/0"

    # --- CORS -------------------------------------------------------------
    #: Origem do frontend. Em dev é o Vite; em produção, o domínio do cliente.
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    @property
    def em_producao(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    # --- Volume e custo (mesmo padrão do Minotto, seção 5) ---------------
    #: Volume contratado pela cliente. A Inova contratou 50/mês.
    LEADS_POR_BUSCA: int = 50
    #: Margem de segurança da pré-seleção: 50 × 1,2 = 60 candidatos pra
    #: entregar 50. Existe porque etapas de enriquecimento falham e alguns
    #: candidatos se revelam inaproveitáveis depois do corte.
    LEADS_MARGEM_PRE_SELECAO: float = 1.2

    # --- Fontes locais da busca (arquivos baixados manualmente) ----------
    #: Diretórios das duas sementes gratuitas. São arquivos grandes (centenas
    #: de MB), baixados à mão e **não versionados** — mesmo padrão do Minotto
    #: com a PGFN. Caminhos relativos resolvem a partir de ``backend/``.
    #:
    #: ⚠️ Se faltarem, a busca **aborta na trava de segurança** antes de
    #: gastar um centavo (``verificar_fontes`` em ``app/workers/busca.py``).
    #: Isso é o comportamento desejado: o modo de falha ruim é "busca
    #: concluída com sucesso, 0 leads", que já aconteceu no Minotto.
    SICOR_DADOS_DIR: str = "dados_locais/sicor"
    RFB_DADOS_DIR: str = "dados_locais/receita_federal"

    #: Safras do Sicor lidas por busca, em CSV (``"2025,2026"``). Multi-ano
    #: mede mais universo e mais recorrência; qual usar no primeiro lote real
    #: ainda é decisão aberta da cliente, então fica configurável em vez de
    #: fixo no código.
    BUSCA_ANOS: str = "2025,2026"

    #: UF-alvo. A Inova prospecta Paraná; parametrizável porque a orquestração
    #: já aceita o parâmetro e travar aqui não economizaria nada.
    BUSCA_UF: str = "PR"

    @property
    def busca_anos(self) -> tuple[int, ...]:
        """``BUSCA_ANOS`` como tupla de inteiros, ignorando lixo.

        Entrada inválida vira tupla vazia, e a trava de segurança da busca
        aborta com motivo explícito — em vez de estourar um ValueError no
        meio do worker, longe de quem configurou.
        """
        anos: list[int] = []
        for pedaco in self.BUSCA_ANOS.split(","):
            pedaco = pedaco.strip()
            if pedaco.isdigit():
                anos.append(int(pedaco))
        return tuple(dict.fromkeys(anos))

    # --- Fontes pagas -----------------------------------------------------
    #: Token da API Full (bureau privado, pré-pago) — resolve nome e telefone
    #: de CPF, que é o que a BrasilAPI não faz. Vazio = etapa pulada com
    #: motivo, nunca uma chamada que toma 401 em silêncio (guarda de
    #: configuração da seção 3).
    API_FULL_TOKEN: str = ""
    API_FULL_BASE_URL: str = "https://api.apifull.com.br"
    #: API de Localidades do IBGE — **gratuita, sem autenticação**. Usada só
    #: pra resolver código IBGE (extraído do CD_CAR do Sicor) -> nome do
    #: município. Uma chamada por UF, cacheada em memória durante a busca.
    IBGE_API_BASE_URL: str = "https://servicodados.ibge.gov.br"

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
    #: Quantas gerações de IA por lead, **por tipo** (e-mail, WhatsApp,
    #: insights), antes de exigir liberação de um admin.
    #:
    #: ⚠️ Controle de **custo**, não de UX: cada geração é uma chamada paga à
    #: Anthropic, e a tela fica acessível a usuários "client".
    #:
    #: ``0`` ou negativo **DESLIGA** o limite em vez de bloquear tudo — mesma
    #: convenção de ``LOGIN_MAX_TENTATIVAS`` e ``LEADS_POR_BUSCA`` (§5): o
    #: sentido do erro numa config zerada por engano é liberar, não travar o
    #: produto inteiro.
    LIMITE_GERACOES_IA_POR_LEAD: int = 2

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
