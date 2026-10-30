# Linux Deployment

The production host has 2 GiB RAM and no swap. Do not run `docker compose build`,
`docker compose up --build`, `npm ci`, or `vite build` on it. The frontend build
previously exhausted host memory while the application was running.

## Build Outside Production

The [production image workflow](../.github/workflows/build-production-images.yml)
builds backend and frontend images on GitHub Actions for `linux/amd64`. A push to
`codex/server-low-memory` or `codex/frontend-localization-polish` builds that
exact commit. A manual workflow dispatch can build another full 40-character
commit SHA from this repository. Images are published as:

```text
ghcr.io/wubin19920612/taoli1-backend:sha-<full-commit-sha>
ghcr.io/wubin19920612/taoli1-frontend:sha-<full-commit-sha>
```

Wait for **both** workflow jobs to finish and verify the two image tags before
updating the server. If the GHCR packages are private, authenticate the server's
Docker client using a token with `read:packages`; keep the token outside the
repository. Public packages can be pulled without a token.

## Windows Operator Access

This workstation has a `taoli1-prod` SSH alias in the local, untracked
`C:\Users\wubin\.ssh\config`. Its dedicated private key and pinned host-key
record stay outside this repository. Codex runs under a different Windows home,
so pass the config file explicitly and verify access with a read-only command:

```powershell
ssh.exe -F C:\Users\wubin\.ssh\config taoli1-prod "git -C ~/wubin/taoli1 rev-parse HEAD"
```

If the Codex sandbox cannot read the dedicated key, the SSH command needs
approved access outside the workspace. Do not copy the key into the repository
or substitute the default GitHub SSH key. The local alias enforces strict host-key
checking. Before a deployment, confirm the target branch, commit, clean tracked
worktree, running backend, database, disk space, and both prebuilt image tags.

## Server Configuration

Install Docker and the Compose plugin. Clone the reviewed deployment branch and
create the production `.env` from `.env.example`. Keep credentials only in the
server's `.env` and preserve its existing backups.

Recommended production bindings:

```env
ENVIRONMENT=production
DATABASE_URL=sqlite:////data/radar.db
FRONTEND_PORT=127.0.0.1:3000
BACKEND_PORT=127.0.0.1:8000
SERVICE_CONTROL_ENABLED=false
```

The application and its SQLite databases use the `taoli1` Compose project and
the `radar-data` named volume. The squeeze route research worker uses
`/data/radar-squeeze-route.db` so its frequent writes do not contend with
`/data/radar.db`. The production overlay removes both build
definitions, assigns prebuilt images, and caps backend/frontend memory at
768/96 MiB. The normal `docker-compose.yml` remains available for local work.

## Update An Existing Server

The server must already have a running backend and a readable `/data/radar.db`.
For the first migration from the old build-on-server script, verify the existing
SQLite backup, then fast-forward the server repository to the reviewed commit on
`codex/server-low-memory`. Do not pull the unrelated commits on the previous
development branch. Confirm `git rev-parse HEAD` equals the image SHA before
running the new script.

For subsequent updates, from the server repository:

```bash
DEPLOY_BRANCH=<reviewed-branch> \
DEPLOY_COMMIT=<full-reviewed-commit-sha> \
bash deploy/linux-update.sh
```

On the Windows workstation, run that remote command through the verified alias:

```powershell
ssh.exe -F C:\Users\wubin\.ssh\config taoli1-prod "cd ~/wubin/taoli1 && DEPLOY_BRANCH=<reviewed-branch> DEPLOY_COMMIT=<full-reviewed-commit-sha> bash deploy/linux-update.sh"
```

Use the branch containing the reviewed commit at its remote tip. The script
checks that exact branch/SHA pair before backing up and updating the server.

The script requires a clean tracked worktree, checks the remote branch tip,
checks free disk space, makes a SQLite backup of `/data/radar.db` with
`integrity_check=ok` and a matching host/container SHA-256, and backs up
`/data/radar-squeeze-route.db` to a separately checked file when it exists.
It uses `git pull --ff-only`, pulls the exact image
tags, and runs `docker compose up -d --no-build --wait`. Git runs as the normal
user; Docker commands use passwordless `sudo`. It never removes volumes or the
server's `.env`.

After a successful Compose update, `deploy/backup_retention.py` removes only old
deployment-created database backups independently for the radar and squeeze
route files. It keeps the latest three of each, one per UTC day
for the past seven days, and one per UTC week for the preceding four weeks.
Named manual backups are not removed automatically. Preview the exact deletion
set with `python3 deploy/backup_retention.py --backup-dir backups` before any
manual cleanup; `--include-legacy` includes older named snapshots. This is a
local retention policy, not an off-host disaster-recovery backup. Use repeated
`--protect BASENAME` options to preserve special historical restore points in a
one-time legacy cleanup. Keep a verified off-host copy of critical data when one
is available; do not mistake local snapshots for protection against host loss.

After updating, verify the two containers, local `/api/health`, the relevant
market data, and actual alert events. Healthy containers and successfully
pulled images alone do not prove notifications work.

```bash
sudo docker compose ps
curl -fsS --max-time 5 http://127.0.0.1:8000/api/health
sudo docker stats --no-stream
```

If a new image fails, use the previously recorded image tags with the production
overlay and `up -d --no-build --wait`, after checking database compatibility.
Retain the verified database restore points selected by the policy above,
`.env` backups, and `CACHED`. Never use
`docker compose down -v`.

## Reverse Proxy

The frontend container proxies `/api/` to the backend container. A public Caddy
or Nginx reverse proxy should forward HTTPS traffic to `127.0.0.1:3000`.
