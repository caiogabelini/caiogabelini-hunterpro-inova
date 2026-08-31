# Imagem ÚNICA, usada por dois App Services diferentes no EasyPanel:
#   - backend: usa o CMD padrão abaixo (uvicorn)
#   - worker:  sobrescreve o comando no campo "Start Command" do painel
#              (celery -A app.workers.celery_app worker --loglevel=info)
#
# Não existe Dockerfile separado pro worker — é a mesma imagem, e o que muda
# é só o comando de start. Padrão herdado do Minotto (backend/Dockerfile lá).
FROM python:3.12-slim

WORKDIR /app

# ⚠️ **Sem gcc/libpq-dev, diferente do Minotto.**
#
# Lá a linha `apt-get install gcc libpq-dev` existe porque alguma dependência
# precisava compilar. Aqui não precisa: conferi os 60 pacotes de
# `requirements.lock` com `pip download --only-binary=:all:` e **todos têm
# wheel pronta** para cp312/manylinux — inclusive o `bcrypt==3.2.2`, que
# apesar de antigo publica wheel `abi3` compatível com o Python 3.12.
#
# O ganho não é só tamanho: compilador dentro de imagem de produção é
# superfície de ataque que não precisa existir.
#
# Se um dia uma dependência nova exigir compilação, o `pip install` abaixo
# falha com "error: command 'gcc' failed" — e o conserto é descomentar:
#
#   RUN apt-get update && apt-get install -y --no-install-recommends \
#       gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# ⚠️ Instala do `.lock`, não do `requirements.txt`.
#
# O `.txt` declara as 15 dependências DIRETAS; o `.lock` fixa as 60 (com as
# transitivas). Instalar do `.txt` deixaria as transitivas flutuando e a
# imagem publicada deixaria de ser o que passou nos testes — que é exatamente
# o achado M-3 da auditoria de 31/08/2026.
#
# Copiado sozinho, antes do resto: só muda quando uma dependência muda, então
# a camada de `pip install` é reaproveitada em todo build que só mexeu em
# código.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

# chmod explícito em vez de confiar no bit de execução vindo do COPY: o modo
# do arquivo depende de como o contexto de build chegou (git preserva o bit,
# um upload/zip nem sempre). Sem isso o container morre no boot com
# "permission denied".
RUN chmod +x /app/entrypoint.sh

# ⚠️ **Usuário não-root.** Diferente do Minotto, que roda como root.
#
# Se alguém escapar do processo (uma RCE numa dependência, por exemplo), o
# que ele encontra do outro lado é uma conta sem privilégio nenhum em vez de
# root no container — sem apt-get, sem escrita em /usr, sem chance de instalar
# ferramenta.
#
# Só foi possível em 3 linhas porque a aplicação **não escreve em disco em
# runtime**: os arquivos do Sicor/Receita são colocados de fora (ver
# `receita_federal.py`, "download é externo"), o log vai pro stdout e a porta
# é 8000 (acima de 1024, não exige privilégio). Conferido antes de escrever
# isto, não suposto.
#
# `chown -R` depois do COPY: sem ele o `/app` fica de root e o Python não
# consegue gravar os `__pycache__`, o que não quebra nada (ele engole o erro)
# mas deixa todo import mais lento. UID alto e explícito pra não colidir com
# usuário do host se o volume for bind mount.
#
# ⚠️ O volume de `/app/data` é montado de fora e os arquivos em lote precisam
# ser LEGÍVEIS por este usuário. Se forem copiados pra lá como root com modo
# restritivo (600), a busca falha ao abrir o arquivo — como root isso passava
# despercebido. `chmod 644` neles ao subir resolve.
RUN useradd --system --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ENTRYPOINT + CMD, e não só CMD: o "Start Command" do EasyPanel é passado
# como CMD (argumentos do ENTRYPOINT), então o worker também passa pela espera
# do banco sem precisar repetir nada na configuração do painel.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
