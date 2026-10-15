# Hostinger production deployment

Target: Ubuntu VPS, 2 CPU cores, 8 GB RAM, existing host Nginx,
`zivainfotech.tech`. No server changes are made by adding these files.
The root `docker-compose.yml` serves both local and production environments; select the appropriate env file explicitly.

## Layout and environment variables

- Code: `/opt/zivabooks` (clone the repository here).
- Secrets: `/etc/zivabooks/production.env` and `/etc/zivabooks/backup.env`, mode 600.
- Live data: `/srv/zivabooks/data/{postgres,redis,media}`.
- Local backup: `/backups/zivabooks/{repository,staging,cache}`.
- Web: `127.0.0.1:8001`, reachable publicly only through Nginx.
- Postgres and Redis: project network only; no host ports.

Copy `.env.production.example` (at the repository root) to `/etc/zivabooks/production.env`.
Set SECRET_KEY and POSTGRES_PASSWORD to separate random values, for example using
`openssl rand -hex 32`. Do not paste secrets into Git, Dockerfiles, or support logs.
Use single quotes for literal `$` or `#` in dotenv values. Compose parses this file;
do not `source` it in a shell. `--env-file` supplies Compose substitutions, and the
explicit service `environment` maps decide which variables enter each container.
Shell variables override dotenv values: deploy from a clean administrative shell.
Do not publish `docker compose config` output because it contains resolved secrets.

Use this function in the commands below:

```bash
dc() {
  docker compose --env-file /etc/zivabooks/production.env \
    -f /opt/zivabooks/docker-compose.yml "$@"
}
```

Change APP_DOMAIN, WEB_PORT, COMPOSE_PROJECT_NAME, DATA_ROOT, and BACKUP_ROOT for
each additional app. Nginx's server name and upstream port must match. Nginx does
not automatically read the Compose environment file. No container names are fixed.
Keep DATA_ROOT and the project name stable across releases.

## First install

1. Inspect the existing VPS before changing it. Do not reinstall its OS. Check for
   existing services on port 8001 and a server block for `zivainfotech.tech`.
2. Install Docker Engine with Compose, Python 3, and Restic (repository format 2
   support, Restic 0.14+). Keep Restic patched. Do not add Docker's socket to apps.
3. Prepare directories with these defaults (adjust if DATA_ROOT differs):

```bash
sudo install -d -m 700 /etc/zivabooks /backups/zivabooks
sudo install -d -m 755 /srv/zivabooks/data
sudo install -d -m 700 /srv/zivabooks/data/postgres /srv/zivabooks/data/redis
sudo install -d -o 10001 -g 10001 -m 750 /srv/zivabooks/data/media
```

The web/worker image runs as UID 10001. Database image entrypoints initialize
their own directory ownership. Existing media needs ownership reviewed and set
to this UID; do not recursively change unrelated app directories.

4. Install the environment file, fill secrets, and validate:

```bash
sudo install -m 600 .env.production.example /etc/zivabooks/production.env
# Edit /etc/zivabooks/production.env before continuing.
dc config --quiet
dc build web
dc up -d --wait db redis
dc run --rm --no-deps migrate
dc run --rm --no-deps migrate python manage.py seed_roles_permissions
dc run --rm --no-deps migrate python manage.py createsuperuser
dc up -d --no-deps --wait web worker
curl --fail http://127.0.0.1:8001/health/
```

Run as an administrator who can access the environment file and Docker. Building
on the VPS is a bootstrap fallback. Prefer building a release in CI and pushing
it to your chosen registry; set APP_IMAGE to that immutable release tag or digest
and use `dc pull web worker migrate`. Build for the VPS CPU architecture. No
registry is assumed or configured yet.

Migrations are tracked in the repository and applied explicitly; production never
runs `makemigrations`. For an EXISTING database, compare its migration history and
schema against these initial migrations before deploying. Do not blindly use
`--fake` or `--fake-initial`. Test an upgrade against a restored copy first.

`home.0002_upgrade_legacy_schema` bridges the legacy migration history containing
`0002_invoice_series` through `0017_inventoryunit_inventoryitem_unit_and_more`
to the replacement initial schema. It only adds missing tables, fields, and named
indexes/constraints, inside a transaction; fresh installations do nothing. Existing
rows and business identifiers are preserved. It deliberately does not replay old
data migrations that renumber invoices/vouchers, or remove legacy columns. Review
business defaults and new feature behavior separately before using those features.
An older legacy database without 0017 is rejected and requires its original chain.
Do not fake this compatibility migration: it repairs actual database structure.

## Existing Nginx and automatic SSL

First inspect locally (do not share full output, which may contain private config):

```bash
sudo nginx -T
command -v certbot
sudo certbot certificates
systemctl list-timers --all
sudo ss -ltnp
```

If this domain already has a server block or certificate automation, update that
configuration and reuse its renewal mechanism. Do not create duplicate server
blocks or install a second Certbot distribution. Preserve other hosted sites.

