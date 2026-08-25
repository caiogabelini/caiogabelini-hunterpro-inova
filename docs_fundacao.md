# HunterPro — Manual de Fundação

**Documento de referência pra replicar a arquitetura em novos clientes (próximo: Inova/Carol).**
Baseado no projeto HunterPro Minotto Contabilidade — do zero até produção real, com 60 leads processados.[^prod]

[^prod]: **Produção ≠ ambiente de desenvolvimento.** Os números de produção citados neste documento (336.853 CNPJs no universo, 60 selecionados, 60 processados) vêm da execução real na VPS/EasyPanel, com os **10 shards** do arquivo de Estabelecimentos da Receita Federal e `LEADS_POR_BUSCA=50`. O banco **local** de desenvolvimento contém apenas execuções de teste com volume baixo (`LEADS_POR_BUSCA=5` → 6 leads, uma fatia só do arquivo), então quem for conferir esses números a partir de um clone do repositório **não vai encontrá-los** — não é divergência, são dois ambientes diferentes. Ao auditar este documento, verifique afirmações de código contra o código, e afirmações de execução contra o banco de produção.

---

## 1. Visão Geral

O HunterPro é um motor de prospecção B2B: busca empresas de um nicho específico, enriquece cada uma com dados públicos e pagos, calcula um score de priorização, e entrega os melhores leads num painel web (Kanban) pro time comercial do cliente trabalhar.

**A arquitetura é 100% reaproveitável entre clientes.** O que muda de cliente pra cliente é só:
- O **nicho** (CNAE-alvo)
- As **fontes de dado específicas** do nicho (pra Minotto: PGFN + CNES; pra Inova: provavelmente Sicor/RADAR/CAR-SICAR)
- Os **pesos do score** (negociados com cada cliente)
- Pequenos ajustes de **UI** no dossiê (campos específicos do nicho)

Tudo o resto — auth, Kanban, Dashboard, Lista de Leads, deploy, segurança, pipeline de orquestração — é a mesma base.

---

## 2. Stack Tecnológico

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy, Alembic |
| Banco | PostgreSQL 16 |
| Fila/cache | Redis + Celery |
| Frontend | React + Vite + TypeScript |
| Deploy | EasyPanel (Docker), DigitalOcean Droplet |
| IA | Claude Haiku (via Anthropic API, HTTP puro — sem SDK, por consistência com o resto do projeto) |

**Fontes de dado padrão (reaproveitáveis em qualquer nicho):**
- Receita Federal — dados abertos (Estabelecimentos + Empresas + Municípios), processados em lote, direto de `.zip` (sem extrair)
- Google Places API — enriquecimento (site, rating, telefone)
- Firecrawl — scrape de site + extração de WhatsApp/Instagram via regex sobre o markdown
- Hunter.io — e-mail do decisor (+ fallback domain-search, opcional)
- ZeroBounce — validação de e-mail
- Evolution API — validação de WhatsApp (self-hosted, compartilhado entre clientes)

**Fontes específicas de nicho (trocam por cliente):**
- Minotto (saúde): PGFN (dívida ativa) + CNES (especialidade médica/RQE)
- Inova (agronegócio): a definir — provavelmente Sicor (crédito rural), RADAR, CAR/SICAR

---

## 3. Arquitetura do Backend

```
backend/
  app/
    api/routes/        — endpoints (leads, dashboard, admin, auth)
    core/               — config, database, security, tempo (datetime helpers),
                          segredos.py (redação de credencial em log),
                          rate_limit.py (anti-força-bruta no login)
    models/             — SQLAlchemy (Lead, User, BuscaLeads, LeadMessage)
    schemas/            — Pydantic (serialização da API)
    scoring/            — rules.py (pesos), pre_selecao.py (2 fases), compute_lead_score.py
    services/           — um módulo por fonte de dado externa
                          + arquivo_utils.py (infra compartilhada de leitura
                            de .zip em streaming — não é fonte de dado)
    workers/            — celery_app.py (orquestração + todas as tasks)
  alembic/               — migrations
  scripts/               — create_user.py, wait_for_db.py
  tests/                 — pytest, 687 testes no fim do Minotto
```

### Padrão de cada módulo de `services/`
Todo módulo de fonte de dado segue o mesmo esqueleto:
1. Cliente HTTP injetável (facilita teste com mock)
2. Dataclass de resultado tipado
3. Nunca lança exceção pro chamador — falha vira `None`/dict vazio, capturado e logado
4. Se a fonte publica arquivo bruto (Receita, PGFN): parser que lê direto do `.zip` via streaming (nunca carrega tudo em memória)
5. Se a fonte é API paga: guarda de configuração (pula com motivo claro se a chave não estiver no `.env`, não deixa a chamada falhar com 401 silencioso)

