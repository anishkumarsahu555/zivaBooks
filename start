#!/usr/bin/env bash
# ==============================================================================
# start.sh — Universal Robust Startup & Orchestration Script
# zivaBooks — Multi-Tenant Enterprise Operations & Accounting Platform
# ==============================================================================

set -eo pipefail

# ANSI Color & Formatting Codes
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
MAGENTA='\033[0;35m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# Determine Script Directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DB_REBUILT=0

# ==============================================================================
# UI Banner & Help
# ==============================================================================

print_header() {
    echo -e "${CYAN}${BOLD}"
    echo "======================================================================"
    echo "      🏢 zivaBooks — Multi-Tenant Enterprise Operations Launcher      "
    echo "======================================================================"
    echo -e "${NC}"
}

print_help() {
    print_header
    echo -e "${BOLD}Usage:${NC} ./start.sh [OPTION]"
    echo ""
    echo -e "${BOLD}Lifecycle Commands (Docker Compose Stack):${NC}"
    echo -e "  ${GREEN}(no args)${NC}       Start all services (Docker Compose + migrations + seed + healthchecks)"
    echo -e "  ${GREEN}--build${NC}         Force rebuild Docker images and start container stack"
    echo -e "  ${GREEN}--build-only${NC}    Build Docker images without starting services"
    echo -e "  ${GREEN}--prune${NC}         Remove dangling <none> images and clean build cache"
    echo -e "  ${GREEN}--clean${NC}         Full wipe: delete volumes, rebuild fresh, migrate & seed"
    echo -e "  ${GREEN}--restart${NC}       Gracefully restart all running services"
    echo -e "  ${GREEN}--stop, --down${NC}  Stop and remove all containers"
    echo ""
    echo -e "${BOLD}Developer & Local Workflows (Host Machine):${NC}"
    echo -e "  ${GREEN}--local, --dev${NC}  Run locally on host (hot-reload, auto-provisions venv & deps)"
    echo -e "  ${GREEN}--init${NC}          First-time local setup (creates .env, syncs deps, migrates & seeds)"
    echo -e "  ${GREEN}--doctor${NC}        Execute full system health diagnostic & dependencies check"
    echo -e "  ${GREEN}--test [target]${NC} Execute automated test suite (e.g. --test home.tests.test_license_plans)"
    echo -e "  ${GREEN}--stress [users]${NC} Execute concurrency stress test (e.g. --stress, --stress 100, --stress 500, --stress 1000)"
    echo -e "  ${GREEN}--shell${NC}         Open interactive Django shell"
    echo -e "  ${GREEN}--superuser${NC}     Create Django administrative superuser"
    echo ""
    echo -e "${BOLD}Database & Ledger Operations:${NC}"
    echo -e "  ${GREEN}--migrate${NC}       Apply pending Django database migrations"
    echo -e "  ${GREEN}--seed${NC}          Seed standard roles, permissions, demo tenants & mock data"
    echo -e "  ${GREEN}--backfill${NC}      Backfill accounting books and customer party ledgers"
    echo -e "  ${GREEN}--reset${NC}         Flush database schema, re-migrate and re-seed from scratch"
    echo ""
    echo -e "${BOLD}Production & Infrastructure Operations:${NC}"
    echo -e "  ${GREEN}--deploy${NC}        One-command production deployment on VPS with SSL & Nginx reverse proxy"
    echo -e "  ${GREEN}--update${NC}        One-command zero-downtime production update & migration"
    echo -e "  ${GREEN}--proxy [action]${NC}Manage shared edge reverse proxy (up, down, reload, logs, cert)"
    echo -e "  ${GREEN}--backup${NC}        Trigger atomic hot backup (PostgreSQL, Redis, media, .env secrets)"
    echo -e "  ${GREEN}--restore [tar]${NC} Restore complete system from backup archive"
    echo ""
    echo -e "${BOLD}Observability & Logs:${NC}"
    echo -e "  ${GREEN}--status, --ps${NC}  Check live container and HTTP endpoint readiness matrix"
    echo -e "  ${GREEN}--logs [svc]${NC}    Tail logs across all containers or specific service (web, worker, postgres)"
    echo -e "  ${GREEN}--help, -h${NC}      Show this help menu"
    echo ""
}

# ==============================================================================
# Pre-Flight Checks & Environment Configuration
# ==============================================================================

