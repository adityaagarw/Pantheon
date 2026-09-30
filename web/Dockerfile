# Pantheon frontend (Next.js). Build from the repo root (see docker-compose.yml).
FROM node:22-alpine AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY web/ .
# The /api proxy target is resolved at build time; the browser's WebSocket
# goes straight to the backend's published port.
ARG PANTHEON_BACKEND_URL=http://backend:8710
ARG NEXT_PUBLIC_PANTHEON_BACKEND_PORT=8710
ARG NEXT_PUBLIC_PANTHEON_WEB_PORT=3710
ARG NEXT_PUBLIC_PANTHEON_DESKTOP_PORT=6901
ENV PANTHEON_BACKEND_URL=$PANTHEON_BACKEND_URL \
    NEXT_PUBLIC_PANTHEON_BACKEND_PORT=$NEXT_PUBLIC_PANTHEON_BACKEND_PORT \
    NEXT_PUBLIC_PANTHEON_WEB_PORT=$NEXT_PUBLIC_PANTHEON_WEB_PORT \
    NEXT_PUBLIC_PANTHEON_DESKTOP_PORT=$NEXT_PUBLIC_PANTHEON_DESKTOP_PORT \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app ./
EXPOSE 3710
CMD ["npm", "run", "start"]