### O motor de score (genérico, reaproveitável)
`app/scoring/rules.py` define os critérios como uma lista de `ScoringCriterion` (key, label, weight, layer, source). **Regra de ouro: a soma dos pesos tem que ser 100** — isso é garantido por um `assert` no import do módulo + teste dedicado, quebra a build se alguém desbalancear sem querer.

`SignalLayer` classifica cada critério por confiabilidade:
- `ESTRUTURADO` — dado direto de fonte oficial (Receita, PGFN, CNES, Google Places)
- `INFERENCIA` — interpretado por IA (leitura de site/redes sociais)
- `VALIDACAO` — confirmação de canal (WhatsApp ativo, e-mail entregável)

Essa classificação aparece na UI (dossiê) com cores diferentes — é reaproveitável, só os critérios em si mudam por cliente.

### Pré-seleção em 2 fases (o coração da arquitetura de custo)
**Esse é o padrão mais importante a replicar.** Sem ele, uma busca nacional processaria centenas de milhares de empresas com API paga — inviável.

```
FASE 1 — Prioriza o critério #1 do cliente (pra Minotto: dívida ativa)
  Ordena TODOS os candidatos com esse sinal, do maior pro menor.
  Enche a cota até o limite (com margem de ~20% de segurança).

FASE 2 — Só se sobrar vaga, completa com quem não tem o sinal principal
  Ordenado pelos outros sinais gratuitos, como desempate.
```

**Por quê 2 fases, não um score único misturado:** testamos um score único (todos os sinais gratuitos somados) e descobrimos que um critério secundário com peso alto (Zona Franca, 20 pontos, binário) podia **competir** e às vezes vencer o critério principal (dívida ativa) pela mesma vaga — efeito emergente que ninguém queria. A solução foi garantir estruturalmente que o critério #1 do cliente **sempre** vence, sem depender de calibrar pesos com precisão cirúrgica.

**Todos os sinais usados na pré-seleção têm que ser GRATUITOS** (dado que já está em arquivo local ou cache Redis) — a pré-seleção decide quem vale a pena enriquecer **antes** de gastar dinheiro nisso. Nunca usar um sinal pago (Google Places, Hunter, etc.) pra decidir a seleção.

### Orquestração (`executar_busca_mensal`)
1. Lê a semente (arquivo bulk da Receita, filtrado por CNAE)
2. Cruza com a(s) fonte(s) gratuita(s) específica(s) do nicho
3. Pré-seleciona (2 fases)
4. Só nos selecionados, roda `process_lead_pipeline` (todas as etapas pagas)
5. Persiste tudo, com tratamento de erro por CNPJ isolado (um falhar não derruba os outros)

### Ordem de dependências dentro de `process_lead_pipeline`
```
1. enrich_receita_federal    — fornece município/UF, código IBGE, decisor
2. enrich_pgfn (ou equivalente do nicho) — independente
3. enrich_zona_franca (ou equivalente) — depende de município/UF da etapa 1
4. enrich_[fonte_especifica_nicho]      — depende de código IBGE da etapa 1
5. search_google_places      — depende do nome (razão social/fantasia)
6. enrich_site_firecrawl     — depende do site_url da etapa 5
7. validate_whatsapp         — depende de telefone. Prioridade (nesta ordem):
                                parâmetro explícito → extraído do site (wa.me)
                                → Google Places. O do Google vem por ÚLTIMO
                                de propósito: costuma ser fixo/central (ver §6)
8. enrich_email              — depende de domínio (da URL da etapa 5) + decisor (opcional)
9. persistência (upsert por CNPJ)
10. compute_lead_score        — precisa do lead.id real (etapa 9)
```

---

## 4. As Telas do Frontend (100% reaproveitáveis)

| Tela | Rota | Visibilidade |
|---|---|---|
| Login | `/login` | pública |
| Dashboard | `/dashboard` | todos autenticados |
| Kanban | `/` | todos autenticados |
| Lista de Leads | `/leads` | todos autenticados |
| Dossiê do Lead | `/leads/:id` | todos autenticados |
| Busca de Leads | `/busca-leads` | **admin only** |

**Dossiê — estrutura em 5 abas** (Dados, Contatos, Análise IA, Mensagens, Insights): reaproveitável 100%, só os *campos* dentro da aba "Dados" mudam por nicho (pra Minotto: seção de dívida PGFN; pra Inova: provavelmente seção de crédito rural/Sicor).