check_env() {
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}⚠️  .env file not found. Copying from .env.example...${NC}"
        cp .env.example .env
        echo -e "${GREEN}✓ Created local .env configuration file.${NC}"
    fi
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}❌ Docker is not installed or not in PATH.${NC}"
        echo "Please install Docker and Docker Desktop before running in Docker mode."
        echo -e "Tip: You can run locally on host without Docker using: ${BOLD}./start.sh --local${NC}"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo -e "${RED}❌ Docker daemon is not running.${NC}"
        echo "Please launch Docker Desktop, or run locally on host using: ${BOLD}./start.sh --local${NC}"
        exit 1
    fi
}

check_port_conflicts() {
    local pg_ext_port="${POSTGRES_EXTERNAL_PORT:-5433}"
    local redis_ext_port="${REDIS_PORT:-6380}"
    local ports=(8000 "$pg_ext_port" "$redis_ext_port")
    for port in "${ports[@]}"; do
        if command -v lsof &> /dev/null; then
            local pid=$(lsof -ti :$port 2>/dev/null || true)
            if [ -n "$pid" ]; then
                local proc_name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
                if [[ "$proc_name" != *"docker"* ]] && [[ "$proc_name" != *"com.docker"* ]]; then
                    echo -e "${YELLOW}⚠️  Port $port is in use by non-Docker process (PID: $pid, $proc_name).${NC}"
                    echo -e "   If containers fail to bind, terminate it via: ${BOLD}kill -9 $pid${NC}"
                fi
            fi
        fi
    done
}

resolve_python() {
    if [ -n "${PYTHON_BIN:-}" ]; then
        return
    fi
    if command -v uv &> /dev/null; then
        PYTHON_BIN="uv run python"
        CELERY_BIN="uv run celery"
    elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
        PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
        CELERY_BIN="$SCRIPT_DIR/.venv/bin/celery"
    elif command -v python3 &> /dev/null; then
        PYTHON_BIN="python3"
        CELERY_BIN="celery"
    else
        echo -e "${RED}❌ No Python interpreter found.${NC}"
        echo "Install uv (curl -LsSf https://astral.sh/uv/install.sh | sh) or create a virtualenv."
        exit 1
    fi
}

# ==============================================================================
# Healthcheck Probing & Service Readiness
# ==============================================================================

wait_for_db() {
    echo -e "${CYAN}⏳ [1/4] Waiting for PostgreSQL database to be healthy...${NC}"
    local retries=35
    local count=0
    until docker compose exec -T postgres pg_isready -p 5433 -U postgres -d zivaBooks &> /dev/null; do
        sleep 1
        count=$((count + 1))
        echo -ne "   Connecting to PostgreSQL... ($count/${retries}s)\r"
        if [ $count -ge $retries ]; then
            echo -e "\n${RED}❌ Timed out waiting for PostgreSQL container.${NC}"
            echo -e "${YELLOW}Showing last 20 lines of postgres log:${NC}"
            docker compose logs --tail=20 postgres
            exit 1
        fi
    done
    echo -e "${GREEN}✓ PostgreSQL is healthy and accepting connections.              ${NC}"
}

wait_for_redis() {
    echo -e "${CYAN}⏳ [2/4] Waiting for Redis cache broker to be ready...${NC}"
    local retries=25
    local count=0
    until docker compose exec -T redis redis-cli ping | grep -q "PONG" 2>/dev/null; do
        sleep 1
        count=$((count + 1))
        echo -ne "   Connecting to Redis... ($count/${retries}s)\r"
        if [ $count -ge $retries ]; then
            echo -e "\n${RED}❌ Timed out waiting for Redis container.${NC}"
            docker compose logs --tail=20 redis
            exit 1
        fi
    done
    echo -e "${GREEN}✓ Redis is healthy (PONG responded).                           ${NC}"
}

