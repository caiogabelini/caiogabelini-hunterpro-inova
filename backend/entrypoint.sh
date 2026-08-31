#!/bin/sh
#
# Entrypoint compartilhado pelos DOIS serviços que usam esta imagem no
# EasyPanel: o backend (uvicorn) e o worker (celery). A imagem é a mesma; o
# que muda é só o comando de start, configurado no painel por serviço.
#
# ## Por que ele NÃO decide qual processo subir
#
# A tentação é ler uma variável tipo `SERVICO=worker` e ramificar aqui. O
# Minotto não faz isso, e a razão é boa: o comando real já chega como
# argumento (`"$@"`), vindo do CMD do Dockerfile no backend e do "Start
# Command" do painel no worker. Ramificar aqui criaria uma segunda fonte de
# verdade sobre o que cada serviço roda — e um dia elas discordariam, com o
# painel dizendo uma coisa e o script fazendo outra.
#
# Este script tem uma responsabilidade só: **preparar o ambiente e sair da
# frente.**
#
# ## `exec "$@"`
#
# Substitui o shell pelo processo final em vez de deixá-lo como filho. Em
# container isso importa: o processo real vira PID 1 e recebe SIGTERM direto
# do Docker no shutdown, permitindo que o uvicorn encerre as conexões e o
# celery termine a task em andamento. Sem o `exec`, o sinal pararia no shell
# e o processo levaria SIGKILL no fim do grace period.
#
set -e

# Espera o Postgres responder. Os dois serviços falam com o banco, então os
# dois se beneficiam — por isso a espera vive aqui, e não embutida no CMD.
python scripts/wait_for_db.py

# ⚠️ **Migrations são OPT-IN e vêm desligadas.**
#
# O Minotto não roda migration nenhuma no entrypoint — lá o `alembic upgrade
# head` é passo manual de deploy. Mantive esse comportamento como PADRÃO, e
# deixei o automático disponível atrás de uma variável, por dois motivos:
#
# 1. **Duas instâncias sobem desta mesma imagem.** Se backend e worker
#    subissem migrando ao mesmo tempo, as duas disputariam a tabela de versão
#    do Alembic. O Postgres serializa (uma espera a outra), mas o resultado é
#    um boot mais lento e um modo de falha que só aparece em deploy
#    concorrente. Por isso: ligue `RODAR_MIGRATIONS=1` **só no serviço do
#    backend**, nunca no worker.
#
# 2. **Migration automática tira a decisão de quem deploya.** Uma migration
#    ruim derruba a aplicação sozinha, no boot, sem ninguém ter olhado. Com
#    isto desligado, o schema só muda quando você mandar.
#
# Rodando manualmente (recomendado, é o padrão do Minotto):
#   docker exec -it <container-do-backend> alembic upgrade head
if [ "${RODAR_MIGRATIONS:-0}" = "1" ]; then
    echo "entrypoint: RODAR_MIGRATIONS=1 — aplicando alembic upgrade head"
    alembic upgrade head
fi

exec "$@"
