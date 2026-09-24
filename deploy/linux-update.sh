#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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

if ! sudo -n docker compose ps --services --status running | grep -qx "backend"; then
  echo "Backend is not running; refusing an update without a database backup." >&2
  exit 1
fi
if ! sudo -n docker compose exec -T backend test -f /data/radar.db; then
  echo "Backend database is missing; refusing to deploy." >&2
  exit 1
fi

backup_file="backups/radar-before-${commit:0:12}-$(date -u +%Y%m%dT%H%M%SZ).db"
echo "Creating SQLite backup at $backup_file"
container_sha="$(sudo -n docker compose exec -T backend python - <<'PY'
import hashlib
import pathlib
import sqlite3
from contextlib import closing

src = pathlib.Path("/data/radar.db")
dst = pathlib.Path("/tmp/radar-backup-deploy.db")
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
sudo -n docker compose cp backend:/tmp/radar-backup-deploy.db "$backup_file"
sudo -n docker compose exec -T backend rm -f /tmp/radar-backup-deploy.db
host_sha="$(sha256sum "$backup_file" | awk '{print $1}')"
if [[ "$host_sha" != "$container_sha" ]]; then
  echo "Backup SHA-256 mismatch; refusing to deploy." >&2
  exit 1
fi
echo "Backup integrity_check=ok sha256=$host_sha"

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

echo "Current containers"
"${compose[@]}" ps
sudo -n docker stats --no-stream
free -m