**Dashboard:** KPIs + Consumo do Plano + Funil de Conversão + Motivos de Perda + Ações Recomendadas + Simulador de Receita — tudo genérico, não depende de nicho.

**Sistema de design:** tema claro, navy + dourado discreto, ancorado na marca real de cada cliente (não numa identidade genérica) — vale fazer esse levantamento de marca com o cliente novo também (site institucional dele, paleta real).

---

## 5. Padrão de Deploy (EasyPanel)

**5 serviços por cliente, projeto isolado no EasyPanel:**
```
hunterpro-[cliente]-db        (postgres)
hunterpro-[cliente]-redis     (redis)
hunterpro-[cliente]-backend   (app)
hunterpro-[cliente]-worker    (app — MESMA imagem do backend, comando de start diferente)
hunterpro-[cliente]-frontend  (app)
```

**Comando de start do worker** (configurado no painel, não no Dockerfile):
```
celery -A app.workers.celery_app worker --loglevel=info
```

**Peças de infraestrutura que precisam existir desde o Dockerfile:**
- `wait_for_db.py` — retry com backoff antes do `uvicorn`/`celery` subir (evita corrida de boot)
- `entrypoint.sh` — roda o wait antes do comando real, `exec "$@"` no final (processo real vira PID 1)
- `nginx.conf.template` (frontend) — proxy `/api/*` pro backend interno + **fallback de SPA** (`try_files`) + rota separada pra `/health` + timeout generoso (300s, geração por IA estoura os 60s padrão)
- `.dockerignore` nos dois projetos (backend e frontend) — sem isso, `.venv`/`node_modules`/`.env` de desenvolvimento vazam pra dentro da imagem publicada
- `api.ts` (frontend) — detecta ambiente automaticamente:
  ```ts
  const API_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.PROD ? "" : "http://localhost:8000");
  ```
  ⚠️ **Em produção o valor é string VAZIA, não `"/api"`.** Cada chamada já
  escreve o prefixo (`fetch(\`${API_URL}/api/leads\`)`), então usar `"/api"`
  aqui geraria `/api/api/leads` e quebraria a aplicação inteira. O
  `/health` é a exceção que confirma a regra: é chamado como
  `${API_URL}/health`, sem o prefixo — por isso o nginx precisa da regra
  `location = /health` separada.
  ⚠️ Variável do Vite é assada no bundle em tempo de **build**, não lida em
  runtime: definir `VITE_API_URL` no painel do EasyPanel depois do build não
  tem efeito nenhum.

**Volume persistente** — crítico pras fontes de dado em lote (Receita/PGFN/etc): sem isso, os arquivos baixados **somem no próximo redeploy** (disco efêmero por padrão no Docker). Monta em `/app/data`, caminho dentro do container.

**Variáveis de ambiente do worker e backend** (mesmo conjunto nos dois):
```
DATABASE_URL=postgresql://... (nunca postgres://, o SQLAlchemy exige o sql)
REDIS_URL=redis://.../0
CELERY_BROKER_URL=redis://.../0
CELERY_RESULT_BACKEND=redis://.../1   ← índice DIFERENTE do broker
SECRET_KEY=(gerada nova, forte — nunca a de dev)
FRONTEND_ORIGIN=https://[dominio-do-cliente]
GOOGLE_API_KEY, HUNTER_API_KEY, ZEROBOUNCE_API_KEY, ANTHROPIC_API_KEY, FIRECRAWL_API_KEY
EVOLUTION_URL, EVOLUTION_KEY, EVOLUTION_INSTANCE  (reaproveitados entre clientes, instância própria por cliente)
[NOME_DA_FONTE_GRATUITA]_DADOS_ABERTOS_DIR=/app/data/[pasta]  (uma por fonte em lote do nicho)

# --- Volume e custo ---
LEADS_POR_BUSCA=50            (o volume contratado do cliente)
LEADS_MARGEM_PRE_SELECAO=1.2  (é daqui que sai a "margem de ~20%" da §3:
                               50 x 1.2 = 60 candidatos pra entregar 50)
PLANO_LEADS_MES=50            (só rotula o "Consumo do Plano" no Dashboard;
                               NÃO limita nada — quem corta volume é a
                               pré-seleção)
LIMITE_GERACOES_IA_POR_LEAD=2 (gerações de IA por lead POR TIPO; 0 desliga)
HUNTER_DOMAIN_SEARCH_FALLBACK=false  (liga o fallback domain-search do
                               Hunter; DOBRA o consumo de crédito — só
                               ligar depois de confirmar o plano)

# --- Segurança ---
LOGIN_MAX_TENTATIVAS=5             (0 desliga o rate limit)
LOGIN_JANELA_BLOQUEIO_MINUTOS=15
```

