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
- 镜像构建、推送、生产部署和线上行为结果仍待验证；不得把本地通过或健康检查当作告警恢复。
- 首次采用新脚本前，生产仓库仍在 `e4fc83f`，必须先核对现有数据库备份和 GHCR 镜像，再用 `git pull --ff-only origin codex/server-low-memory` 快进到这个分支的已审核提交。后续使用 `DEPLOY_BRANCH`、`DEPLOY_COMMIT` 运行脚本。
- 生产 `.env`、其备份、`CACHED`、数据库卷与备份必须保留。不得执行 `down -v` 或把私钥、Webhook、令牌写入仓库。
- 本地原开发分支的未跟踪告警交接文档、`output/` 和 `script/` 产物均属于其他任务，不纳入本分支。

## 下一步

1. 验证 workflow 两项镜像构建均成功，并确认服务器可拉取固定 SHA 镜像；私有 GHCR 包需在服务器配置仅 `read:packages` 的访问令牌。
2. 完成生产备份、快进与部署，记录提交号、镜像 digest、容器状态、内存、`/api/health`、Gate/Aster 行情和告警事件。
3. 在无构建任务时观察后端 RSS 与容器内存趋势。若仍持续上涨，再对业务对象和缓存做定点剖析；当前证据不能认定业务轮询泄漏。