wait_for_backend_http() {
    local port="${PORT:-8000}"
    echo -e "${CYAN}⏳ [3/4] Probing zivaBooks Web Application (http://localhost:${port}/health/)...${NC}"
    local retries=45
    local count=0
    until curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/health/" 2>/dev/null | grep -q "200" || \
          docker compose exec -T web curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/health/" 2>/dev/null | grep -q "200"; do
        sleep 1
        count=$((count + 1))
        echo -ne "   Waiting for Django/Uvicorn server to boot... ($count/${retries}s)\r"
        if [ $count -ge $retries ]; then
            echo -e "\n${RED}❌ Timed out waiting for zivaBooks Web Application.${NC}"
            echo -e "${YELLOW}Showing last 25 lines of web logs:${NC}"
            docker compose logs --tail=25 web
            exit 1
        fi
    done
    echo -e "${GREEN}✓ zivaBooks Web Application is live & responding (HTTP 200).     ${NC}"
}

wait_for_workers() {
    echo -e "${CYAN}⏳ [4/4] Verifying Celery worker & periodic beat status...${NC}"
    if docker compose ps | grep -q "zivabooks_worker.*running"; then
        echo -e "${GREEN}✓ Celery background worker is operational.                       ${NC}"
    else
        echo -e "${YELLOW}⚠️  Celery worker container starting up in background.           ${NC}"
    fi
}

# ==============================================================================
# Database Migrations & Seeding Operations
# ==============================================================================

run_migrations() {
    echo -e "${CYAN}🔄 Applying database migrations...${NC}"
    if docker compose ps | grep -q "zivabooks_web.*running"; then
        docker compose exec -T web uv run python manage.py migrate --noinput
    else
        resolve_python
        $PYTHON_BIN manage.py migrate --noinput
    fi
    echo -e "${GREEN}✓ Database schema is up to date.${NC}"
}

run_seed() {
    echo -e "${CYAN}🌱 Seeding default roles, permissions, and mock demo data...${NC}"
    if docker compose ps | grep -q "zivabooks_web.*running"; then
        docker compose exec -T web uv run python manage.py seed_roles_permissions
        docker compose exec -T web uv run python manage.py seed_mock_data
    else
        resolve_python
        $PYTHON_BIN manage.py seed_roles_permissions
        $PYTHON_BIN manage.py seed_mock_data
    fi
    echo -e "${GREEN}✓ Database seeded successfully.${NC}"
}

run_backfill() {
    echo -e "${CYAN}📚 Backfilling accounting books and customer party ledgers...${NC}"
    if docker compose ps | grep -q "zivabooks_web.*running"; then
        docker compose exec -T web uv run python manage.py backfill_books
        docker compose exec -T web uv run python manage.py backfill_party_ledger
    else
        resolve_python
        $PYTHON_BIN manage.py backfill_books
        $PYTHON_BIN manage.py backfill_party_ledger
    fi
    echo -e "${GREEN}✓ Accounting books and party ledgers backfilled.${NC}"
}

# ==============================================================================
# Presentation Summary
# ==============================================================================

print_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}======================================================================${NC}"
    echo -e "${GREEN}${BOLD}  🚀 zivaBooks Enterprise Operations Platform is Operational!         ${NC}"
    echo -e "${GREEN}${BOLD}======================================================================${NC}"
    echo ""
    echo -e "  🌐 ${BOLD}Web Application URL:${NC}    http://localhost:8000/"
    echo -e "  🩺 ${BOLD}Healthcheck Endpoint:${NC}   http://localhost:8000/health/"
    local pg_ext="${POSTGRES_EXTERNAL_PORT:-5433}"
    local redis_ext="${REDIS_PORT:-6380}"
    echo -e "  🐘 ${BOLD}PostgreSQL Database:${NC}    localhost:${pg_ext} (user: postgres, db: zivaBooks)"
    echo -e "  📮 ${BOLD}Redis Cache / Broker:${NC}   localhost:${redis_ext} (internal: 6379)"
    echo -e "  ⚙️  ${BOLD}Celery Background:${NC}      Worker & Beat running"
    echo ""
    echo -e "${CYAN}${BOLD}Default Demo Credentials:${NC}"
    echo -e "  • ${BOLD}Admin / Owner:${NC}      admin@acme.com        ${DIM}(Full business control center)${NC}"
    echo -e "  • ${BOLD}Field Collector:${NC}    collector@acme.com    ${DIM}(OTP collections, mobile billing)${NC}"
    echo -e "  • ${BOLD}Customer Portal:${NC}    customer@acme.com     ${DIM}(Party ledgers, OTP authorization)${NC}"
    echo -e "  • ${BOLD}Password:${NC}           password123           ${DIM}(For all seeded demo accounts)${NC}"
    echo ""
    echo -e "${BOLD}Quick Operational Commands:${NC}"
    echo -e "  • View live logs:     ${BOLD}./start.sh --logs${NC}"
    echo -e "  • Check health status:${BOLD}./start.sh --status${NC}"
    echo -e "  • Stop containers:    ${BOLD}./start.sh --stop${NC}"
    echo -e "  • Rebuild & update:   ${BOLD}./start.sh --build${NC}"
    echo "======================================================================"
}

