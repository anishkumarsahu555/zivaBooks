#!/usr/bin/env python3
"""Host-side backup/release operations. Requires Python 3, Docker, and Restic.

Never sources application environment files as shell code. Docker Compose parses
them. Configuration JSON stays in memory and is not printed (it contains secrets).
"""
import argparse
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


class Operations:
    def __init__(self, env_file):
        self.compose = ["docker", "compose", "--env-file", str(Path(env_file).resolve()),
                        "-f", str(Path(__file__).resolve().parents[2] / "docker-compose.yml")]
        self.config = json.loads(run(self.compose + ["config", "--format", "json"],
                                     capture_output=True, text=True).stdout)
        self.root = Path(self.config["x-backup"]["root"])
        self.media = Path(next(v["source"] for v in self.config["services"]["web"]["volumes"]
                               if v["target"] == "/app/media"))
        if not self.root.is_absolute() or self.root == Path("/"):
            raise ValueError("BACKUP_ROOT must be an absolute app-specific directory")
        if self.root == self.media or self.root in self.media.parents or self.media in self.root.parents:
            raise ValueError("Backup and media directories must not contain one another")
        self.repository = self.root / "repository"
        self.staging = self.root / "staging"
        self.restic_env = dict(os.environ)
        self.restic_env["RESTIC_PASSWORD_FILE"] = os.environ.get(
            "RESTIC_PASSWORD_FILE", "/etc/zivabooks/restic-password")
        self.restic_env["RESTIC_CACHE_DIR"] = str(self.root / "cache")

    def dc(self, *args, **kwargs):
        return run(self.compose + list(args), **kwargs)

    def restic(self, *args, **kwargs):
        return run(["restic", "--repo", self.repository, *args], env=self.restic_env, **kwargs)

    @contextlib.contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / "operations.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def running_writers(self):
        output = self.dc("ps", "--status", "running", "--services",
                         capture_output=True, text=True).stdout.splitlines()
        return [s for s in ("web", "worker") if s in output]

    def stop(self, writers):
        if writers:
            # Wait for active requests/tasks. Never force-kill a writing task.
            self.dc("stop", "--timeout", "-1", *writers)

    def start(self, writers):
        if writers:
            self.dc("start", *writers)

    def snapshot(self):
        """Call only with all app writers stopped; admin writes must also cease."""
        self.restic("snapshots", "--json", stdout=subprocess.DEVNULL)
        if not self.media.is_dir():
            raise ValueError("Media directory does not exist")
        self.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        dump = self.staging / "database.dump"
        try:
            with dump.open("wb") as out:
                self.dc("exec", "-T", "db", "sh", "-c",
                        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z0', stdout=out)
            metadata = {
                "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "image": self.config["services"]["web"]["image"],
                "project": self.config["name"],
                "media_path": str(self.media),
                "database_image": self.config["services"]["db"]["image"],
            }
            (self.staging / "release.json").write_text(json.dumps(metadata, indent=2))
            self.restic("backup", "--host", self.config["name"], "--tag", "application",
                        self.staging, self.media)
            (self.root / "last-local-success").write_text(metadata["created_utc"] + "\n")
        finally:
            # Never keep a partial plaintext database dump after a failure.
            dump.unlink(missing_ok=True)

    def offsite(self):
        remote = os.environ.get("OFFSITE_REPOSITORY")
        if not remote:
            print("LOCAL BACKUP ONLY: OFFSITE_REPOSITORY is not configured.", flush=True)
            return
        remote_password = os.environ.get("OFFSITE_PASSWORD_FILE")
        if not remote_password:
            raise ValueError("Set OFFSITE_PASSWORD_FILE when offsite backup is enabled")
        env = dict(self.restic_env, RESTIC_PASSWORD_FILE=remote_password)
        run(["restic", "--repo", remote, "copy", "--from-repo", self.repository,
             "--from-password-file", self.restic_env["RESTIC_PASSWORD_FILE"]], env=env)
        (self.root / "last-offsite-success").write_text(
            datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n")

    def backup(self):
        writers = self.running_writers()
        self.remember_writers(writers)
        try:
            self.stop(writers)
            self.snapshot()
        finally:
            self.start(writers)
            self.clear_writers()
        # Network upload happens after the app is available again.
        self.offsite()

    def remember_writers(self, writers):
        marker = self.root / "backup-paused.json"
        if marker.exists():
            raise ValueError("Previous backup pause is unresolved; run resume-backup first")
        temporary = self.root / "backup-paused.tmp"
        temporary.write_text(json.dumps(writers))
        temporary.replace(marker)

    def clear_writers(self):
        (self.root / "backup-paused.json").unlink(missing_ok=True)

    def resume_backup(self):
        marker = self.root / "backup-paused.json"
        if marker.exists():
            writers = json.loads(marker.read_text())
            if not isinstance(writers, list) or any(s not in ("web", "worker") for s in writers):
                raise ValueError("Invalid backup recovery marker")
            self.start(writers)
            self.clear_writers()

    def upgrade(self):
        writers = self.running_writers()
        if not writers:
            raise ValueError("No running app found; use the documented first-install procedure")
        # Image is prebuilt/pulled before this command. Database/Redis not recreated.
        self.dc("run", "--rm", "--no-deps", "migrate", "python", "manage.py", "check", "--deploy")
        # Record actual running web image, not the new tag in the environment file.
        old_id = self.dc("ps", "-q", "web", capture_output=True, text=True).stdout.strip()
        old_image = run(["docker", "inspect", "--format", "{{.Config.Image}}", old_id],
                        capture_output=True, text=True).stdout.strip()
        new_image = self.config["services"]["web"]["image"]
        migration_started = False
        try:
            self.stop(writers)
            self.config["services"]["web"]["image"] = old_image
            self.snapshot()
            self.config["services"]["web"]["image"] = new_image
            migration_started = True
            self.dc("run", "--rm", "--no-deps", "migrate")
            self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "web", "worker")
        except BaseException:
            if not migration_started:
                self.start(writers)
            else:
                print("Upgrade failed after migrations started. Do not blindly roll back the image; "
                      "inspect schema compatibility and the saved snapshot.", file=sys.stderr)
            raise
        self.offsite()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/zivabooks/production.env")
    parser.add_argument("action", choices=["init", "backup", "resume-backup", "upgrade", "check", "prune", "restore"])
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    ops = Operations(args.env_file)
    with ops.locked():
        if args.action == "init":
            ops.restic("init", "--repository-version", "2")
        elif args.action == "backup":
            ops.backup()
        elif args.action == "resume-backup":
            ops.resume_backup()
        elif args.action == "upgrade":
            ops.upgrade()
        elif args.action == "check":
            ops.restic("check", "--read-data")
        elif args.action == "prune":
            # Explicit maintenance only. Review retention before scheduling.
            ops.restic("forget", "--keep-within", os.environ.get("LOCAL_KEEP_WITHIN", "3d"),
                       "--keep-last", "2", "--prune")
        else:
            if not args.target or not args.target.is_absolute() or args.target.exists():
                raise ValueError("Restore requires a NEW absolute --target directory; live data is never overwritten")
            target = args.target.resolve()
            protected = [ops.root.resolve()]
            for service in ops.config["services"].values():
                protected.extend(Path(v["source"]).resolve()
                                 for v in service.get("volumes", []) if v.get("type") == "bind")
            if any(p == target or p in target.parents or target in p.parents for p in protected):
                raise ValueError("Restore outside all backup and live-data directories")
            ops.restic("restore", args.snapshot, "--target", target)
            print("Snapshot extracted only. Follow README to restore into an isolated database.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Operation failed: {exc}", file=sys.stderr)
        sys.exit(1)