⚠️ **Convenção de "desligar": `0` (ou negativo) LIBERA, não bloqueia.**
Vale pra `LIMITE_GERACOES_IA_POR_LEAD` e `LOGIN_MAX_TENTATIVAS`. Uma
config zerada por engano deve soltar o produto, não trancar todo mundo
pra fora.

⚠️ **Duas variáveis que existem no `config.py` do Minotto e NÃO devem ser
copiadas às cegas:**
- `ENVIRONMENT` — existe, mas **não é usada em lugar nenhum do código**.
  Não há chaveamento por ambiente. O comportamento seguro em produção
  (FastAPI não devolve traceback em 500) acontece por ser o padrão do
  Starlette, não por decisão nossa. Se o próximo projeto quiser desabilitar
  `/docs` e `/openapi.json` em produção — que hoje ficam **públicos** —, é
  aqui que se usa essa variável.
- `SERP_API_KEY` — **config morta**: declarada e nunca lida por nenhum
  módulo. Sobrou de uma fonte de dado que não foi implementada. Não copiar.

**DNS:** registro tipo A apontando pro IP do Droplet, subdomínio próprio por cliente (`[cliente].4handsai.com.br`), SSL automático via Let's Encrypt dentro do EasyPanel.

---

## 6. Lições Aprendidas — Armadilhas Reais (a parte mais valiosa deste documento)

Cada item abaixo foi um bug real, encontrado testando contra dado de verdade — não teoria.

### Sobre pré-seleção e custo
- **Nunca gastar API sem um corte de volume antes.** Sem pré-seleção, uma busca nacional processaria centenas de milhares de CNPJs.
- **Um critério binário de peso alto pode "sequestrar" a seleção** se estiver no mesmo score que o critério principal do cliente. Solução: fases separadas, não score único.
- **Trava de segurança se o índice da fonte gratuita não foi populado** (ex: PGFN nunca rodou `refresh_index`) — aborta ANTES de gastar dinheiro, não depois.

### Sobre parsing de arquivo em lote
- **Nomes de arquivo real ≠ nomes assumidos.** Sempre confirmar contra o arquivo baixado de verdade antes de escrever o parser. Aconteceram **dois bugs distintos** dessa família no Minotto, e vale distinguir porque a correção de um não previne o outro:
  - **Case-sensitivity (Receita Federal):** o glob procurava `*ESTABELE*` (maiúsculo, nome do CSV extraído) e o arquivo baixado era `Estabelecimentos0.zip` (capitalizado). Nenhum casava.
  - **Padrão de nome incompleto (PGFN):** o finder só reconhecia `*.csv`, e os arquivos publicados são `Dados_abertos_*.zip`. Não era questão de caixa — o padrão simplesmente não previa o formato `.zip`.
  Nos dois casos o sintoma foi o **mesmo e silencioso**: `{"status": "sem_arquivos"}`, que a orquestração tratava como "concluído com 0 leads". Busca "bem-sucedida", zero resultado, nenhuma pista.
- **Ler direto do `.zip` (streaming) em vez de extrair** — evita duplicar o espaço em disco (zip + extraído ao mesmo tempo) e evita o container ficar sem espaço no meio de um processamento.
- **Formato de número pode ser americano (ponto decimal) mesmo em fonte brasileira** — não presumir vírgula decimal sem checar.
- **Filtro de "situação/status" precisa ser checado contra os valores REAIS do arquivo**, não adivinhados — um filtro que nunca bate com nenhum valor real é um no-op silencioso (aconteceu com "EXTINTA" que nunca aparece nos dados abertos da PGFN, porque a base só publica inscrição ativa).
- **Deduplicação por chave de negócio (CNPJ), nunca por posição/formato de arquivo** — evita contar duas vezes se o mesmo dado existir em `.zip` e extraído na mesma pasta.

