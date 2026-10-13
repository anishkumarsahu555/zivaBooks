FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.14-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Native libraries and fonts used by WeasyPrint PDF reports.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    libjpeg62-turbo libopenjp2-7 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd --uid 10001 --create-home app \
    && mkdir -p media staticfiles logs \
    && chown -R app:app media staticfiles logs

FROM runtime-base AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM runtime-base AS development
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=dependencies /app/.venv /app/.venv
COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]

FROM development AS production-assets
RUN DEBUG=true DATABASE_ENGINE=sqlite USE_REDIS_CACHE=false \
    WRITE_LOG_ON_FILE=false python manage.py collectstatic --noinput
# Tests are retained in development, but not copied into the runtime image.
RUN find home inventory activation zivaBooks utils -type d -name tests -prune -exec rm -rf '{}' + \
    && find home inventory activation zivaBooks utils -type f -name 'test*.py' -delete

FROM runtime-base AS production
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=production-assets --chown=app:app /app/manage.py /app/manage.py
COPY --from=production-assets --chown=app:app /app/zivaBooks /app/zivaBooks
COPY --from=production-assets --chown=app:app /app/home /app/home
COPY --from=production-assets --chown=app:app /app/inventory /app/inventory
COPY --from=production-assets --chown=app:app /app/activation /app/activation
COPY --from=production-assets --chown=app:app /app/utils /app/utils
COPY --from=production-assets --chown=app:app /app/templates /app/templates
COPY --from=production-assets --chown=app:app /app/static /app/static
COPY --from=production-assets --chown=app:app /app/staticfiles /app/staticfiles
USER app
EXPOSE 8000
CMD ["uvicorn", "zivaBooks.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