# ==============================================================================
# Docker Orchestration Engine
# ==============================================================================

build_docker_images() {
    print_header
    check_docker
    check_env
    echo -e "${CYAN}🔨 Building zivaBooks production Docker image...${NC}"
    docker compose build web celery_worker celery_beat

    echo -e "${CYAN}🧹 Cleaning up dangling intermediate <none> images...${NC}"
    docker image prune -f > /dev/null 2>&1 || true

    echo -e "${GREEN}✓ Docker images compiled successfully.${NC}"
}

prune_docker() {
    print_header
    check_docker
    echo -e "${YELLOW}🧹 [1/2] Pruning dangling (<none>) Docker images...${NC}"
    docker image prune -f
    echo -e "${YELLOW}🧹 [2/2] Pruning reclaimable BuildKit builder cache...${NC}"
    docker builder prune -f
    echo -e "${GREEN}✓ Docker dangling images and builder cache pruned successfully.${NC}"
}

start_docker() {
    local build_flag="$1"
    print_header
    check_docker
    check_env
    check_port_conflicts
    # Resolve persistent data directory (macOS ~/appData/zivabooks vs Linux VPS /appData/zivabooks)
    if [ -z "${DATA_DIR:-}" ]; then
        if [[ "$(uname -s)" == "Darwin" ]]; then
            export DATA_DIR="${HOME}/appData/zivabooks"
        else
            export DATA_DIR="/appData/zivabooks"
        fi
    fi
    mkdir -p "${DATA_DIR}/postgres" "${DATA_DIR}/redis" "${DATA_DIR}/media"

    # Ensure external proxy network exists
    docker network create proxy_network 2>/dev/null || true

    if [ "$build_flag" == "--build" ]; then
        echo -e "${CYAN}🔨 Rebuilding and launching all services via Docker Compose...${NC}"
        docker compose up --build -d --remove-orphans
        echo -e "${CYAN}🧹 Cleaning up dangling intermediate <none> images...${NC}"
        docker image prune -f > /dev/null 2>&1 || true
    else
        echo -e "${CYAN}📦 Launching services via Docker Compose...${NC}"
        docker compose up -d
    fi

    # Step-by-step verification pipeline
    wait_for_db
    wait_for_redis
    run_migrations
    wait_for_backend_http
    wait_for_workers

    print_summary
}

stop_docker() {
    print_header
    check_docker
    echo -e "${YELLOW}🛑 Stopping all zivaBooks containers...${NC}"
    docker compose down
    echo -e "${GREEN}✓ All services stopped cleanly.${NC}"
}

restart_docker() {
    print_header
    check_docker
    echo -e "${YELLOW}🔄 Restarting zivaBooks services...${NC}"
    docker compose restart
    wait_for_db
    wait_for_redis
    wait_for_backend_http
    echo -e "${GREEN}✓ All services restarted and healthy.${NC}"
}

tail_logs() {
    check_docker
    local target_svc="${1:-}"
    if [ -n "$target_svc" ]; then
        echo -e "${CYAN}📜 Streaming logs for [${target_svc}] (Ctrl+C to exit)...${NC}"
        docker compose logs -f "$target_svc"
    else
        echo -e "${CYAN}📜 Streaming logs across all services (Ctrl+C to exit)...${NC}"
        docker compose logs -f
    fi
}

