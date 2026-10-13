# 🚀 zivaBooks — Docker Compose & Reverse Proxy Deployment Guide

This guide details the production deployment architecture for **zivaBooks** using **Docker Compose** and a **Shared Edge Reverse Proxy (Nginx + Let's Encrypt Certbot)**.

---

## 🏗️ 1. Directory & Storage Layout (Portable & Backup-Ready)

To ensure applications and persistent data can be backed up or migrated to another VPS in minutes, code/config is strictly separated from stateful persistent volumes:

```text
/opt/
├── apps/                          # Git repos / docker-compose configs
│   ├── zivabooks/                 # zivaBooks application stack
│   │   ├── docker-compose.yml     # Web (Uvicorn), Celery Worker, Celery Beat, DB, Redis
│   │   ├── Dockerfile
│   │   └── .env                   # Secret credentials (chmod 600)
│   └── proxy/                     # Shared edge router for all host applications
│       ├── docker-compose.yml     # Nginx + Let's Encrypt Certbot
│       ├── nginx.conf             # Master Nginx configuration
│       └── conf.d/                # Virtual hosts
│           ├── default.conf       # ACME challenge handler & catch-all
│           └── zivabooks.conf     # SSL reverse proxy to zivabooks_web:8000
│
/appData/                         # ALL stateful persistent data (mount targets)
├── zivabooks/
│   ├── postgres/              # PostgreSQL cluster data files
│   ├── redis/                 # Redis dump.rdb & AOF
│   └── media/                 # Django uploaded media files & company logos
├── app2/
│   ├── db/                    # App 2 database
│   └── uploads/               # App 2 assets
└── proxy/
    ├── certs/                 # Let's Encrypt SSL certificates (/etc/letsencrypt)
    └── certbot/               # ACME challenge webroot (/var/www/certbot)
```

### 💡 Why this layout?
1. **Zero Data Loss on Rebuild**: Destroying, updating, or rebuilding application containers leaves `/appData/` untouched.
2. **Multi-App Multi-Tenancy**: Every app owns `/appData/<app-name>/*`, allowing you to run 10+ independent apps on the same VPS with zero crosstalk or path collision.
3. **Atomic Backup**: Backing up `/appData` and `/opt/apps/*/.env` gives you 100% of the state needed to clone or restore the entire server in minutes.

---

## ⚙️ 2. VPS Prerequisites & Initial Bootstrap

On a fresh Ubuntu 24.04 / 22.04 LTS VPS:

### Step 2.1: Install Docker & Git
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw

# Install Docker Engine & Compose plugin
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# Configure firewall
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

### Step 2.2: Clone Repository & Run Setup Bootstrap
```bash
# Clone zivaBooks into /opt/apps/zivabooks
sudo git clone https://github.com/your-org/zivaBooks.git /opt/apps/zivabooks
cd /opt/apps/zivabooks

# Run the automated VPS setup script (creates directory tree & permissions)
sudo ./deploy/setup_vps.sh
```

---

## 🔒 3. SSL Certificate Setup & Reverse Proxy Launch

### Step 3.1: Configure Your Domain
1. In DNS management (Cloudflare, Route53, Namecheap), create an `A` record pointing your domain (e.g. `app.zivabooks.com`) to your VPS public IP address.
2. Update the server name in `/opt/apps/proxy/conf.d/zivabooks.conf`:
   ```bash
   sudo sed -i 's/app.yourdomain.com/app.zivabooks.com/g' /opt/apps/proxy/conf.d/zivabooks.conf
   ```

### Step 3.2: Obtain SSL Certificate via Certbot
Before starting the HTTPS proxy, generate the initial certificate using Certbot standalone/webroot:

```bash
# Start proxy with default.conf listening on port 80 for ACME challenge
cd /opt/apps/proxy
docker compose up -d

# Request certificate for your domain
docker compose run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot -d app.zivabooks.com --email admin@zivabooks.com --agree-tos --no-eff-email" certbot

# Reload Nginx to load the new certificate
docker compose exec nginx nginx -s reload
```

---

## 🚀 4. Launching the zivaBooks Application Stack

### Step 4.1: Configure Production Environment Variables
```bash
cd /opt/apps/zivabooks
sudo cp deploy/apps/zivabooks/.env.production.example .env
sudo nano .env
# Update:
#   - SECRET_KEY
#   - ALLOWED_HOSTS (e.g. app.zivabooks.com,zivabooks_web)
#   - CSRF_TRUSTED_ORIGINS (e.g. https://app.zivabooks.com)
#   - POSTGRES_PASSWORD
#   - EMAIL credentials

# Lock permissions
sudo chmod 600 .env
```

### Step 4.2: Build and Launch Containers
```bash
cd /opt/apps/zivabooks

# Build image and start all services in background
docker compose up -d --build

# Verify container health
docker compose ps
```

The container entrypoint will automatically:
1. Wait for PostgreSQL to be ready.
2. Run database migrations (`python manage.py migrate`).
3. Collect static files (`python manage.py collectstatic`).
4. Start Uvicorn ASGI server with 4 worker processes.

### Step 4.3: Initial Database Seeding (First-Time Only)
```bash
# Seed default system roles and permissions
docker compose exec web uv run python manage.py seed_roles_permissions

# Create superuser for Django administration
docker compose exec web uv run python manage.py createsuperuser
```

---

## 📋 5. Operational Commands & Maintenance

### 5.1 Container Management
```bash
# View real-time logs
docker compose logs -f web
docker compose logs -f celery_worker
docker compose logs -f postgres

# Restart application stack
docker compose restart

# Graceful restart after code update
git pull origin main
docker compose up -d --build
```

### 5.2 Running Django Commands
```bash
# Execute any Django management command inside container
docker compose exec web uv run python manage.py <command>

# Examples:
docker compose exec web uv run python manage.py check
docker compose exec web uv run python manage.py showmigrations
docker compose exec web uv run python manage.py backfill_books
```

---

## 💾 6. Atomic Backups & Disaster Recovery

### 6.1 Automated Daily Backups
The setup script automatically adds a nightly cron job running at **02:00 AM**:
```text
0 2 * * * /opt/backups/scripts/backup.sh >> /var/log/zivabooks_backup.log 2>&1
```

### 6.2 Running a Manual Backup
```bash
sudo /opt/backups/scripts/backup.sh
```
This produces:
- `/opt/backups/daily/zivabooks_backup_YYYYMMDD_HHMMSS.tar.gz`
- `/opt/backups/daily/zivabooks_backup_YYYYMMDD_HHMMSS.tar.gz.sha256`

The archive contains:
- Complete PostgreSQL SQL dump
- Redis `dump.rdb`
- Complete `/opt/data/zivabooks/media/` tree
- All `/opt/apps/*/.env` configurations

### 6.3 Disaster Recovery / Migrating to a New Server
To restore the entire server from a backup archive:

```bash
# 1. Run restore script pointing to your backup archive
sudo /opt/backups/scripts/restore.sh /opt/backups/daily/zivabooks_backup_20261010_120000.tar.gz

# Or restore the latest backup automatically:
sudo /opt/backups/scripts/restore.sh
```

The script will:
1. Verify SHA256 archive checksum.
2. Gracefully stop live web and worker containers.
3. Restore `.env` files and user media.
4. Launch PostgreSQL and stream the database dump.
5. Restore Redis state and restart all application services.
6. Verify operational health.

---

## 🌐 7. Adding Additional Applications to the Same VPS

To host another application (e.g. `app1-fastapi`) on the same server:
1. Place its code and compose file in `/opt/apps/app1-fastapi/`.
2. Map its persistent volumes to `/opt/data/app1/`.
3. Connect its web service to `proxy_network`.
4. Add a virtual host file in `/opt/apps/proxy/conf.d/app1.conf`.
5. Run `docker compose exec nginx nginx -s reload` in `/opt/apps/proxy`.