### Sobre memória em processamento de arquivo grande
- **Medir de verdade contra o arquivo real antes de assumir que "está ok"** — um teste local com dado sintético pequeno não prova nada sobre o pico de memória real.
- **Dataclasses sem `__slots__` custam caro em escala** (cada instância carrega um `__dict__` próprio) — considerar `@dataclass(slots=True)` pra índices com milhões de entradas.
- **Remover campos acumulados que nunca são lidos** — um campo esquecido "só por garantia" pode custar centenas de MB sem servir pra nada.
- **Pipeline de escrita no Redis/banco precisa de batching**, nunca empilhar milhões de comandos antes de executar de uma vez.
- **Somar `getsizeof` erra o custo real — e erra pros DOIS lados, por motivos opostos.** Não são a mesma coisa, e confundi-los leva a estimativas ruins nas duas direções:
  - **Fragmentação do alocador faz `getsizeof` SUBESTIMAR.** Alocar milhões de objetos pequenos intercalados fragmenta as arenas do pymalloc, e memória liberada não volta pro SO. No Minotto, `getsizeof` contabilizava 724 MB nas listas removidas; a economia real medida foi **2.681 MB** — ~3,7x mais.
  - **Chaves compartilhadas do `__dict__` (PEP 412) fazem uma soma ingênua por instância SUPERESTIMAR.** As strings das chaves de atributo existem **uma vez por classe**, não por instância. Contá-las por instância inflou uma estimativa de 1,5 GB pra 3,3 GB — errei pro dobro, na direção contrária.
  - **Conclusão prática:** medir o pico real (`VmHWM` via `/proc/self/status`, amostrado durante a execução) é o único número confiável. `getsizeof` serve pra saber *onde* está o custo, não *quanto* ele é.

### Sobre o worker do Celery (recorrente, 3 vezes seguidas nesse projeto)
- **O worker só "conhece" os modelos SQLAlchemy que forem importados naquele processo especificamente** — se `uvicorn` importa por acidente (via outras rotas) mas o `celery worker` não, o worker quebra com `NoReferencedTableError` mesmo o banco estando correto. Solução: `app/models/__init__.py` como ponto único de registro, importado explicitamente em todos os entrypoints (main.py, celery_app.py, alembic/env.py, scripts).
- **"Código no GitHub" ≠ "código rodando no worker".** Python carrega módulos na inicialização do processo — editar um arquivo depois não afeta um processo já rodando. **Sempre reiniciar o worker depois de qualquer deploy**, e implementar um detector automático (comparar mtime dos arquivos com o horário de início do processo, avisar em `task_prerun`) pra parar de perder tempo investigando "bugs" que são só processo desatualizado.
- **Task chamada direto no console/terminal morre se a sessão cair.** Usar `.delay()` (enfileira via Redis) em vez de chamar a função solta.
- ⚠️ **GAP CONHECIDO — `.delay()` NÃO garante que a task sobrevive à morte do worker.** É tentador supor que sim; não é o caso com a configuração atual. `celery_app.conf.update(...)` no Minotto define só serialização e timezone — **`task_acks_late` não está configurado**, e o padrão do Celery é `False`: a mensagem é confirmada (ack) **antes** de a task executar. Se o worker morrer no meio (redeploy, OOM, `SIGKILL`), **a task é perdida**, não volta pra fila. Numa busca mensal de 15+ minutos, isso é uma janela real.
  Opção pro próximo projeto: `task_acks_late=True` (+ `task_reject_on_worker_lost=True`). **Ressalva importante:** com isso a task pode ser **executada duas vezes** se o worker cair depois de já ter gasto API — ou seja, exige que a task seja idempotente. No Minotto, `process_lead_pipeline` faz upsert por CNPJ (idempotente na persistência), mas as chamadas pagas seriam refeitas. Decidir com o custo em mente, não por reflexo.

### Sobre qualidade de dado real (só aparece testando com CNPJ de verdade)
- **Google Places pode devolver um perfil de rede social (Instagram) como "website"** quando a empresa não tem site próprio — se isso não for filtrado, contamina a etapa seguinte (ex: Hunter.io gera um e-mail "válido" pro domínio errado, tipo `nome@instagram.com`).
- **Sempre validar domínio antes de aceitar e-mail gerado por IA/heurística** — bloquear lista conhecida de domínios de plataforma (redes sociais, agregadores de link, diretórios, mensageria).
- **Telefone "principal" de empresa (Google Places) costuma ser fixo/central, não celular** — não confiar só nele pra validar WhatsApp. Extrair link `wa.me`/`api.whatsapp.com` direto do conteúdo raspado do site é uma fonte melhor, e sem custo adicional (reaproveita o mesmo scrape).
- **Distinguir "sinal ausente porque não existe" de "sinal ausente porque não conseguimos ler"** — um campo vazio no dossiê pode significar duas coisas opostas comercialmente. Persistir um booleano de "sucesso da leitura" ao lado de cada fonte não confiável evita ambiguidade.
- **Separar nome completo em nome/sobrenome pra APIs de e-mail precisa tratar nomes brasileiros longos** — pegar só o último token como sobrenome, não a string inteira restante.