check_status() {
    print_header
    check_docker
    echo -e "${CYAN}${BOLD}Container Runtime Status:${NC}"
    docker compose ps
    echo ""
    echo -e "${CYAN}${BOLD}Live Endpoint Reachability Matrix:${NC}"

    # Web App check
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health/ 2>/dev/null | grep -q "200"; then
        echo -e "  Web Application (http://localhost:8000/health/): ${GREEN}● ONLINE (HTTP 200)${NC}"
    else
        echo -e "  Web Application (http://localhost:8000/health/): ${RED}○ OFFLINE${NC}"
    fi

    # PostgreSQL check
    local pg_ext="${POSTGRES_EXTERNAL_PORT:-5433}"
    if docker compose exec -T postgres pg_isready -p 5433 -U postgres -d zivaBooks 2>/dev/null | grep -q "accepting connections"; then
        echo -e "  PostgreSQL Database (host: ${pg_ext}, internal: 5433): ${GREEN}● READY${NC}"
    else
        echo -e "  PostgreSQL Database (host: ${pg_ext}, internal: 5433): ${RED}○ DOWN${NC}"
    fi

    # Redis check
    local redis_ext="${REDIS_PORT:-6380}"
    if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
        echo -e "  Redis Broker / Cache (host: ${redis_ext}, internal: 6379): ${GREEN}● READY${NC}"
    else
        echo -e "  Redis Broker / Cache (host: ${redis_ext}, internal: 6379): ${RED}○ DOWN${NC}"
    fi

    # Proxy check (if running)
    if docker ps --format '{{.Names}}' | grep -q "edge_proxy"; then
        echo -e "  Shared Edge Reverse Proxy:                       ${GREEN}● READY${NC}"
    else
        echo -e "  Shared Edge Reverse Proxy:                       ${DIM}○ NOT RUNNING (optional)${NC}"
    fi
    echo ""
}

# ==============================================================================
# Local Host Mode (Non-Docker / Developer Mode)
# ==============================================================================

start_local() {
    print_header
    echo -e "${YELLOW}${BOLD}⚙️  Starting zivaBooks in Local Host Mode...${NC}"
    check_env
    resolve_python

    # 1. Dependency check
    echo -e "${CYAN}🐍 [1/3] Verifying Python runtime and dependencies...${NC}"
    if command -v uv &> /dev/null; then
        uv sync
    fi
    echo -e "${GREEN}✓ Python dependencies synced (${PYTHON_BIN}).${NC}"

    # Ensure isolated database service is up if using Docker postgres on 5433
    if [ "${DATABASE_ENGINE:-postgres}" = "postgres" ] && [ "${POSTGRES_PORT:-5433}" = "5433" ]; then
        if command -v docker &> /dev/null && docker info &> /dev/null; then
            if ! docker compose ps postgres 2>/dev/null | grep -q "healthy\|Up"; then
                echo -e "${CYAN}📦 Starting isolated PostgreSQL container on port 5433...${NC}"
                docker compose up -d postgres redis
                wait_for_db
            fi
        fi
    fi

    # 2. Database migrations
    echo -e "${CYAN}🐘 [2/3] Checking database schema & migrations...${NC}"
    $PYTHON_BIN manage.py migrate --noinput
    echo -e "${GREEN}✓ Database migrations applied.${NC}"

    # 3. Launch Development Server with Graceful Trap
    local port="${PORT:-8000}"
    local host="${HOST:-127.0.0.1}"
    echo -e "${CYAN}🚀 [3/3] Launching Django development server...${NC}"
    echo -e "   • Listening on: ${BOLD}http://${host}:${port}/${NC}"
    echo -e "   • Healthcheck:  ${BOLD}http://${host}:${port}/health/${NC}"
    echo -e "   Press ${BOLD}Ctrl+C${NC} to terminate.\n"

    exec $PYTHON_BIN manage.py runserver "${host}:${port}"
}

# ==============================================================================
# System Health Diagnostic (Doctor)
# ==============================================================================

run_doctor() {
    print_header
    echo -e "${CYAN}${BOLD}🩺 Executing System Health Diagnostic...${NC}\n"

    # Python
    if command -v python3 &> /dev/null; then
        local py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
        echo -e "  ${GREEN}[✓]${NC} Python Runtime:    v${py_ver}"
    else
        echo -e "  ${RED}[✗]${NC} Python Runtime:    Not installed"
    fi

    # uv
    if command -v uv &> /dev/null; then
        local uv_ver=$(uv --version | cut -d ' ' -f2)
        echo -e "  ${GREEN}[✓]${NC} uv Package Mgr:    v${uv_ver}"
    else
        echo -e "  ${YELLOW}[!]${NC} uv Package Mgr:    Not installed (recommended: curl -LsSf https://astral.sh/uv/install.sh | sh)"
    fi

    # Virtual environment
    if [ -d "$SCRIPT_DIR/.venv" ]; then
        echo -e "  ${GREEN}[✓]${NC} Virtualenv:        Present (.venv)"
    else
        echo -e "  ${YELLOW}[!]${NC} Virtualenv:        Not found (run: ./start.sh --init)"
    fi

    # Configuration file
    if [ -f "$SCRIPT_DIR/.env" ]; then
        echo -e "  ${GREEN}[✓]${NC} Configuration:     .env file present"
    else
        echo -e "  ${RED}[✗]${NC} Configuration:     .env file missing"
    fi

    resolve_python

    # Django, WeasyPrint & DB
    $PYTHON_BIN -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'zivaBooks.settings')
