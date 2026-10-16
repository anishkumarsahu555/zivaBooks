# zivaBooks Setup

## Requirements

- Python compatible with the version declared in `pyproject.toml`
- `uv`
- SQLite for quick local development, or Postgres for production-like local testing
- Redis if `USE_REDIS_CACHE=true`

## Local development with SQLite

SQLite is used unless `DATABASE_ENGINE=postgres` is set.

```bash
uv run python manage.py migrate
uv run python manage.py seed_roles_permissions
uv run python manage.py seed_mock_data
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Local development with Postgres

Install and start Postgres:

```bash
brew install postgresql
brew services start postgresql@18
createdb -h 127.0.0.1 -p 5432 -U postgres zivaBooks
```

Create a `.env` file:

```dotenv
DEBUG=true
SECRET_KEY=local-dev-secret
DATABASE_ENGINE=postgres
POSTGRES_DB=zivaBooks
POSTGRES_USER=postgres
POSTGRES_PASSWORD=pass
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
USE_REDIS_CACHE=false
```

Run setup:

```bash
uv run python manage.py migrate
uv run python manage.py seed_roles_permissions
uv run python manage.py seed_mock_data
uv run python manage.py runserver
```

## Production environment checklist

Set these values before running with `DEBUG=false`:

```dotenv
DEBUG=false
SECRET_KEY=replace-with-a-long-random-secret
ALLOWED_HOSTS=app.example.com
CSRF_TRUSTED_ORIGINS=https://app.example.com
DATABASE_ENGINE=postgres
POSTGRES_DB=zivaBooks
POSTGRES_USER=zivabooks
POSTGRES_PASSWORD=replace-me
POSTGRES_HOST=db.example.com
POSTGRES_PORT=5432
```

Recommended production hardening:

```dotenv
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
SECURE_SSL_REDIRECT=true
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=true
USE_REDIS_CACHE=true
REDIS_CACHE_URL=redis://redis.example.com:6379/1
```

## Email settings

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_HOST_USER=sender@example.com
EMAIL_HOST_PASSWORD=app-password
DEFAULT_FROM_EMAIL=zivaBooks <sender@example.com>
```

## Health check

Use `/health/` to confirm database and cache connectivity:

```bash
curl http://127.0.0.1:8000/health/
```

Expected healthy response:

```json
{
  "status": "ok",
  "checks": {
    "database": {"ok": true},
    "cache": {"ok": true}
  }
}
```

## Tests

Run all tests:

```bash
uv run python manage.py test
```

Run a focused test target:

```bash
uv run python manage.py test zivaBooks.tests
uv run python manage.py test home.tests.test_regression
```

## Clean Single Docker Compose Deployment

zivaBooks features a clean, unified `docker-compose.yml` for zero-friction deployment. Database migrations, static assets, and permission seeding run automatically on startup.

### Quick Start

```bash
docker compose up -d --build
```

Access the application at `http://localhost:8000/`.

All services (`postgres`, `redis`, `web`, `worker`, `beat`) start with built-in healthchecks. Persistent data is preserved in managed Docker volumes (`postgres_data`, `redis_data`, `media_data`).

To customize settings (ports, secrets, domain, email), copy `.env.example` to `.env` and Docker Compose will load it automatically:

```bash
cp .env.example .env
docker compose up -d --build
```

