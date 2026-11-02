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
the `radar-data` named volume. The squeeze route research worker is retired
(已退役). Its legacy `/data/radar-squeeze-route.db`, when present, remains
covered by the backup and restore procedures below; do not delete it as part of
feature retirement. The production overlay removes both build
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

### S4：隔离备份与容量门槛

默认 `BACKUP_MODE=isolated`：从 Compose 查找当前 backend 容器，使用其不可变镜像
ID（`docker inspect .Image`），独立运行临时容器；不拉取备份镜像、不共享 backend
内存额度。限制为 128 MiB 内存及相同总内存/交换额度、0.5 CPU、32 PID，禁用网络，
容器根文件系统只读。通过 `docker inspect` 只提取 backend 的 `/data` 挂载，以
`--mount` 挂载该 volume 名或 bind source，并另挂宿主机 `backups/`；临时容器只有这
两个挂载，不继承 backend 的 Docker socket 等其他挂载。主库与存在时的 route 库
逐一执行 `source.backup(pages=-1)`，各自先通过 `integrity_check`，再核对容器与
宿主机 SHA-256，全部通过后才发布备份。
每个库是独立时间点，两个库不构成跨库原子快照。route 库不存在会明确输出 skip。

数据卷以可写方式提供 SQLite 所需的 WAL/SHM 锁元数据权限，但源连接始终使用
`mode=ro`，不执行源库写 SQL。不能因暂时没有 WAL/SHM 就把在线库设为 `immutable=1`。
备份先写宿主机 `backups/*.db.partial`，新模式文件权限 0600、归该目录所有者所有；
校验全部通过后在同一目录原子 rename 为正式 `.db`，拒绝覆盖同名备份。校验失败
保留为 `*.db.failed` 并中止部署；进程中断可能留下 `.partial`。两种后缀均不匹配
自动保留器正则，必须人工检查，不作为恢复点。

部署前检查 `backups/` 内所有文件的递归逻辑大小（包括未纳入清理的文件；链接本身
计数但不跟随）。`BACKUP_DIR_MAX_GB` 默认 **15 GiB（15 × 1024³ bytes）**；
超过上限拒绝部署，等于上限允许继续。双库备份后、`git pull` 前再检查一次；若新
备份触发上限，保留这些恢复点并中止更新。现有剩余磁盘至少 2 GiB 的检查仍保留。
该阈值不预留本次备份的空间，也不取代文件系统剩余空间检查。

每次检查输出 `UNMANAGED` 文件名、大小及汇总，如 `env-before-*`、旧手工命名、
嵌套目录内文件和 `.partial`；不输出文件内容、不删除文件。仅检查本地目录可用：

```bash
bash deploy/linux-update.sh --check-backups /path/to/copied-backups
BACKUP_DIR_MAX_GB=20 bash deploy/linux-update.sh --check-backups /path/to/copied-backups
```

检查模式不连接 Git 远端、不调用 Docker、不读取 `.env`。容量过限时先核对清单及下文
保留器预览；迁出人工确认的文件，或确认容量预算后临时调整命令环境变量。不要为
绕过门槛而直接批量删除备份或修改自动保留规则。

### 恢复演练（临时目录，不覆盖数据库）

在测试机准备已关闭、无 `-wal/-shm/-journal` 的独立备份文件以及 Python 3.10+，
无需安装业务依赖或 Docker。下面命令中的时间戳与 SHA 应替换为真实备份文件名：

```bash
bash deploy/restore_drill.sh "/path/to/copied-backups/radar-before-<12hex>-<timestamp>.db"
bash deploy/restore_drill.sh "/path/to/copied-backups/squeeze-route-before-<12hex>-<timestamp>.db"
# 旧命名文件要显式指定库类型；自定义关键表可重复传 --table NAME。
bash deploy/restore_drill.sh /path/to/copied-backups/manual-snapshot.db --kind radar
```

脚本仅以普通文件方式读取输入，先复制到权限受限的随机临时目录，再从临时副本
执行 SQLite Backup API 恢复到另一临时库。检查副本和恢复库的 `integrity_check`、
关键表行数，并确认输入备份 SHA-256 没有变化；成功或失败均清理自己的临时目录。
主库默认对比 `app_settings`、`opportunity_history`、`alert_events`；route 库默认
对比 `squeeze_route_state`、`squeeze_route_latest`、`squeeze_route_events`。
缺少任何指定表即失败，零行可以通过。这证明该快照可恢复，不能证明它包含快照
之后的业务数据；行数比较对象是备份副本，不访问线上库，也不验证业务语义。

退出码：`0` 为 `RESTORE_DRILL PASS`，`1` 为数据、校验或文件操作失败，`2` 为命令
用法错误。脚本拒绝 `/data`、`/var/lib/docker`、线上库文件名 `radar.db` /
`radar-squeeze-route.db`、符号链接及带侧文件的快照；所有 SQLite 连接只打开临时文件。
临时目录按 `TMPDIR`、`TEMP`、`TMP` 的优先级取首个非空环境变量；未设置时检查固定
候选 `/tmp`、`/var/tmp`（Windows 另有系统 Temp）。先解析并拒绝线上数据路径、输入
快照所在目录及仓库 `backups/` 的内部路径，再显式传入 `TemporaryDirectory(dir=...)`；
不调用会探测写入的 `gettempdir()`，受保护路径检查通过前不创建或写入文件。环境变量
指定受保护路径时直接失败。临时空间预留至少双倍备份体积。

测试机上进一步验收隔离容器行为时，使用独立 Compose 项目、测试数据卷及测试配置，
先记录 backend 镜像 ID 和 `docker stats`，再对测试分支/完整 SHA 运行更新脚本。
确认两份 `Backup integrity_check=ok sha256=...`、备份容器的 128 MiB 限制和业务容器
内存变化，再把双库备份分别送入上述恢复演练。该步骤会更新测试环境，不能在本机
或生产环境照抄运行；本次 S4 本地验收不包含此项运行证据。

### 备份路径回退与数据恢复边界

旧 `backup_sqlite` 保留一个版本。仅需要回退备份实现时，在**已批准的测试机更新**
命令前加 `BACKUP_MODE=legacy`，其余 `DEPLOY_BRANCH` / `DEPLOY_COMMIT` 仍按上文提供。
容量门槛和双库校验继续生效，但旧备份会重新共享 backend 的内存限额，有历史 OOM
风险；不会在隔离备份失败时自动切换。验证后取消该变量即可恢复默认路径。

若要回退业务镜像，记录当前版本及两份验证备份，检查旧镜像与数据库兼容性后按上文
生产 overlay 指定之前的完整 SHA 标签并 `up -d --no-build --wait`，再查健康与业务接口。
恢复演练脚本只验证快照，不负责线上还原。真正回写数据前须单独批准维护窗口：停止
所有写入者、保存当前双库与侧文件、在停写后整体替换已演练的双库快照并清除旧
WAL/SHM，再启动并验收。不要向运行中的库覆盖 `.db`，不要用 `down -v`。

每日定时备份的待决方案见 [S4 定时备份评估](s4-daily-backup-proposal.md)；当前没有
安装或启用 timer。

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
