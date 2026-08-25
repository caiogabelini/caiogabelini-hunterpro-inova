"""Fontes de dado externas — uma por módulo.

Fase 1 não tem nenhuma fonte implementada. O padrão de cada módulo daqui
(seção 3 do docs_fundacao.md) é sempre o mesmo:

1. Cliente HTTP injetável (facilita teste com mock)
2. Dataclass de resultado tipado
3. Nunca lança exceção pro chamador — falha vira ``None``/dict vazio, logado
4. Fonte com arquivo bruto: parser lê direto do ``.zip`` em streaming
5. Fonte paga: guarda de configuração (pula com motivo claro se faltar a chave)

Fontes previstas pro nicho da Inova (agronegócio/grãos, foco Paraná):
Sicor, RADAR e CAR/SICAR — Fase 3.
"""
