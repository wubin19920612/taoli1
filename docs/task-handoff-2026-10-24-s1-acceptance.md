# S1 运行时监督与健康分层：验收交接（2026-10-24）

用途：在新会话中继续 S1 的收尾（Claude 验收 → 提交 → 用户批准后推送/部署）。不依赖旧会话上下文。

## 1 背景与分工

- 设计来源：`docs/architecture-review-2026-10-23-claude.md`（未跟踪）§4 P0-3 与 §8 任务书。
- 分工：Claude 只做设计、派发、验收；代码由 Codex 编写（桌面版 `codex.exe exec`，模型 gpt-6-astra high）。Claude 不直接改业务源码（≤20 行的小修可例外）。
- 本轮 Codex 提示词：`C:\Users\wubin\.claude\projects\D-------claude---\memory\taoli1-s1-codex-prompt.txt`；运行日志：同目录 `taoli1-s1-codex-run1.log`。
- Codex 第一轮因用户重启电脑被中止，但代码、测试、Codex 交接文档已全部写完，**唯一缺的是本地提交**。

## 2 当前工作区状态

- 分支：`codex/runtime-supervision`（从 `045ed8e` 切出），**全部改动未提交、未推送、未部署**。
- 本任务改动（应提交）：
  - 修改：`backend/app/main.py`、`backend/app/api/routes_health.py`、`backend/app/services/collector.py`、`backend/app/services/snapshot_store.py`、`backend/app/services/fs_monitor.py`、`backend/app/services/announcements.py`、`backend/app/services/announcement_polling.py`、`backend/app/services/pair_spread_query.py`（只加只读 `realtime_cache_stats()`）、`backend/tests/test_api.py`（fake 改为接收 coroutine factory）
  - 新增：`backend/app/runtime/__init__.py`、`backend/app/runtime/supervisor.py`、`backend/tests/test_task_supervisor.py`、`backend/tests/test_health_routes.py`、`backend/tests/s1_offline_guard.py`（opt-in 离线 pytest 插件）、`docs/task-handoff-2026-10-23-runtime-supervision.md`（Codex 写的实现交接）、本文件
- **不得提交/修改**：`frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx`（用户 WIP）；`docs/architecture-review-2026-10-23-claude.md`；`script/dexe_bybit_bitget_chain.py`（归属未知）；`output/s1_offline_guard.py`、`output/s1-tests-c.txt`；所有 `.pytest_*`、`.worktrees/`、DB、备份、日志。

## 3 实现要点（Claude 已逐段审阅 diff）

| 目标 | 位置 | 审阅结论 |
| --- | --- | --- |
| TaskSupervisor（状态、心跳、5→300s 退避重启、Cancelled 正常退出） | `backend/app/runtime/supervisor.py` | 符合规格；`beat()` 通过 ContextVar 找到当前 supervisor，子任务继承上下文 |
| 替换裸 create_task | `main.py::_start_background_task`，各任务改为 lambda 工厂 | 任务名与启动条件未变；critical = market-collector、alert-loop |
| 心跳 | collector、alert-loop、fs-monitor、announcement 两个 loop | 只在已有 try/except 处加 beat，业务逻辑未改 |
| 阶段耗时 + generation/published_at | `collector.py::collect_once` 包装 `_collect_once`；`SnapshotStore.mark_published` | 纯缩进包装，逻辑逐行一致；setter 语义未变 |
| 事件循环延迟 | `EventLoopLag`，作为 `event-loop-lag` 任务启动 | 5 分钟窗口 |
| live / ready / detail | `routes_health.py` | 旧 `/api/health` 未改；detail 需密码；ready 仅在 start_collector 时检查 |
| 关闭流程 | `supervisor.stop()` 用 gather(return_exceptions=True) | 异常任务不再中断后续资源关闭 |

## 4 Claude 亲自验收结果

（见 §4.1，测试结果由本会话实际运行得到，不采信 Codex 自报）

### 4.1 测试

全部带 `-p s1_offline_guard`（阻断真实外部网络），2026-10-24 在本机分支工作区运行：

| 文件 | 结果 |
| --- | --- |
| `test_task_supervisor.py` | 18 passed |
| `test_health_routes.py` | 9 passed |
| `test_collector.py` | 24 passed |
| `test_collector_pipeline.py` | 39 passed |
| `test_alert_loop.py` | 21 passed |
| `test_fs_monitor.py` | 29 passed |
| `test_api.py`（整文件，约 15 分钟） | 105 passed, 5 failed, 1 error |
| `python -c "import app.main"` | 通过 |

`test_api.py` 的 5 failed + 1 error **与基线完全相同**：用 `git archive 045ed8e` 导出未改动代码，同样命令运行结果也是 105 passed / 5 failed / 1 error，失败项逐一一致。因此**不是 S1 引入的回归**。失败项：

- ERROR `test_collector_startup_uses_multi_exchange_index_component_provider`：离线插件在 teardown 发现测试尝试真实外网；另记录到 `FakeExchangeClient` 无 `is_closed`，`TradeAvailabilityService.aclose` 关闭时报错（被 `_close_state_resources` 吞掉）。
- FAILED（409 ≠ 200）`test_astro_preview_endpoint_uses_saved_card_defaults`、`..._uses_env_defaults_before_settings_are_saved`、`..._uses_saved_defaults_even_when_they_match_model_defaults`、`test_astro_manual_card_create_endpoint_uses_saved_defaults_with_real_service`、`test_live_pilot_auto_create_service_uses_pilot_settings_for_real_service`：这些测试依赖真实交易所网络做 fresh 市场复核，离线时复核失败返回 409。

