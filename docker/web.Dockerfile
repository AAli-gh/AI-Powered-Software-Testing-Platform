# TestMind dashboard: built with Node, served by an unprivileged nginx that also proxies the
# API, so the browser talks to one origin (no CORS). Build context: ./web
#   docker build -f docker/web.Dockerfile -t testmind-web:local web

# ------------------------------------------------------------------ build
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ------------------------------------------------------------------ serve
FROM nginxinc/nginx-unprivileged:1.29-alpine AS web
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
