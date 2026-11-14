#!/usr/bin/env bash
# ==============================================================================
# zivaBooks — Hostinger SSL Certificate Initializer & Bootstrap Script
# ==============================================================================
# Usage:
#   ./deploy/hostinger/init-ssl.sh yourdomain.com admin@yourdomain.com
# ==============================================================================
set -eo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ]; then
    echo -ne "Enter your production domain (e.g. app.zivabooks.com): "
    read -r DOMAIN
fi

if [ -z "$EMAIL" ]; then
    echo -ne "Enter your email for Let's Encrypt renewal alerts: "
    read -r EMAIL
fi

COMPOSE_FILE="docker-compose.yml"


echo "======================================================================"
echo "  🔒 Initializing Let's Encrypt SSL for: ${DOMAIN}"
echo "======================================================================"

# Step 1: Update Nginx server_name in app.conf
CONF_FILE="deploy/hostinger/nginx/conf.d/app.conf"
if [ -f "$CONF_FILE" ]; then
    sed -i.bak "s/server_name .*/server_name ${DOMAIN} www.${DOMAIN};/g" "$CONF_FILE"
    rm -f "${CONF_FILE}.bak"
    echo "✓ Configured Nginx virtual host for ${DOMAIN}."
fi

# Step 2: Start database, redis and web first
echo "🚀 Bootstrapping core backend containers (Postgres, Redis, Web, Celery)..."
docker compose -f "$COMPOSE_FILE" up -d postgres redis web celery_worker celery_beat

# Step 3: Check if genuine certificates already exist
echo "🔑 Preparing SSL certificate structure..."
docker compose -f "$COMPOSE_FILE" run --rm --entrypoint "sh -c '
    mkdir -p /etc/letsencrypt/live/app
    if [ ! -f /etc/letsencrypt/live/app/fullchain.pem ]; then
        echo \"Generating temporary bootstrap certificate for Nginx startup...\"
        openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
            -keyout /etc/letsencrypt/live/app/privkey.pem \
            -out /etc/letsencrypt/live/app/fullchain.pem \
            -subj \"/CN=localhost\"
    fi
'" certbot

# Step 4: Launch Nginx with bootstrap/existing certificate
echo "🌐 Launching Nginx reverse proxy..."
docker compose -f "$COMPOSE_FILE" up -d nginx

# Step 5: Request official Let's Encrypt certificate
echo "📜 Requesting genuine Let's Encrypt SSL certificate..."
docker compose -f "$COMPOSE_FILE" run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot \
    --cert-name app \
    -d ${DOMAIN} \
    --email ${EMAIL} \
    --rsa-key-size 4096 \
    --agree-tos \
    --force-renewal \
    --non-interactive" certbot

# Step 6: Reload Nginx with new certificate
echo "🔄 Reloading Nginx with new SSL certificate..."
docker compose -f "$COMPOSE_FILE" exec nginx nginx -s reload

echo ""
echo "======================================================================"
echo "  🎉 SSL Successfully Provisioned & Active!"
echo "======================================================================"
echo "  🌐 Live HTTPS URL: https://${DOMAIN}/"
echo "  🩺 Healthcheck:    https://${DOMAIN}/health/"
echo "  🔄 Auto-Renewal:   Managed automatically by zivabooks_certbot"
echo "======================================================================"
