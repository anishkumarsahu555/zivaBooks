# zivaBooks

zivaBooks is a Django-based business operations app for tenant-scoped sales, collections, orders, inventory, accounting books, staff permissions, reports, activation, and customer portal workflows.

## What is included

- Multi-tenant staff and customer access
- Role and permission-aware navigation
- Collections with OTP verification
- Sales, orders, proforma invoices, and invoice series
- Cash counter, cashbook, bankbook, and party ledger workflows
- Inventory masters, stock, purchases, vouchers, and analysis
- PDF and Excel reports
- PWA support with offline shell assets
- Activation/license middleware
- Automated tests for tenant isolation, permissions, API smoke coverage, books, reports, OTP, and key workflows

## Documentation Hub

Explore the project documentation:
- 🗺️ **[INDEX.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/INDEX.md)** — Documentation Master Index & Sitemap
- 🏗️ **[ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)** — System Architecture, Multi-Tenancy & FEFO Stock Ledger
- 💡 **[DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md)** — Step-by-step Developer Guide for adding features, APIs & models
- 🔌 **[API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)** — API Contracts & JSON Endpoint Specification
- 🗄️ **[DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)** — Database Schema & ER Diagrams
- 🛠️ **[setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/setup.md)** — Environment & Local Setup Guide
- 🧹 **[MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)** — Code Refactoring Boundaries & Service Layer Guidelines
- 🧪 **[TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)** — QA Test Scenarios & Verification Matrix
- 🤖 **[SKILLS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/SKILLS.md)** — AI Agent Skill & Context Guide

## Quick start

```bash
uv run python manage.py migrate
uv run python manage.py seed_roles_permissions
uv run python manage.py seed_mock_data
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Docker Compose (local development)

With Docker and the Compose plugin installed, run:

```bash
docker compose up --build -d
docker compose exec web python manage.py seed_roles_permissions
docker compose exec web python manage.py createsuperuser
```

Open `http://127.0.0.1:8000/`. The stack includes Django, PostgreSQL,
Redis, and a Celery worker. Migrations and static-file collection run at startup.
Emails are printed to the container logs.

Compose reads `SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
and `WEB_PORT` from your shell or `.env`, with local defaults when unset. It sets
the database and Redis hosts to their container service names. Other `.env`
settings are not passed into the containers. This setup uses Django's development
server with debug enabled and binds only to localhost; it is intended for local use.

```bash
docker compose logs -f web worker
docker compose down
```

Database, Redis, and uploaded media persist in visible host folders through bind
mounts, by default `local-data/zivabooks/postgres`, `local-data/zivabooks/redis`, and
`local-data/zivabooks/media` inside this project. They are excluded from Git and
Docker builds. Set `LOCAL_DATA_ROOT=/absolute/path/to/local-data` and
`APP_NAME=zivabooks` in `.env` to choose the parent location and app subdirectory.
The resulting pattern is `${LOCAL_DATA_ROOT}/${APP_NAME}/{postgres,redis,media}`.
Use a different app name, Compose project name, and web port for each local app.
The production configuration uses its separate `DATA_ROOT` variable.

`docker compose down` and container rebuilds preserve these folders. Do not delete
them or point `LOCAL_DATA_ROOT` at an empty location when expecting existing data.
Existing native PostgreSQL databases and previous named volumes are not imported
automatically. When migrating named volumes, stop all writers and shut down
PostgreSQL/Redis cleanly before copying, retain the originals, and verify data after
restart. Raw PostgreSQL directory copies require the same compatible database
version and platform; use dump/restore otherwise. Never edit database files in
Finder. On Docker Desktop, database bind mounts can be slower than named volumes;
this layout prioritizes visible host storage.

After changing source code, rebuild with `docker compose up --build -d`.

## Configuration

For Hostinger VPS deployment with existing host Nginx, persistent data, environment
variables, and backup/restore procedures, see [Production deployment](deploy/README.md).

Local development works without a `.env` file. Production deployments must set at least:

```dotenv
DEBUG=false
SECRET_KEY=change-me
ALLOWED_HOSTS=app.example.com
CSRF_TRUSTED_ORIGINS=https://app.example.com
DATABASE_ENGINE=postgres
POSTGRES_DB=zivaBooks
POSTGRES_USER=postgres
POSTGRES_PASSWORD=change-me
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

Useful optional settings:

```dotenv
USE_REDIS_CACHE=true
REDIS_CACHE_URL=redis://127.0.0.1:6379/1
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_HOST_USER=sender@example.com
EMAIL_HOST_PASSWORD=app-password
DEFAULT_FROM_EMAIL=zivaBooks <sender@example.com>
```

## Health check

The app exposes:

```text
/health/
```

It returns JSON with database and cache status. Use it from deployment monitors, load balancers, or uptime checks.

## Tests

Run the full test suite:

```bash
uv run python manage.py test
```

Run one app or test file:

```bash
uv run python manage.py test home
uv run python manage.py test zivaBooks.tests
```