### Sobre segurança
- **Nunca colocar credencial em query string** — sempre header (`Authorization: Bearer`). Se a API não suportar alternativa, documentar isso como risco aceito.
- **Nunca deixar exceção HTTP crua virar log** — ela pode conter a URL completa com a chave. Ter uma função central de redação de segredo (por nome de variável E por padrão de connection string com senha embutida).
- **Timing attack em login é real e simples de introduzir sem querer** — um `if usuario is None or not verify_password(...)` com curto-circuito faz "e-mail não existe" responder muito mais rápido que "senha errada", vazando quais e-mails têm conta. Sempre rodar o hash mesmo quando o usuário não existe (contra um hash dummy).
- **Rate limiting simples via Redis (INCR+EXPIRE) é suficiente**, não precisa de biblioteca externa se o Redis já está no projeto. Duas decisões que vêm junto:
  - **Reaplicar o `EXPIRE` a cada falha (não só na primeira) é deliberado**, não descuido: transforma a janela em "15 min de inatividade" em vez de "15 min desde a primeira tentativa" — quem continua martelando mantém o bloqueio de pé, que é o comportamento desejado contra força bruta.
  - **O trade-off é real e mordeu em produção:** o usuário legítimo que insiste também renova o próprio bloqueio. Combinado com uma mensagem de erro genérica, vira um ciclo vicioso — ele tenta de novo achando que é só senha errada, e cada tentativa afasta a liberação. **Por isso esta lição e a do 401-vs-429 (logo abaixo) são a mesma história:** se optar pela janela de inatividade, a mensagem de 429 na tela deixa de ser cosmética e passa a ser parte da correção.
  - **Falhar ABERTO se o Redis cair** (login continua, sem limite, com warning no log). Fechar trancaria todo mundo pra fora por instabilidade de cache.
- **Frontend precisa diferenciar 401 (credenciais erradas) de 429 (rate limit)** — um `catch` genérico que ignora o corpo da resposta e sempre mostra a mesma mensagem confunde o usuário real (achou que era só senha errada, quando na real precisava só esperar).
- **`.dockerignore` nos dois projetos, sempre** — sem isso, segredos de desenvolvimento (`.env`) e artefatos pesados (`node_modules`, `.venv`) vazam pra dentro da imagem publicada.

### Sobre arquitetura de resiliência e teste

- **Isolar cada etapa do pipeline num wrapper que engole exceção** (`_rodar_etapa` no Minotto). Uma etapa que falha vira `None` — "sem sinal pra esse critério" — e o pipeline segue até a persistência e o score. **É a decisão de resiliência mais importante do enriquecimento**: sem ela, um timeout do Firecrawl no 12º de 60 leads derrubaria a busca inteira e perderia os 11 já processados. Duas consequências que vêm junto e precisam ser tratadas de propósito:
  - O erro fica **invisível** se ninguém olhar o log. Toda etapa pulada/falhada deve registrar o **motivo** num campo que chegue à tela (no Minotto: `etapas_puladas` no retorno, e avisos agregados em `busca.erros`, renderizados no painel admin).
  - "Etapa devolveu `None`" e "etapa nem rodou" viram o mesmo estado se você não distinguir — ver a lição do booleano de sucesso de leitura, em "qualidade de dado real".

- 🔴 **Fixture `autouse` que bloqueia chamada externa nos testes — isso custa dinheiro de verdade.** Dois incidentes reais no Minotto:
  - um teste que mockou tudo **menos** `search_google_places` fez uma chamada real de ~21 s ao Google com a chave de produção;
  - um teste que esqueceu de mockar `enrich_email` **queimou um crédito** do plano Free do Hunter.io (50/mês).

  A correção não é "lembrar de mockar" — é uma fixture `autouse` por arquivo de teste que substitui **toda** etapa externa por um fake que levanta `AssertionError` se chamada. Quem precisa do caminho real sobrescreve depois (o `monkeypatch` é o mesmo, vale a última escrita). Confiar na memória de cada autor de teste é o modo de falha, não a solução.

- **Convenção de teste no frontend sem jsdom/RTL.** O Minotto não tem infra de teste de componente (decisão consciente: não foi adicionada preventivamente). A resposta foi **extrair toda regra de exibição pra um módulo puro e testá-lo** — 11 módulos hoje (`leadScore`, `insights`, `limitesIa`, `siteScrape`, `erroLogin`, `emailsSecundarios`, `format`…). Isso cobre a **lógica** (quando desabilitar o botão, qual mensagem mostrar, como ler um payload malformado) sem montar DOM. ⚠️ Ser honesto sobre o limite: a **renderização em si continua não testada**; validação visual fica com o humano.
  Corolário forte: `fetch().json()` é tipado `any`, então o TypeScript **não garante nada em runtime**. Todo payload que vira `.map()` ou leitura encadeada deve passar por um parser defensivo que devolve vazio/`null` em vez de quebrar a tela.

