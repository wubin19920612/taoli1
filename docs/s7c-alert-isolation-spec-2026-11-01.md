# S7-C 告警命中处理隔离（2026-11-01）

## 背景（生产 d3cb493，只读调查已确认）
- `backend/app/main.py` `_run_alert_loop` 每轮对 `ordered_matches` **串行**处理：交易/充提诊断 → 最新信号校验 → 卡片参数校验 → 订单簿校验 → Astro 建卡 → 飞书 → 落库。前一个命中慢，后面的命中全部顺延，下一轮也跟着推迟。
- 现有超时只是局部的：诊断每个请求 15 秒；订单簿校验买卖两腿顺序 await，没有总期限；Astro/飞书 HTTP 各 10 秒；自动建卡各 variant 之间还要 sleep 3 秒。累计可达数十秒。
- `now` 在整轮开始时取一次，后面命中的“最新信号校验”用的是旧时间，过期信号可能被当成新鲜的。
- 某个命中抛出未捕获异常会中断整轮，后面的命中这一轮都不处理。
- 生产现状：近 48 小时 1734 条告警，27 个不同的告警键，自动建卡和飞书都开着；24 小时内告警链路没有超时或失败记录。现在没有每个命中的耗时日志，所以无法量化慢的程度。本任务是预防性的隔离，同时补上计时。
- S7-A（告警投递状态持久化）、S7-B（建卡锁 + attempt 账本）已上线。Astro 建卡阶段已有按 pair 的锁和 pending→unknown 账本。

## 要做的事（只改 `main.py`、`core/config.py` 和测试）
1. **拆出单命中函数**：把 `for match in ordered_matches:` 的循环体原样移到 `async def _process_alert_match(...)`。消息拼接、状态判断、静默规则、落库顺序**一字不改**。只做下面 2–6 点列出的改动。
2. **有界并发**：用 `asyncio.Semaphore(settings.alert_match_concurrency)` 并发处理本轮命中，按 `ordered_matches` 的顺序创建任务（Semaphore 先到先得，live-pilot 仍然先开始）。本轮所有命中处理完后，才进入 `wait_for(stop_event.wait(), interval)`，轮与轮之间不重叠。
   - 新配置 `ALERT_MATCH_CONCURRENCY`，默认 3，限制 1–8，非法值回退默认值。等于 1 时行为与现在完全相同（串行、顺序一致）。
3. **单命中异常隔离**：每个命中任务里的未捕获异常记 `logger.exception("alert match processing failed: %s", key)`，不影响其他命中。本轮只要有一个命中异常，`beat("alert-loop", ok=False)`；全部正常则 `ok=True`。`CancelledError` 必须向上传播，不能吞掉（停止时要能正常退出）。
4. **共享数据库写入串行化**：`delivery_store.mark_pending`、`delivery_store.record`、`event_repo.create` 共用一个 aiosqlite 连接。用一把 `asyncio.Lock` 包住每一次写调用，保证并发的命中之间不会交叉提交。锁只包住写调用本身，**不能**把飞书发送或 Astro 调用放进锁里。
5. **阶段期限**（只限 Astro 之前的只读阶段；超时后的处理与该阶段现有的失败分支一致）：
   - 交易/充提诊断：整体 `asyncio.wait_for(..., settings.alert_diagnostic_timeout_seconds)`，默认 20 秒。超时时与现有异常分支一样，生成“诊断：未知（超时）”报告，`opening_restricted=True`，告警照常继续。
   - 订单簿校验：整体 `asyncio.wait_for(..., settings.alert_orderbook_timeout_seconds)`，默认 15 秒。超时时设 `card_condition_failure=True`，`card_summary="未创建（订单簿校验超时）"`，消息追加 `Astro: 订单簿校验超时`，**不再继续建卡**。
   - **Astro 建卡和飞书发送不加 wait_for**：取消进行中的 add 会把 S7-B 的 attempt 留在 unknown 状态，而且这两步已经有 HTTP 超时。在代码注释里写明原因。
   - 两个新配置都限制在 1–120 秒，非法值回退默认值。
6. **每个命中刷新时间**：`_latest_signal_validation_failure(...)` 改用调用时刻的 `datetime.now(UTC)`，不再用整轮开始时的 `now`。`evaluate`、过滤等整轮共用的计算继续用整轮的 `now`。
7. **计时日志**：每个命中处理完写一行 INFO JSON（与项目里现有计时日志的风格一致），例如 `{"alert_timing":1,"key":...,"symbol":...,"status":...,"diag_ms":...,"orderbook_ms":...,"astro_ms":...,"feishu_ms":...,"total_ms":...,"timeouts":[...]}`。没有执行的阶段写 null。不能写入密码、webhook、API key。

## 不改
- `alert_engine.py`、`astro_alerts.py`、`orderbook_validator.py`、`opportunity_trade_availability.py`、飞书模块、消息模板、静默/冷却/重试规则、S7-A/S7-B 的逻辑。
- live-pilot 的选择逻辑和顺序；compact 消息格式。

