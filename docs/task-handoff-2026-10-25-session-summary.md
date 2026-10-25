# taoli1 会话交接：S1/S2 上线、M1 内存排查、M2/S9 待派发（2026-10-25）

新会话先读本文件。分工不变：Claude 只做设计、派发（桌面版 `codex.exe exec`）、亲自验收和部署；Codex 写代码与测试；Claude 不直接改业务源码（≤20 行的小修可例外）。回复用户一律用中文。

## 1 当前生产状态（2026-10-25 14:00 CST）

| 项 | 值 |
|---|---|
| 部署分支 | `codex/frontend-localization-polish` = `6eb53b3`（chore: backend 限额临时 1024m） |
| 生产镜像 | `ghcr.io/wubin19920612/taoli1-backend:sha-6eb53b3524d2a9c9899fc7314c6b3a9c297e72f2`（前端同 SHA） |
| 后端限额 | **1024m**（临时，原 768m） |
| 生产 `.env` 中本次改动 | `MEMORY_PROBE_ENABLED=true`（保留）；`MALLOC_ARENA_MAX`、`MEMORY_TRACEMALLOC_SECONDS` 都已删除 |
| 健康 | live/ready 200，旧 `/api/health` 正常；13:38 后无 OOM，后端约 730–750MB，整机可用约 380–560MB |
| 磁盘 | **50G 已用 43G，剩约 4.1G**（见 §5） |

已上线的提交（按顺序）：`85bc077` S1 运行时监督 → `3be4e8f` S2 采集链解耦 → `f794324` M1-A 内存探针 → `f38ced1` M1-C（去重复拷贝 + 身份目录首次刷新延后 300s）→ `6eb53b3` 限额 1024m。

## 2 本地仓库状态

- 当前分支：`codex/memory-fix`（已推送到 origin），HEAD 为本交接的文档提交。
- 用户 WIP，**不得修改、暂存或提交**：`frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx`。
- 未跟踪、属于 S9 的输入：`backend/requirements.lock`（生产镜像 `pip freeze`，43 个包；与 `6eb53b3` 镜像逐行一致）。
- 其他未跟踪的文件（`docs/architecture-review-2026-10-23-claude.md`、`output/*`、`.pytest_*`、`.worktrees/` 等）保持原样，不清理。

## 3 待办（按优先级）

1. **M2-1 交易可用性缓存治理**：任务书 `docs/m2-1-trade-availability-cache-spec-2026-10-25.md`，**等用户确认后**派发 Codex（建议 gpt-6-astra high，因为缓存语义要求等价）。验收后本地提交，部署需用户批准；上线后用探针采 1 小时，和 1024m 观察期（平台期约 740MB）对比。
2. **S9 测试离线化 + 依赖锁定 + CI 门禁**：任务书 `docs/s9-test-gate-spec-2026-10-25.md` 已完整，**等用户确认**。可以和 M2 并行（文件不重叠）。全量离线结果：1740 passed / 5 failed（`test_api.py` 409）/ 4 errors（`test_pair_spread_query.py` 外连），耗时 45 分钟；大量测试卡在约 30/60 秒，已列为调查项。
3. **Docker 清理**（约 17GB）：等用户决定**自己执行**，还是**授权 Claude 执行**。命令：`sudo docker builder prune -af`（约 6.5GB）、`sudo docker image prune -af`（约 10.6GB，回滚时可从 GHCR 重新拉）。属于永久删除，必须有用户的明确授权。
4. **S4 备份策略修正**：还没写任务书。问题：只按份数保留（最多约 14 份全量，每份约 760MB）、不压缩、手工备份不受管理、和数据库在同一块盘、空间预检只要求 2GB。建议：压缩 + 份数与总量双上限 + 预检“2×库大小 + 一份备份” + 手工备份纳管；异地备份是否要做由用户决定。旧手工备份（5 份，约 3.2GB）删不删也由用户决定。
5. M2-2 ~ M2-4：见 `docs/m2-memory-slimming-plan-2026-10-25.md`。
6. 后续：S3 价差查询/公开访问复用、S6 SQLite 维护（VACUUM 仍在共享连接上运行，会阻塞同一连接上的 API）。

## 4 M1 内存排查结论（详见各文档）

