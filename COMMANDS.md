# zivaBooks Developer Command Reference & Runbook

This document is the definitive operational command reference for `zivaBooks`. It contains all essential CLI commands for setup, database operations, development, testing, Docker, Celery, and production maintenance.

---

## 1. Quick Start Commands

```bash
# 1. Install dependencies via uv
uv sync

# 2. Apply database migrations
uv run python manage.py migrate

# 3. Seed required system roles and permissions
uv run python manage.py seed_roles_permissions

# 4. (Optional) Seed mock data for development & testing
uv run python manage.py seed_mock_data

# 5. Start the local Django development server
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/` in your browser.

---

## 2. Environment & Dependency Management (`uv`)

```bash
# Sync environment with pyproject.toml / uv.lock
uv sync

# Add a new runtime dependency
uv add <package-name>

# Add a development-only dependency
uv add --dev <package-name>

# Update dependencies
uv lock --upgrade

# Execute any command inside the uv virtual environment
uv run <command>
```

---

## 3. Database & Migration Management

### 3.1 Migration Commands
```bash
# Create new migrations for model changes
uv run python manage.py makemigrations

# Create migrations for a specific app
uv run python manage.py makemigrations home
uv run python manage.py makemigrations inventory
uv run python manage.py makemigrations activation

# Inspect SQL generated for a specific migration
uv run python manage.py sqlmigrate home 0001

# Apply migrations
uv run python manage.py migrate

# Check status of applied and unapplied migrations
uv run python manage.py showmigrations
```

### 3.2 Seeding & Administrative Users
```bash
# Seed role definitions and default permission sets
uv run python manage.py seed_roles_permissions

# Seed tenant mock data (demo parties, items, batches, invoices)
uv run python manage.py seed_mock_data

# Create superuser for Django Admin (/admin/)
uv run python manage.py createsuperuser
```

---

## 4. Testing & Quality Assurance

### 4.1 Running Automated Tests
```bash
# Run full automated test suite using SQLite (fastest, standalone)
DATABASE_ENGINE=sqlite uv run python manage.py test

# Run tests with PostgreSQL (requires running Postgres server on configured port)
uv run python manage.py test

# Run tests for a specific application
DATABASE_ENGINE=sqlite uv run python manage.py test home
DATABASE_ENGINE=sqlite uv run python manage.py test activation
DATABASE_ENGINE=sqlite uv run python manage.py test inventory

# Run a specific test module
DATABASE_ENGINE=sqlite uv run python manage.py test home.tests.test_inventory_purchase_sales
DATABASE_ENGINE=sqlite uv run python manage.py test home.tests.test_order_collection_approval_permissions
DATABASE_ENGINE=sqlite uv run python manage.py test home.tests.test_sales_apis

# Run a specific test class or test method
DATABASE_ENGINE=sqlite uv run python manage.py test home.tests.test_sales_apis.SalesApiTests.test_create_sale_success

# Run with fail-fast flag (stops at first failure)
DATABASE_ENGINE=sqlite uv run python manage.py test --failfast

# Run with increased verbosity
DATABASE_ENGINE=sqlite uv run python manage.py test -v 2
```

### 4.2 Code Coverage
```bash
# Run tests under coverage
DATABASE_ENGINE=sqlite uv run coverage run --source='.' manage.py test

# Generate console coverage report
uv run coverage report

# Generate HTML coverage report (outputs to htmlcov/index.html)
uv run coverage html
```

---

## 5. Docker Compose Operations

zivaBooks uses a clean, unified `docker-compose.yml` for local and production deployments with automated migrations, static files, and role seeding on startup.

```bash
# 1. Single command start (builds and starts postgres, redis, web, worker, beat)
docker compose up -d --build

# 2. View running services and health status
docker compose ps

# 3. Follow application logs
docker compose logs -f web worker beat

# 4. Run Django management commands
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py showmigrations

# 5. Access database shell (psql)
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

# 6. Stop containers (preserves data volumes)
docker compose stop

# 7. Tear down containers and network
docker compose down
```

For custom configurations (e.g. ports, domain, production secrets), simply copy `.env.example` to `.env` and Docker Compose automatically loads them.

The image and root Compose file default to one web worker and one Celery worker
process to reduce RAM use. Set `WEB_WORKERS` and `CELERY_CONCURRENCY` in the
selected env file to increase throughput when needed. Lower concurrency reduces
parallel capacity; it does not disable background or scheduled jobs. After changing
these values, recreate web and worker with `docker compose up -d --build web worker`.
Use `docker stats --no-stream` to compare memory under similar workloads. Removing
redundant image copies reduces image/build overhead, not runtime process memory.


---

### Local development script

`start.sh` works on macOS Bash and Linux, from any working directory. Paths passed
with `--env-file` are relative to the repository root unless absolute. It does not
source dotenv files or print resolved secrets; Compose parses them. Startup checks
container health and preserves bind-mounted data. On a fresh checkout, use `--init`,
review credentials, then `--build --migrate`. The default startup expects an existing
app image and does not apply schema changes.