django.setup()
import weasyprint
print('  \033[0;32m[✓]\033[0m Django Core:       v' + django.__version__)
print('  \033[0;32m[✓]\033[0m WeasyPrint PDF:    v' + weasyprint.__version__ + ' (Cairo/Pango OK)')

from django.db import connection
try:
    connection.cursor()
    engine = connection.settings_dict['ENGINE'].split('.')[-1]
    name = connection.settings_dict['NAME']
    print('  \033[0;32m[✓]\033[0m Database:          Connected (' + engine + ': ' + str(name) + ')')
except Exception as e:
    print('  \033[0;31m[✗]\033[0m Database:          Failed to connect (' + str(e) + ')')

from django.core.cache import cache
try:
    cache.set('doc_test', '1', 5)
    print('  \033[0;32m[✓]\033[0m Cache Engine:      Functional')
except Exception as e:
    print('  \033[1;33m[!]\033[0m Cache Engine:      ' + str(e))
" 2>/dev/null || echo -e "  ${YELLOW}[!]${NC} Django check:      Could not evaluate runtime settings"

    # Docker
    if command -v docker &> /dev/null; then
        local d_ver=$(docker --version | cut -d ' ' -f3 | tr -d ',')
        echo -e "  ${GREEN}[✓]${NC} Docker Engine:     v${d_ver}"
    else
        echo -e "  ${YELLOW}[!]${NC} Docker Engine:     Not installed"
    fi

    # Storage layout
    if [ -d "/appData" ]; then
        echo -e "  ${GREEN}[✓]${NC} Storage Layout:    /appData provisioned"
    elif [ -d "${HOME}/appData/zivabooks" ]; then
        echo -e "  ${GREEN}[✓]${NC} Storage Layout:    ${HOME}/appData/zivabooks provisioned"
    elif [ -d "${SCRIPT_DIR}/data" ]; then
        echo -e "  ${GREEN}[✓]${NC} Storage Layout:    Local ${SCRIPT_DIR}/data provisioned"
    else
        echo -e "  ${YELLOW}[!]${NC} Storage Layout:    /appData not provisioned (run: sudo ./deploy/setup_vps.sh)"
    fi

    echo ""
    echo -e "${GREEN}Diagnostic completed successfully.${NC}"
}

# ==============================================================================
# Automated Test Suite Runner
# ==============================================================================

run_test_suite() {
    print_header
    local target="${1:-}"
    echo -e "${CYAN}${BOLD}🧪 Executing Automated Test Suite...${NC}\n"

    resolve_python
    if [ -n "$target" ]; then
        echo -e "${CYAN}Running specific test target: ${BOLD}${target}${NC}"
        $PYTHON_BIN manage.py test "$target"
    else
        echo -e "${CYAN}Running full test suite across all apps...${NC}"
        $PYTHON_BIN manage.py test
    fi
    echo -e "\n${GREEN}${BOLD}✨ Test suite execution completed!${NC}"
}

# ==============================================================================
# Stress & Load Testing Suite
# ==============================================================================

