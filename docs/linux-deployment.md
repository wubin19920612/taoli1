# Linux Deployment

The production host has 2 GiB RAM and no swap. Do not run `docker compose build`,
`docker compose up --build`, `npm ci`, or `vite build` on it. The frontend build
previously exhausted host memory while the application was running.

## Build Outside Production

The [production image workflow](../.github/workflows/build-production-images.yml)
builds backend and frontend images on GitHub Actions for `linux/amd64`. A push to
`codex/server-low-memory` builds that exact commit. A manual workflow dispatch
can build another full 40-character commit SHA from this repository. Images are
published as:

```text
ghcr.io/wubin19920612/taoli1-backend:sha-<full-commit-sha>
ghcr.io/wubin19920612/taoli1-frontend:sha-<full-commit-sha>
```

Wait for **both** workflow jobs to finish and verify the two image tags before
updating the server. If the GHCR packages are private, authenticate the server's
Docker client using a token with `read:packages`; keep the token outside the
repository. Public packages can be pulled without a token.

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

The application and its SQLite database use the `taoli1` Compose project and
the `radar-data` named volume. The production overlay removes both build
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
DEPLOY_BRANCH=codex/server-low-memory \
DEPLOY_COMMIT=<full-reviewed-commit-sha> \
bash deploy/linux-update.sh
```

The script requires a clean tracked worktree, checks the remote branch tip,
checks free disk space, makes a SQLite backup with `integrity_check=ok` and a
matching host/container SHA-256, uses `git pull --ff-only`, pulls the exact image
tags, and runs `docker compose up -d --no-build --wait`. Git runs as the normal
user; Docker commands use passwordless `sudo`. It never removes volumes or the
server's `.env`.

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
Retain the database backups, `.env` backups, and `CACHED`. Never use
`docker compose down -v`.

## Reverse Proxy

The frontend container proxies `/api/` to the backend container. A public Caddy
or Nginx reverse proxy should forward HTTPS traffic to `127.0.0.1:3000`.
