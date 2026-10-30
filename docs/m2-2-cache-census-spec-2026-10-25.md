# M2-2 缓存规模普查：任务书（Claude，2026-10-25，待用户确认）

规划来源：`docs/m2-memory-slimming-plan-2026-10-25.md` §3 M2-2。上线背景：`docs/task-handoff-2026-10-25-session-summary.md` §7。

## 1 现状（已核实）

- M2-1 上线后 1 小时：RSS 底部约 700MB（原来约 740MB），周期性尖峰 740–785MB。tracemalloc 记录的约 74MB 常驻 JSON 还剩大半没有找到持有者。
- 现有探针 `app/runtime/memory_probe.py`（`MemoryProbe.sample`，每 10 秒一次）只统计 `SnapshotStore.sizes()` 和 `pair_spread_query.realtime_cache_stats()`，**不估算体积**，也不覆盖其他服务的缓存。
- 线上 `task_rss_deltas`（每个任务两次心跳之间整机 RSS 的变化，只能看相关性）：`announcement-enrichment-loop` 中位数 **+8MB/次**，最大 +73MB，其他任务的中位数都是 0。**这是尖峰的头号嫌疑**。另外约 14 个任务没有样本（`samples=0`），看不到它们的情况。
- 用 grep 初筛出来的长期容器（不完整，Codex 需要补全）：

| 文件:行 | 容器 | 有没有清理 |
|---|---|---|
| `exchanges/lighter.py:210` | `_cached`（按市场类型保存整张快照列表） | 没有 |
| `services/account_positions.py:230` | `_cache` | 没有 |
| `services/fs_public.py:74` | `self.cache` | 没有（2 处写入） |
| `services/gate_crossex_hyperliquid.py:25` | `_cache` | 没有 |
| `services/announcement_research.py:268` | `_cache`（OrderedDict） | 有 |
| `services/announcement_localization.py:31` | `_cache` | 待查 |
| `services/instrument_market_cap.py:29-30` | `_search_cache`、`_market_cache` | 待查 |
| `services/account_connections.py:157` | `_provider_cache` | 有 |
| `services/trade_availability.py` | `_cache`（M2-1 已加上限） | 有 |
| `services/pair_spread_query.py` | `_REALTIME_*` 4 个模块级缓存 | 探针已统计条数 |

## 2 目标

1. 新增一个默认关闭的“缓存规模普查”：每 5 分钟输出一次已登记的每个长期容器的条目数和**估算深度体积**，按体积排序。不用 tracemalloc。
2. 找出 `announcement-enrichment-loop` 每轮做了什么、产生了哪些临时大对象、有没有被长期持有（**只读调查**，结论写进交接）。
3. **不改任何业务行为**；普查关闭时零开销。

## 3 设计

- 新建 `app/runtime/cache_census.py`：
  - `CensusTarget(name: str, getter: Callable[[], object | None])`。getter 返回要测量的容器；属性不存在时返回 None，报告里记为 `missing`，不抛异常。
  - `deep_size(obj, *, max_objects: int, deadline: float) -> (bytes, objects, truncated)`：用迭代（不用递归）遍历 dict/list/tuple/set/deque、pydantic 模型（`__dict__`、`__pydantic_extra__` 等）、dataclass/`__slots__`；用 `id` 集合去重。**同一轮普查内，各目标共用一个已访问集合**，所以共享对象只算给先测的那个目标；输出里同时给出各目标单独测量的体积（`own_bytes`）和去重后的体积（`unique_bytes`）。超过对象数或时间预算就停止，并把 `truncated` 置为 true。
  - `run_census(targets, *, max_objects=2_000_000, budget_seconds=3.0) -> dict`。
