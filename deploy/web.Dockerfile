FROM node:22.23.3-bookworm-slim AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/src ./src
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
ENV VITE_API_BASE_URL=/api/v1
RUN npm run build
FROM caddy:2.11.7-alpine
COPY --from=build /src/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
