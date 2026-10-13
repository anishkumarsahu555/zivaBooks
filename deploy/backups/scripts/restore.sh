#!/usr/bin/env bash
# ==============================================================================
# /opt/backups/scripts/restore.sh
# ==============================================================================
# Disaster Recovery & System Restore Script for zivaBooks
# Usage:
#   ./restore.sh [path/to/archive.tar.gz] [--force]
# If no archive is provided, uses the most recent archive in /opt/backups/daily/
# ==============================================================================

set -eo pipefail

BACKUP_ROOT="/opt/backups"
DAILY_DIR="${BACKUP_ROOT}/daily"
ARCHIVE="$1"
FORCE_MODE=false

if [ "$1" = "--force" ] || [ "$2" = "--force" ]; then
    FORCE_MODE=true
fi

if [ -z "$ARCHIVE" ] || [ "$ARCHIVE" = "--force" ]; then
    ARCHIVE=$(find "${DAILY_DIR}" -maxdepth 1 -name "zivabooks_backup_*.tar.gz" | sort -r | head -n 1)
fi

if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "[RESTORE] ERROR: No valid backup archive found to restore."
    echo "Usage: $0 [/opt/backups/daily/zivabooks_backup_YYYYMMDD_HHMMSS.tar.gz]"
    exit 1
fi

echo "======================================================================"
echo "[RESTORE] Selected Backup Archive: ${ARCHIVE}"
echo "======================================================================"

# Verify checksum if present
if [ -f "${ARCHIVE}.sha256" ]; then
    echo "[RESTORE] Verifying archive checksum..."
    (cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "${ARCHIVE}.sha256")")
fi

if [ "$FORCE_MODE" = false ]; then
    echo -n "[RESTORE] WARNING: This will overwrite live database and media data! Proceed? (yes/no): "
    read -r CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "[RESTORE] Operation cancelled by user."
        exit 0
    fi
fi

STAGING_DIR="${BACKUP_ROOT}/restore_staging_$(date +%s)"
mkdir -p "${STAGING_DIR}"

echo "[RESTORE] Unpacking archive into staging directory..."
tar -xzf "${ARCHIVE}" -C "${STAGING_DIR}"

# ------------------------------------------------------------------------------
# 1. Stop Application Services
# ------------------------------------------------------------------------------
echo "[RESTORE] Halting web and worker containers..."
if [ -d "/opt/apps/zivabooks" ]; then
    (cd /opt/apps/zivabooks && docker compose stop web celery_worker celery_beat || true)
fi

# ------------------------------------------------------------------------------
# 2. Restore Environment Secrets (.env)
# ------------------------------------------------------------------------------
if [ -d "${STAGING_DIR}/env/opt/apps" ]; then
    echo "[RESTORE] Restoring environment files (.env)..."
    cp -r "${STAGING_DIR}/env/opt/apps/"* /opt/apps/
    chmod -R 600 /opt/apps/*/.env 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 3. Restore User Media Files
# ------------------------------------------------------------------------------
local data_root="${DATA_DIR:-/appData/zivabooks}"
if [ -d "${STAGING_DIR}/media" ]; then
    echo "[RESTORE] Restoring media files to ${data_root}/media/..."
    mkdir -p "${data_root}/media"
    cp -r "${STAGING_DIR}/media/"* "${data_root}/media/"
fi

# ------------------------------------------------------------------------------
# 4. Restore PostgreSQL Database
# ------------------------------------------------------------------------------
if [ -f "${STAGING_DIR}/db/zivaBooks.sql" ]; then
    echo "[RESTORE] Ensuring postgres container is running..."
    (cd /opt/apps/zivabooks && docker compose up -d postgres)

    # Source db credentials
    POSTGRES_USER=$(grep -E '^POSTGRES_USER=' /opt/apps/zivabooks/.env | cut -d '=' -f2- | tr -d ' "\r\n' || echo "postgres")
    POSTGRES_DB=$(grep -E '^POSTGRES_DB=' /opt/apps/zivabooks/.env | cut -d '=' -f2- | tr -d ' "\r\n' || echo "zivaBooks")

    echo "[RESTORE] Waiting for postgres to accept connections..."
    until docker exec zivabooks_postgres pg_isready -p 5433 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; do
        sleep 2
    done

    echo "[RESTORE] Importing SQL dump into PostgreSQL..."
    docker exec -i zivabooks_postgres psql -p 5433 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < "${STAGING_DIR}/db/zivaBooks.sql"
    echo "[RESTORE] Database restored successfully."
fi

# ------------------------------------------------------------------------------
# 5. Restore Redis Snapshot
# ------------------------------------------------------------------------------
if [ -f "${STAGING_DIR}/redis/dump.rdb" ]; then
    echo "[RESTORE] Restoring Redis snapshot..."
    (cd /opt/apps/zivabooks && docker compose stop redis || true)
    mkdir -p "${data_root}/redis"
    cp "${STAGING_DIR}/redis/dump.rdb" "${data_root}/redis/dump.rdb"
    (cd /opt/apps/zivabooks && docker compose up -d redis)
fi

# ------------------------------------------------------------------------------
# 6. Restart All Application Services
# ------------------------------------------------------------------------------
echo "[RESTORE] Restarting full application stack..."
if [ -d "/opt/apps/zivabooks" ]; then
    (cd /opt/apps/zivabooks && docker compose up -d)
fi

# Cleanup
rm -rf "${STAGING_DIR}"

echo "======================================================================"
echo "[RESTORE] SUCCESS! System restore completed."
echo "======================================================================"
