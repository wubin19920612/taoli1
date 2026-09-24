# 生产服务器频繁卡死排查

日期：2026-09-24。模块：服务器运维。范围为只读取证、根因判断与后续治理；未修改服务器配置、容器或数据库。

## 目标与基线

- 目标：解释生产主机反复无响应，区分已证实的 OOM 事件与 9 月 24 日未记录 OOM 的构建卡顿。
- 仓库分支：`codex/frontend-localization-polish`。服务器仓库在重启后确认仍为 `e4fc83f`。后续 `643cb25`、`2aa4477` 属于其他模块，不应随本次调查部署。
- 主机为腾讯云 CVM，2 vCPU、1.9 GiB RAM、无 swap。运行 `taoli1` 前后端及 `lp` 的 gateway、backend、frontend，共五个容器。
- 业务代码入口与部署配置：`frontend/Dockerfile`、`frontend/package.json`、`docker-compose.yml`、`deploy/linux-update.sh`。前端构建为 `tsc -b && vite build`；Dockerfile 设置 `NODE_OPTIONS=--max-old-space-size=768`，仅限制 V8 堆，并不限制 Node 总 RSS、esbuild、BuildKit 或 Docker daemon。更新脚本在生产主机执行 `docker compose build --pull`。

## 历史故障证据（服务器本地时间，CST）

| 时间 | 直接证据 | 判断 |
| --- | --- | --- |
| 2026-09-13 00:41:04 | 上上次启动内核日志：`docker-buildx invoked oom-killer`、`global_oom`，内核杀死 `uvicorn`（匿名 RSS 191,692 kB）。同一 OOM 快照有 `npm run build`、`node`（RSS 143,551 页，约 561 MiB）、`esbuild`（约 92 MiB）及 `dockerd`（约 398 MiB）；swap 为 0。`systemd-journald` 同时出现 watchdog 失败并重启。 | **确认发生全局内存耗尽**，前端构建与业务容器同时运行，业务进程被杀。 |
| 2026-09-23 12:07:15-16 | 上次启动内核日志：`systemd invoked oom-killer`、`global_oom`，内核杀死 `uvicorn`（匿名 RSS 355,956 kB）。OOM 快照同时存在 `docker-buildx`、`npm run build`、`node`（约 347 MiB）和 `dockerd`（约 473 MiB）；swap 为 0。 | **确认第二次全局内存耗尽**，仍发生在前端构建与业务并行期间。日志不能独立确定这次构建属于哪个 Compose 项目。 |
| 2026-09-24 08:12:20 | `sudo` 日志记录在 `~/wubin/taoli1` 执行 `docker compose build --pull`。08:12:25 起 Docker/BuildKit healthcheck 报连接错误；08:14 起 containerd 事件超时，多个容器 healthcheck 启动超时。最后一批有效 Docker 错误在 08:15:42；08:28:53 还有一条 cron 日志，上一轮日志截至 08:29:08。新一轮启动从 08:47:23 开始。 | **确认构建与整机/容器失去响应时间吻合**。本次内核日志没有 OOM、hung-task 或文件系统错误记录；不能把本次卡死的具体机制直接定为 OOM。缺少冻结时的内存、CPU、磁盘延迟和云平台指标。 |

## 重启后的现状

- SSH 已恢复。`taoli1` 前后端容器 healthy，服务器本机后端 `/api/health` 返回 HTTP 200（约 1.04 秒），前端代理 `/api/health` 返回 HTTP 200（约 0.10 秒）。没有发现仍在运行的 `docker-buildx`、`npm`、`vite` 或 `esbuild` 进程。此状态只证明重启后的服务可达，不能证明告警业务行为或新镜像已部署。
- 服务器仓库为 `e4fc83f`；9 月 24 日中断前未执行 `docker compose up`，因此当前容器不能视为已运行该提交的新镜像。`docker inspect` 当前容器显示 `OOMKilled=false`，这不能推翻先前内核记录的历史 OOM。
- 重启后 `free -h` 显示约 1.0 GiB available，五个容器的当次 `docker stats` 合计约 453 MiB；`taoli1` 后端约 357 MiB。没有持续的历史内存采样，不能据重启后的空闲量推定构建期间也有充足内存。
- 根分区 50 GiB，已用 40 GiB，剩余 7.2 GiB（85%）；inode 使用 12%。Docker 构建缓存 7.002 GiB，其中 6.541 GiB 标记为可回收。上一轮日志未发现 `ENOSPC`、ext4 错误或 I/O error。空间紧张会削弱后续构建余量，但**磁盘写满不是当前有证据的根因**；云盘延迟或 IOPS 限速仍需平台指标验证。
- 已有 SQLite 备份 `backups/radar-before-aster-alert-20260924T000714Z.db`，重启后 SHA-256 再次核对为 `f951cf3f87d3ebcaf2a94b00a31cabb38219b76a7604e330c174b3cfd0bb85a3`。服务器原有 `.env` 备份及 `CACHED` 均仍在，未清理。

## 根因判断与缺口

1. **已证实的反复故障模式**：2 GiB、无 swap 的主机同时运行五个容器和生产前端构建；至少两次构建期间触发全局 OOM，并误伤业务 `uvicorn`。构建时 `node`、Docker daemon 和业务进程的实测 RSS 已足以解释内存竞争。V8 堆上限不是整机内存隔离；Compose 配置也未设置资源限制。
2. **9 月 24 日这一次的最可能诱因**：相同的生产主机内构建再次使 Docker/BuildKit、containerd 和健康检查接连超时。没有保留下来证明内核 OOM 的记录，也没有冻结时指标；可能是内存回收停顿、磁盘 I/O 饱和，或两者叠加。不要把该次事件写成“已确认 OOM”。
3. **尚未证实的其他因素**：云主机 CPU 抢占、云盘延迟/IOPS 限流、BuildKit 自身故障。当前 `vmstat` 没显示持续的 CPU steal 或 I/O wait，但这是重启后的短样本，不能排除冻结前发生过。

## 治理顺序

1. 停止在这台生产主机上运行前端 `npm ci`、`vite build` 或完整 `docker compose build --pull`。优先在 CI/独立构建机生成带提交号或 digest 的镜像，再由生产主机拉取并更新；在此流程完成前不要为了验证而重演本次高负载构建。
2. 增加主机内存。4 GiB 是应先评估的下限，若仍承载两个 Compose 项目和偶发本机构建，则需结合峰值指标评估更大规格。swap 可作为短期缓冲，但会把内存尖峰转为云盘 I/O 和长时间迟滞，不能替代离机构建。
3. 在云平台查看 9 月 13 日 00:35-00:45、9 月 23 日 12:00-12:10、9 月 24 日 08:10-08:47 的内存使用、CPU/steal、云盘吞吐/延迟/队列、网络与实例事件。启用分钟级外部监控与告警；下次异常优先保留冻结时指标，再判断是否需要调整 BuildKit 并行度或服务资源边界。
4. 构建迁出后，评估清理仅构建缓存以回收约 6.5 GiB；先确认没有进行中的构建和回滚依赖。不要清理 Docker volume、数据库备份、`.env` 备份或 `CACHED`，也不要使用 `docker compose down -v`。

## 交付边界

- 本次为分析与文档交接，没有修改生产配置或镜像，也没有执行新一轮构建/部署。告警模块的生产验收继续按 `docs/task-handoff-2026-09-24-spread-alerts-aster-production.md` 执行，不属于本服务器运维任务的验收结果。
- 本地工作区中其他任务的 `output/`、`script/dexe_bybit_bitget_chain.py` 和前述告警模块交接文件均未纳入本次提交范围。
