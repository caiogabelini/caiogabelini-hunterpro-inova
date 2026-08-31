# --- Etapa 1: build do bundle ---
FROM node:22-slim AS build
WORKDIR /app

COPY package*.json ./
# `npm ci` em vez de `npm install`: instala exatamente o que está no
# package-lock.json e **falha** se os dois estiverem fora de sincronia —
# build de produção não deve resolver versões novas por conta própria.
#
# ⚠️ Isto só passou a ser possível na Fase 12: até a auditoria de 31/08/2026
# não existia `package-lock.json` no projeto (achado M-3), e `npm ci` teria
# quebrado com ENOLOCK. O lock está versionado agora — se este passo falhar
# com "can only install with an existing package-lock.json", é sinal de que
# alguém apagou o arquivo.
RUN npm ci

COPY . .

# VITE_API_URL fica DELIBERADAMENTE indefinido aqui.
#
# Variável do Vite é assada no bundle em tempo de build, não lida em runtime —
# então não adianta configurá-la no painel do EasyPanel depois. Com ela
# indefinida, o api.ts usa "" em build de produção, o que gera URLs relativas
# (/api/...) que o proxy do nginx desta mesma imagem resolve. Ver o comentário
# no topo de src/api.ts.
#
# Só defina VITE_API_URL (ex.: --build-arg) se o backend for servido em outro
# domínio — aí volta a existir CORS, o FRONTEND_ORIGIN do backend passa a ser
# o que faz a app funcionar (e não só defesa em profundidade), e o
# `connect-src` da CSP em seguranca.conf precisa liberar o novo domínio.
RUN npm run build

# --- Etapa 2: nginx servindo o build + proxy pra API ---
FROM nginx:alpine

# Nome/porta do App Service do backend dentro da rede interna do EasyPanel.
# São ENV (não valores fixos no .conf) pra poderem ser sobrescritos pelo
# painel sem rebuild da imagem — se o serviço tiver outro nome, basta ajustar
# BACKEND_HOST no EasyPanel.
ENV BACKEND_HOST=hunterpro-inova-backend
ENV BACKEND_PORT=8000

# A imagem oficial do nginx roda envsubst nos arquivos desta pasta no boot e
# escreve o resultado em /etc/nginx/conf.d/default.conf.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# ⚠️ Fora de /etc/nginx/templates/, de propósito: este arquivo NÃO passa pelo
# envsubst e não tem nenhuma variável pra substituir. Se estivesse em
# templates/, o `$` de uma futura diretiva do nginx viraria string vazia.
COPY seguranca.conf /etc/nginx/seguranca.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
