# syntax=docker/dockerfile:1
# Production image: build the SPA, serve it from nginx (deploy/nginx/cawp.conf adds TLS and
# proxies /api -> the api service with the prefix stripped, like vite.config.ts does in dev).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Same-origin API: the browser calls https://<host>/api/..., nginx forwards to api:9797.
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80 443
