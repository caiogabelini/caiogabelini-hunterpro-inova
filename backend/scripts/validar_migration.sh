#!/usr/bin/env bash
# Ritual de validação de migration (seção 7 do docs_fundacao.md).
#
#   initdb + pg_ctl numa porta alta, socket Unix só, data dir descartável
#   alembic upgrade head     # todas em cadeia, na ordem
#   alembic downgrade -1     # reverte SÓ a nova; as anteriores sobrevivem
#   alembic upgrade head     # re-aplica limpo
#   \d tabela                # conferir tipo/nullable/default contra o model
#   pg_ctl stop + rm -rf     # destruir o cluster
#
# O socket Unix do Postgres tem limite de 107 bytes no caminho — por isso o
# diretório de socket é um `mktemp -d /tmp/pgsock_x.XXXXXX` separado do data
# dir, e nunca o próprio data dir (que é aninhado e estoura o limite).
set -euo pipefail

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PORTA="${PORTA:-55432}"
TABELAS="${TABELAS:-leads}"
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DATADIR="$(mktemp -d /tmp/hunterpro_pgdata.XXXXXX)"
SOCKDIR="$(mktemp -d /tmp/pgsock_x.XXXXXX)"

etapa() { printf '\n=== %s ===\n' "$*"; }

limpar() {
  etapa "ETAPA 7 — destruir o cluster"
  "$PGBIN/pg_ctl" -D "$DATADIR" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$DATADIR" "$SOCKDIR"
  echo "cluster destruído: $DATADIR / $SOCKDIR"
}
trap limpar EXIT

etapa "ETAPA 0 — initdb + pg_ctl (porta $PORTA, socket Unix descartável)"
"$PGBIN/initdb" -D "$DATADIR" -U postgres --encoding=UTF8 --no-sync >/dev/null
"$PGBIN/pg_ctl" -D "$DATADIR" -o "-p $PORTA -k $SOCKDIR -c listen_addresses=''" -w start
"$PGBIN/createdb" -h "$SOCKDIR" -p "$PORTA" -U postgres hunterpro_inova
echo "cluster no ar; socket=$SOCKDIR (${#SOCKDIR} bytes, limite 107)"

export DATABASE_URL="postgresql://postgres@/hunterpro_inova?host=$SOCKDIR&port=$PORTA"
cd "$BACKEND_DIR"
ALEMBIC="${ALEMBIC:-$BACKEND_DIR/.venv/bin/alembic}"

etapa "ETAPA 1 — alembic upgrade head"
"$ALEMBIC" upgrade head
"$ALEMBIC" current

etapa "ETAPA 2 — alembic downgrade -1"
"$ALEMBIC" downgrade -1
"$ALEMBIC" current

etapa "ETAPA 3 — alembic upgrade head (re-aplica limpo)"
"$ALEMBIC" upgrade head
"$ALEMBIC" current

etapa "ETAPA 4 — \\d das tabelas ($TABELAS) — conferir contra o model"
for t in $TABELAS; do
  "$PGBIN/psql" -h "$SOCKDIR" -p "$PORTA" -U postgres -d hunterpro_inova -c "\d $t"
done

etapa "ETAPA 5 — constraints mordem de verdade (INSERT cru, sem passar pelo model)"
PSQL=("$PGBIN/psql" -h "$SOCKDIR" -p "$PORTA" -U postgres -d hunterpro_inova -v ON_ERROR_STOP=1 -q -t)

espera_erro() {  # espera_erro "<rotulo>" "<sql>"
  if "${PSQL[@]}" -c "$2" >/dev/null 2>/tmp/pgerr.$$; then
    echo "FALHOU: $1 — o banco ACEITOU o que deveria rejeitar"; rm -f /tmp/pgerr.$$; exit 1
  fi
  echo "ok: $1 — rejeitado ($(grep -oE 'violates [a-z ]+constraint \"[a-z_]+\"' /tmp/pgerr.$$ | head -1))"
  rm -f /tmp/pgerr.$$
}

BASE="INSERT INTO leads (documento, tipo_documento, nome, created_at, updated_at) VALUES"
"${PSQL[@]}" -c "$BASE ('52998224725','CPF','Produtor PF',now(),now()), ('11222333000181','CNPJ','Agro Ltda',now(),now());"
echo "ok: CPF (11) e CNPJ (14) convivem na mesma tabela"
espera_erro "CPF duplicado"        "$BASE ('52998224725','CPF','Outra fonte',now(),now());"
espera_erro "CNPJ duplicado"       "$BASE ('11222333000181','CNPJ','Outra fonte',now(),now());"
espera_erro "CPF marcado as CNPJ"  "$BASE ('11144477735','CNPJ','Incoerente',now(),now());"
espera_erro "CNPJ marcado as CPF"  "$BASE ('19012345000193','CPF','Incoerente',now(),now());"
espera_erro "tipo fora do dominio" "$BASE ('52998224725','RG','Tipo invalido',now(),now());"

# --- Fase 8b: Kanban e registro de busca ------------------------------------
DEFAULT_KANBAN="$("${PSQL[@]}" -c "SELECT kanban_status FROM leads LIMIT 1;" | tr -d ' ')"
if [ "$DEFAULT_KANBAN" = "novo_lead" ]; then
  echo "ok: lead inserido sem kanban_status nasce em 'novo_lead' (server_default)"
else
  echo "FALHOU: kanban_status default veio '$DEFAULT_KANBAN', esperado 'novo_lead'"; exit 1
fi
espera_erro "kanban_status fora do funil" \
  "UPDATE leads SET kanban_status = 'coluna_inventada' WHERE documento = '52998224725';"
"${PSQL[@]}" -c "UPDATE leads SET kanban_status = 'ganho' WHERE documento = '52998224725';"
echo "ok: transição pra um status válido do funil é aceita"

espera_erro "busca com usuário inexistente (FK)" \
  "INSERT INTO buscas_leads (id, iniciado_por_id, iniciado_em, status) VALUES ('b1','fantasma',now(),'executando');"

"${PSQL[@]}" -c "DELETE FROM leads;" >/dev/null

etapa "ETAPA 6 — autogenerate deve estar vazio (model == banco)"
"$ALEMBIC" check
