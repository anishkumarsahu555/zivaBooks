# 🐘 PostgreSQL Setup Notes (Ubuntu + Remote pgAdmin Access)

---

## 📦 1. Install PostgreSQL
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib -y
```

---

## ▶️ 2. Start & Enable Service
```bash
sudo systemctl start postgresql
sudo systemctl enable postgresql
sudo systemctl status postgresql
```

---

## 🔐 3. Access PostgreSQL (postgres user)
```bash
sudo -i -u postgres
psql
```

---

## 🔑 4. Set Password for postgres
```sql
ALTER USER postgres WITH PASSWORD 'yourpassword';
```

---

## 🗄️ 5. Create Database
```sql
CREATE DATABASE mydb;
```

---

## 📋 6. List Databases
```sql
\l
-- or
\list
```

---

## 🌐 7. Enable Remote Access

### Edit postgresql.conf
```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

Update:
```conf
listen_addresses = '*'
```

---

### Edit pg_hba.conf
```bash
sudo nano /etc/postgresql/*/main/pg_hba.conf
```

Add:
```conf
host    all    all    0.0.0.0/0    md5
```

👉 Recommended (restrict to your IP):
```conf
host    all    all    YOUR_IP/32    md5
```

---

## 🔁 8. Restart PostgreSQL
```bash
sudo systemctl restart postgresql
```

---

## 🔥 9. Allow Port in Firewall
```bash
sudo ufw allow 5432/tcp
sudo ufw status
```

---

## 🌍 10. Get Server IP
```bash
curl ifconfig.me
```

---

## 🧪 11. Connect via pgAdmin

**Connection Details:**
- Host: `your-server-ip`
- Port: `5432`
- Username: `postgres`
- Password: `yourpassword`

---

## ⚡ 12. Test Connection (CLI)
```bash
psql -U postgres -d mydb -h localhost -W
```

---

## ⚠️ Security Notes
- Avoid using `postgres` user in production  
- Create a separate DB user for applications  
- Restrict IP in `pg_hba.conf`  
- Keep firewall enabled  

---

## 🚀 Optional (Django Config)
```python
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
```

---

## 🧠 Common Errors

### ❌ Connection refused
- PostgreSQL not running  
- `listen_addresses` not set  

### ❌ No pg_hba.conf entry
- IP not allowed  

### ❌ Timeout
- Firewall blocking port  

---