Otherwise copy `nginx/zivabooks.conf` into sites-available and enable it. This file
is an HTTP bootstrap, not the finished HTTPS configuration. Point the domain A
record to the VPS; publish AAAA only if IPv6 is working. Allow inbound 80/443 and
restrict SSH. Check `sudo nginx -t` before `sudo systemctl reload nginx`.

Install Certbot and its Nginx plugin using the official instructions matching
Ubuntu and your existing installation method. Then:

```bash
sudo certbot --nginx -d zivainfotech.tech --redirect
sudo nginx -t
sudo certbot renew --dry-run
```

Verify that the installer enabled a renewal timer and that Nginx reloads after
renewal. If the existing renewal setup requires a deploy hook, use
`nginx -t && systemctl reload nginx` in that hook; do not stop Nginx for renewal.
Check HTTPS login, media, and static assets before considering setup complete.
Nginx overwrites forwarded headers; Django trusts X-Forwarded-Proto from this
private upstream. Do not expose port 8001 publicly. Uvicorn proxy-header parsing
is disabled to avoid blindly trusting container-network callers.

## Backup setup

Install `backup/backup.env.example` as `/etc/zivabooks/backup.env`, mode 600.
Generate a Restic password file, mode 600, at `/etc/zivabooks/restic-password`.
Keep a recoverable copy of this password OUTSIDE the VPS. Do not replace it after
initializing a repository. Initialize once:

```bash
sudo python3 /opt/zivabooks/deploy/production/operations.py init
sudo python3 /opt/zivabooks/deploy/production/operations.py backup
```

The backup job gracefully stops only this project's running web and worker
containers, waits for active tasks, dumps Postgres, and backs up the database dump
and media together. It resumes the previously running containers even on a backup
error. No admin scripts or other writers may modify this app during capture.
The service's recovery hook also resumes recorded writers if the backup process
is killed. After a host crash or interrupted manual run, inspect the app and run
`operations.py resume-backup` before another backup if a pause marker remains.
There is a maintenance interruption for the dump and local media scan; on a large
dataset this can be substantial. An initial backup should run in a planned window.
For near-zero downtime, a later filesystem-snapshot or versioned-media design is
needed. A nightly schedule is chosen initially to limit these interruptions; this
allows up to about 24 hours of data loss between successful backups. Increase the
frequency or adopt continuous database recovery if that is unacceptable.

Backups contain application DB objects/data and media, not Redis queues, server
roles, other apps, or host configuration. The application DB owner is recreated
from the environment configuration. Redis persists across app upgrades but queues
are not guaranteed recoverable after a VPS disaster. After disaster recovery,
reconcile interrupted business tasks before retrying them to avoid duplicates.
Back up deployment configuration, encrypted secrets, Nginx and `/etc/letsencrypt`
separately offsite; they are needed for a full server rebuild.

Install `backup/zivabooks-backup.service` and `.timer` under `/etc/systemd/system/`:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zivabooks-backup.timer
sudo systemctl start zivabooks-backup.service
sudo journalctl -u zivabooks-backup.service
```

The timer runs nightly around 03:30 India time. It reads backup.env; manual Python
commands inherit the shell environment and do NOT automatically read backup.env.
Use `systemctl start` to run manually with the configured offsite credentials.
Locks prevent backups, upgrades, and maintenance from overlapping for this app.
An indefinitely stuck task requires operator attention; the job does not force
kill it. Alert on failed services and on stale `last-local-success` and, when
enabled, `last-offsite-success` timestamps. Connect these to your monitoring
provider; no external notification account is configured by these files.

Offsite is intentionally disabled until you choose a provider. Local backups alone
do not protect against losing the VPS. Initialize a separate remote Restic
repository, then set OFFSITE_REPOSITORY, OFFSITE_PASSWORD_FILE and provider
credentials in backup.env. The job copies completed snapshots after restarting
the app. Schedule remote retention/pruning from a trusted machine, with separate
deletion credentials where supported. Do not mirror local three-day retention
onto your longer-term offsite repository.

Local maintenance (review retention first; these commands require Restic password
access). Run at separate off-peak times; pruning reclaims actual disk space:

```bash
sudo python3 /opt/zivabooks/deploy/production/operations.py check
sudo python3 /opt/zivabooks/deploy/production/operations.py prune
```

Prune keeps snapshots within three days plus at least the last two per group.
After the first successful restore test, install the supplied
`zivabooks-backup-maintenance.service` and `.timer`, reload systemd, and enable
`zivabooks-backup-maintenance.timer`. It verifies repository data before pruning
on Sundays around 05:30 India time; if verification fails, pruning is skipped.
Until enabled, no automatic pruning occurs. Plaintext dump staging is temporary; media is
read directly and deduplicated, not copied into another full staging tree. Reserve
space for one DB dump, initial backup, changed data, caches, and pruning overhead.

## Restore without overwriting live data

Extract a chosen snapshot into a NEW directory:

```bash
sudo python3 /opt/zivabooks/deploy/production/operations.py restore \
  --snapshot latest --target /srv/zivabooks-restore-test
