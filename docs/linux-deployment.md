# Linux Deployment

This project is intended to run on Linux with Docker Compose. The backend stores SQLite data in the named Docker volume `radar-data`, so updates should rebuild containers without deleting volumes.

## First Deploy

Install Docker and the Compose plugin on the server, then clone the repository:

```bash
git clone -b codex/frontend-localization-polish https://github.com/wubin19920612/taoli1.git
cd taoli1
cp .env.example .env
nano .env
```

Recommended production values:

```env
ENVIRONMENT=production
DASHBOARD_PASSWORD=change-this-password
DATABASE_URL=sqlite:////data/radar.db
FRONTEND_PORT=3000
BACKEND_PORT=127.0.0.1:8000
CORS_ORIGINS=http://YOUR_SERVER_IP:3000
SERVICE_CONTROL_ENABLED=false
```

If you already have a domain and reverse proxy, keep the app private on localhost:

```env
FRONTEND_PORT=127.0.0.1:3000
BACKEND_PORT=127.0.0.1:8000
CORS_ORIGINS=https://YOUR_DOMAIN
```

Start the stack:

```bash
docker compose up -d --build
docker compose ps
```

Open:

- Frontend: `http://YOUR_SERVER_IP:3000`
- Backend health: `http://YOUR_SERVER_IP:8000/api/health` when `BACKEND_PORT=8000`, or `http://127.0.0.1:8000/api/health` on the server when bound to localhost.

## Easy Updates

After the first deploy, update with:

```bash
bash deploy/linux-update.sh
```

The script:

- verifies Docker Compose is available;
- creates `.env` from `.env.example` on first run and asks you to edit it;
- backs up `/data/radar.db` into `backups/` when the backend is already running;
- runs `git fetch` and `git pull --ff-only`;
- rebuilds images with fresh base images;
- restarts containers without deleting the `radar-data` volume.

To deploy a different branch:

```bash
DEPLOY_BRANCH=main bash deploy/linux-update.sh
```

## Reverse Proxy

Use Caddy or Nginx in front of the frontend container. The frontend container already proxies `/api/` to the backend container, so the reverse proxy only needs to forward traffic to the frontend port.

Caddy example:

```caddyfile
YOUR_DOMAIN {
  reverse_proxy 127.0.0.1:3000
}
```

Nginx example:

```nginx
server {
  listen 80;
  server_name YOUR_DOMAIN;

  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

Use HTTPS for any public deployment.

## Operations

View logs:

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

Restart only one service:

```bash
docker compose restart backend
docker compose restart frontend
```

Check container health:

```bash
docker compose ps
curl http://127.0.0.1:8000/api/health
```

Roll back to an older commit:

```bash
git log --oneline -5
git checkout <commit>
docker compose up -d --build
```

Do not run `docker compose down -v` unless you intentionally want to delete the SQLite data volume.