## 验收（测试，主要加到 `backend/tests/test_alert_loop.py`）
- **隔离**：两个命中，第一个的诊断（或订单簿校验）挂起（用 Event 卡住），并发数=3 时第二个命中在挂起解除之前已经完成落库。
- **诊断超时**：诊断挂起超过期限（测试里把期限设成很小）→ 告警照常发送，消息含“诊断：未知”，`opening_restricted` 生效（rating header 显示校验失败）。
- **订单簿超时**：订单簿校验挂起 → 不调用 `handle_alert`/`handle_live_pilot`，`card_summary` 是“未创建（订单簿校验超时）”。
- **异常隔离**：一个命中抛异常 → 其他命中照常落库，本轮 beat 为 ok=False；`CancelledError` 能让循环退出。
- **串行等价**：并发数=1 时，命中处理顺序和事件内容与改动前一致（现有测试全部通过即可证明；至少新增一条断言处理顺序 = live-pilot 在前）。
- **live-pilot 优先**：并发数=1 和 3 时，live-pilot 命中都先开始处理。
- **时间刷新**：整轮 `now` 之后时间前进到超过信号新鲜度期限时，`_latest_signal_validation_failure` 拿到的是新时间（monkeypatch 时钟）。
- **写入串行**：用假的存储记录写调用的进入和退出，断言并发的命中之间写调用不重叠。
- **配置**：三个新配置的默认值、上下限和非法值回退各有断言。
- **计时日志**：用 caplog 断言每个命中一行 `alert_timing`，字段齐全，不含敏感值。
- 已有测试一个都不能放宽或删除。

## 范围
- 可改：`backend/app/main.py`、`backend/app/core/config.py`、`backend/tests/test_alert_loop.py`（必要时新增 `backend/tests/test_alert_loop_isolation.py`）、本任务书（末尾追加交接记录）。
- 不可改：其他所有文件。
- 提交信息：`feat: isolate alert match processing with bounded concurrency and stage timeouts`
- 基线：分支 `codex/s7c-alert-isolation`，基于 `d3cb493`（生产版本，部署分支 tip）。

## 测试环境（必须）
CI 用 `backend/requirements.lock` 锁定依赖（FastAPI 0.162）。在本工作树的 `backend` 目录运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -c "import app.main as m; print(m.__file__)"
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

第一条命令输出的路径必须在 `.worktrees\s7c` 下。日志里保留：执行的命令、这条路径的输出、汇总行。不要运行 WSL；不要访问生产、交易所、Astro、飞书；**不要 git push**，只在本地分支提交。

## 输出（最后给我）
改动文件清单；`_process_alert_match` 和原循环体相比有哪些行为变化（应该只有第 2–7 点）；新增测试清单；全量测试命令和汇总行。

## Claude 验收（Codex 不用做）
- 只读 Codex 审查：拆函数前后的 diff 是否只多了第 2–7 点；有没有把 Astro 或飞书放进锁或 wait_for；`CancelledError` 是否被吞。
- 上线后 +1h：`alert_timing` 每个命中一行，看 total_ms 的分布和 timeouts；alert-loop 心跳正常；告警事件数量和 muted/sent 比例与上线前同时段相近；restarts/oom 为 0。

## S7-C 本地交接记录（2026-11-01）

