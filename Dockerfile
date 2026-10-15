# Multi-stage build for zivaBooks
FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.14-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Native libraries required by WeasyPrint (PDF generation), system fonts, and tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    libjpeg62-turbo libopenjp2-7 fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root system user and directories
RUN useradd --uid 10001 --create-home app \
    && mkdir -p media staticfiles logs \
    && chown -R app:app media staticfiles logs

FROM runtime-base AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM dependencies AS production-assets
COPY --chown=app:app . .
# Pre-compile static files into /app/staticfiles
RUN DEBUG=true DATABASE_ENGINE=sqlite USE_REDIS_CACHE=false \
    WRITE_LOG_ON_FILE=false python manage.py collectstatic --noinput

FROM runtime-base AS production
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=production-assets --chown=app:app /app /app

# Ensure directory ownership and executable entrypoint
RUN chown -R app:app /app && chmod +x /app/docker-entrypoint.sh


USER app
EXPOSE 8001

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "zivaBooks.asgi:application", "--host", "0.0.0.0", "--port", "8001", "--workers", "2", "--no-proxy-headers"]

