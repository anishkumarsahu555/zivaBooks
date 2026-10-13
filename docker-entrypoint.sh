#!/usr/bin/env bash
# ==============================================================================
# zivaBooks — Container Entrypoint Script
# ==============================================================================
set -eo pipefail

echo "[ENTRYPOINT] Initializing zivaBooks container environment..."

# ------------------------------------------------------------------------------
# 1. Wait for PostgreSQL Database (if configured)
# ------------------------------------------------------------------------------
if [ "$DATABASE_ENGINE" = "postgres" ] || [ -n "$POSTGRES_HOST" ]; then
    DB_HOST="${POSTGRES_HOST:-postgres}"
    DB_PORT="${POSTGRES_PORT:-5432}"
    DB_USER="${POSTGRES_USER:-postgres}"

    echo "[ENTRYPOINT] Waiting for PostgreSQL database at ${DB_HOST}:${DB_PORT}..."
    RETRIES=30
    until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1 || [ $RETRIES -eq 0 ]; do
        echo "[ENTRYPOINT] Database not ready yet. Waiting 2 seconds... ($RETRIES attempts remaining)"
        RETRIES=$((RETRIES - 1))
        sleep 2
    done

    if [ $RETRIES -eq 0 ]; then
        echo "[ENTRYPOINT] ERROR: Timed out waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}."
        exit 1
    fi
    echo "[ENTRYPOINT] PostgreSQL is ready and accepting connections."
fi

# ------------------------------------------------------------------------------
# 2. Database Migrations & Static Files (Web Server Only)
# ------------------------------------------------------------------------------
# Static files are pre-compiled at build time into /app/staticfiles.
# Migrations are executed by the dedicated 'migrate' container in Docker Compose,
# but can run here as a fallback if SKIP_MIGRATIONS is not set to true.
if [[ "$*" == *"uvicorn"* ]] || [[ "$*" == *"runserver"* ]] || [[ "$*" == *"gunicorn"* ]]; then
    if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
        echo "[ENTRYPOINT] Executing database migrations..."
        uv run python manage.py migrate --noinput
    else
        echo "[ENTRYPOINT] SKIP_MIGRATIONS=true: Schema migrations managed by external runner."
    fi

    if [ "${FORCE_COLLECTSTATIC:-false}" = "true" ]; then
        echo "[ENTRYPOINT] FORCE_COLLECTSTATIC=true: Re-collecting static files..."
        uv run python manage.py collectstatic --noinput
    fi

    # Optional automatic role & permission seeding on initial launch
    if [ "${AUTO_SEED:-false}" = "true" ]; then
        echo "[ENTRYPOINT] AUTO_SEED=true: Seeding system roles & permissions..."
        uv run python manage.py seed_roles_permissions || true
    fi
fi

# ------------------------------------------------------------------------------
# 3. Hand over execution to container command
# ------------------------------------------------------------------------------
echo "[ENTRYPOINT] Starting container process: $*"
exec "$@"
