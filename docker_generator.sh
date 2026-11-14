#!/usr/bin/env bash
# Portable scaffolding only: never starts Docker or modifies existing output files.
set -euo pipefail
umask 077
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: docker_generator.sh --project NAME --output DIR --host-port PORT [options]

Required:
  --project NAME       Unique VPS project name: lowercase letters, digits, hyphens
  --output DIR         Project root (must contain Dockerfile unless --image is used)
  --host-port PORT     Unique localhost port for a host reverse proxy, e.g. 8101
Options:
  --database ENGINE    postgres (default), mysql, or none
  --redis              Include authenticated Redis with persistent storage
  --app-port PORT      Port the application listens on (default: 8000)
  --image IMAGE        Use an existing image instead of building ./Dockerfile
  --help               Show help

Generates compose.yaml, .env, app.env, DOCKER.generated.md, and ignore rules.
Existing generated files or Compose files are never overwritten. Requires Bash
and OpenSSL; Docker is only required when validating/starting the generated stack.
Your image/Dockerfile must provide its own startup command, listen on 0.0.0.0,
and support the generated database/cache environment variables (or adapt them).
HELP
}
project='' output='' host_port='' app_port=8000 database=postgres redis=false app_image=''
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --redis) redis=true; shift ;;
        --project|--output|--host-port|--app-port|--database|--image)
            (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || fail "Missing value for $1"
            case "$1" in
                --project) project=$2 ;; --output) output=$2 ;;
                --host-port) host_port=$2 ;; --app-port) app_port=$2 ;;
                --database) database=$2 ;; --image) app_image=$2 ;;
            esac
            shift 2 ;;
        *) fail "Unknown option: $1 (see --help)" ;;
    esac
done
[[ "$project" =~ ^[a-z][a-z0-9-]{0,39}$ ]] || fail 'Supply --project: 1-40 lowercase letters/digits/hyphens, starting with a letter'
[[ -n "$output" ]] || fail 'Supply --output'
for port in "$host_port" "$app_port"; do
    [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && ((port <= 65535)) || fail 'Ports must be integers from 1 to 65535 (no leading zeros)'
done
case "$database" in postgres|mysql|none) ;; *) fail '--database must be postgres, mysql, or none' ;; esac
[[ -z "$app_image" || "$app_image" =~ ^[a-zA-Z0-9][a-zA-Z0-9./:@_-]*$ ]] || fail 'Invalid image reference'
command -v openssl >/dev/null || fail 'OpenSSL is required to generate credentials'
[[ -n "$app_image" || -f "$output/Dockerfile" ]] || fail 'Provide --image or an existing Dockerfile in --output'
mkdir -p -- "$output"
output=$(cd -- "$output" && pwd)
# Alternate Compose filenames can otherwise silently take precedence.
for file in compose.yaml compose.yml docker-compose.yml docker-compose.yaml .env app.env DOCKER.generated.md; do
    [[ ! -e "$output/$file" && ! -L "$output/$file" ]] || fail "Refusing to overwrite $output/$file"
done
for file in .gitignore .dockerignore; do
    [[ ! -L "$output/$file" ]] || fail "Refusing to follow symlink $output/$file"
    [[ ! -e "$output/$file" || -f "$output/$file" ]] || fail "$file must be a regular file"
