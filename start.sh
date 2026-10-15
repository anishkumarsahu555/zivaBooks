#!/usr/bin/env bash
# Local Docker workflow. Compatible with macOS Bash 3.2 and Linux Bash.
set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  cat <<'HELP'
Usage: ./start.sh [options] [action]

Default: start db, redis, web and worker in Docker, waiting for health checks.

Local Dev (NO Docker image build required):
  --dev [PORT]      Start DB/Redis in Docker & run Django dev server on host (live reload)
  --services        Start only DB and Redis in Docker (ports exposed for host dev)
  --dev-worker      Run Celery worker locally on host

Startup options:
  --build           Rebuild the shared web/worker image (use after code changes)
  --migrate         Apply database migrations before starting the app
  --no-worker       Stop/omit the Celery worker for this startup
  --follow          Follow web/worker logs after startup (Ctrl-C leaves app running)
  --sqlite          Use SQLite instead of PostgreSQL for --dev or --test

Actions (choose one; default is containerized startup):
  --dev [PORT]      Run local host dev server (live reload, no Docker image build)
  --services        Start DB & Redis containers only (no web/worker, no build)
  --dev-worker      Run Celery worker on host (connecting to local/Docker Redis)
  --init            Create .env.local if missing and local data directories
  --status          Show container status
  --stop            Stop containers, preserving data
  --down            Remove containers/network, preserving data
  --restart         Restart web and worker without rebuilding or migrating
  --logs [SERVICE…] Follow logs (default: web worker)
  --shell           Open a shell in the running web container
  --dbshell         Open psql in the running database container
  --manage ARGS…    Run manage.py in the running web container
  --test [ARGS…]    Run host source tests via uv using SQLite
  -h, --help        Show help

Global option (place before actions that accept trailing arguments):
  --env-file PATH   Alternative LOCAL env file; default: <repo>/.env.local

Examples:
  ./start.sh --dev                    # Fast local dev: Docker DB/Redis + host Django (no build)
  ./start.sh --dev --migrate          # Run migrations on host then start dev server
  ./start.sh --dev --sqlite           # Run entirely on host with SQLite (no Docker needed)
  ./start.sh --services               # Start DB & Redis in Docker for manual host commands
  ./start.sh --init                   # Initialize local folders and .env.local
  ./start.sh --build --migrate        # Full containerized stack with image rebuild
  ./start.sh --manage check
  ./start.sh --test home.tests.test_services

Migrations are opt-in; back up valuable data before --migrate. If migration fails,
writers remain stopped. There is no reset, volume deletion or pruning command.
Local data must be inside <repo>/local-data/. This script is not for production.
HELP
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
action=start
selected=0
build=0
migrate=0
no_worker=0
follow=0
use_sqlite=0
env_file="$ROOT/.env.local"
args=()
choose() {
  [ "$selected" -eq 0 ] || fail 'Choose only one action.'
  selected=1
  action="$1"
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --env-file)
      [ "$#" -ge 2 ] && [ -n "$2" ] || fail '--env-file requires a path.'
      env_file="$2"; shift ;;
    --build) build=1 ;;
    --migrate) migrate=1 ;;
    --no-worker) no_worker=1 ;;
    --follow) follow=1 ;;
    --sqlite) use_sqlite=1 ;;
    --dev)
      choose dev
      shift
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --sqlite) use_sqlite=1 ;;
          --migrate) migrate=1 ;;
          *) args+=("$1") ;;
        esac
        shift
      done
      break
      ;;
    --init|--status|--stop|--down|--restart|--shell|--dbshell|--services|--dev-worker)
      choose "${1#--}" ;;
    --logs|--manage|--test)
      choose "${1#--}"; shift; args=("$@"); break ;;
    *) fail "Unknown option: $1 (use --help)." ;;
  esac
  shift
done
if [ "$action" != start ] && [ "$action" != dev ] && [ "$((build + no_worker + follow))" -gt 0 ]; then
  fail '--build, --no-worker and --follow are container startup options only.'
fi
if [ "$action" = test ]; then
  command -v uv >/dev/null || fail 'Install uv to run host tests.'
  exec env DATABASE_ENGINE=sqlite USE_REDIS_CACHE=false WRITE_LOG_ON_FILE=false \
    uv run python manage.py test "${args[@]}"
fi
if [ "$action" = manage ] && [ "${#args[@]}" -eq 0 ]; then
  fail '--manage requires a management command.'
fi