run_stress_test() {
    print_header
    local target_users="${1:-}"
    local duration="${2:-30s}"
    echo -e "${CYAN}${BOLD}⚡ Executing Stress & Concurrency Test Suite...${NC}\n"

    # Ensure application is running and accessible
    if ! curl -sf http://localhost:8000/health/ > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  zivaBooks is not responding at http://localhost:8000/health/${NC}"
        echo -e "${CYAN}Starting container stack first...${NC}"
        start_docker ""
        echo -e "${CYAN}Waiting for services to become healthy...${NC}"
        sleep 5
    fi

    local runner_script="${SCRIPT_DIR}/stress_testing/run_stress_tests.py"
    if [ ! -f "$runner_script" ]; then
        echo -e "${RED}❌ Stress test runner not found at ${runner_script}${NC}"
        exit 1
    fi

    resolve_python
    if [ -n "$target_users" ]; then
        echo -e "${CYAN}Running targeted stress test for ${BOLD}${target_users}${NC}${CYAN} concurrent users (duration: ${duration})...${NC}\n"
        $PYTHON_BIN "$runner_script" "$target_users" "$duration"
    else
        echo -e "${CYAN}Running full benchmark suite across ${BOLD}100, 500, and 1000 users${NC}...\n"
        $PYTHON_BIN "$runner_script"
    fi

    echo -e "\n${GREEN}${BOLD}✨ Stress testing completed!${NC}"
    echo -e "Interactive HTML reports and metrics saved in: ${BOLD}${SCRIPT_DIR}/stress_testing/results/${NC}\n"
}

# ==============================================================================
# Production VPS Deployment & Backup Integration
# ==============================================================================

run_deploy() {
    print_header
    local domain="${1:-}"
    local email="${2:-}"

    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}❌ Deployment setup requires root/sudo privileges.${NC}"
        echo "Please run: sudo ./start.sh --deploy"
        exit 1
    fi

    if [ -z "$domain" ]; then
        echo -ne "Enter production domain name (e.g. app.zivabooks.com): "
        read -r domain
    fi

    if [ -z "$email" ]; then
        echo -ne "Enter administrator email for Let's Encrypt SSL: "
        read -r email
    fi

    echo -e "${CYAN}🚀 Initializing automated VPS deployment for ${domain}...${NC}"
    ./deploy/setup_vps.sh

    # Auto-generate production .env if missing
    local app_env="/opt/apps/zivabooks/.env"
    if [ ! -f "$app_env" ]; then
        local random_secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))" 2>/dev/null || openssl rand -hex 32)
        local random_pass=$(openssl rand -hex 16)
        cp "$SCRIPT_DIR/deploy/apps/zivabooks/.env.production.example" "$app_env"
        sed -i "s/replace-with-a-64-character-random-secret-key-in-production/${random_secret}/g" "$app_env"
        sed -i "s/replace-with-secure-postgres-password/${random_pass}/g" "$app_env"
        sed -i "s/app.zivabooks.com/${domain}/g" "$app_env"
        chmod 600 "$app_env"
        echo -e "${GREEN}✓ Production .env configured at ${app_env}.${NC}"
    fi

    # Update Nginx domain
    sed -i "s/app.yourdomain.com/${domain}/g" "/opt/apps/proxy/conf.d/zivabooks.conf"

    # Start reverse proxy and request SSL
    (cd /opt/apps/proxy && docker compose up -d)
    (cd /opt/apps/proxy && docker compose run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot -d ${domain} --email ${email} --agree-tos --no-eff-email" certbot) || true
    (cd /opt/apps/proxy && docker compose exec nginx nginx -s reload || true)

    # Launch application stack
    (cd /opt/apps/zivabooks && docker compose up -d --build)

    echo ""
    echo -e "${GREEN}${BOLD}======================================================================${NC}"
    echo -e "${GREEN}${BOLD}  🎉 zivaBooks Successfully Deployed to Production!                    ${NC}"
    echo -e "${GREEN}${BOLD}======================================================================${NC}"
    echo -e "  🌐 Live URL:           https://${domain}/"
    echo -e "  🩺 Health Check:       https://${domain}/health/"
    echo -e "  🔒 SSL Certificate:    Let's Encrypt (Auto-renewed)"
    echo -e "  💾 Nightly Backups:    Scheduled at 02:00 AM (/opt/backups/daily/)"
    echo "======================================================================"
}

run_update() {
    print_header
    echo -e "${CYAN}🔄 Pulling latest code and applying zero-downtime update...${NC}"
    git pull origin main

    if [ -f "/opt/apps/zivabooks/docker-compose.yml" ] && docker ps | grep -q "zivabooks_web"; then
        (cd /opt/apps/zivabooks && docker compose up -d --build)
        (cd /opt/apps/zivabooks && docker compose exec web uv run python manage.py migrate --noinput)
        (cd /opt/apps/zivabooks && docker compose exec web uv run python manage.py collectstatic --noinput)
        echo -e "${GREEN}✓ Production Docker update completed cleanly.${NC}"
    else
        resolve_python
        if command -v uv &> /dev/null; then uv sync; fi
        $PYTHON_BIN manage.py migrate --noinput
        $PYTHON_BIN manage.py collectstatic --noinput
        echo -e "${GREEN}✓ Local update completed cleanly.${NC}"
    fi
}

