#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

branch="${DEPLOY_BRANCH:-$(git branch --show-current)}"
if [[ -z "$branch" ]]; then
  echo "Cannot detect the current branch. Set DEPLOY_BRANCH=<branch> and rerun." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not in PATH." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available. Install docker-compose-plugin first." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it first, then rerun:"
  echo "  nano .env"
  exit 1
fi

mkdir -p backups

if docker compose ps --services --status running | grep -qx "backend"; then
  if docker compose exec -T backend test -f /data/radar.db; then
    backup_file="backups/radar-$(date -u +%Y%m%dT%H%M%SZ).db"
    echo "Creating SQLite backup at $backup_file"
    docker compose exec -T backend python - <<'PY'
import pathlib
import sqlite3

src = pathlib.Path("/data/radar.db")
dst = pathlib.Path("/tmp/radar-backup.db")
dst.unlink(missing_ok=True)
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
target = sqlite3.connect(dst)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
    docker compose cp backend:/tmp/radar-backup.db "$backup_file"
    docker compose exec -T backend rm -f /tmp/radar-backup.db
  else
    echo "Backend is running, but /data/radar.db does not exist; skipping database backup."
  fi
else
  echo "Backend is not running; skipping database backup."
fi

echo "Fetching latest code from origin/$branch"
git fetch --prune origin
git pull --ff-only origin "$branch"

echo "Rebuilding and restarting containers"
docker compose build --pull
docker compose up -d --remove-orphans

echo "Current containers"
docker compose ps
