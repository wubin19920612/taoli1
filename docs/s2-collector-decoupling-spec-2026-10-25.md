# S2 采集链解耦：设计与任务书（Claude，2026-10-25）

设计来源：`docs/architecture-review-2026-10-23-claude.md` §4 P0-2、§7 S2。前置 S1 已上线（`85bc077`）。
用户决定：不等 24 小时基线，直接做 S2；效果用“注入慢依赖”测试 + 上线前后 `/api/health/detail` 对比验收。

## 1 现状（已核实）

`MarketCollector._collect_once`（`backend/app/services/collector.py:185-259`）一轮依次执行：

1. `await risk_settings_loader()` → `settings_repo.get_risk_settings()`，走**共享 `db` 连接**。
2. poll → prepare → build → publish（`store.mark_published`）。
3. publish 之后仍在同一轮内 `await history_recorder.record()`：insert → prune → 可能 `VACUUM`（共享 `db` 连接，主库约 780MB）。
4. 再 `await _process_index_components()`：
   - `index_component_monitor.watched_symbols()` → `auto_watch.sync()`，每 60 秒一次 `astro.list_pairs()`（外部网络）；
   - `observe_markets()` → 写指数价、`_send_due_trends()` → `_deliver()` 飞书；
   - `MultiIndexComponentProvider.fetch_components()`：5 个 provider 串行，每个最多 20 个标的串行 HTTP；
   - `process_snapshots()` → 发生变化时 `_deliver()` 飞书。

后果：3、4 里的任何慢操作都会推迟下一轮行情采集。而且由于第 1 步和 VACUUM 共用一个连接，**VACUUM 运行期间，下一轮采集一开始就会被卡住**，只把历史移到后台并不够。

## 2 目标与非目标

目标：
- 采集循环只负责“读设置 → 拉行情 → 计算 → 发布快照”。历史记录和指数成分改为订阅快照代次的独立后台任务。
- 后台任务**只处理最新一代**（容量为 1 的邮箱，旧代直接覆盖）。慢消费者只会跳过中间代，不会积压，也不会反压采集。
- 读风险设置加超时，超时就沿用上一次成功读到的设置，避免被共享连接上的长操作卡住。
- 保留旧的内联路径，用配置开关切换，保留一个版本用于回滚。

非目标（不在 S2 做）：
- VACUUM 换独立连接或低峰调度（属 S6）。S2 只保证它不再阻塞采集，API 层面的阻塞仍然存在，交接里要写明。
- 采集整轮 deadline、adapter 超时调整、provider 并发化。
- 修改任何告警、Astro、FS 的业务逻辑，以及 `trade_availability.py`。

## 3 设计

### 3.1 新模块 `backend/app/services/snapshot_consumers.py`

```python
@dataclass(frozen=True)
class PublishedSnapshot:
    generation: int
    published_at: datetime
    markets: tuple[MarketSnapshot, ...]        # filtered_markets
    opportunities: tuple[Opportunity, ...]     # filtered_opportunities

class LatestSnapshotSlot:
    """容量为 1 的邮箱：put 覆盖未消费的旧代；不阻塞生产者。"""
    def put(self, snapshot: PublishedSnapshot) -> None: ...      # 同步、O(1)；覆盖时 dropped += 1
    async def get_newer(self, after_generation: int, stop_event: asyncio.Event) -> PublishedSnapshot | None: ...
        # 有比 after_generation 新的就立即返回；否则等待 put 或 stop；stop 时返回 None
    def stats(self) -> dict[str, int | None]: ...                # latest_generation, dropped

class SnapshotConsumer:
    """订阅一个 slot，逐代调用 handler；异常记录并继续，不退出循环。"""
    def __init__(self, name: str, slot: LatestSnapshotSlot,
                 handler: Callable[[PublishedSnapshot], Awaitable[None]],
                 now_fn: Callable[[], float] = time.monotonic, window: int = 120) -> None: ...
    async def run(self, stop_event: asyncio.Event) -> None: ...
        # 每处理一代：成功 beat(name, ok=True)，异常 logger.exception + beat(name, ok=False)
    def stats(self) -> dict[str, object]: ...
        # processed_generation, processed, failed, last_duration_seconds, duration_p95_seconds,
        # lag_generations（slot.latest_generation - processed_generation）
```

- 每个消费者各有一个 slot（collector 发布时对每个 slot 各 put 一次），两个消费者互不影响。
- 用 `tuple(...)` 浅拷贝列表本身。不修改 `Opportunity`/`MarketSnapshot` 对象，消费者只读。

### 3.2 `MarketCollector` 改动（`collector.py`）

- 构造参数新增 `snapshot_slots: Sequence[LatestSnapshotSlot] | None = None` 和 `risk_settings_timeout_seconds: float = 2.0`。
- **`snapshot_slots is None`（默认）= 旧内联行为，逐行保持不变**，现有测试不改。
- `snapshot_slots` 非空时，publish 之后：用 `store.generation` / `store.published_at` 构造 `PublishedSnapshot`，对每个 slot 调 `put`，**不再 await history / index**。`stage_timings` 里不再出现 history/index 两个阶段（由消费者自己的 stats 体现）。
- 抽出两个公开方法给消费者用，内联路径也改为调用它们，保证两条路径逻辑一致：
  - `async def record_history(self, snapshot: PublishedSnapshot) -> None`：`history_recorder.record(list(snapshot.opportunities), now=snapshot.published_at)`。
  - `async def process_index_components(self, markets: Sequence[MarketSnapshot]) -> None`：原 `_process_index_components` 的内容（含 try/except 吞异常、打日志）。
  - 注意：内联路径原来调用 `record(filtered_opportunities)` 时没传 `now`。内联路径保持不传，避免改变旧行为；后台路径传 `published_at`。