# ==============================================================================
# Command Dispatcher
# ==============================================================================

case "${1:-}" in
    --help|-h|help)
        print_help
        ;;
    --build|build)
        start_docker "--build"
        ;;
    --build-only)
        build_docker_images
        ;;
    --prune|prune)
        prune_docker
        ;;
    --clean|clean)
        print_header
        check_docker
        echo -e "${YELLOW}🧹 Wiping old Docker volumes (resetting PostgreSQL & Redis data)...${NC}"
        docker compose down -v
        echo -e "${CYAN}🔨 Rebuilding and launching all services fresh...${NC}"
        start_docker "--build"
        ;;
    --restart|restart)
        restart_docker
        ;;
    --stop|--down|down|stop)
        stop_docker
        ;;
    --local|--dev|local|dev)
        start_local
        ;;
    --init|init)
        print_header
        check_env
        resolve_python
        echo -e "${CYAN}🌱 Initializing local development environment...${NC}"
        if command -v uv &> /dev/null; then uv sync; fi
        run_migrations
        run_seed
        echo -e "${GREEN}✓ Setup complete! Start anytime via: ./start.sh --local${NC}"
        ;;
    --doctor|doctor)
        run_doctor
        ;;
    --migrate|migrate)
        check_docker 2>/dev/null || true
        run_migrations
        ;;
    --seed|seed)
        check_docker 2>/dev/null || true
        run_seed
        ;;
    --backfill|backfill)
        check_docker 2>/dev/null || true
        run_backfill
        ;;
    --reset|reset)
        print_header
        echo -e "${YELLOW}🔄 Resetting database schema and re-seeding...${NC}"
        resolve_python
        $PYTHON_BIN manage.py flush --noinput
        run_migrations
        run_seed
        echo -e "${GREEN}✓ Database reset and re-seeded successfully.${NC}"
        ;;
    --logs|logs)
        tail_logs "${2:-}"
        ;;
    --status|--ps|status|ps)
        check_status
        ;;
    --test|test)
        run_test_suite "${2:-}"
        ;;
    --stress|stress)
        run_stress_test "${2:-}" "${3:-}"
        ;;
    --shell|shell)
        resolve_python
        exec $PYTHON_BIN manage.py shell
        ;;
    --superuser|createsuperuser)
        resolve_python
        exec $PYTHON_BIN manage.py createsuperuser
        ;;
    --deploy|deploy)
        run_deploy "${2:-}" "${3:-}"
        ;;
    --update|update)
        run_update
        ;;
    --proxy|proxy)
        PROXY_DIR="/opt/apps/proxy"
        if [ ! -d "$PROXY_DIR" ] && [ -d "${SCRIPT_DIR}/deploy/apps/proxy" ]; then
            PROXY_DIR="${SCRIPT_DIR}/deploy/apps/proxy"
        fi
        (cd "$PROXY_DIR" && docker compose "${2:-up}" "${@:3}")
        ;;
    --backup|backup)
        BACKUP_SCRIPT="/opt/backups/scripts/backup.sh"
        if [ ! -f "$BACKUP_SCRIPT" ] && [ -f "${SCRIPT_DIR}/deploy/backups/scripts/backup.sh" ]; then
            BACKUP_SCRIPT="${SCRIPT_DIR}/deploy/backups/scripts/backup.sh"
        fi
        exec "$BACKUP_SCRIPT"
        ;;
    --restore|restore)
        RESTORE_SCRIPT="/opt/backups/scripts/restore.sh"
        if [ ! -f "$RESTORE_SCRIPT" ] && [ -f "${SCRIPT_DIR}/deploy/backups/scripts/restore.sh" ]; then
            RESTORE_SCRIPT="${SCRIPT_DIR}/deploy/backups/scripts/restore.sh"
        fi
        exec "$RESTORE_SCRIPT" "${2:-}"
        ;;
    *)
        start_docker ""
        ;;
esac
