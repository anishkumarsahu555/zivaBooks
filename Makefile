# ==============================================================================
# zivaBooks — Developer & Operations Makefile
# ==============================================================================
.PHONY: help build clean restart stop local dev init doctor status test migrate makemigrations showmigrations check-migrations static seed backfill reset deploy update backup restore logs shell superuser

help:
	@./start.sh --help

build:
	@./start.sh --build

clean:
	@./start.sh --clean

restart:
	@./start.sh --restart

stop:
	@./start.sh --stop

down:
	@./start.sh --stop

local:
	@./start.sh --local

dev:
	@./start.sh --local

init:
	@./start.sh --init

doctor:
	@./start.sh --doctor

status:
	@./start.sh --status

ps:
	@./start.sh --status

test:
	@./start.sh --test

makemigrations:
	@uv run python manage.py makemigrations

migrate:
	@./start.sh --migrate

showmigrations:
	@uv run python manage.py showmigrations

check-migrations:
	@uv run python manage.py makemigrations --check --dry-run

static:
	@uv run python manage.py collectstatic --noinput

seed:
	@./start.sh --seed

backfill:
	@./start.sh --backfill

reset:
	@./start.sh --reset

shell:
	@./start.sh --shell

superuser:
	@./start.sh --superuser

deploy:
	@./start.sh --deploy

update:
	@./start.sh --update

backup:
	@./start.sh --backup

restore:
	@./start.sh --restore

logs:
	@./start.sh --logs