### Sobre integrar API de terceiro

- **Confirmar contrato contra RESPOSTA REAL, nunca contra documentação renderizada.** Repetiu-se três vezes no Minotto:
  - **CNES** — o Swagger é uma SPA que não dá pra raspar; o contrato real (paginação com teto próprio, CNPJ ignorado como filtro) só apareceu batendo no endpoint.
  - **Firecrawl** — a documentação promove v2, mas o único spec verificável publicado é v1. Ficou-se em v1 **de propósito**, com o motivo escrito no código.
  - **Hunter.io** — a sondagem de autenticação por header foi **inconclusiva sem chave válida** (a API devolve o mesmo erro pra chave errada e pra chave ausente). Só deu pra confirmar depois, com chave real.
  ⚠️ **Quando não der pra verificar, marcar explicitamente como "não verificado" no código** — e revisitar quando der. É diferente de "assumido e funcionando".

- **Guarda de configuração antes de toda chamada paga.** Chave ausente deve **pular a etapa com motivo visível**, não sair e tomar 401. No Minotto isso custou uma investigação inteira: 6 leads sem e-mail, 6 falhas silenciosas, nenhuma pista — a chave estava vazia e o 401 era engolido pelo `_rodar_etapa`. E a guarda de **domínio** (não consultar Hunter pra `instagram.com`) economiza crédito além de evitar dado sujo.

### Sobre custo de IA por usuário

- **Toda geração de IA clicável precisa de limite por entidade, não só por plano.** No Minotto os botões "Gerar mensagem"/"Gerar Insights" no dossiê eram clicáveis à vontade — cada clique é uma chamada paga. Com a tela indo pra usuários do cliente (`role=client`), virava custo sem teto. O padrão implementado: **N gerações por lead, por tipo** (429 quando estoura), com **reset só pelo admin** (quem é limitado não pode ser quem libera).
  Detalhes que valem replicar: **checar o limite ANTES de chamar a IA** (o ponto é não gastar); **falha da IA não consome cota** (um 502 que o usuário não viu não pode custar uma tentativa); e **o reset não apaga histórico** — usa marca d'água temporal, senão zerar o contador destruiria o registro de gerações que a própria tabela existe pra guardar.

### Sobre datas e fuso

- **Gravar timestamp em UTC e ler o relógio local é uma armadilha de diagnóstico.** O Minotto usa `agora_utc()` (naive-UTC) em todo `created_at`/`iniciado_em`, e a máquina de desenvolvimento é UTC−3. Numa investigação, li `13:23` do banco como hora local e concluí que uma busca tinha rodado **depois** de uma correção — quando na verdade rodou **11 minutos antes** dela existir. Isso desviou a análise inteira até alguém conferir `date` e o `lstart` do processo.
  Regra: ao correlacionar "quando isso rodou" com "quando o código mudou", **converter tudo pro mesmo fuso explicitamente** antes de concluir qualquer coisa.

### Sobre ambiente de desenvolvimento local
- **WSL2 reporta espaço "livre" do disco virtual dele, que pode não bater com o espaço real do Windows** — o disco virtual cresce dinamicamente dentro do C:; se o C: físico estiver cheio, operações de escrita travam mesmo o WSL "achando" que tem espaço.
- **Sempre manter 2-3 terminais abertos e dedicados**: um pro backend (`uvicorn`), um pro worker (`celery`), um livre pra comandos avulsos — nunca reaproveitar o terminal do backend/worker pra rodar outro comando (mata o processo).

---

## 7. Processo de Trabalho (como conduzir as sessões com o Claude Code)