- 输出：沿用 `_log`，格式 `{"memory_probe":1,"cache_census":[{"name","entries","own_kb","unique_kb","objects","truncated"|"missing"}...],"census_ms":..., "rss_anon_mb":...}`。**不得输出任何键、值、URL、币种或异常文本**（与现有探针的约定一致）。
- 在事件循环线程上同步执行，避免容器在遍历时被改动；`census_ms` 记录阻塞了多久。一轮的总时间预算 3 秒，超出就截断。
- 登记目标：在 `cache_census.py` 里写一个 `build_census_targets(app_services) -> list[CensusTarget]`。`main.py` 只负责把已经构造好的服务对象传进去（只加接线，不改服务）。至少覆盖 §1 表格中的全部容器，加上 `SnapshotStore` 的内部快照（包括可能残留的上一代快照引用）、身份目录（market identity catalog）、历史记录器的缓冲区、公告服务常驻的公告和富化数据，以及 `functools.lru_cache`/`cache` 装饰的函数（输出 `cache_info().currsize`，不估算体积）。**先盘点再实现**：用 grep 搜 `= {}`、`OrderedDict(`、`deque(`、`lru_cache`、`cache` 等，补全 `main.py` 里构造的所有长期服务的容器，在交接里列成表（文件:行、类型、有没有上限/清理、是否已登记）。
- 开关：`config.py` 新增 `memory_census_enabled: bool = False`（环境变量 `MEMORY_CENSUS_ENABLED`）和 `memory_census_interval_seconds: float = 300`（`MEMORY_CENSUS_INTERVAL_SECONDS`，必须是有限正数）。只有 `memory_probe_enabled` 也为 true 时才生效。`MemoryProbe` 增加可选参数 `census_targets` 和 `census_interval_seconds`，在 `run` 循环里按间隔调用；普查出错只记 `{"census_errors":1}`，不影响采样。
- 补充任务覆盖（可选，改动 ≤20 行才做）：如果 `samples=0` 是因为这些任务的 `beat` 没有经过 `TaskRSSDeltas.record`，就在交接里说明原因，不要修改 supervisor。

### 3.1 announcement-enrichment-loop 调查（只读）

- 从 `main.py:1113` 和 `services/announcements.py:~2681` 读起：每轮拉取哪些数据、单轮解析出的 JSON 和对象有多大、轮与轮之间留下了什么（实例属性、模块级变量、闭包）。
- 在本机用 fake 数据或录制的样本，测量单轮的峰值增量和轮后的常驻增量（`tracemalloc` 只能在本机测试里用，不能进生产代码路径）。结论和最小修改建议写进交接，由 Claude 决定是否作为 M2-3 立项。

## 4 文件范围与测试

- 新建：`backend/app/runtime/cache_census.py`、`backend/tests/test_cache_census.py`、`docs/task-handoff-2026-10-25-cache-census.md`
- 修改：`backend/app/runtime/memory_probe.py`（只加普查调度）、`backend/app/core/config.py`（只加两个配置）、`backend/app/main.py`（只加接线）
- 禁止修改：所有 `services/`、`exchanges/` 业务代码、supervisor、前端、compose、部署脚本、CI。

测试（先写测试，离线；S9 护栏默认生效）：
1. `deep_size`：嵌套 dict/list/pydantic/dataclass 的体积单调；共享对象在同一轮普查里只计一次；循环引用不会死循环。
2. 对象数或时间预算耗尽时 `truncated=true`，并且能及时返回（用 fake clock）。
3. getter 返回 None 或抛异常时：返回 None 记为 missing，抛异常记为 `census_errors`；其他目标照常输出。
4. 输出中不包含容器里的任何键或值（构造带有敏感字符串的键和值，断言日志里没有）。
5. 关闭时（默认）不构造目标、不调度、不输出；只开探针不开普查时，探针输出与现在完全一致。
6. `build_census_targets` 能覆盖盘点表里的每一项（用 `create_app(start_background_workers=False)` 或已有的 fake 服务验证名称都在）。
7. 回归：`tests/test_memory_probe.py`、`tests/test_config.py`、`tests/test_task_supervisor.py`，以及后端全量 `python -m pytest -q`（本机要加 `--basetemp=<可写目录>`）。

## 5 上线验收（Claude 执行，部署需用户批准）

- 生产 `.env` 增加 `MEMORY_CENSUS_ENABLED=true`（属于已授权的 `MEMORY_*` 键，改之前先备份），探针保持开启，1024m 限额不变。
- 采 1 小时：得到按体积排序的缓存表；`unique_kb` 合计与 `rss_anon_mb` 对照，算出“未归属”的部分；`census_ms` 的 p95 应小于 1 秒，否则调大间隔或者关闭。
- 结合 `announcement-enrichment-loop` 心跳时间，判断尖峰是不是它造成的。
- 产出 M2-3 的治理清单（每项写明预期降幅）。普查完成后把 `MEMORY_CENSUS_ENABLED` 改回关闭。
- 回滚：删除这个环境变量即可（代码默认关闭）；必要时 revert 提交。

## 6 派发

- Codex：gpt-6-astra high（涉及运行时探针和事件循环阻塞预算）。独立 worktree，分支 `codex/m2-2-cache-census`，基于 `codex/deploy-m2-s9`（`d986ab0`，即当前生产版本）。