### 范围、基线与交付
- 工作树：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\s7c`。
- 分支：`codex/s7c-alert-isolation`；起始提交：`d3cb493`。
- 仅在本地提交，提交信息：`feat: isolate alert match processing with bounded concurrency and stage timeouts`。提交号可用 `git log -1 --format=%H` 查询本记录所在提交。
- 改动文件仅为 `backend/app/main.py`、`backend/app/core/config.py`、新增的 `backend/tests/test_alert_loop_isolation.py` 和本任务书。隔离测试独立成文件，复用 `test_alert_loop.py` 的假对象；已有测试未修改、放宽或删除。
- 起始未跟踪文件仅有本任务书，原文保留，仅在末尾追加本记录；本任务书随本次实现提交。没有其他需要保留的已知工作区改动或未跟踪产物。
- 未执行 git push、WSL、生产备份或部署；未访问生产、交易所、Astro、飞书或其他外网。测试使用仓库现有的离线拦截 fixture，network/benchmark 标记维持默认排除。

### 实现入口与行为变化
- `main.py::_process_alert_match` 提取原循环体，原消息内容、compact 格式、静默/冷却/重试规则、S7-A/S7-B 分支和投递写入顺序保留。提取后原 `continue` 对应函数返回，已捕获失败仍沿用原处理。
- `_run_alert_loop` 按原 `ordered_matches` 顺序创建任务，以 Semaphore 限流；并发数为 1 时串行，live-pilot 仍先开始。本轮 gather 完成后才等待下一轮，不跨轮重叠。
- 每个命中的未捕获异常独立记录并返回失败；本轮 heartbeat 汇总所有命中结果。`CancelledError` 向上传播，并取消、收拢同轮子任务，避免退出后遗留任务。
- `mark_pending`、`record`、`event_repo.create` 每次调用各自持有同一把写锁。每命中的 pending → 飞书 → record → event 顺序保留；Astro、飞书均不持有写锁。
- 诊断整体期限默认 20 秒，超时报告为“诊断：未知（超时）”，保留 `opening_restricted=True` 与继续告警的失败路径。订单簿整体期限默认 15 秒，超时设置卡片条件失败、指定卡片摘要与消息，禁止继续调用普通或 live-pilot 建卡。
- Astro 与飞书没有新增 `wait_for`，代码注释说明 HTTP 超时与取消 add 导致 S7-B attempt unknown 的原因。
- 最新信号校验使用调用时的 `datetime.now(UTC)`；evaluate、过滤等仍使用整轮时间。
- 每个进入处理的命中在 finally 输出一条 INFO JSON `alert_timing`，含 key、symbol、status、四阶段毫秒数、total_ms、timeouts；未调用阶段为 null，字段中没有消息正文、配置或凭据。异常、取消路径也记录计时。
- 新配置：`ALERT_MATCH_CONCURRENCY=3`（1–8 整数）、`ALERT_DIAGNOSTIC_TIMEOUT_SECONDS=20`、`ALERT_ORDERBOOK_TIMEOUT_SECONDS=15`（均 1–120 秒）。非法输入、越界和非有限值回退各自默认值。
- 自查：去除缩进后的提取差异仅涉及任务书第 2–7 点及函数结构；AST 比较确认 main.py 原有函数中只有 `_run_alert_loop` 发生变化，新增函数只有 `_process_alert_match`。`git diff --check` 通过。

### 新增测试清单（12 个测试函数，参数化后 21 项）
1. `test_slow_match_does_not_block_other_match`：诊断/订单簿分别被 Event 卡住时，第二个命中先落库，并等待本轮剩余任务结束。
2. `test_diagnostic_timeout_delivers_restricted_rating`：诊断超时仍发送、报告与评级降级生效、超时计时字段正确。
3. `test_orderbook_timeout_skips_all_card_creation`：普通/live-pilot 与详细/compact 四种组合，超时不建卡，指定摘要/消息及静默规则生效。
4. `test_unhandled_match_exception_isolated_and_heartbeat_unhealthy`：异常不阻断其他命中，heartbeat 为 false，异常命中也输出计时。
5. `test_cancel_propagates_and_drains_inflight_matches`：父循环取消和子命中主动取消都向上传播，并收拢其他在途命中。
6. `test_live_pilot_starts_first_and_success_heartbeat`：并发 1/3 都以 live-pilot 开始，串行落库顺序正确，正常 heartbeat 为 true。
7. `test_concurrency_bounded_and_round_does_not_overlap`：6 个命中最多同时执行 3 个，轮间不重叠。
8. `test_latest_validation_uses_time_after_diagnostic`：推进假时钟后校验使用新时间，旧信号过期并停止建卡，evaluate 时间保持不变。
9. `test_all_three_database_writes_share_lock_but_external_calls_do_not`：三类写调用无重叠、每命中写入顺序一致；同时挂起飞书发送不阻塞其他命中进入发送。
10. `test_mutating_stages_have_no_outer_deadline`：Astro/飞书超过只读阶段期限仍可正常完成。
11. `test_one_safe_timing_json_per_match_with_null_unexecuted_stages`：每个命中一条完整计时 JSON，空阶段为 null，敏感值哨兵不出现在日志。
12. `test_alert_isolation_settings_defaults_boundaries_and_invalid_fallback`：三个配置的默认值、上下限、非法/越界/非有限值回退；并发拒绝小数，超时允许范围内小数。

### TDD 与全量测试证据
在实现前先新增测试，使用指定 venv 运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider tests/test_alert_loop_isolation.py
```

红灯汇总：`17 failed, 4 passed in 21.81s`。

实现后定向回归命令：

```powershell
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider tests/test_alert_loop.py tests/test_alert_loop_isolation.py --tb=short
```

绿灯汇总：`42 passed in 8.41s`。

全量运行目录为本工作树的 `backend`，实际执行任务书原命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -c "import app.main as m; print(m.__file__)"
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

导入路径输出：

```text
C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\s7c\backend\app\main.py
```

全量汇总：

```text
2548 passed, 6 deselected, 13 warnings in 120.31s (0:02:00)
```

13 条警告来自 Starlette TestClient/httpx 弃用提示和索引相关测试的 `datetime.utcnow()` 弃用提示；本任务未改依赖或相关模块。

### 验收边界与下一步
- 当前完成范围为本地实现、离线测试和本地提交。没有线上验证数据，也不据此宣称已部署或生产验收通过。
- 后续按上文 Claude 验收清单审查提交；如需上线，另开发布/运维任务授权推送、备份、部署及 +1h 观察。其他功能模块继续使用独立任务，本文件可直接作为交接入口。
