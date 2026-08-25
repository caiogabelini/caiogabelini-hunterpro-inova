"""Fontes de dado externas — uma por módulo.

Padrão de cada módulo daqui (seção 3 do docs_fundacao.md):

1. Cliente HTTP injetável (facilita teste com mock)
2. Dataclass de resultado tipado
3. Nunca lança exceção pro chamador — falha vira ``None``/resultado vazio,
   com o motivo registrado, e logado
4. Fonte com arquivo bruto: parser lê direto do ``.gz``/``.zip`` em streaming
5. Fonte paga: guarda de configuração (pula com motivo claro se faltar a chave)

Implementados:

- ``sicor`` — Sicor/Bacen, fonte gratuita e específica do nicho (crédito
  rural). Arquivo em lote, não API: o ponto 1 não se aplica (não há cliente
  HTTP; a leitura é de arquivo local). Os pontos 2, 3 e 4 valem integralmente.
- ``arquivo_utils`` — **não é fonte de dado**. É a infra compartilhada de
  leitura em streaming (``.gz``/``.zip``/``.csv``), usada por todo parser de
  arquivo em lote.

Ainda não implementados:

- RADAR (Receita Federal) — **bloqueado**: é formulário ASP e os parâmetros do
  POST seguem desconhecidos. Baixa prioridade: o critério ``radar_exportacao``
  está com peso 0 a pedido da cliente.
- Ponte CAR/SICAR — **descartada por decisão de arquitetura**: a área do imóvel
  já vem em ``VL_AREA_INFORMADA``, direto da tabela de operação do Sicor
  (99,7% preenchida no PR). O ``CD_CAR`` continua sendo extraído e fica no
  dossiê como dado bônus, sem enriquecimento em cima dele.
- Fontes padrão reaproveitáveis (Receita Federal, Google Places, Firecrawl,
  Hunter.io, ZeroBounce, Evolution) — fases seguintes.
"""
