#!/usr/bin/env bash
# ==============================================================================
# zivaBooks — Single Entrypoint Script for Docker Deployment
# ==============================================================================
set -eo pipefail

echo "[ENTRYPOINT] Initializing zivaBooks container..."

# ------------------------------------------------------------------------------
# 1. Wait for PostgreSQL Database (when configured)
# ------------------------------------------------------------------------------
if [ "$DATABASE_ENGINE" = "postgres" ] || [ -n "$POSTGRES_HOST" ]; then
    DB_HOST="${POSTGRES_HOST:-postgres}"
    DB_PORT="${POSTGRES_PORT:-5432}"
    DB_USER="${POSTGRES_USER:-postgres}"
    DB_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
    DB_NAME="${POSTGRES_DB:-zivaBooks}"

    echo "[ENTRYPOINT] Checking PostgreSQL connectivity at ${DB_HOST}:${DB_PORT}..."
    python - <<EOF
import os, sys, time

host = "${DB_HOST}"
port = int("${DB_PORT}")
user = "${DB_USER}"
password = "${DB_PASSWORD}"
dbname = "${DB_NAME}"

ready = False
last_error = None
for attempt in range(45):
    try:
        import psycopg
        try:
            conn = psycopg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
                connect_timeout=3
            )
        except Exception as db_err:
            conn = psycopg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname="postgres",
                connect_timeout=3
            )
        conn.close()
        ready = True
        break
    except Exception as e:
        last_error = e
        time.sleep(1)

if ready:
    print(f"[ENTRYPOINT] PostgreSQL is ready and accepting connections.")
    sys.exit(0)
else:
    print(f"[ENTRYPOINT] ERROR: Timed out waiting for PostgreSQL at {host}:{port} -> {last_error}")
    sys.exit(1)
EOF
fi

# ------------------------------------------------------------------------------
# 2. Database Migrations, Static Files & Setup (Web Server Only)
# ------------------------------------------------------------------------------
if [[ "$*" == *"uvicorn"* ]] || [[ "$*" == *"runserver"* ]] || [[ "$*" == *"gunicorn"* ]]; then
    if [ "${AUTO_MIGRATE:-true}" = "true" ] && [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
        echo "[ENTRYPOINT] Applying database migrations..."
        python manage.py migrate --noinput
    fi

    # Ensure static files exist
    if [ ! -d "/app/staticfiles" ] || [ -z "$(ls -A /app/staticfiles 2>/dev/null)" ] || [ "${FORCE_COLLECTSTATIC:-false}" = "true" ]; then
        echo "[ENTRYPOINT] Collecting static files..."
        python manage.py collectstatic --noinput
    fi

    # Seed roles and permissions
    if [ "${AUTO_SEED:-true}" = "true" ]; then
        echo "[ENTRYPOINT] Seeding roles & permissions..."
        python manage.py seed_roles_permissions || true
    fi
fi

# ------------------------------------------------------------------------------
# 3. Hand over execution to container command
# ------------------------------------------------------------------------------
echo "[ENTRYPOINT] Starting container process: $*"
exec "$@"
