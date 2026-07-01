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

## Quick start

```bash
uv run python manage.py migrate
uv run python manage.py seed_roles_permissions
uv run python manage.py seed_mock_data
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Configuration

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