1. **Nunca implementar direto** — sempre auditar/investigar primeiro quando o assunto for volume, custo ou segurança, reportar achado, só corrigir depois de confirmado com o humano.
2. **Testar localmente com volume BAIXO antes de qualquer coisa em produção** (ex: `LEADS_POR_BUSCA=5`) — pegou pelo menos 9 bugs reais que só apareceriam gastando dinheiro de verdade em produção.
3. **Sempre rodar a suíte de testes completa depois de cada mudança**, confirmar o número exato antes de seguir.
4. **Sempre confirmar `alembic current` vs `alembic heads`** depois de qualquer sessão que mexeu em modelo — esquecer de aplicar migration (local ou produção) foi a causa mais repetida de erro 500 nesse projeto.
   **Ritual de validação de migration** (usado nas 13 do Minotto, todas escritas à mão porque o banco real nem sempre está alcançável pro `--autogenerate`):
   ```
   initdb + pg_ctl numa porta alta, socket Unix só, data dir descartável
   alembic upgrade head        # todas em cadeia, na ordem
   alembic downgrade -1        # reverte SÓ a nova; conferir que as anteriores sobreviveram
   alembic upgrade head        # re-aplica limpo
   \d tabela                   # conferir tipo/nullable/default contra o modelo
   pg_ctl stop + rm -rf        # destruir o cluster
   ```
   ⚠️ O socket Unix do Postgres tem limite de **107 bytes** no caminho — um data dir aninhado fundo estoura isso. Usar `mktemp -d /tmp/pgsock_x.XXXXXX` só pro socket.
   **Regra de `server_default`:** usar quando a coluna é `NOT NULL` numa tabela já populada (é o que preenche as linhas existentes — sem ele o `ALTER` falha). **Não** usar quando `NULL` já é o estado semanticamente correto ("nunca rodou", "não sei ainda") — e cuidado: um default errado pode ser **ativamente enganoso**, tipo marcar `false` em "falhou ao ler o site" para todo lead que nunca teve site.
5. **Sempre reiniciar o worker depois de qualquer mudança de código**, local ou produção — nunca assumir que reimplantar via painel reinicia sozinho sem confirmar.
6. **Nunca colar valor de chave/senha real no chat** — usar métodos seguros (contagem de caracteres via `awk`, substituição via `sed`, ou digitar direto no console sem colar).
7. **Delegar validação de custo/plano de terceiros pro humano decidir** (ex: upgrade de plano da Hunter.io) — é decisão de negócio, não técnica.
8. **Documentar decisões arquiteturais no próprio código/README**, principalmente as que "parecem" simplificáveis mas não são (ex: por que a pré-seleção é em 2 fases, não score único).

---

## 8. Checklist para Novo Cliente (Inova/Carol)

### Descoberta (com o cliente)
- [ ] Nicho e CNAE(s) alvo
- [ ] Região (nacional ou restrita)
- [ ] Qual é o critério #1 de priorização do cliente (equivalente à "dívida ativa" do Minotto)
- [ ] Fontes de dado gratuitas específicas do nicho (pra Inova: Sicor, RADAR, CAR/SICAR — já mapeadas na documentação do projeto Inova)
- [ ] Volume de leads/mês contratado
- [ ] Identidade visual real do cliente (site institucional, paleta)

### Arquitetura (reaproveitar 100%)
- [ ] Clonar a estrutura de módulos (services, scoring, workers) do projeto Minotto
- [ ] Trocar o parser de fonte gratuita específica (PGFN/CNES → Sicor/RADAR/CAR-SICAR)
- [ ] Recalibrar `SCORING_CRITERIA` com os pesos negociados com a Inova (soma = 100, sempre)
- [ ] Reimplementar a pré-seleção em 2 fases, com o critério #1 do cliente na Fase 1
- [ ] Ajustar campos específicos do dossiê (aba Dados) pro novo nicho

### Deploy
- [ ] Criar projeto novo isolado no EasyPanel (5 serviços)
- [ ] Configurar DNS + SSL
- [ ] Configurar volume persistente
- [ ] **Medir o pico de memória real contra o ARQUIVO REAL antes de dimensionar a VPS** — não assumir com base em teste local com dado sintético. No Minotto o teste local media ~15 MB; o arquivo de verdade (45,5 milhões de linhas) deu pico de **4,74 GB** e o processo era morto por OOM numa VPS com 4,2 GB livres. Depois de corrigir dois acumuladores (campo nunca lido + pipeline de escrita sem batching) o pico caiu pra **1,5 GB** — ou seja, o problema era o código, não o tamanho da VPS. Método: amostrar `VmHWM`/`VmRSS` de `/proc/self/status` numa thread durante a execução, não `getrusage` no final.
- [ ] Confirmar plano dos serviços pagos compartilhados (Hunter.io, Evolution) comporta o volume combinado dos dois clientes

### Antes de liberar acesso ao cliente
- [ ] Teste local com volume baixo primeiro, sempre
- [ ] Auditoria de segurança (mesmo checklist: timing attack, rate limit, CORS, segredos em log, autorização de rotas)
- [ ] Confirmar `SECRET_KEY` de produção é forte e diferente da de desenvolvimento
- [ ] Criar usuário do cliente como `role=client`, testar login e confirmar que "Busca de Leads" não aparece pra ele

---

*Documento gerado a partir da jornada completa do projeto HunterPro Minotto — da primeira reunião de kickoff até a primeira execução real em produção com 60 leads processados com sucesso.*
