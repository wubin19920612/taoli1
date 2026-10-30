# M1 内存剖析与治理：设计与任务书（Claude，2026-10-25，待用户确认）

## 1 现象（已实测）

- 后端容器 `mem_limit: 768m`（`docker-compose.prod.yml:5`），自 2026-10-23 起每小时被 cgroup OOM 杀 5–16 次（内核日志 `Memory cgroup out of memory: Killed process … (uvicorn) anon-rss≈780MB`）。S1、S2 上线前后频率不变。
- 2026-10-25 00:53–00:59 每 9 秒一次 `docker stats` 采样（重启后约 3 分钟开始）：
  - 基线从约 625MB 缓慢升到 670MB；刚启动时约 420MB。
  - 每 1–2 分钟出现一次 +40~70MB 的尖峰（最高 718MB），并伴随单核 100% CPU 的突发。
  - 结论：既有**周期性的大块分配**（尖峰），也有**基线持续抬升**（泄漏、缓存增长或内存碎片化导致不归还）。某次尖峰越过 768MB 就会被 OOM 杀掉。
- 连带影响：OOM 前约 1 分钟出现大量 `database is locked`；每次重启都会丢失内存里的告警冷却、连续命中计数和 Gate TWAP 作业，采集也会中断；部署时在线备份耗时从约 1 分钟变成约 12.5 分钟。

## 2 嫌疑点（未证实，M1 的任务就是用数据区分）

| # | 嫌疑 | 位置 | 对应特征 |
|---|---|---|---|
| H1 | 价差实时缓存无上限、键永不删除 | `pair_spread_query.py:519-525` | 基线抬升 |
| H2 | 采集每轮重建全部 `MarketSnapshot`/`Opportunity`（约 1.2 万市场，`build_all_opportunities` 约 190ms CPU） | `collector.py`、`spread_engine.py` | 每轮的分配峰值 |
| H3 | 某个周期性研究/监控任务一次性加载大量数据（单核 100% 尖峰约每 1.5 分钟一次） | 20 个后台任务之一（如 squeeze、negative-basis、second-level-sampler、market-identity-catalog、multi-exchange-premium） | 周期尖峰 |
| H4 | glibc malloc 多 arena 碎片：十余个线程（每个 aiosqlite 连接一个线程，外加 httpx 线程）导致峰值内存不归还给系统 | 运行时 | 基线只升不降 |

## 3 方案：分三步，本任务书只做 A + B

- **A 测量**（代码，默认关闭，开关打开时才生效）：给出“哪个任务、哪类对象、哪行代码”占用了内存的证据。
- **B 零代码缓解实验**：`MALLOC_ARENA_MAX=2`，验证 H4。
- **C 定点修复**：根据 A、B 的数据另写任务书（例如给 H1 缓存加预算，或修 H3 对应的任务），本次不做。

### A1 内存采样任务 `memory-sampler`（新模块 `backend/app/runtime/memory_probe.py`）

- 开关 `MEMORY_PROBE_ENABLED`（默认 `false`）；间隔 `MEMORY_PROBE_INTERVAL_SECONDS`（默认 10）。
- 每次采样输出**一行 JSON 日志**（logger 名 `memory_probe`，INFO），可以直接用 `docker logs` 读取，不需要 dashboard 密码：
  - `rss_mb`、`rss_anon_mb`（读 `/proc/self/status` 的 `VmRSS`/`RssAnon`；非 Linux 时为 null）、`threads`；
  - `gc_counts`（`gc.get_count()`），以及自上次采样以来的 gc 次数；
  - 快照规模：`store.get_markets()`、`get_all_markets()`、`get_opportunities()` 的长度（只取长度，不复制；如果 getter 会复制，就改为直接读内部列表长度，并加只读的 `sizes()` 方法到 `SnapshotStore`）；
  - `realtime_cache_stats()` 的键数与点数（S1 已有）；
  - `active_tasks`：自上次采样以来 `last_iteration_at` 有更新的 supervisor 任务名列表（用于把尖峰和任务对应起来）。
- 由 `TaskSupervisor` 启动，非 critical；出错时只记录日志，不影响其他任务。
- 采样本身不得持有大对象，单次采样开销 < 5ms（测试里断言不调用 `gc.get_objects`）。

### A2 按任务的内存增量（`supervisor.py` 小改动）