| Command | Purpose |
|---|---|
| `./start.sh --help` | All options and examples |
| `./start.sh --dev` | **Fast local dev (No Docker build required)**: starts Docker DB/Redis and launches host Django dev server with live reload |
| `./start.sh --dev --migrate` | Run migrations on host and launch Django dev server |
| `./start.sh --dev --sqlite` | Run entirely on host with SQLite (no Docker needed at all) |
| `./start.sh --services` | Start only PostgreSQL & Redis in Docker (ports exposed for host tools) |
| `./start.sh --dev-worker` | Run Celery worker on host |
| `./start.sh --init` | Create missing local env/data folders; preserve existing files |
| `./start.sh` | Start the local containerized stack and wait for health checks |
| `./start.sh --build` | Rebuild the shared image and start containerized stack |
| `./start.sh --build --migrate --follow` | Build, stop writers, migrate, start, follow logs |
| `./start.sh --no-worker` | Start web/DB/Redis and stop any existing worker |
| `./start.sh --status` | Show all containers, including stopped ones |
| `./start.sh --logs web worker` | Follow selected logs (last 100 lines initially) |
| `./start.sh --stop` | Wait for containers to stop; preserve data |
| `./start.sh --down` | Stop gracefully, remove containers/network; preserve data |
| `./start.sh --restart` | Restart existing web and worker containers |
| `./start.sh --shell` | Open web container shell |
| `./start.sh --dbshell` | Open psql using container credentials |
| `./start.sh --manage createsuperuser` | Execute a Django command in running web |
| `./start.sh --test zivaBooks.tests` | Test current host source via uv/SQLite |
| `./start.sh --env-file .env.local-alt --status` | Select another local env file |

Choose one action. Startup flags (`--build`, `--migrate`, `--no-worker`, `--follow`)
can be combined. Put global options before `--logs`, `--manage`, or `--test`, which
consume all remaining arguments. `--test` requires host uv but not Docker. Other
actions require Docker Compose and host Python 3; `--init` does not require the daemon.

Back up valuable data before `--migrate`. Writers remain stopped if a migration
fails; fix the error and rerun startup with `--migrate`. The script never deletes
volumes or prunes images. It rejects DEBUG=false, production project names ending
in `-prod`, and storage outside the repository's `local-data` directory. This is a
local workflow guard, not a replacement for checking your selected project/env.
Changing database credentials in an env file does not update an initialized cluster.

Source is baked into the image, with no hot reload. `--manage` operates on that
image: generate migration files on the host using
`DATABASE_ENGINE=sqlite uv run python manage.py makemigrations`, then rebuild and
apply them with `--build --migrate`. Files generated inside a container are not
saved into your source checkout. Container shells require running services.

---

## 6. Celery & Asynchronous Background Tasks

```bash
# Start Celery Worker locally
uv run celery -A zivaBooks worker -l info

# Start Celery Beat Scheduler (for periodic tasks)
uv run celery -A zivaBooks beat -l info

# Start Worker with auto-reload (development only)
uv run celery -A zivaBooks worker --watchmedo auto-restart --directory=./home -l info

# Inspect active Celery workers and queues
uv run celery -A zivaBooks status
```

---

## 7. Static Assets & PWA Compilation

```bash
# Collect static assets into staticfiles/ directory for WhiteNoise
uv run python manage.py collectstatic --noinput

# Collect static and clear existing directory beforehand
uv run python manage.py collectstatic --clear --noinput
```

---

## 8. Database Backup, Restore & Inspection

### 8.1 PostgreSQL Backups
```bash
# Dump PostgreSQL database to SQL file
pg_dump -h 127.0.0.1 -p 5433 -U postgres -d zivaBooks -F c -b -v -f backup_$(date +%Y%m%d_%H%M%S).dump

# Restore PostgreSQL database from dump
pg_restore -h 127.0.0.1 -p 5433 -U postgres -d zivaBooks -v backup_file.dump
```

### 8.2 SQLite Backups
```bash
# Create a timestamped copy of SQLite database
cp db.sqlite3 backups/db_$(date +%Y%m%d_%H%M%S).sqlite3
```

---

## 9. Diagnostics & Troubleshooting

```bash
# Verify health check endpoint (returns JSON with DB and Cache status)
curl -s http://127.0.0.1:8000/health/ | jq .

# Verify Django configuration and check for warnings
uv run python manage.py check

# Verify deployment settings (checks security and production settings)
uv run python manage.py check --deploy

# Check open ports on macOS (e.g. checking if 8000 or 5433 is in use)
lsof -i :8000
lsof -i :5433
```

---

## 10. Update Protocol for COMMANDS.md

Whenever:
1. A new management command is added in `home/management/commands/`,
2. A new CLI flag, test runner option, or environment variable is introduced,
3. Docker Compose services or container commands are changed,

**Update this file immediately** to keep all team members and AI agents aligned.
