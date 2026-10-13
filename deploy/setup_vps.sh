#!/usr/bin/env bash
# ==============================================================================
# /opt/apps/zivabooks/deploy/setup_vps.sh
# ==============================================================================
# Automated Provisioning Script for VPS Storage Layout & Docker Infrastructure
# Establishes the exact layout:
#   /opt/apps/    -> Repositories and Compose configurations
#   /opt/data/    -> Stateful persistent data mount targets
#   /opt/backups/ -> Daily snapshots and automated backup/restore scripts
# ==============================================================================

set -eo pipefail

echo "======================================================================"
echo "      zivaBooks — VPS Storage Layout & Docker Bootstrap Script        "
echo "======================================================================"

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This setup script must be executed as root or with sudo."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ------------------------------------------------------------------------------
# 1. Create Directory Hierarchy
# ------------------------------------------------------------------------------
echo "[STEP 1] Creating standard /opt/ directory hierarchy..."

# Applications & Compose configs
mkdir -p /opt/apps/zivabooks
mkdir -p /opt/apps/proxy/conf.d

# Stateful Persistent Data Volumes (/appData/<app>)
mkdir -p /appData/zivabooks/postgres
mkdir -p /appData/zivabooks/redis
mkdir -p /appData/zivabooks/media
mkdir -p /appData/proxy/certs
mkdir -p /appData/proxy/certbot

# Backups & Automation
mkdir -p /opt/backups/daily
mkdir -p /opt/backups/scripts

# ------------------------------------------------------------------------------
# 2. Permissions & Security
# ------------------------------------------------------------------------------
echo "[STEP 2] Setting file system permissions..."

chmod 755 /opt/apps /appData
chmod 700 /opt/backups
chmod 750 /appData/zivabooks/postgres /appData/zivabooks/redis /appData/zivabooks/media

# ------------------------------------------------------------------------------
# 3. Deploy Reverse Proxy Configuration
# ------------------------------------------------------------------------------
echo "[STEP 3] Copying edge reverse proxy configs..."

if [ -f "${SCRIPT_DIR}/apps/proxy/docker-compose.yml" ]; then
    cp "${SCRIPT_DIR}/apps/proxy/docker-compose.yml" /opt/apps/proxy/
    cp "${SCRIPT_DIR}/apps/proxy/nginx.conf" /opt/apps/proxy/
    cp -r "${SCRIPT_DIR}/apps/proxy/conf.d/"* /opt/apps/proxy/conf.d/
fi

# ------------------------------------------------------------------------------
# 4. Deploy Backup & Disaster Recovery Scripts
# ------------------------------------------------------------------------------
echo "[STEP 4] Deploying backup and disaster recovery scripts..."

if [ -f "${SCRIPT_DIR}/backups/scripts/backup.sh" ]; then
    cp "${SCRIPT_DIR}/backups/scripts/backup.sh" /opt/backups/scripts/
    cp "${SCRIPT_DIR}/backups/scripts/restore.sh" /opt/backups/scripts/
    chmod +x /opt/backups/scripts/backup.sh /opt/backups/scripts/restore.sh
fi

# ------------------------------------------------------------------------------
# 5. Create Shared Docker Edge Network
# ------------------------------------------------------------------------------
echo "[STEP 5] Initializing shared Docker network: 'proxy_network'..."

if command -v docker >/dev/null 2>&1; then
    docker network create proxy_network 2>/dev/null || echo "Network 'proxy_network' already exists."
else
    echo "[WARN] Docker is not installed yet. Remember to run 'docker network create proxy_network' after installing Docker."
fi

# ------------------------------------------------------------------------------
# 6. Configure Nightly Cron Backup Job
# ------------------------------------------------------------------------------
echo "[STEP 6] Configuring nightly automated backup cron job (02:00 AM)..."

CRON_JOB="0 2 * * * /opt/backups/scripts/backup.sh >> /var/log/zivabooks_backup.log 2>&1"
(crontab -l 2>/dev/null | grep -F -v "/opt/backups/scripts/backup.sh" ; echo "$CRON_JOB") | crontab -

echo "======================================================================"
echo "[SUCCESS] VPS Directory layout & Docker infrastructure provisioned!"
echo "======================================================================"
echo ""
echo "Layout Summary:"
echo "  Code / Configs:    /opt/apps/zivabooks/ & /opt/apps/proxy/"
echo "  Persistent Data:   /opt/data/zivabooks/ & /opt/data/proxy/"
echo "  Atomic Backups:    /opt/backups/daily/ (scheduled at 02:00 AM)"
echo ""
echo "Next Steps:"
echo "  1. Copy your .env to /opt/apps/zivabooks/.env (chmod 600)"
echo "  2. Update domain in /opt/apps/proxy/conf.d/zivabooks.conf"
echo "  3. Start proxy:       cd /opt/apps/proxy && docker compose up -d"
echo "  4. Start zivaBooks:   cd /opt/apps/zivabooks && docker compose up -d"
echo "======================================================================"