结论：既有测试套件中有一批测试**依赖真实网络**，这本身是 S9（CI 测试门禁）要先解决的问题；不在 S1 范围内修。未运行全量 backend 测试（95 个文件），只跑了 S1 涉及范围。

### 4.2 审阅发现（未阻断，留给下一会话决定）

1. **lifespan 重入**：`TaskSupervisor` 在 `create_app` 时创建一次，`stop()` 后 `_stopping=True` 且 `tasks` 不清空。同一个 app 对象的 lifespan 第二次进入会因“supervisor stopped”报 ValueError。生产不会发生（单次启动），但测试复用同一 app 两次 `TestClient` 会失败。建议：在 lifespan 开始时新建 supervisor（`app.state.task_supervisor = TaskSupervisor()`），或 `stop()` 末尾重置状态。约 3 行，可 Claude 直接修或交 Codex。
   - **已修（2026-10-24，Claude）**：`main.py` lifespan 中改为每次新建 `TaskSupervisor()` 并写回 `app.state.task_supervisor`（3 行）。重跑 `test_task_supervisor.py` + `test_health_routes.py`：27 passed；同一 app 连续两次 `TestClient` 启停均返回 200。
2. **错误信息全部脱敏**：`safe_error` 只保留异常类型，不保留消息。安全但诊断信息偏少；如需更多信息，可以只对已知安全的异常类型（如 TimeoutError、httpx.HTTPStatusError 的状态码）补充字段。可选。
3. `routes_health.py` 中 `from app.services.pair_spread_query import realtime_cache_stats` 前多了一个空行，属风格小问题。
4. 所有任务都 `restart=True`；循环本身多有 try/except，重启只在意外退出时发生。行为变化需在交接里向用户说明（以前任务死了就静默停止，现在会自动重启）。

## 5 下一会话要做的事（按顺序）

1. 读本文件与 `docs/task-handoff-2026-10-23-runtime-supervision.md`。确认分支 `codex/runtime-supervision`、`git status` 与 §2 一致。
2. 决定 §4.2 第 1 项是否先修（建议修，改动极小）；如修，修后重跑 `test_task_supervisor.py` 与 `test_health_routes.py`。
3. 本地提交：只 `git add` §2 列出的本任务文件，提交信息 `feat: add runtime task supervision and layered health checks`（附 Co-Authored-By 行）。
4. **向用户确认**后才做：push、隔离备份双库（使用 `output/*-isolated-deploy-*.sh` 的隔离方式，不要用 `deploy/linux-update.sh` 里的容器内备份）、`git pull --ff-only` 部署、`/api/health` 与新路由线上验证。
5. 部署后 24 小时采集基线（各交易所数据年龄 p95、collector 各阶段 p95、事件循环延迟 p95、缓存键数峰值），写入交接文档，作为 S2（采集链解耦）和 S3（价差查询/公开访问复用）的输入。

## 6 运行测试的方法（Windows，Git Bash）

```bash
cd /c/Users/wubin/Desktop/code/codex/taoli1/backend
PYTHONPATH=tests python -m pytest -q -p no:cacheprovider --basetemp=<scratchpad 下新目录> -p s1_offline_guard tests/<file>.py
```

注意：`-p s1_offline_guard` 需要 `PYTHONPATH=tests`；每次用新的 basetemp，不要清理仓库里已有的 `.pytest_*` 目录（部分目录有受限 ACL）。`test_api.py` 整文件较慢，可按文件逐个跑。

## 7 线上部署结果（2026-10-25，Claude 执行，用户批准）

- 推送：`codex/runtime-supervision` 新分支；部署分支 `codex/frontend-localization-polish` 由 `045ed8e` 快进到 `85bc07776a5712711e5f210c1f5d4614de2e78aa`。
- CI：GitHub Actions run 40107518072 success；服务器 `docker manifest inspect` 前后端 `sha-85bc077…` 镜像均通过。
- 部署脚本：`output/s1-isolated-deploy-20261025.sh`（复用 pair-ratio 隔离包装，LF，服务器 `bash -n` 通过，SHA256 两端一致 `8345b75e…`）；退出码 0；日志在服务器 `output/s1-deployment-20261025.log`。
- 备份（integrity_check=ok，容器与宿主机 SHA256 一致）：
  - 主库 `backups/radar-before-85bc07776a57-20261024T153529Z.db`，SHA256 `21cb755473f38d890bd3c522f9316374136842163479c3fb3e5045598604b074`
  - 路线库 `backups/squeeze-route-before-85bc07776a57-20261024T153529Z.db`，SHA256 `ac022b9ca4067a32ba269c8a1b96be21d6361756ea52ecd2a3a24b793bcf2c76`
  - 保留策略各删除 1 份最旧的非保留备份（`79eef7614370`），各保留 10 份。
- 线上验证（启动约 90 秒后）：Git HEAD = 85bc077，tracked clean；backend/frontend healthy，RestartCount=0；`/api/health` status=ok、9407 市场、exchange_errors={}（旧契约不变）；`/api/health/live` 200；`/api/health/ready` 200 `{"status":"ready","reasons":[]}`；`/api/health/detail` 未带密码 401（鉴权生效，Claude 未用密码访问 detail）。
- 观察：后端日志中 `trade_availability.py:1577 check_once` 的 `RuntimeError: 聚合行情中未找到指定原始市场` 约 4 次/分钟，该文件不在 S1 改动内，属既有行为，另行排查。部署前 backend 曾“Up 2 minutes”，即既有的自行重启/内存问题，S1 不宣称修复。
- 待办：24 小时后采集基线（需带 dashboard 密码访问 `/api/health/detail`，由用户执行或另行授权），写入本节，作为 S2/S3 输入。
