# S1 运行时监督与健康分层交接

## 目标、范围与版本

- 规格：本地未跟踪的 `docs/architecture-review-2026-10-23-claude.md` §4 P0-3、§8，以及本任务用户补充要求。该报告没有修改或提交。
- 分支：`codex/runtime-supervision`；基线：`045ed8e4bbf3dd9031a7911c38f0f697ed340a82`。
- 本交接与代码同一提交，提交信息：`feat: add runtime task supervision and layered health checks`。最终 SHA 可用 `git log -1 --format=%H` 查看。
- 本次交付仅本地提交；**尚未推送、尚未部署**。没有 SSH、线上备份、线上接口验证或线上数据库变更。当前线上版本未经本任务核实。
- 不改变告警、Astro、FS、Gate TWAP、价差、市场身份判断或阈值；无新增依赖、表结构或前端改动。

## 六项目标与代码入口

| 目标 | 位置 | 行为 |
| --- | --- | --- |
| 1. 任务监督 | `backend/app/runtime/__init__.py`、`supervisor.py` 的 `TaskSupervisor`；`backend/app/main.py` 的 `_start_background_task` | 统一 coroutine factory 包装，记录 running/backoff/failed/stopped、started_at、last_iteration_at、last_ok_at、last_error、iterations、restarts、critical；额外提供 last_failure_at、backoff_seconds、iteration_age_seconds。原有 20 个任务的名称、调用参数和启动条件保持一致。 |
| 2. 每轮心跳 | `services/collector.py`、`main.py`、`services/fs_monitor.py`、`services/announcement_polling.py`、`services/announcements.py` | collector、alert、FS、announcement、announcement-enrichment 循环完成或捕获异常后报告 beat；取消不虚增迭代。其他任务仅提供存活和重启状态。 |
| 3. 阶段耗时与代次 | `services/collector.py` 的 `collect_once`/`_collect_once`；`services/snapshot_store.py` 的 `mark_published`；`runtime/supervisor.py` 的 `StageTimings` | 单调时钟记录 poll/prepare/build/publish/history/index；默认最近 120 轮（构造参数 timing_window 可调整），秒为单位，nearest-rank p50/p95/max 和样本数。发布后立即递增 generation、写 published_at，发生后续 history 异常也不撤销发布。 |
| 4. 事件循环延迟 | `runtime/supervisor.py` 的 `EventLoopLag`；lifespan 启动 `event-loop-lag` | 每秒测量 sleep 偏差，保留最近 300 秒且最多 300 点；输出 p50/p95/max。API-only lifespan 也启动该诊断探针。 |
| 5. 健康分层 | `api/routes_health.py`；`services/pair_spread_query.py` 的 `realtime_cache_stats` | 旧 health 原样；live 恒 200；ready 仅在 start_collector 时检查两个 critical 任务及快照时效；detail 复用 dashboard 密码校验并输出任务、阶段、各源年龄、延迟、四个缓存的键数和点数。 |
| 6. 关闭安全 | `runtime/supervisor.py` 的 `stop`；`main.py` lifespan 的 finally | 统一取消后 gather(return_exceptions=True)，逐个记录非取消异常，继续原有 DB/client 关闭流程。 |

测试文件：新增 `backend/tests/test_task_supervisor.py`、`backend/tests/test_health_routes.py`、`backend/tests/s1_offline_guard.py`。`backend/tests/test_api.py` 仅调整启动记录 fake，使其接受 coroutine factory。

## 业务兼容与指标语义

- `/api/health` 仍然仅含 `status`、`markets`、`opportunities`、`exchange_errors`、`exchange_states`；原有过滤规则和时间序列化不变。Compose 配置未改。
- ready：`market-collector` 和 `alert-loop` 均为 running，且 `0 <= now - published_at < 3 * poll_interval_seconds + 30`；默认 5 秒采集周期对应 45 秒阈值。恰好到阈值即 503。未启动采集时不要求快照和 critical 任务。
- 单源甚至多个外部来源失败不直接影响 ready/live；只要发布继续推进、critical 任务 running，ready 可为 200。ready 不是交易可执行性或行情质量保证。
- 重启等待为 5、10、20、40、80、160、300、300…秒。每次失败的首秒保持 failed 可见，随后显示 backoff，首秒包含在退避内。重试期间不创建重复任务；完成过成功轮次后再异常，退避恢复到 5 秒。正常返回和 CancelledError 不重启。
- last_error 不保留任意异常原文、cause、traceback；仅保留异常类型和 `[message redacted]`，再截断为 500 字，避免未知格式凭证泄露。恢复运行后仍保留最近一次错误证据。
- 心跳通过 ContextVar 绑定当前 supervisor，独立调用原有循环无需额外参数。iterations 是完成的轮次数，不是采集成功交易所数量；ok 表示该层循环未向外抛出异常，不覆盖业务层自行处理的局部失败。
- announcement-loop 是多个来源及提醒任务的聚合心跳。一个来源挂起可能被其他来源心跳掩盖；既有 provider 轮询记录仍用于逐来源诊断，本阶段未改子任务调度结构。
- 阶段指标保留已结束阶段，包括抛异常阶段的耗时；跳过阶段没有样本，不伪造零值。无 active adapter 时仍记录空快照发布与 generation，但保持原有 all_markets setter 行为。
- SnapshotStore 原有 getter/setter、复制规则和返回值未变。只有 collector 的完整发布调用新增 mark_published；外部手动 setter 不自动推进代次。
- 各交易所 data_age_seconds 来自 collector 已有 last_success_at；从未成功时为 null，不把失败时间作为成功时间。此值是来源采集成功年龄，不是每个合约行情时间戳年龄。
- 四个缓存只读统计，不暴露用户键、不清理缓存。symbol 缓存点数累加内层各交易所序列。
- detail 沿用 verify_dashboard_password：配置密码时无密码/错误密码返回 401；密码为空时沿用现有放行语义。

