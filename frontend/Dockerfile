# syntax=docker/dockerfile:1
# Nubi frontend image — nginx serving the Vite production build.
# Proxies /api/* and /health to the backend service.
# Build context: repo root (docker build -f frontend/Dockerfile .)
#
# OPEN-CORE NOTE: The ee/ tree (src/ee/) is EXCLUDED from this image.
# This is the community/OSS image.  Commercial UI extensions are absent.

# ── Stage 1: Vite build ─────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app

# VITE_BACKEND_URL is empty for the compose stack (same-host proxied via nginx).
# Override at build time for hosted deployments where frontend and backend are
# on separate domains.
ARG VITE_BACKEND_URL=""
ENV VITE_BACKEND_URL=${VITE_BACKEND_URL}

# Install dependencies before copying source for better layer caching.
COPY package.json package-lock.json ./
RUN npm ci

# Copy source — ee/ is excluded via .dockerignore (OSS image).
COPY . .

# Production build.  Uses 'main' mode (.env.main) if present.
RUN npm run build

# ── Stage 2: nginx serving ───────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# Remove the default nginx config.
RUN rm /etc/nginx/conf.d/default.conf

# Our config.
COPY frontend/nginx.conf /etc/nginx/conf.d/nubi.conf

# SPA build artefacts.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
