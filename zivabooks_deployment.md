# 🚀 ZivaBooks Deployment Documentation
(Ubuntu + Django + PostgreSQL + Nginx + Uvicorn + Redis + SSL)

---

## 📦 1. Server Preparation
sudo apt update
sudo apt upgrade -y

---

## 🐘 2. PostgreSQL Setup
sudo apt install postgresql postgresql-contrib -y
sudo -i -u postgres
psql

ALTER USER postgres WITH PASSWORD 'yourpassword';
CREATE DATABASE mydb;
\l

---

## ⚡ 3. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH=$HOME/.local/bin:$PATH

---

## 🐍 4. Django Setup
mkdir -p /root/zB/zivaBooks
cd /root/zB/zivaBooks

uv init
uv add django
uv sync
source .venv/bin/activate

django-admin startproject zivaBooks .

---

## 🗄️ 5. Django DB Config
uv add psycopg2-binary

# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'mydb',
        'USER': 'postgres',
        'PASSWORD': 'yourpassword',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

python manage.py migrate

---

## 🌐 6. Install Nginx
sudo apt install nginx -y
sudo systemctl start nginx
sudo systemctl enable nginx
sudo ufw allow 'Nginx Full'

---

## ⚙️ 7. Uvicorn Setup
uv add uvicorn

/root/zB/zivaBooks/.venv/bin/python -m uvicorn zivaBooks.asgi:application --host 0.0.0.0 --port 8000

---

## ⚙️ 8. Systemd Service
[Unit]
Description=Uvicorn daemon for Django (zivaBooks)
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/root/zB/zivaBooks/
Environment="PYTHONMALLOC=malloc"

ExecStart=/root/zB/zivaBooks/.venv/bin/python -m uvicorn zivaBooks.asgi:application \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

sudo systemctl daemon-reload
sudo systemctl start zivaBooks
sudo systemctl enable zivaBooks

---

## 🔐 9. SSL (Certbot)
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d zivabooks.in -d www.zivabooks.in

---

## 🌍 10. Nginx Config
server {
    listen 80;
    server_name zivabooks.in www.zivabooks.in;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name zivabooks.in www.zivabooks.in;

    ssl_certificate /etc/letsencrypt/live/zivabooks.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/zivabooks.in/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
    }
}

---

## 📊 Logs
sudo journalctl -u zivaBooks.service -f
sudo tail -f /var/log/nginx/error.log

---

## 🔥 Redis
sudo apt install redis-server -y
redis-cli ping

---

## 🚀 Architecture
User → Nginx → Uvicorn → Django → PostgreSQL