## 验证命令与结果

仓库根目录，PowerShell。以下是已执行的完整通过命令；**再次运行必须换用新的 basetemp 字母**，不要复用或清理 a/b/c。

```powershell
$env:PYTHONPATH="$PWD\backend\tests;$PWD\backend"
python -m pytest -q -p s1_offline_guard -p no:cacheprovider `
  backend/tests/test_task_supervisor.py backend/tests/test_health_routes.py `
  backend/tests/test_collector.py backend/tests/test_collector_pipeline.py `
  backend/tests/test_alert_loop.py backend/tests/test_fs_monitor.py `
  backend/tests/test_api.py::test_health_endpoint `
  backend/tests/test_api.py::test_api_only_mode_does_not_start_background_workers `
  backend/tests/test_api.py::test_price_collector_starts_announcement_and_oil_news_workers `
  backend/tests/test_api.py::test_health_endpoint_applies_global_symbol_and_exchange_exclusions `
  backend/tests/test_api.py::test_health_endpoint_reports_exchange_poll_states `
  backend/tests/test_api.py::test_retired_monitor_endpoints_are_unavailable `
  --basetemp=backend/.pytest_s1_c --tb=short
```

结果：**146 passed in 159.07s**。结果保留在未跟踪的 `output/s1-tests-c.txt`。包括全部六项现有直接引用 `/health` 的测试；已递归扫描 backend/tests 确认。

离线插件阻断 httpx 的真实 HTTP transport 和外部 socket 连接；仅允许 Windows asyncio socketpair 所需的本机回环连接。ASGITransport/MockTransport/fake 不受影响；即使业务代码捕获了被拦截异常，fixture 结束时仍会使测试失败。本次通过集没有真实飞书、Astro、Gate 或交易所网络访问尝试。

```powershell
python -m ruff check backend/app/runtime backend/tests/test_task_supervisor.py backend/tests/test_health_routes.py backend/tests/s1_offline_guard.py
git diff --check
```

结果：新增 Python 文件 lint 全通过；diff 空白检查通过。

在 backend 目录执行：

```powershell
python -c "import app.main"
```

结果：退出码 0。

另外通过 AST 对比基线和当前代码，确认全部 20 个原有后台任务的调用、名称、条件一致，旧 health 三个函数结构一致。前端两个 WIP 和架构报告 SHA256 与任务开始时完全相同。

测试过程保留：a 轮的网络拦截器误拦 Windows asyncio 内部回环连接，导致测试初始化错误；修正隔离插件后 b 轮 26 passed、1 failed（新测试把原有 UTC `Z` 序列化误写为 `+00:00`）；修正预期后 c 轮 146 全通过。失败未通过删除临时目录解决。

## 本地保留项与已知限制

- 用户 WIP：`frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx`，未修改、未暂存、未提交或还原。
- 架构报告保持未跟踪；原有 `.worktrees/`、大量 `output/`、`script/dexe_bybit_bitget_chain.py`、旧交接文档及全部数据库、日志、备份、pytest 目录均保留。本任务没有清理这些内容。
- 本任务新增未提交产物：`output/s1_offline_guard.py`（初始隔离插件副本）、`output/s1-tests-c.txt`；`backend/.pytest_s1_a`、`backend/.pytest_s1_b`、`backend/.pytest_s1_c` 均保留。
- 指标在当前进程内保存，重启清空；没有持久化或 24 小时采样任务。窗口 p95 不应被当作完整 24 小时的总体 p95。
- 挂起检测通过心跳年龄和快照时效暴露，不强制取消/重启仍在运行的任务。只有异常退出触发重启，避免新增业务超时和执行副作用。
- 关闭等待没有强制超时，依赖任务正常响应取消；本次解决已失败任务跳过资源关闭的问题，不承诺能终止吞掉取消的协程。
- 本地验收用离线故障注入的 negative-basis-monitor 证明 detail 首次显示 failed、退避后 running；不代表已在线上注入故障或验证恢复。
- 尚未开展部署后 24 小时数据年龄 p95、采集阶段 p95、事件循环延迟 p95 和缓存键数峰值观察。这是用户限定本地交付后的待验收项。

## 后续与回滚

1. 用户另行授权后，在新的运维任务执行推送、隔离备份、部署与线上验收，遵循届时的部署文档及线上证据。
2. 部署后定期采样受保护的 detail，记录各来源年龄及缓存键数峰值、明确时间窗口的阶段和事件循环延迟 p95，作为 S2/S3 基线；不要将不同窗口 p95 简单平均为全天 p95。
3. 完成本阶段线上观察后，在新任务开展下一模块，不在本任务内改变采集链、缓存预算或业务判断。
4. 回滚为对本次单一提交执行 `git revert <本提交SHA>`。无数据库迁移，无需删除数据库或指标产物；旧 health 与 Compose 不受新增路由移除影响。含用户 WIP 的工作区应继续保持隔离，勿使用破坏性回退。