- 文档：`docs/m1-memory-profiling-spec-2026-10-25.md`、`docs/m1c-memory-fix-spec-2026-10-25.md`、`docs/task-handoff-2026-10-25-memory-probe.md`、`docs/task-handoff-2026-10-25-memory-fix.md`（末尾是线上结果）、`docs/m2-memory-slimming-plan-2026-10-25.md`。
- 768m 时每小时 OOM 4–5 次；`MALLOC_ARENA_MAX=2` 无效（7 次）；M1-C 无改善（5 次）；**1024m 下 33 分钟 0 次**。
- tracemalloc（1 帧）开销约 +300MB，在 1024m 下也撑不住，**不要再开**。常驻内容里最大的是 `json/decoder.py:354` 74–76MB（约 120 万对象，原始 JSON 被长期持有）。本机复现时采集链只常驻 1.9MB JSON，所以大头在其他后台任务；已确认 `TradeAvailabilityService._cache` 过期条目不删、条目数无上限（即 M2-1）。
- 本机测量脚本（scratchpad，新会话需要时可以重写）：按阶段测峰值、60 轮泄漏测量、JSON 持有者 25 帧定位、接口 payload 解析体积测量。

## 5 运维要点

- SSH 别名 `taoli1-prod`；仓库目录 `/home/ubuntu/wubin/taoli1`；服务器时区 CST（UTC+8）。
- 部署：`git push origin <sha>:refs/heads/codex/frontend-localization-polish` 触发 CI（`build-production-images.yml`）；本机没有 `gh`，CI 状态用 `curl https://api.github.com/repos/wubin19920612/taoli1/actions/runs?head_sha=<sha>` 查。镜像就绪后，把 `output/s1-isolated-deploy-20261025.sh` 的 LF 版本 scp 到服务器，**在服务器端用 nohup 运行**：`DEPLOY_BRANCH=codex/frontend-localization-polish DEPLOY_COMMIT=<40位sha>`（本地 ssh 长连接会断，退出码 255 不代表失败），日志写 `output/*-deployment-*.log`，以 `DEPLOY_EXIT=` 作为结束标志。
- 部署前必须检查：RestartCount、`journalctl -k | grep "Killed process"`、剩余磁盘（脚本要求 ≥2GB；每次部署约增加 760MB 备份）。
- 用户授权 Claude 读写生产 `.env` 中与 M1 相关的键（`MEMORY_*`、`MALLOC_ARENA_MAX`）：改之前先备份到 `backups/env-before-*`，只 grep 指定的键，不输出其他内容。
- 用户曾在对话里贴出 dashboard 密码，已建议轮换；Claude **不使用密码**访问 `/api/health/detail`。探针数据从 `docker logs taoli1-backend-1 | grep '"memory_probe":1'` 读取。
- 已知的既有日志噪声：`trade_availability.py` 约每分钟 4 次 `RuntimeError: 聚合行情中未找到指定原始市场`（M2-1 中只读调查）。

## 6 追加进展（2026-10-25 17:10–18:00）

- Docker 清理已执行（用户授权）：回收 13.7G，磁盘剩 23G；13:38 重启后 0 OOM，RSS 平台约 741MB。
- M2-1 已完成并验收：分支 `codex/m2-1-cache` @ `9560a36`（worktree `.worktrees/m2-1-cache`），只改 `trade_availability.py` + 新测试 + 交接；Claude 复跑 126 passed。RuntimeError 调查见 `docs/task-handoff-2026-10-25-trade-availability-cache.md` §3.1（三类原因，含 HL `dex` None/空串 vs `main`、monitor 二次匹配不 strip/lower、诊断错误被吞），是否另开修复任务待定。
- S9 已完成并验收：分支 `codex/s9-test-gate` @ `e4e43b6`（worktree `.worktrees/s9-test-gate`）。后端全量 1745 passed / 93 秒（原 45 分钟，慢因是每个 client 重复解析 CA 证书）；离线护栏 Claude 亲测有效。本机跑测试要加 `--basetemp=<可写目录>`，否则 `pytest-of-wubin` 权限错误会造成 56 个 error。
- 尚未验证：Docker 镜像构建（本机/WSL 无 docker）与 CI test job，只能推送部署分支后由 CI 验证。
- 两个分支都没有推送。下一步：合并 M2-1+S9 → 推送部署分支（CI 先测试再构建）→ 部署 → 用探针采 1 小时做对比。推送和部署都需要用户批准。