```

Restic preserves absolute source paths beneath the target, so inspect the result:
the dump is under `backups/zivabooks/staging/database.dump`, media under
`srv/zivabooks/data/media`, and release.json records the captured app version.

Create a separate environment file for a restore-test project: distinct project
name, DATA_ROOT, BACKUP_ROOT and localhost port. Prepare fresh data directories and
start only its DB and Redis using Compose with that environment. Import into the
EMPTY database before running app migrations:

```bash
# Define restore_dc like dc above, but using the restore-test environment file.
restore_dc exec -T db sh -c \
  'pg_restore --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < /srv/zivabooks-restore-test/backups/zivabooks/staging/database.dump
```

Copy restored media into the test project's media directory, set UID/GID 10001,
and run the captured image. Do not start workers during an initial restore test;
disable external email/AI integrations. Verify records, login and media. Test
future migrations there. A live recovery requires an explicit maintenance window
and validated data cutover; the restore script deliberately never imports over
the live database. Test monthly and measure recovery time.

## App upgrades

1. Commit code and reviewed migrations, build/test a new immutable image.
2. Update APP_IMAGE in production.env; pull or build it BEFORE maintenance.
3. Run the upgrade command below. It locks out backups, drains writers, captures a
   local recovery point, applies migrations once, and replaces web/worker only.

```bash
sudo python3 /opt/zivabooks/deploy/production/operations.py upgrade
```

If offsite variables are required for this manual command, supply them through a
protected service environment. Failure after migration begins requires inspection;
the command does not blindly resume the old image against a changed schema.
Success means web health is ready and worker is running; inspect Celery logs and
exercise a task too. Run login, PDF, and media smoke checks after deployment.

The deployment check intentionally reports W008 (Nginx handles HTTPS redirects),
W005 (HSTS does not cover unknown subdomains), and W021 (preload is not enabled).
Verify Nginx's HTTPS redirect before launch; do not blindly silence these warnings.

Keep the old image until the release is verified. Never change DATA_ROOT to an
empty directory during an upgrade. Do not prune volumes, delete bind directories,
or run database reset commands. Schema changes can still delete data: prefer
backward-compatible migrations. Postgres major upgrades and Redis upgrades are
separate maintenance procedures. App-image rollback does not undo migrations.

## Capacity and limits

### Five or six apps on this VPS

Image size (disk) is separate from container memory and CPU usage. Production
images use a multi-stage build: uv and its download cache, tests, deployment files,
and documentation stay out of the final image. Web and worker use the SAME image,
so its read-only layers are stored once. Reusing the same Python base and database
image version across compatible apps also allows Docker to share identical layers.
Do not copy zivaBooks' PDF/AI dependencies into apps that do not need them.

Measured locally on ARM64, this change reduced the uncompressed production image
from 504,167,671 to 320,788,367 bytes (about 36%). VPS architecture and future
dependency versions may produce different sizes. The production image was checked
for Django startup, migrations, PDF rendering, Celery task discovery, and static
asset presence; build tooling and application tests are absent from runtime.

The default is one web process and one Celery child per app. These are starting
points, not a capacity guarantee. Six copies of the resource ceilings below exceed
8 GB; do not deploy six full stacks unchanged. Measure memory during peak requests,
PDF generation and Celery jobs, then allocate a total app budget with at least
1.5–2 GB left for the host, Nginx, backups and temporary deployment work. Use workers
only for apps that actually need background tasks. Stagger expensive jobs and
backups. Two CPU cores can become the limiting factor even when RAM is available.

Keep separate databases for the isolation requirement. Sharing a database server
would reduce some overhead but couples failures and upgrades; it is not enabled
here. If measured working sets do not fit, use a larger VPS or move heavy apps to
another VPS instead of lowering memory limits until jobs are killed.

Build images in CI, retain the current and previous known-good releases, and remove
only reviewed obsolete images/build caches. Never include persistent data in image
cleanup. Local inspection: `docker system df`, `docker image inspect IMAGE`, and
`docker stats`. Image sizes include shared layers and must not simply be summed to
estimate unique disk consumption. Compressed registry download size differs from
the uncompressed image size reported locally.

Default running-container RAM ceilings total about 3.6 GB, leaving capacity on the
8 GB host for Ubuntu, Nginx, backups and another small app. Limits are ceilings,
not reserved RAM. CPU ceilings can exceed two cores in aggregate, so concurrent
peaks still compete; measure and allocate budgets across ALL apps. One Celery
process is the initial setting. No Beat service is added because this project has
no periodic schedule configured.

Rotate/compress Docker logs and disable duplicate file logging. Build off-host
when possible; clean obsolete images and build caches only after rollback review.
Monitor disk space, inodes, DB connections, memory, queue size and backup age.
Redis rejects writes at its limit rather than evicting queued work; alert on this
and separate cache/broker instances if traffic warrants it. Bind mounts do not
enforce disk quotas. Shared kernel, disk and Nginx remain shared failure points.

References: [Uvicorn](https://www.uvicorn.org/deployment/),
[Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html),
[Restic](https://restic.readthedocs.io/en/stable/).
