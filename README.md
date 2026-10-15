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

## Living Documentation & Governance Suite

`zivaBooks` maintains an integrated, synchronized suite of 8 core living documents that keep track of project state, architecture, tasks, and standards across every change:

| Document | Purpose & What It Tracks | When to Update |
|---|---|---|
| 📖 **[README.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/README.md)** | **Master Portal & Overview**: Quick start, stack, Docker setup, and documentation map. | Onboarding changes, top-level features, environment setup. |
| 🧠 **[MEMORY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MEMORY.md)** | **Project Memory & State Ledger**: Active milestones, ADR decisions, known quirks/gotchas, and change ledger. | After every feature, architectural decision, or discovered quirk. |
| 🏗️ **[ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)** | **System Blueprint & Technical Specs**: Multi-tenancy, service layers, FEFO inventory, and Docker topology. | When domain models, services, middleware, or infrastructure evolve. |
| 📋 **[TASKS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TASKS.md)** | **Sprint Board & WBS**: Active tasks, product backlog, bug tracker, technical debt, and milestone changelog. | When claiming tasks, updating progress, or shipping milestones. |
| 🎨 **[DESIGN.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DESIGN.md)** | **UI/UX Design System**: Color tokens, typography, component standards, DataTables, and A4/thermal printing. | When creating/modifying templates, styles, or print formats. |
| 📏 **[RULES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/RULES.md)** | **Engineering Standards & Invariants**: Multi-tenant query isolation, service boundary rules, and PR checklist. | When establishing or refining coding standards and constraints. |
| ⚡ **[COMMANDS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/COMMANDS.md)** | **Developer Runbook & CLI Reference**: Management commands, uv commands, testing, Docker, Celery, and backups. | When adding management commands, test flags, or operational scripts. |
| 🤖 **[AGENTS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/AGENTS.md)** | **AI Agent Operating Protocol**: Golden 5-step agent workflow, safety guardrails, and doc sync matrix. | When modifying AI agent instructions, workflows, or guardrails. |

### Supplementary Reference Guides
- 🗺️ **[INDEX.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/INDEX.md)** — Master documentation index & sitemap
- 💡 **[DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md)** — Step-by-step developer tutorial for adding new views & models
- 🔌 **[API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)** — JSON API endpoint contracts & payloads
- 🗄️ **[DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)** — Database schema diagrams & table relationships
- 🧹 **[MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)** — Service layer refactoring boundaries
- 🧪 **[TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)** — QA manual test scenarios & verification matrix

### 🔄 Change Synchronization Protocol
Whenever a change is introduced to `zivaBooks`, update the corresponding documents in lockstep:
1. **Starting work**: Claim or add the task in `TASKS.md` (`[IN_PROGRESS]`).
2. **Writing code**: Follow all constraints in `RULES.md` and design specifications in `DESIGN.md`.
3. **Completing work**:
   - Mark task `[COMPLETED]` in `TASKS.md`.
   - Record the decision, change summary, and any gotchas in `MEMORY.md`.
   - If architecture, APIs, or commands changed, update `ARCHITECTURE.md`, `API.md`, or `COMMANDS.md` respectively.


## Quick start

```bash
uv run python manage.py migrate
uv run python manage.py seed_roles_permissions
uv run python manage.py seed_mock_data
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Docker Deployment (Single Docker Compose)

Deploying zivaBooks is completely streamlined with a single, self-contained Docker Compose stack. Database migrations, static assets, and permission seeding run automatically on startup.

### Quick Start (One Command)

```bash
docker compose up -d --build
```

That's it! Open `http://localhost:8000/`.

The stack automatically provisions:
- **`web`**: Django ASGI application running via Uvicorn with automated migrations and static assets.
- **`worker`**: Celery asynchronous background worker.
- **`beat`**: Celery periodic task scheduler.
- **`postgres`**: PostgreSQL 17 database with persistent storage (`postgres_data`).
- **`redis`**: Redis 7 cache and Celery task broker with persistent storage (`redis_data`).

### Managing the Stack

```bash
# View running services and health status
docker compose ps

# Follow logs
docker compose logs -f web worker beat

# Stop containers without losing data
docker compose stop

# Tear down containers and network
docker compose down
```

### Environment Configuration (Optional)

All settings have built-in production-ready defaults. To customize credentials, port, domain, or email:

```bash
cp .env.example .env
# Edit .env with your custom credentials
docker compose up -d --build
```

For convenient shortcuts, you can also use `./start.sh` (e.g. `./start.sh --status`, `./start.sh --logs`, or `./start.sh --dev` for fast host development).

## Configuration

For Hostinger VPS deployment with existing host Nginx, persistent data, environment
variables, and backup/restore procedures, see [Production deployment](deploy/README.md).

Native Python development works without a `.env` file. Docker requires an explicit environment file. Production deployments must set at least:

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
