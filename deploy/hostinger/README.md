# 🚀 Deploying zivaBooks on Hostinger VPS using Docker Manager

This guide walks you through deploying **zivaBooks** on Hostinger VPS using the built-in **Docker Manager** in hPanel, complete with **Nginx Reverse Proxy** and **free Let's Encrypt SSL**.

---

## 📋 Architecture Overview

All services run inside a unified Docker network managed via Hostinger's Docker Manager:
* **Nginx (`zivabooks_nginx`)**: Listens on public ports `80` (HTTP) and `443` (HTTPS), handles SSL termination, serves `/media/` uploads, and forwards traffic to Uvicorn ASGI.
* **Certbot (`zivabooks_certbot`)**: Automatically issues and renews Let's Encrypt SSL certificates twice daily.
* **Web (`zivabooks_web`)**: Uvicorn ASGI application running Django on port 8000 (internal only).
* **Celery Worker (`zivabooks_worker`)**: Handles asynchronous jobs, billing calculations, and PDF rendering.
* **Celery Beat (`zivabooks_beat`)**: Runs scheduled background crons.
* **PostgreSQL (`zivabooks_postgres`)**: PostgreSQL 17 database engine on port 5433 (internal only).
* **Redis (`zivabooks_redis`)**: Redis 7 memory cache and Celery broker on port 6379 (internal only).

---

## 🛠️ Step 1: Point Your DNS Record to Hostinger

Before issuing an SSL certificate, your domain must point to your Hostinger VPS IP:
1. Log into your domain registrar (Hostinger, Cloudflare, Namecheap, GoDaddy, etc.).
2. Go to **DNS Management** and add an **A Record**:
   - **Type:** `A`
   - **Name:** `@` (or `app` for a subdomain like `app.yourdomain.com`)
   - **Points to:** `YOUR_HOSTINGER_VPS_IP`
   - **TTL:** 300 (or Automatic)
3. Wait 2-5 minutes for DNS propagation.

---

## 🐳 Step 2: Deploy Stack in Hostinger Docker Manager

### Option 2A: Via Hostinger Docker Manager UI (Connect GitHub Repo)

1. Open **Hostinger hPanel** ➔ Navigate to **VPS** ➔ Click on **Docker Manager**.
2. Click **Create Application / Add Compose Project**:
   - **Project Name:** `zivabooks`
   - **Repository URL:** Enter your GitHub repo URL (e.g. `https://github.com/your-username/zivaBooks.git`)
   - **Branch:** `main`
   - **Compose File Path:** `docker-compose.prod.yml`
3. Under **Environment Variables**, paste the following keys:
   ```ini
   DEBUG=false
   SECRET_KEY=generate-a-strong-secret-key-here
   ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,127.0.0.1
   CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
   DATABASE_ENGINE=postgres
   POSTGRES_DB=zivaBooks
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=your_secure_db_password
   ```
4. Click **Deploy / Build**. Hostinger Docker Manager will pull the code, build the images, and start the containers.

---

### Option 2B: Via Hostinger Web Terminal (One-Command Bootstrap)

If you have SSH or the **Web Terminal** open in Hostinger hPanel:

1. Clone or pull your repository onto the VPS:
   ```bash
   git clone https://github.com/your-username/zivaBooks.git /opt/zivabooks
   cd /opt/zivabooks
   ```

2. Copy and configure your production environment:
   ```bash
   cp .env.example .env
   nano .env
   # Ensure DEBUG=false, ALLOWED_HOSTS=yourdomain.com, CSRF_TRUSTED_ORIGINS=https://yourdomain.com
   ```

3. Run the automated SSL and stack initialization script:
   ```bash
   ./deploy/hostinger/init-ssl.sh yourdomain.com admin@yourdomain.com
   ```

This single command:
- Bootstraps PostgreSQL, Redis, Web, and Celery
- Creates a temporary self-signed certificate so Nginx starts smoothly
- Requests your official Let's Encrypt certificate from Certbot
- Swaps in the valid certificate and reloads Nginx
- Starts automatic renewal in the background

---

## 🔒 Step 3: SSL Verification & Status

1. Check container health in Hostinger Docker Manager:
   All 7 containers should show status **Running / Healthy**:
   - `zivabooks_web`
   - `zivabooks_worker`
   - `zivabooks_beat`
   - `zivabooks_postgres`
   - `zivabooks_redis`
   - `zivabooks_nginx`
   - `zivabooks_certbot`

2. Visit your site in any web browser:
   - `https://yourdomain.com` ➔ Shows valid HTTPS lock icon 🔒
   - `https://yourdomain.com/health/` ➔ Returns `{"status": "ok"}`