if [ "$action" = dev ] && [ "$use_sqlite" -eq 1 ]; then
  command -v uv >/dev/null || fail 'Install uv to run host development server.'
  printf '🚀 Starting zivaBooks in local host mode with SQLite (no Docker)...\n'
  export DATABASE_ENGINE=sqlite
  export USE_REDIS_CACHE=false
  export WRITE_LOG_ON_FILE=false
  export DEBUG=true
  if [ "$migrate" -eq 1 ]; then
    printf 'Applying database migrations with SQLite...\n'
    uv run python manage.py migrate
  fi
  port="${args[0]:-8000}"
  printf '✨ Server starting at http://127.0.0.1:%s/\n\n' "$port"
  exec uv run python manage.py runserver "127.0.0.1:$port"
fi

command -v docker >/dev/null || fail 'Install Docker Desktop (Mac) or Docker Engine with Compose.'
command -v python3 >/dev/null || fail 'Python 3 is required to validate local settings.'
docker compose version >/dev/null
if [ "$action" = init ] && [ ! -f "$env_file" ]; then
  (umask 077; set -o noclobber; cat "$ROOT/.env.local.example" > "$env_file")
  printf 'Created %s. Match credentials to any existing database before startup.\n' "$env_file"
fi
[ -f "$env_file" ] || fail 'Environment file missing. Run ./start.sh --init, then edit .env.local.'

# Compose definitions
compose=(docker compose --project-directory "$ROOT" --env-file "$env_file" -f "$ROOT/docker-compose.yml")
dev_compose_file="$ROOT/docker-compose.dev.yml"
compose_dev_args=(-f "$ROOT/docker-compose.yml")
if [ -f "$dev_compose_file" ]; then
  compose_dev_args+=(-f "$dev_compose_file")
fi
compose_dev=(docker compose --project-directory "$ROOT" --env-file "$env_file" "${compose_dev_args[@]}")

# Compose parses dotenv syntax; never source an env file as executable shell code.
# Validate the resolved config, including shell overrides, without printing secrets.
"${compose[@]}" config --format json | python3 -c '
import json, pathlib, sys
config = json.load(sys.stdin)
root = pathlib.Path(sys.argv[1]).resolve() / "local-data"
web = config["services"]["web"]
if str(web["environment"].get("DEBUG", "")).lower() != "true":
    sys.exit("Refusing non-local config: DEBUG must be true.")
if config["name"].endswith("-prod"):
    sys.exit("Refusing production project name.")
paths = []
for service in ("db", "redis", "web"):
    for mount in config["services"][service].get("volumes", []):
        path = pathlib.Path(mount["source"]).resolve()
        if mount["type"] != "bind" or root not in path.parents:
            sys.exit("Refusing storage outside the repository local-data directory.")
        paths.append(path)
if sys.argv[2] == "init":
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
else:
    if any(not path.is_dir() for path in paths):
        sys.exit("Local data directories are missing. Check DATA_ROOT; use --init only for intended storage.")
' "$ROOT" "$action"
[ "$action" != init ] || { printf 'Local folders ready; review your env file before starting.\n'; exit 0; }
docker info >/dev/null 2>&1 || fail 'Docker is not available. Start Docker Desktop or the Docker daemon.'

case "$action" in
  status) "${compose[@]}" ps -a ;;
  stop) "${compose[@]}" stop --timeout -1 ;;
  down) "${compose[@]}" stop --timeout -1; "${compose[@]}" down ;;
  restart) "${compose[@]}" stop --timeout -1 web worker; "${compose[@]}" start web worker ;;
  logs)
    [ "${#args[@]}" -gt 0 ] || args=(web worker)
    for service in "${args[@]}"; do
      case "$service" in web|worker|db|redis) ;; *) fail "Unknown log service: $service" ;; esac
    done
    "${compose[@]}" logs --follow --tail 100 "${args[@]}" ;;
  shell) "${compose[@]}" exec web /bin/sh ;;
  dbshell) "${compose[@]}" exec db sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' ;;
  manage) "${compose[@]}" exec web python manage.py "${args[@]}" ;;
  services)
    printf '📦 Starting PostgreSQL & Redis in Docker (no custom image build required)...\n'
    "${compose_dev[@]}" up -d --wait db redis
    "${compose_dev[@]}" ps db redis
    printf '\n✨ Backing services ready!\n'
    printf '   Run local dev: ./start.sh --dev\n'
    printf '   Or host commands: uv run python manage.py runserver\n'
    ;;
  dev-worker)
    command -v uv >/dev/null || fail 'Install uv to run Celery worker on host.'
    export DATABASE_ENGINE=postgres
    export POSTGRES_HOST=127.0.0.1
    export POSTGRES_PORT="${POSTGRES_HOST_PORT:-5433}"
    export POSTGRES_DB="${POSTGRES_DB:-zivaBooks}"
    export POSTGRES_USER="${POSTGRES_USER:-postgres}"
    export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-pass}"
    export USE_REDIS_CACHE=true
    export REDIS_CACHE_URL="redis://127.0.0.1:${REDIS_HOST_PORT:-6380}/1"
    export CELERY_BROKER_URL="redis://127.0.0.1:${REDIS_HOST_PORT:-6380}/0"
    export CELERY_RESULT_BACKEND="redis://127.0.0.1:${REDIS_HOST_PORT:-6380}/0"
    printf '🚀 Starting Celery worker on host...\n'
    exec uv run celery -A zivaBooks worker -l info
    ;;
  dev)
    command -v uv >/dev/null || fail 'Install uv to run host development server.'
    printf '📦 Starting PostgreSQL & Redis containers in Docker (no image build required)...\n'
    "${compose_dev[@]}" up -d --wait db redis

    dev_env_exports="$("${compose_dev[@]}" config --format json | python3 -c '