- 风险设置超时（两种模式都生效）：
  - `asyncio.wait_for(self.risk_settings_loader(), timeout=self.risk_settings_timeout_seconds)`；
  - `TimeoutError` 或其他异常时：已有上次成功值就沿用，`logger.warning` 并计数 `risk_settings_fallbacks`；**首轮还没有成功值就把异常抛出**（与现状一致，由 `run_collector_loop` 记录）；
  - 计数通过 `collector.runtime_stats()` 暴露：`{"risk_settings_fallbacks": int}`。

### 3.3 配置（`backend/app/core/config.py`）

- `collector_post_publish_mode: Literal["background", "inline"] = "background"`
- `collector_risk_settings_timeout_seconds: float = 2.0`（>0 校验）
- 环境变量 `COLLECTOR_POST_PUBLISH_MODE`、`COLLECTOR_RISK_SETTINGS_TIMEOUT_SECONDS`，在 `config.py` 读取 `POLL_INTERVAL_SECONDS` 的同一位置（约 161 行）按相同写法读取；非法 mode 值报错，不要静默回退。

### 3.4 `main.py` 装配

在现有 `MarketCollector(...)` 创建处（约 `main.py:1008`）：
- mode == "background"：创建 `history_slot`、`index_slot`，传给 collector 的 `snapshot_slots`。创建两个 `SnapshotConsumer`：
  - `history-recorder`：handler = `collector.record_history`
  - `index-components`：handler = `lambda s: collector.process_index_components(s.markets)`
  - 用 `_start_background_task(supervisor, lambda: consumer.run(stop_event), name=...)` 启动。非 critical，restart 策略与其他任务一致。
  - 存到 `app.state.snapshot_consumers: dict[str, SnapshotConsumer]`。
- mode == "inline"：不建 slot、不启动消费者，`app.state.snapshot_consumers = {}`。
- 只在 `start_collector` 分支内做这些事，其他启动条件不变。

### 3.5 `/api/health/detail`（`routes_health.py`）

新增字段（只读，出错时省略该字段，不影响其他字段）：
- `"snapshot_consumers": {name: consumer.stats() 加上 slot.stats()}`
- `"collector_runtime": collector.runtime_stats()`（collector 不存在时省略）

旧的 `/api/health`、`live`、`ready` 不改。

### 3.6 已知取舍（写进实现交接）

- 历史采样：`record()` 自己按 `sample_seconds`（默认 120 秒）节流。只取最新代不会降低采样频率，采样时间改用 `published_at`，更准确。
- 指数价采样（`observe_markets`）：消费者慢的时候，采样点会变稀疏（跳过中间代）。这是有意为之，用来换取行情不受影响。
- 指数成分变化不会漏记：provider 是按标的节流的，每次处理都用最新行情。
- VACUUM 仍在共享连接上运行，会阻塞该连接上的 API 读写，留给 S6。

## 4 测试要求（先写测试）

新增 `backend/tests/test_snapshot_consumers.py`：
1. slot：连续 put 3 代后 get_newer(0) 只返回第 3 代，dropped == 2；get_newer(3) 会等待，put 第 4 代后返回；stop_event 置位时返回 None。
2. consumer：handler 抛异常 → failed+1、循环继续处理下一代；stats 中 lag_generations 正确。
3. **慢依赖注入**：history handler `await asyncio.sleep(10)`（用可控 Event 代替真实睡眠），collector 连续 `collect_once()` 3 次都能立即返回，`store.generation` 增加到 3；history 消费者最终只处理最新一代。
4. 慢指数注入同理：`fetch_components` 卡住时 collector 照常推进。
5. 风险设置超时：loader 第一次正常返回，第二次卡住 → 第二轮在约 timeout 秒内完成并用旧设置，`risk_settings_fallbacks == 1`；首轮就超时 → 抛 `TimeoutError`。
6. 后台模式下 `record_history` 收到的 `now == published_at`；内联模式下 `history_recorder.record` 的调用参数与改动前一致。

扩展 `backend/tests/test_health_routes.py`：detail 带密码时包含 `snapshot_consumers` 和 `collector_runtime`。

回归（必须全过，与基线一致）：`test_collector.py`、`test_collector_pipeline.py`、`test_history.py`、`test_history_maintenance.py`、`test_index_components.py`、`test_index_watch_filter_equivalence.py`、`test_task_supervisor.py`、`test_health_routes.py`、`test_alert_loop.py`。

## 5 文件范围

- 新建：`backend/app/services/snapshot_consumers.py`、`backend/tests/test_snapshot_consumers.py`、`docs/task-handoff-2026-10-25-collector-decoupling.md`（实现交接）
- 修改：`backend/app/services/collector.py`、`backend/app/core/config.py`、`backend/app/main.py`、`backend/app/api/routes_health.py`、`backend/tests/test_health_routes.py`
- 禁止修改：`history.py`、`index_components.py`、`repositories.py`、`trade_availability.py`、所有前端文件、`docs/architecture-review-*.md`、本任务书

## 6 上线验收（Claude 执行）

- `/api/health/ready` 持续 200，`generation` 正常推进。
- 只读查询线上库：上线后 `opportunity_history` 相邻采样间隔中位数约 120 秒。
- 用户用密码访问 `/api/health/detail` 对比上线前后：collector 各阶段 p95（history/index 不再计入）、两个消费者的 `lag_generations` 与 `duration_p95_seconds`、`risk_settings_fallbacks`。
- 回滚：`.env` 设 `COLLECTOR_POST_PUBLISH_MODE=inline` 重启即可回到旧路径；或 revert 提交。