done
# Refuse races with existing files as well as the preflight checks above.
set -o noclobber
{
    printf 'COMPOSE_PROJECT_NAME=%s\nHOST_PORT=%s\nAPP_PORT=%s\n' "$project" "$host_port" "$app_port"
    if [[ "$database" != none ]]; then
        printf 'DB_NAME=app\nDB_USER=app\nDB_PASSWORD=%s\n' "$(openssl rand -hex 32)"
        [[ "$database" != mysql ]] || printf 'MYSQL_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    fi
    [[ "$redis" != true ]] || printf 'REDIS_PASSWORD=%s\n' "$(openssl rand -hex 32)"
} > "$output/.env"
printf '# Add application-specific environment variables here. Do not commit secrets.\n' > "$output/app.env"
{
    cat <<'YAML'
name: ${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME in .env}

x-defaults: &defaults
  restart: unless-stopped
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"

services:
  web:
    <<: *defaults
YAML
    if [[ -n "$app_image" ]]; then
        printf '    image: "%s"\n' "$app_image"
    else
        printf '    build:\n      context: .\n      dockerfile: Dockerfile\n'
    fi
    cat <<'YAML'
    env_file: [app.env]
    ports:
      - "127.0.0.1:${HOST_PORT:?Set HOST_PORT}:${APP_PORT:?Set APP_PORT}"
    environment:
      PORT: ${APP_PORT}
YAML
    if [[ "$database" != none ]]; then
        if [[ "$database" == postgres ]]; then db_port=5432; scheme=postgresql; else db_port=3306; scheme=mysql; fi
        cat <<YAML
      DATABASE_ENGINE: $database
      DB_HOST: $database
      DB_PORT: "$db_port"
      DB_NAME: \${DB_NAME}
      DB_USER: \${DB_USER}
      DB_PASSWORD: \${DB_PASSWORD:?Set DB_PASSWORD}
      DATABASE_URL: "$scheme://\${DB_USER}:\${DB_PASSWORD}@$database:$db_port/\${DB_NAME}"
YAML
    fi
    if [[ "$redis" == true ]]; then
        cat <<'YAML'
      REDIS_URL: "redis://:${REDIS_PASSWORD:?Set REDIS_PASSWORD}@redis:6379/0"
      REDIS_CACHE_URL: "redis://:${REDIS_PASSWORD}@redis:6379/1"
      CELERY_BROKER_URL: "redis://:${REDIS_PASSWORD}@redis:6379/0"
      CELERY_RESULT_BACKEND: "redis://:${REDIS_PASSWORD}@redis:6379/0"
YAML
    fi
    if [[ "$database" != none || "$redis" == true ]]; then
        printf '    depends_on:\n'
        [[ "$database" == none ]] || printf '      %s:\n        condition: service_healthy\n' "$database"
        [[ "$redis" != true ]] || printf '      redis:\n        condition: service_healthy\n'
    fi
    if [[ "$database" == postgres ]]; then
        cat <<'YAML'

  postgres:
    <<: *defaults
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD:?Set DB_PASSWORD}
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: [CMD-SHELL, 'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"']
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 10s
YAML
    elif [[ "$database" == mysql ]]; then
        cat <<'YAML'

  mysql:
    <<: *defaults
    image: mysql:8.4
    environment:
      MYSQL_DATABASE: ${DB_NAME}
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASSWORD:?Set DB_PASSWORD}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?Set MYSQL_ROOT_PASSWORD}
    volumes: [mysql_data:/var/lib/mysql]
    healthcheck:
      test: [CMD-SHELL, 'MYSQL_PWD="$${MYSQL_PASSWORD}" mysql --protocol=TCP -h 127.0.0.1 -u "$${MYSQL_USER}" "$${MYSQL_DATABASE}" -e "SELECT 1" >/dev/null']
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 30s
YAML
    fi
    if [[ "$redis" == true ]]; then
        cat <<'YAML'

  redis:
    <<: *defaults
    image: redis:7-alpine
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:?Set REDIS_PASSWORD}
    command: [sh, -c, 'exec redis-server --appendonly yes --requirepass "$${REDIS_PASSWORD}"']
    volumes: [redis_data:/data]
    healthcheck:
      test: [CMD-SHELL, 'REDISCLI_AUTH="$${REDIS_PASSWORD}" redis-cli ping | grep -qx PONG']
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 5s
YAML
    fi
    if [[ "$database" != none || "$redis" == true ]]; then
        printf '\nvolumes:\n'
        [[ "$database" == none ]] || printf '  %s_data:\n' "$database"
        [[ "$redis" != true ]] || printf '  redis_data:\n'
    fi
    printf '\nnetworks:\n  default:\n    driver: bridge\n'
} > "$output/compose.yaml"
cat > "$output/DOCKER.generated.md" <<GUIDE
# $project deployment

Generated from zivaBooks deployment patterns. This is a framework-neutral scaffold.
Use a distinct project name AND host port for every deployment on the VPS.
Compose scopes containers, networks and persistent volumes to the project name.
Do not change COMPOSE_PROJECT_NAME or use a different -p after deploying: this
selects different resources and can make existing data appear missing.

## Before starting

- Supply a working Dockerfile or image with its own production startup command.
  The server must listen on 0.0.0.0:$app_port. PORT is supplied, but not every
  framework reads it; adapt the application's command/configuration as needed.
- Map DB_* / DATABASE_URL and REDIS_URL to your application's settings and install
  the corresponding database driver. The generator does not modify source code.
  DATABASE_ENGINE=none is not injected; configure SQLite/external services in app.env
  if needed. zivaBooks itself expects POSTGRES_* rather than generic DB_*.
- Add secrets, allowed hosts, trusted origins, and other settings to app.env.
  Explicit environment entries in compose.yaml take precedence over app.env.
- Add application-specific persistent volumes (uploads/media) and HTTP healthchecks.
  No media paths, framework commands, migrations, workers or scheduler are assumed.
- Use a non-root application image, size worker counts for your VPS, and configure
  tested backups for databases, Redis (if needed), and application uploads.

## Operations

Run from this directory, with Docker Engine and Compose v2 installed:

\`\`\`sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=100 -f web
docker compose --env-file .env -f compose.yaml down
\`\`\`

Generation does not start services or check VPS port availability. Check for port
conflicts before deploying. Do not use down -v unless intentionally deleting data.
.env and app.env are private (mode 600) and ignored by Git/Docker build contexts.
Keep credentials backed up securely; changing .env does not rotate initialized DB
credentials. Generated passwords are URL-safe hex. If replacing them, URL-encode
special characters in DATABASE_URL/Redis URLs. Resolved Compose config includes
secrets: use config --quiet and do not publish plain config output.

## One VPS, multiple domains

The app is published only on 127.0.0.1:$host_port. Database and Redis ports are
not published. Use one host-installed Nginx/Caddy reverse proxy for all projects;
point each domain's DNS at the VPS, terminate HTTPS there and forward to the
corresponding localhost port. For example, a host Caddy configuration:

\`\`\`caddyfile
app.example.com {
    reverse_proxy 127.0.0.1:$host_port
}
\`\`\`

Replace the domain, configure DNS/firewall and HTTPS before public use. A proxy
inside a Docker container cannot use its own 127.0.0.1 to reach this app: adapt to
an external proxy network with a unique project-specific web alias, attaching
only web to that network. Keep each project's DB/Redis on its own default network.
Only the shared edge proxy should publish ports 80/443.

Image major versions follow this generator's baseline (Postgres 17, MySQL 8.4,
Redis 7). Review/pin image digests for reproducible releases and plan database
major-version upgrades with backups; do not simply swap a tag on existing data.
GUIDE
for file in .gitignore .dockerignore; do
    printf '\n# Generated Docker credentials\n.env\n.env.*\napp.env\n' >> "$output/$file"
done
printf 'Generated deployment in %s\nReview DOCKER.generated.md and app.env before starting.\n' "$output"
