FROM node:22-slim AS admin-build

WORKDIR /build/admin
COPY admin/package*.json ./
RUN npm ci
COPY admin ./
RUN npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md alembic.ini docker-entrypoint.sh ./
COPY src ./src
COPY migrations ./migrations
COPY --from=admin-build /build/static ./static
RUN pip install --no-cache-dir .
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
