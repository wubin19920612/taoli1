# 2 GB 生产服务器内存治理交接

日期：2026-09-24。模块：服务器运维。验收目标：生产发布不再启动本机前端构建；在 2 GB 内存下持续运行服务并保留数据库与告警验证。

## 基线与原因

- 独立分支 `codex/server-low-memory` 从生产版本 `e4fc83f` 派生，不包含后续其他模块提交 `643cb25`、`2aa4477`。原有开发分支和未跟踪产物未更改。
- 主机为 2 vCPU、1.9 GiB RAM、无 swap，运行 `taoli1` 与 `lp` 共五个容器。重启后的 `taoli1` 后端约 350-365 MiB。
- 9 月 13 日、23 日的内核日志证实前端 Docker 构建期间全局 OOM，最终杀死 `uvicorn`。9 月 24 日 08:12 的 `docker compose build --pull` 之后内存逼近 99%，页面回收和读盘暴涨，Docker 健康检查与 SSH 失去响应；这次没有 OOM kill 记录。详见 `docs/task-handoff-2026-09-24-server-freeze-investigation.md`（该文档在开发分支，未纳入本生产分支）。
- 云监控自动摘要把峰值标为北京时间 00:20，但本机该时段采样内存约 70-71%；本机真正的高峰在 08:14。需用云监控原始数据继续核对摘要时间标签。

## 本分支改动

- `.github/workflows/build-production-images.yml`：在 GitHub Actions 构建 amd64 后端和前端镜像，按完整提交号推送 GHCR。
- `docker-compose.prod.yml`：生产只用预构建镜像，移除 build 配置，backend/frontend 分别限制为 768/96 MiB。
- `deploy/linux-update.sh`：固定分支和提交号、校验已有数据库备份、快进代码、拉取镜像、以 `--no-build` 启动并等待健康；不删除卷。
- `backend/app/services/data_filters.py`：行情和机会列表每次过滤只计算一次排除集合，避免每条记录重复分配。
- `backend/app/services/announcement_research.py`：研究结果缓存限制为 256 个最近使用条目，避免常驻缓存无界增长。

## 交付与上线

- 定向后端测试 35 项通过。全量测试 784 项通过，11 项在系统临时目录创建夹具时因本机沙箱权限报错；改用可写临时目录重跑相关测试 13 项通过。生产 Compose 在服务器解析确认两个 `build` 均为空、内存上限为 768/96 MiB；发布脚本通过服务器 Bash 语法检查。
- 实现提交 `f4fa38ee8e731d4b0a50e8947937a993e416ec2c` 已推送到 `origin/codex/server-low-memory`。GitHub Actions run `35947030288` 的前后端镜像构建均成功。生产机匿名读取镜像清单成功；后端 index digest 为 `sha256:09e764bab1b1539d4ebfafb35923f0b1bdea5ec4920fca4a74943bbe99e56cce`，前端为 `sha256:d8a5e7a601bcb79ace5ea641af2f0a7c5bac59eccef025918acd188518a79f21`。
- 服务器从 `e4fc83f` 使用 `git pull --ff-only origin codex/server-low-memory` 仅快进到 `f4fa38e`。部署脚本新建 `backups/radar-before-f4fa38ee8e73-20260924T022853Z.db`，大小 605,343,744 字节，`integrity_check=ok`，容器内外 SHA-256 一致：`7722b693166d3ec472018864efbb9776dd115e4f7eb5244f5c717145536205ce`。此前备份 `radar-before-aster-alert-20260924T000714Z.db` 的 SHA-256 也再次核对一致；`.env` 备份、`CACHED` 和数据卷未清理。
- 服务器仅执行 `compose pull` 与 `compose up -d --no-build --wait`；未运行本机构建或 `down -v`。两个容器使用 `f4fa38e` 镜像，健康检查正常，`OOMKilled=false`。启动约 9 分钟后后端约 347 MiB/768 MiB、前端约 3.6 MiB/96 MiB，主机 `MemAvailable` 约 999 MiB、根盘剩余约 7.7 GiB。这是短时观察，仍需持续看趋势。
- 后端与前端代理 `/api/health` 均正常；Gate 现货、Gate 永续和 Aster 永续 `TAKEUSDT` 报价与成交额均有新数据，Aster 资金费率周期为 4 小时。新后端 02:29:42 UTC 启动后，最近 100 条告警事件中有 20 条新事件：`sent=15`、`muted=5`、`failed=0`。代码仅在飞书 webhook 返回成功后记为 `sent`，尚未验证用户终端实际收件。当前没有 `TAKEUSDT` 新事件；其 Gate 现货买入、Aster 永续卖出的盘口价差当时不是正值，不能人为制造告警验证。
- 后端日志仍有 Gate 公告 API 返回 HTTP 567 的错误，与 Gate 行情采集及本次内存治理不同；属于后续独立模块问题。
- 首次采用新脚本前，生产仓库仍在 `e4fc83f`，必须先核对现有数据库备份和 GHCR 镜像，再用 `git pull --ff-only origin codex/server-low-memory` 快进到这个分支的已审核提交。后续使用 `DEPLOY_BRANCH`、`DEPLOY_COMMIT` 运行脚本。
- 生产 `.env`、其备份、`CACHED`、数据库卷与备份必须保留。不得执行 `down -v` 或把私钥、Webhook、令牌写入仓库。
- 本地原开发分支的未跟踪告警交接文档、`output/` 和 `script/` 产物均属于其他任务，不纳入本分支。

## 下一步

1. 连续观察至少 24 小时的主机 `MemAvailable`、后端容器内存、OOM、健康检查与云盘读延迟。当前仅有约 9 分钟的上线后稳定数据，不能据此证明长期无泄漏。
2. 后续发布继续使用已审核提交的 CI 镜像和 `--no-build` 脚本。不要在 2 GB 主机复现旧 `docker compose build --pull` 路径。
3. 若没有构建任务时后端 RSS 持续上涨，再对业务对象与缓存做定点剖析。Gate 公告 API 567 和特定 TAKE 告警条件应分别在对应功能模块任务处理。
