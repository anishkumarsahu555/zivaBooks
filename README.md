# zivaBooks

**zivaBooks** is a high-performance, multi-tenant Django enterprise platform for business operations, wholesale billing, FEFO batch inventory, dual-party OTP collections, accounting ledgers, POS cash counters, custom subscription licensing, and conversational AI business intelligence.

---

## 🌟 Core Capabilities

- 🏢 **Rigid Row-Level Multi-Tenancy**: Complete isolation of customers, vendors, staff, and financial ledgers via `TenantOwnedModel`.
- 📦 **FEFO Batch Inventory**: Automated First-Expiry-First-Out batch tracking with warehouse transfers, physical adjustment vouchers, and ledger audit logs.
- 💰 **Accounting Books & Ledgers**: Real-time Cashbook, Bankbook, and Party Ledgers with automated balance reconciliation and double-entry journal vouchers.
- 🔒 **Dual-Party OTP Verification**: Payer OTP confirmation on collection vouchers and deliveries to prevent fraud and discrepancies.
- ⚡ **POS Cash Counters**: Counter drawer sessions, instant thermal receipt (80mm / 3-inch) printing, and automated end-of-day cash reconciliation.
- 📜 **Dynamic License Plans**: Custom subscription tiers with 25+ granular subsystem feature toggles, seat limits, and automatic permission syncing to `{slug}:Tenant Admin`.
- 🤖 **Ziva AI Business Assistant**: Integrated AI copilot powered by OpenAI / Ollama local LLM with monthly quota controls and tenant context grounding.
- 📱 **Progressive Web App (PWA)**: Mobile-optimized interface with offline shell caching, camera-ready workflows, and GPS attendance route tracking.
- 📄 **Export Engine**: High-fidelity A4 GST Tax Invoices and accounting statements via WeasyPrint and Excel reports via openpyxl.

---

## 📚 Documentation Hub

Explore the comprehensive project documentation suite:

| Document | Focus & Description |
|---|---|
| 🗺️ **[INDEX.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/INDEX.md)** | Master Documentation Index, Sitemap, and quick navigation matrix |
| 🧠 **[MEMORY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MEMORY.md)** | Core system memory, persistent domain knowledge, and architectural gotchas |
| 📋 **[TASKS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TASKS.md)** | Completed milestones, active development backlog, and technical debt |
| 🏗️ **[ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)** | High-level system architecture, middleware pipeline, and data mutation flows |
| 🎨 **[DESIGN.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DESIGN.md)** | UI/UX design tokens, typography, component guidelines, and print layouts |
| 💻 **[COMMANDS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/COMMANDS.md)** | Exhaustive CLI reference for uv, manage.py, Celery, Redis, and backups |
| 📜 **[RULES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/RULES.md)** | Engineering invariants, tenant isolation rules, and coding standards |
| 🧪 **[VALIDATION.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/VALIDATION.md)** | Automated test execution matrix, QA protocols, and release checklists |
| 🔌 **[API.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/API.md)** | JSON API specification, endpoint contracts, and error handling |
| 🗄️ **[DATABASE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DATABASE.md)** | Database schema, ER diagrams, and model inheritance hierarchy |
| 💡 **[DEVELOPMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DEVELOPMENT.md)** | Step-by-step developer guide for creating features, APIs, and services |
| 🧹 **[MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md)** | Code refactoring boundaries and service layer guidelines |
| 🔬 **[TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md)** | QA end-to-end manual test scenarios |
| 🏷️ **[CUSTOM_TENANT_PLAN_GUIDE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/CUSTOM_TENANT_PLAN_GUIDE.md)** | Admin guide for 25+ feature toggles and custom enterprise plans |
| 🚢 **[deploy/DEPLOYMENT.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/deploy/DEPLOYMENT.md)** | Production Docker Compose + Reverse Proxy deployment & backup layout (`/opt/`) |
| 🖥️ **[server-setup.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/server-setup.md)** | PostgreSQL and server provisioning guide |
| 🚢 **[zivabooks_deployment.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/zivabooks_deployment.md)** | Production systemd, Nginx reverse proxy, and SSL hardening |

---

## 🚀 Effortless Local Development (1 Command)

```bash
# 1. One-command fresh initialization (creates .env, installs deps, runs migrations, seeds demo data)
./start.sh init

# 2. Start local development server (http://127.0.0.1:8000)
./start.sh
# or simply:
make dev

# 3. Diagnose system health & dependencies anytime
./start.sh doctor
# or:
make doctor
```

---

## 🚢 Effortless Production Deployment (1 Command)

Deploy to any Ubuntu / Debian VPS with automated SSL, reverse proxy, and persistent storage:

```bash
# One-command automated production deployment:
sudo ./start.sh deploy --domain app.zivabooks.com --email admin@zivabooks.com

# Zero-downtime updates anytime code changes:
./start.sh update
# or:
make update
```

---

## ⚙️ Environment Configuration

The application works out of the box with SQLite for local development. For production or staging environments, create a `.env` file in the project root:

```dotenv
DEBUG=false
SECRET_KEY=generate-a-strong-secret-key-here
ALLOWED_HOSTS=app.zivabooks.com,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://app.zivabooks.com

# PostgreSQL Configuration
DATABASE_ENGINE=postgres
POSTGRES_DB=zivaBooks
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

# Redis Cache & Celery
USE_REDIS_CACHE=true
REDIS_CACHE_URL=redis://127.0.0.1:6379/1
CELERY_BROKER_URL=redis://127.0.0.1:6379/0

# Email Notifications (SMTP)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=support@zivabooks.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=zivaBooks <support@zivabooks.com>

# Ziva AI Integration
OPENAI_API_KEY=sk-...
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e2b
```

---

## 🩺 Health Check & Monitoring

The system exposes a lightweight operational health check endpoint:

```text
GET /health/
```

Returns JSON with database and cache connectivity status:
```json
{
  "status": "healthy",
  "database": "ok",
  "cache": "ok"
}
```
Use this endpoint for uptime monitors, container probes, and load balancer health checks.

---

## 🧪 Testing

Run the full automated test suite:
```bash
./start.sh test
# or
uv run python manage.py test
```

Run specific test modules:
```bash
uv run python manage.py test home.tests.test_license_plans
uv run python manage.py test home.tests.test_functional_end_to_end_flows
uv run python manage.py test home.tests.test_cache_tenant_isolation
```

---

## 📜 License & Support

Developed by **Ziva Infotech**. For licensing inquiries, custom enterprise deployments, or technical assistance, contact `support@zivabooks.com`.
