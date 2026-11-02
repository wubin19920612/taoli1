#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Read-only local check: no Git network, Docker, .env, or deployment actions.
if [[ "${1:-}" == "--check-backups" ]]; then
  if (( $# > 2 )); then
    echo "Usage: bash deploy/linux-update.sh --check-backups [DIRECTORY]" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" "$ROOT_DIR/deploy/backup_tools.py" check-dir \
    --backup-dir "${2:-$ROOT_DIR/backups}" --max-gb "${BACKUP_DIR_MAX_GB:-15}"
elif (( $# != 0 )); then
  echo "Usage: bash deploy/linux-update.sh [--check-backups [DIRECTORY]]" >&2
  exit 2
fi

backup_mode="${BACKUP_MODE:-isolated}"
case "$backup_mode" in
  isolated|legacy) ;;
  *) echo "BACKUP_MODE must be isolated or legacy." >&2; exit 2 ;;
esac

branch="${DEPLOY_BRANCH:?Set DEPLOY_BRANCH to the reviewed deployment branch}"
commit="${DEPLOY_COMMIT:?Set DEPLOY_COMMIT to the full reviewed 40-character commit SHA}"
if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "DEPLOY_COMMIT must be a full lowercase 40-character SHA." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked server files have local changes; inspect them before deploying." >&2
  exit 1
fi

remote_commit="$(git ls-remote --heads origin "$branch" | awk '{print $1}')"
if [[ "$remote_commit" != "$commit" ]]; then
  echo "origin/$branch is $remote_commit, expected $commit; refusing to deploy." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not in PATH." >&2
  exit 1
fi

if ! sudo -n docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available. Install docker-compose-plugin first." >&2
  exit 1
fi
if ! sudo -n docker info >/dev/null 2>&1; then
  echo "Passwordless sudo Docker access is required for this update script." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Missing production .env; refusing to create or replace it." >&2
  exit 1
fi

available_mb="$(df -Pm "$ROOT_DIR" | awk 'NR == 2 {print $4}')"
if (( available_mb < 2048 )); then
  echo "Less than 2 GiB free on the server; inspect disk usage before pulling images." >&2
  exit 1
fi

mkdir -p backups
python3 deploy/backup_tools.py check-dir --backup-dir "$ROOT_DIR/backups" \
  --max-gb "${BACKUP_DIR_MAX_GB:-15}"

if ! sudo -n docker compose ps --services --status running | grep -qx "backend"; then
  echo "Backend is not running; refusing an update without a database backup." >&2
  exit 1
fi
if ! sudo -n docker compose exec -T backend test -f /data/radar.db; then
  echo "Backend database is missing; refusing to deploy." >&2
  exit 1
fi

backup_sqlite() {
  local database_path="$1"
  local temporary_path="$2"
  local backup_file="$3"
  local container_sha host_sha
  echo "Creating SQLite backup at $backup_file"
  container_sha="$(sudo -n docker compose exec -T backend python - "$database_path" "$temporary_path" <<'PY'
import hashlib
import pathlib
import sqlite3
import sys
from contextlib import closing

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
dst.unlink(missing_ok=True)
with closing(sqlite3.connect(f"file:{src}?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(dst)) as target:
        source.backup(target)
        check = target.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"backup integrity_check={check}")
digest = hashlib.sha256()
with dst.open("rb") as backup:
    for chunk in iter(lambda: backup.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
)"
  sudo -n docker compose cp "backend:$temporary_path" "$backup_file"
  sudo -n docker compose exec -T backend rm -f "$temporary_path"
  host_sha="$(sha256sum "$backup_file" | awk '{print $1}')"
  if [[ "$host_sha" != "$container_sha" ]]; then
    echo "Backup SHA-256 mismatch; refusing to deploy." >&2
    exit 1
  fi
  echo "Backup integrity_check=ok sha256=$host_sha"
}

backup_sqlite_isolated() {
  local database_path="$1"
  local backup_file="$3"
  local partial_file="$backup_file.partial"
  local failed_file="$backup_file.failed"
  local container_sha host_sha
  local existing
  for existing in "$backup_file" "$partial_file" "$failed_file"; do
    if [[ -e "$existing" || -L "$existing" ]]; then
      echo "Backup destination already exists: $existing; refusing to overwrite." >&2
      exit 1
    fi
  done
  echo "Creating isolated SQLite backup at $backup_file (image=$backup_image memory=128m)"
  if container_sha="$(sudo -n docker run --rm -i --pull=never --network none --read-only \
    --memory=128m --memory-swap=128m --pids-limit=32 --cpus=0.5 \
    --mount "$backup_data_mount" \
    --mount "type=bind,source=$ROOT_DIR/backups,target=/backups" \
    --entrypoint python "$backup_image" - "$database_path" "/backups/$(basename "$partial_file")" <<'ISOLATED_PY'
import hashlib
import os
import pathlib
import sqlite3
import sys
from contextlib import closing

src, dst = map(pathlib.Path, sys.argv[1:])
# Only write the .partial path; the host publishes after verifying its hash.
with dst.open("xb"):
    pass
os.chmod(dst, 0o600)
owner = dst.parent.stat()
os.chown(dst, owner.st_uid, owner.st_gid)
# WAL readers may need to create/update -shm even with mode=ro. Allow SQLite
# locking metadata on the volume; never use immutable=1 on a live database.
with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as source:
    source.execute("PRAGMA cache_size=-2048")
    with closing(sqlite3.connect(dst)) as target:
        target.execute("PRAGMA cache_size=-2048")
        # One step avoids repeated restarts under continuous source writes.
        source.backup(target, pages=-1)
        check = target.execute("PRAGMA integrity_check").fetchall()
        if check != [("ok",)]:
            raise RuntimeError(f"backup integrity_check={check}")

digest = hashlib.sha256()
with dst.open("rb+") as backup:
    for chunk in iter(lambda: backup.read(1024 * 1024), b""):
        digest.update(chunk)
    os.fsync(backup.fileno())
print(digest.hexdigest())
ISOLATED_PY
)" && host_sha="$(sha256sum "$partial_file" | awk '{print $1}')" \
    && [[ "$host_sha" =~ ^[0-9a-f]{64}$ && "$host_sha" == "$container_sha" ]]; then
    # Both paths are in backups/: rename atomically, never replace an existing
    # restore point. A no-clobber skip leaves .partial and must count as failure.
    if mv -T --no-clobber -- "$partial_file" "$backup_file" \
      && [[ ! -e "$partial_file" && ! -L "$partial_file" ]]; then
      echo "Backup integrity_check=ok sha256=$host_sha"
      return 0
    fi
    echo "Backup publication failed; refusing to deploy." >&2
  else
    echo "Backup integrity/SHA-256 verification failed; refusing to deploy." >&2
  fi
  if [[ -e "$partial_file" || -L "$partial_file" ]]; then
    mv -T --no-clobber -- "$partial_file" "$failed_file"
    if [[ -e "$partial_file" || -L "$partial_file" ]]; then
      echo "Cannot quarantine backup; retained at $partial_file." >&2
    else
      echo "Failed backup retained at $failed_file." >&2
    fi
  fi
  exit 1
}

backup_function=backup_sqlite_isolated
if [[ "$backup_mode" == "legacy" ]]; then
  echo "BACKUP_MODE=legacy: backup shares the backend memory limit; use only as a temporary fallback." >&2
  backup_function=backup_sqlite
else
  backend_container="$(sudo -n docker compose ps -q backend)"
  if [[ ! "$backend_container" =~ ^[0-9a-f]{12,64}$ ]]; then
    echo "Expected exactly one backend container; refusing to deploy." >&2
    exit 1
  fi
  backup_image="$(sudo -n docker inspect "$backend_container" --format '{{.Image}}')"
  if [[ ! "$backup_image" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Unable to resolve backend image ID; refusing to deploy." >&2
    exit 1
  fi
  # Inspect only /data. Never inherit backend mounts such as docker.sock.
  backend_data="$(sudo -n docker inspect "$backend_container" --format \
    '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Type}}|{{if eq .Type "volume"}}{{.Name}}{{else}}{{.Source}}{{end}}{{"\n"}}{{end}}{{end}}')"
  backend_data_type="${backend_data%%|*}"
  backend_data_source="${backend_data#*|}"
  if [[ -z "$backend_data_source" || "$backend_data_source" == *[,$'\r\n|']* ]]; then
    echo "Invalid or ambiguous backend /data mount; refusing to deploy." >&2
    exit 1
  fi
  case "$backend_data_type" in
    volume)
      if [[ ! "$backend_data_source" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
        echo "Invalid backend /data volume name; refusing to deploy." >&2
        exit 1
      fi ;;
    bind)
      if [[ "$backend_data_source" != /* ]]; then
        echo "Backend /data bind source must be absolute; refusing to deploy." >&2
        exit 1
      fi ;;
    *) echo "Backend /data must be a volume or bind mount; refusing to deploy." >&2; exit 1 ;;
  esac
  backup_data_mount="type=$backend_data_type,source=$backend_data_source,target=/data"
fi

backup_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
"$backup_function" "/data/radar.db" "/tmp/radar-backup-deploy.db" \
  "backups/radar-before-${commit:0:12}-${backup_timestamp}.db"
route_database_state="$(sudo -n docker compose exec -T backend sh -c \
  'if [ -f /data/radar-squeeze-route.db ]; then echo present; else echo absent; fi')"
if [[ "$route_database_state" == "present" ]]; then
  "$backup_function" "/data/radar-squeeze-route.db" "/tmp/squeeze-route-backup-deploy.db" \
    "backups/squeeze-route-before-${commit:0:12}-${backup_timestamp}.db"
elif [[ "$route_database_state" == "absent" ]]; then
  echo "No /data/radar-squeeze-route.db; skipping optional route backup."
else
  echo "Cannot determine route database presence; refusing to deploy." >&2
  exit 1
fi

# Refuse the update if the newly created restore points crossed the limit too.
python3 deploy/backup_tools.py check-dir --backup-dir "$ROOT_DIR/backups" \
  --max-gb "${BACKUP_DIR_MAX_GB:-15}"

echo "Fast-forwarding to reviewed commit $commit from origin/$branch"
git pull --ff-only origin "$branch"
if [[ "$(git rev-parse HEAD)" != "$commit" ]]; then
  echo "Repository HEAD differs from DEPLOY_COMMIT; refusing to restart services." >&2
  exit 1
fi

backend_image="ghcr.io/wubin19920612/taoli1-backend:sha-$commit"
frontend_image="ghcr.io/wubin19920612/taoli1-frontend:sha-$commit"
compose=(sudo -n env "BACKEND_IMAGE=$backend_image" "FRONTEND_IMAGE=$frontend_image" docker compose -f docker-compose.yml -f docker-compose.prod.yml)
"${compose[@]}" config -q
echo "Pulling prebuilt images; no build runs on this server"
"${compose[@]}" pull backend frontend
"${compose[@]}" up -d --no-build --wait --wait-timeout 120 --remove-orphans

python3 deploy/backup_retention.py --backup-dir "$ROOT_DIR/backups" --apply
python3 deploy/backup_retention.py --backup-dir "$ROOT_DIR/backups" --prefix squeeze-route --apply

echo "Current containers"
"${compose[@]}" ps
sudo -n docker stats --no-stream
free -m
