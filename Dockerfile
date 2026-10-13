# ==============================================================================
# zivaBooks — Production Multi-Stage Dockerfile
# ==============================================================================
# Base Python: 3.14-slim (with fallback support via ARG)
# Package Manager: uv (Astral)
# System Requirements: WeasyPrint (Cairo, Pango, GDK-Pixbuf), PostgreSQL client
# ==============================================================================

ARG PYTHON_VERSION=3.14-bookworm-slim
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION} AS base

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    APP_HOME=/app

WORKDIR ${APP_HOME}

# Install essential system libraries required by WeasyPrint, PostgreSQL, and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    # WeasyPrint / Cairo / Pango dependencies
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    fonts-liberation \
    # Database & Networking utilities
    postgresql-client \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Dependency Caching Layer
# ------------------------------------------------------------------------------
# Copy dependency manifests first to leverage Docker layer caching
COPY pyproject.toml uv.lock ./

# Install Python packages into isolated virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ------------------------------------------------------------------------------
# Application Source Layer
# ------------------------------------------------------------------------------
# Copy the full application codebase
COPY . ${APP_HOME}/

# Install application package metadata
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Pre-compile and compress static files at build time (WhiteNoise manifest)
RUN SECRET_KEY="build-dummy-key" DATABASE_ENGINE=sqlite \
    uv run python manage.py collectstatic --noinput --clear

# Ensure execution permissions for startup and entrypoint scripts
RUN chmod +x ${APP_HOME}/docker-entrypoint.sh ${APP_HOME}/start.sh

# Create persistent storage directories
RUN mkdir -p ${APP_HOME}/media ${APP_HOME}/staticfiles ${APP_HOME}/logs

# Expose internal ASGI/HTTP port
EXPOSE 8000

# Set entrypoint and default command (Uvicorn ASGI server)
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uv", "run", "uvicorn", "zivaBooks.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
