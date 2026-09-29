# API + пайплайн + LibreOffice (рендер PNG/PDF для VLM-аудита). Модель — отдельный сервис vLLM (docker-compose).
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress fonts-dejavu fonts-liberation curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[docs]"
COPY . .
COPY --from=web /web/dist ./web/dist
RUN python scripts/fetch_fonts.py data/templates || true
ENV API_HOST=0.0.0.0 API_PORT=8080 DECKGEN_OUTPUTS=/data/outputs
EXPOSE 8080
CMD ["deckgen", "serve"]