import json, shlex, sys
config = json.load(sys.stdin)
db = config.get("services", {}).get("db", {})
env = db.get("environment", {})
ports = db.get("ports", [])
pub_port = "5433"
for p in ports:
    if p.get("target") == 5432:
        pub_port = str(p.get("published", "5433"))
        break
pg_db = str(env.get("POSTGRES_DB", "zivaBooks"))
pg_user = str(env.get("POSTGRES_USER", "postgres"))
pg_pass = str(env.get("POSTGRES_PASSWORD", "pass"))

redis = config.get("services", {}).get("redis", {})
r_ports = redis.get("ports", [])
r_pub_port = "6380"
for p in r_ports:
    if p.get("target") == 6379:
        r_pub_port = str(p.get("published", "6380"))
        break

print("export POSTGRES_PORT=" + shlex.quote(pub_port))
print("export POSTGRES_DB=" + shlex.quote(pg_db))
print("export POSTGRES_USER=" + shlex.quote(pg_user))
print("export POSTGRES_PASSWORD=" + shlex.quote(pg_pass))
print("export REDIS_PORT=" + shlex.quote(r_pub_port))
')"
    eval "$dev_env_exports"

    export DATABASE_ENGINE=postgres
    export POSTGRES_HOST=127.0.0.1
    export USE_REDIS_CACHE=true
    export REDIS_CACHE_URL="redis://127.0.0.1:${REDIS_PORT}/1"
    export CELERY_BROKER_URL="redis://127.0.0.1:${REDIS_PORT}/0"
    export CELERY_RESULT_BACKEND="redis://127.0.0.1:${REDIS_PORT}/0"
    export DEBUG=true
    export DB_CONN_MAX_AGE=0

    if [ "$migrate" -eq 1 ]; then
      printf '⚡ Applying database migrations on host...\n'
      uv run python manage.py migrate
    fi

    port="${args[0]:-8000}"
    printf '\n✨ zivaBooks Local Development Server Ready!\n'
    printf '   URL:          http://127.0.0.1:%s/\n' "$port"
    printf '   Database:     PostgreSQL at 127.0.0.1:%s (db: %s)\n' "$POSTGRES_PORT" "$POSTGRES_DB"
    printf '   Cache/Broker: Redis at 127.0.0.1:%s\n' "$REDIS_PORT"
    printf '   Live Reload:  Active (code edits reload instantly without rebuilding images)\n\n'
    exec uv run python manage.py runserver "127.0.0.1:$port"
    ;;
  start)
    if [ "$build" -eq 1 ]; then "${compose[@]}" build web; fi
    "${compose[@]}" up -d --wait db redis
    if [ "$migrate" -eq 1 ]; then
      "${compose[@]}" stop --timeout -1 web worker
      "${compose[@]}" run --rm --no-deps migrate || fail 'Migration failed; web/worker remain stopped. Review the error before retrying.'
    fi
    services=(web worker)
    if [ "$no_worker" -eq 1 ]; then
      "${compose[@]}" stop --timeout -1 worker
      services=(web)
    fi
    "${compose[@]}" up -d --wait --no-build "${services[@]}"
    "${compose[@]}" ps
    printf 'Local app ready. Run ./start.sh --logs to view logs.\n'
    if [ "$follow" -eq 1 ]; then "${compose[@]}" logs --follow --tail 100 "${services[@]}"; fi ;;
esac