- 开关打开时，`beat(name, ok)` 额外记录调用时刻的 `rss_anon_mb`，并保存为该任务的 `last_rss_delta_mb`（本次 beat 与上次 beat 之间整个进程的 RSS 变化）。这只是相关性信号，不是精确归因；注释和交接里要写明这一点。
- `memory-sampler` 每 60 秒额外输出一行 `task_rss_deltas`：各任务最近 N 次 delta 的最大值和 p50。
- 关闭时 `beat` 的行为和开销与现在完全一致。

### A3 可选 tracemalloc 窗口

- 开关 `MEMORY_TRACEMALLOC_SECONDS`（默认 0 = 不启用）。>0 时在启动后开启 `tracemalloc.start(1)`（只记 1 帧），每 60 秒输出 top 25 `lineno` 统计和与上一次快照的 top 25 差异（只输出 文件:行号、size_kb、count，不输出任何数据内容）；到期后 `tracemalloc.stop()`。
- tracemalloc 本身会额外消耗 20–40% 内存，所以只在用户批准的临时窗口里打开（见 §5 决策 2）。

### B `MALLOC_ARENA_MAX=2`

- `docker-compose.yml` 的 backend 已配置 `env_file: .env`，所以**不改 compose 和代码**：A 上线并取得 1 小时基线后，在生产 `.env` 追加 `MALLOC_ARENA_MAX=2`，然后 `docker compose up -d --no-build` 重建 backend 生效；回滚时删掉这一行再重建。
- 用户已确认顺序：A 先上线取基线，再打开 B；暂不开 tracemalloc，不调限额。效果通过 A1 的 `rss_anon_mb` 基线斜率和 OOM 次数对比。

## 4 Codex 任务范围

- 新建：`backend/app/runtime/memory_probe.py`、`backend/tests/test_memory_probe.py`、`docs/task-handoff-2026-10-25-memory-probe.md`
- 修改：`backend/app/runtime/supervisor.py`（A2，仅开关打开时生效）、`backend/app/main.py`（启动 memory-sampler 和 tracemalloc 窗口）、`backend/app/core/config.py`（3 个开关，读取方式同 `POLL_INTERVAL_SECONDS`）、`backend/app/services/snapshot_store.py`（如需只读 `sizes()`）
- 禁止修改：所有业务服务的判断逻辑、`pair_spread_query.py`（只调用已有的 `realtime_cache_stats`）、前端、`deploy/`、`docker-compose*.yml`、本任务书。
- A3（tracemalloc）照常实现，默认关闭；本轮不启用。

测试（先写测试）：
1. 开关关闭：不启动 memory-sampler；`beat` 不读 `/proc`（monkeypatch 断言）。
2. 采样一行 JSON 的字段齐全；`/proc` 不可读时字段为 null，不抛异常。
3. `active_tasks` 只列出两次采样之间有心跳的任务。
4. A2 的 delta 计算正确，窗口有界。
5. tracemalloc 窗口到期后会调用 stop；输出里不包含 traceback 以外的内容。
6. 回归：`test_task_supervisor.py`、`test_health_routes.py`、`test_snapshot_consumers.py`、`test_collector.py`。

## 5 需要用户决定

1. **是否同时上线 B（`MALLOC_ARENA_MAX=2`）**。建议：A 先单独上线 1 小时取基线，再打开 B 观察 1 小时，这样能区分 H4 的作用；也可以 A、B 一起上线以更快缓解，但效果就分不清来源。
2. **tracemalloc 窗口**。建议：先只用 A1/A2（开销很小）。如果数据仍然无法定位，再开一次 20 分钟的 tracemalloc 窗口；那时是否临时把限额调到 1024MB，到时候再看整机余量决定（当前可用约 680–845MB、无 swap）。
3. **限额本身暂不调整**（建议）。

## 6 上线与判读（Claude 执行，部署需用户批准）

- 部署流程同 S1/S2；`.env` 里打开 `MEMORY_PROBE_ENABLED=true`（这一步要改生产配置，需要用户批准，或由用户自己改）。
- 采集 1 小时日志：`docker logs taoli1-backend-1 | grep memory_probe`，结合内核 OOM 记录分析：
  - 尖峰时刻的 `active_tasks` 交集 → 定位 H3；
  - `realtime_caches` 键数/点数随时间的变化 → 判断 H1；
  - 打开 B 前后 `rss_anon_mb` 基线斜率和 OOM 次数的对比 → 判断 H4；
- 输出 M1-C 定点修复任务书。
- 回滚：`MEMORY_PROBE_ENABLED=false`；`MALLOC_ARENA_MAX` 删除或覆盖；revert 提交。
