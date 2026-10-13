#!/usr/bin/env bash
# ==============================================================================
# /opt/backups/scripts/backup.sh
# ==============================================================================
# Atomic Hot Backup Script for zivaBooks & Shared Infrastructure
# Backs up:
#   1. PostgreSQL Database Dump (SQL)
#   2. Redis Persistent Data (dump.rdb)
#   3. Uploaded User Media (/opt/data/zivabooks/media/)
#   4. Environment Configurations (/opt/apps/*/.env)
# Target: /opt/backups/daily/backup_YYYYMMDD_HHMMSS.tar.gz
# ==============================================================================

set -eo pipefail

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_ROOT="/opt/backups"
DAILY_DIR="${BACKUP_ROOT}/daily"
STAGING_DIR="${BACKUP_ROOT}/staging_${TIMESTAMP}"
ARCHIVE_NAME="zivabooks_backup_${TIMESTAMP}.tar.gz"
RETENTION_DAYS=7

# Source zivabooks environment variables if available
if [ -f "/opt/apps/zivabooks/.env" ]; then
    # shellcheck disable=SC1091
    POSTGRES_USER=$(grep -E '^POSTGRES_USER=' /opt/apps/zivabooks/.env | cut -d '=' -f2- | tr -d ' "\r\n' || echo "postgres")
    POSTGRES_DB=$(grep -E '^POSTGRES_DB=' /opt/apps/zivabooks/.env | cut -d '=' -f2- | tr -d ' "\r\n' || echo "zivaBooks")
else
    POSTGRES_USER="postgres"
    POSTGRES_DB="zivaBooks"
fi

echo "======================================================================"
echo "[BACKUP] Starting zivaBooks backup at $(date) [${TIMESTAMP}]"
echo "======================================================================"

# Create necessary directories
mkdir -p "${DAILY_DIR}" "${STAGING_DIR}/env" "${STAGING_DIR}/db" "${STAGING_DIR}/redis"

# ------------------------------------------------------------------------------
# 1. PostgreSQL Database Dump
# ------------------------------------------------------------------------------
echo "[BACKUP] Dumping PostgreSQL database (${POSTGRES_DB})..."
if docker ps --format '{{.Names}}' | grep -q "^zivabooks_postgres$"; then
    docker exec -t zivabooks_postgres pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" > "${STAGING_DIR}/db/zivaBooks.sql"
    echo "[BACKUP] PostgreSQL dump completed successfully."
else
    local data_root="${DATA_DIR:-/appData/zivabooks}"
    if [ -d "${data_root}/postgres" ]; then
        cp -r "${data_root}/postgres" "${STAGING_DIR}/db/postgres_raw"
    elif [ -d "/opt/data/zivabooks/postgres" ]; then
        cp -r /opt/data/zivabooks/postgres "${STAGING_DIR}/db/postgres_raw"
    fi
fi

# ------------------------------------------------------------------------------
# 2. Redis RDB Snapshot
# ------------------------------------------------------------------------------
echo "[BACKUP] Capturing Redis state..."
if docker ps --format '{{.Names}}' | grep -q "^zivabooks_redis$"; then
    docker exec zivabooks_redis redis-cli bgsave || true
    sleep 2
fi
local data_root="${DATA_DIR:-/appData/zivabooks}"
if [ -f "${data_root}/redis/dump.rdb" ]; then
    cp "${data_root}/redis/dump.rdb" "${STAGING_DIR}/redis/"
    echo "[BACKUP] Redis dump.rdb copied."
elif [ -f "/opt/data/zivabooks/redis/dump.rdb" ]; then
    cp /opt/data/zivabooks/redis/dump.rdb "${STAGING_DIR}/redis/"
    echo "[BACKUP] Redis dump.rdb copied."
fi

# ------------------------------------------------------------------------------
# 3. User Media Files
# ------------------------------------------------------------------------------
echo "[BACKUP] Archiving user media files (${data_root}/media)..."
if [ -d "${data_root}/media" ]; then
    cp -r "${data_root}/media" "${STAGING_DIR}/media"
elif [ -d "/opt/data/zivabooks/media" ]; then
    cp -r /opt/data/zivabooks/media "${STAGING_DIR}/media"
fi

# ------------------------------------------------------------------------------
# 4. Environment Configurations (.env)
# ------------------------------------------------------------------------------
echo "[BACKUP] Collecting application environment configurations..."
find /opt/apps -name ".env" -exec cp --parents {} "${STAGING_DIR}/env/" \; 2>/dev/null || true
# Secure permissions on copied secrets
chmod -R 600 "${STAGING_DIR}/env" 2>/dev/null || true

# ------------------------------------------------------------------------------
# 5. Compress into Final Backup Bundle
# ------------------------------------------------------------------------------
echo "[BACKUP] Creating compressed tarball: ${DAILY_DIR}/${ARCHIVE_NAME}..."
tar -czf "${DAILY_DIR}/${ARCHIVE_NAME}" -C "${STAGING_DIR}" .

# Generate SHA256 checksum for verification
(cd "${DAILY_DIR}" && sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256")

# Cleanup staging directory
rm -rf "${STAGING_DIR}"

# ------------------------------------------------------------------------------
# 6. Retention Policy (Prune older than N days)
# ------------------------------------------------------------------------------
echo "[BACKUP] Pruning archives older than ${RETENTION_DAYS} days..."
find "${DAILY_DIR}" -type f -name "zivabooks_backup_*.tar.gz*" -mtime "+${RETENTION_DAYS}" -delete

BACKUP_SIZE=$(du -h "${DAILY_DIR}/${ARCHIVE_NAME}" | cut -f1)
echo "======================================================================"
echo "[BACKUP] SUCCESS! Archive created: ${DAILY_DIR}/${ARCHIVE_NAME} (${BACKUP_SIZE})"
echo "======================================================================"
