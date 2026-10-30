# M2-2 cache census handoff (2026-10-25)

## Scope and baseline

Branch `codex/m2-2-cache-census`; initial HEAD `35a0669` (spec-only commit), parent production baseline `d986ab0`. Initially clean. Only the six files in spec section 4 may change. Local commit only: no push, deployment, WSL or Docker. Production has not been inspected or changed.

## Pre-implementation inventory

This table was written before tests and implementation and corrected during the final ownership audit. Source paths below are relative to `backend/app`; line numbers refer to the baseline. All planned targets below are now registered, except the explicitly excluded request-local resolver and dependency-only services. Static configuration constants and HTTP/DB/lock internals are not independent business caches. Runtime supervision infrastructure is outside this business-cache inventory. Async task objects are opaque; selected coroutine referents are inspected explicitly without materializing frame locals.

| Source:line | Containers and types | Bounds / cleanup | Registration plan |
|---|---|---|---|
| services/snapshot_store.py:13-16 | _markets, _all_markets, _opportunities (list), _exchange_errors (dict) | Replaced on publish; all_markets can be None | All four |
| services/snapshot_consumers.py:30,78,86 | slot._latest (PublishedSnapshot), _durations (deque), run local snapshot | Latest-only slot; 120 samples; local can retain previous generation across await | Both consumers, each slot/local/durations |
| services/collector.py:161-168 | _poll_states, _exchange_snapshots, _exchange_errors (dict), snapshot_slots | Per-adapter replacement; successful fetch removes error; fixed slots | Three dicts; slots covered by consumers |
| services/history.py:36-42 | record locals selected, rows (list) | Per-call, keep_top_n; no persistent instance buffer | Suspended record rows; no invented instance cache |
| exchanges/lighter.py:209-210 | _markets, _cached (dict) | Refresh by market type; no general eviction | Both collector and availability adapter groups |
| exchanges/bitget.py:82 | _rtoken_spot_symbols (set) | Replaced on refresh | Both adapter groups |
| exchanges/hyperliquid.py:26-27 | market_errors, _collateral_names (dict) | Errors reset each fetch; names updated | Both adapter groups |
| services/account_positions.py:230 | _cache (dict) | No eviction; TTL reads do not remove entries | Yes |
| services/account_connections.py:157,471 | _provider_cache (dict) | Invalidated connections pop and close provider | Yes |
| services/fs_monitor.py:97,101 | report (model), _states (dict) | Replaced during scan | Both |
| services/fs_public.py:74 | cache (dict) | No general eviction | Every public provider |
| services/fs_bybit.py:59 | _metadata (dict) | Refresh per category | Yes |
| services/gate_crossex_hyperliquid.py:25 | _cache (dict) | TTL refresh; no general eviction | Yes |
| services/announcement_localization.py:31-32,110 | _cache, _missing_until (dict) | Cache replaced; misses removed only on translation hit, no expiry sweep | Every announcement provider |
| services/announcements.py:1770-1771,1937-1938 | last_source_articles, last_source_errors (dict) | Replaced each fetch | Every announcement provider; articles added after following local-to-instance assignment |
| services/announcement_research.py:268-274 | _cache (OrderedDict) | max_cache_entries + LRU | Bound researcher if mounted; default main has none: missing |
| services/announcements.py:2391-2403,2676 | fetch_batches locals tasks/rows; loop local announcements | Tasks retain all completed results until generator exits; final announcements survives sleep | Loop final batch; temporary peak measured separately |
| services/instrument_market_cap.py:29-31,135 | _search_cache, _market_cache (OrderedDict), _locks (list) | 256 entries per cache; fixed lock stripes | All three |
| services/instrument_statistics.py:67-71,127,146 | _cache, _pending, _coverage | 128-entry LRU; pending finally pop; coverage follows eviction | All three + query_service caches |
| services/trade_availability.py:417,473 | _cache (OrderedDict) | M2-1 bounded LRU | Yes |
| services/pair_spread_query.py:519-525 | Four _REALTIME_*_CACHE dicts | Point-window pruning; distinguish keys from points | Four separate targets |
| services/pair_spread_query.py:1010-1013 | Four per-query dicts | Lifetime follows service; statistics/premium instances persist | Both persistent ownership paths |
| services/market_identity.py:18 | registry.bindings (mappingproxy) | Atomic replacement | Yes |
| services/market_identity_catalog.py:397 | status (dict) | Fixed status structure | Yes |
| services/market_validation_review.py:34-50 | books/reviews/failures/review_attempted (OrderedDict); tasks/review_inflight/_last_incident (dict); discovered/collision_symbols/invalidated_ids (set) | bounded_put default1000; completed tasks removed; some sets lack global eviction | All ten + fx |
| services/alert_engine.py:32-36 | _hits, _last_sent, _retry_after, _retry_attempts, _observations | Inactive cleanup for some; last_sent lacks global eviction | All five |
| services/opportunity_radar.py:18-20 | _hits, _alerted_active, _last_sent | Inactive cleanup; last_sent lacks general eviction | All three |
| services/phone_price_alerts.py:22 | _last_sent (dict) | No general eviction | Yes |
| services/gate_twap.py:454-455 | jobs, tasks (dict) | No general eviction | Both |
| services/index_components.py:289,496 | monitor._latest_prices, auto_watch._last_attempt_by_symbol | Prices replaced; attempts lack general eviction | index_component_monitor + auto_watch; corrected owner is monitor, not provider |
| services/multi_exchange_premium.py:210-215 | _next_allowed, _locks, _request_at, _exchange_retry_at, status | next_allowed pruned by active set; others follow request scope | All five + service caches |
| services/negative_basis_monitor.py:1215-1219 | _last_run_at, _last_sent_at, _last_sent_level, _auto_candidates | Candidates replaced; time maps lack general eviction | All four |
| services/oil_news.py:732 | _translation_retry_after (dict) | No global bound established in this audit | Yes |
| services/squeeze_arbitrage/runner.py:58 | _verified_symbols (set) | Replaced on reload | Optional target; missing when disabled |
| services/squeeze_arbitrage/route_runner.py:56,66; route_tracker.py:26; route_provider.py:45 | tracker (dataclass, including baseline_samples); _queue._queue (deque); provider._route | Queue max_pending_scans; tracker follows route lifecycle; route replaced | Optional targets; discovered by Queue/default_factory follow-up search |
| services/service_control.py:44 | _tasks (set) | Completion callback removes tasks | Read memory only; no control command |
| services/symbol_aliases.py:75-82 | _by_key, _implicit_dex_aliases (dict) | Resolver lifetime | No persistent main-owned resolver; not a separate target |
| core/config.py:159; services/market_sessions.py:106 | get_settings, us_stock_session_close (lru_cache) | Decorator bounds 128 | currsize only; no sizing, no invocation |
| main.py; services/astro_preadd.py:244; services/hyperliquid_trade_status.py:165; services/second_level_sampler.py:1200; services/pair_spread_funding_recorder.py:243 | Remaining constructed services and repositories | Configuration, dependencies, clients, DB; no persistent business buffer found | Dependency containers above; SQLite data is not an in-memory cache |

## Implementation and tests

实现文件严格限于任务书 §4 的六个文件；没有业务服务、supervisor、前端、部署或 CI 改动。

- `runtime/cache_census.py`：惰性迭代 DFS；支持映射（含 mappingproxy）、序列、集合、deque、Pydantic extra/private/fields_set、dataclass、继承/私有 slots。循环引用去重。每个目标独立计算 own，同轮共享 visited 计算 unique；按 own 降序，unique 归属按登记顺序确定。只输出静态目标标签、计数、体积、布尔状态。
- 对象上限 2,000,000 为**整轮访问次数**，重复引用也消耗预算，防止重复对象组成的大列表绕过限制；objects 字段是每目标实际计入的不同对象数。整轮共用 3 秒 deadline；后续未测目标标 truncated，不再调用 getter。迭代器避免复制大容器。返回值与日志均含 own_bytes/unique_bytes 及 KB。
- getter 异常和对象测量异常只记 census_errors 计数；部分测量仍保留已统计数，避免共享对象出现遗漏归属。缺失服务为 missing。LRU 只读 currsize，体积为 null。
- `core/config.py`：两个 MEMORY_CENSUS 配置，默认 false / 300 秒，间隔必须有限且为正。
- `main.py`：只传入现有 app.state；仅后台任务、memory_probe、memory_census 三个条件均满足才构造目标。
- `runtime/memory_probe.py`：独立普查时钟，首轮在一个普查间隔后执行；普查失败不影响原采样。同步执行，没有线程分发；关闭时不导入 census、不构造目标、不执行普查，原 sample 输出保持一致。
- 快照槽、消费者当前协程持有的旧代、历史写库中的非空 rows、公告富化循环最后一批均有目标。使用已知协程链上的 CPython GC 直接引用与领域类型筛选，**不读取 frame.f_locals**，避免 Python 3.12 物化 locals 字典自身保留旧快照。没有匹配的非空历史/公告列表时记 missing；不同解释器若不暴露对应引用也记 missing。
- 登记采用静态路径和数字索引，不使用容器键、交易对或 provider 的业务标识生成标签；getter 每次重新解引用，不闭包捕获旧缓存。可选 provider 组不存在时有 missing 占位。当前配置中 provider 列表固定；若未来支持运行中增减 provider，需要重新构造登记表。

盘点采用 AST 全目录扫描赋值、构造函数、装饰器、slots/default_factory，配合文本检索与实际调用链阅读；本机没有 rg，使用 PowerShell Select-String 与 Python AST。最初显式字面量扫描漏掉的别名赋值和 Queue 已补入上表。实际实例覆盖测试验证两组 adapters、所有公告 provider、必需目标以及替换缓存后的 getter；默认未挂载 research，因此该项是 missing，而非假造研究常驻缓存。

执行顺序与证据：

1. 先写盘点表，再新增测试。RED：模块未实现，pytest 收集退出码 2，记录 `backend/.pytest_m2_2_evidence/red.txt`。
2. 指定四文件回归：**91 passed in 23.63s**（随后仅增加基础标量遍历快路，最终代码由全量测试覆盖）。命令：`python -m pytest -q tests/test_cache_census.py tests/test_memory_probe.py tests/test_config.py tests/test_task_supervisor.py --basetemp=.pytest_m2_2 --tb=short`，cwd 为 backend。
3. 最终后端全量：**1812 passed, 12 warnings in 91.92s**，退出码 0；警告类型为 DeprecationWarning。命令：`python -m pytest -q --basetemp=.pytest_m2_2_full --tb=short`，cwd 为 backend。包含最终 28 项 census 测试及全部指定回归。

所有 pytest 均指定 worktree 内 basetemp，沿用 S9 默认离线网络护栏。没有运行 WSL、Docker 或生产连接。测试摘要只打印计数，不打印失败详情或缓存数据；本地证据不加入提交。

## Local measurements and section 3.1 investigation

### 普查耗时

本机 Windows / CPython 3.12.10，实际服务构造器（后台任务关闭）加 12,000 个 Pydantic 模型，每模型 8 个观察值，并由两个快照列表共享同一模型图。最终全量中的 235 个登记目标，空服务普查 **0.745 ms**；三轮 census_ms：**262.776、265.758、266.341 ms**，unique 合计 **7,615,241 B（约 7.26 MiB）**，没有错误或截断。这只是本地固定规模，不是生产数据；数字证据位于 `backend/.pytest_m2_2_full/test_actual_service_registry_a0/census_metrics.json`。实际生产 p95 < 1 秒尚未验收。

估算限制：浅大小来自 sys.getsizeof；不等同 RSS，不覆盖原生库/allocator arena/SQLite 文件页、网络缓冲全部占用。mappingproxy 可遍历内容但其底层映射结构开销未完整反映；未知 C 扩展对象为浅估算。shared 集合自身也消耗临时内存，不纳入缓存归属。时间限制是协作式检查，已知 getter 都是本地读操作；单次扩展类型 sizeof/迭代操作无法强制抢占，最终排序、日志序列化和必要释放还有小额开销。census_ms 包含遍历、排序和共享记账集合释放，不包含后续日志序列化。

### §3.1 调用链与存活期（只读）

`main.py` 创建并共享一个公告 provider 组和 monitor，两个公告循环使用同一组 provider。富化循环 `run_announcement_enrichment_loop`（基线 2658 起）先等待 60 秒，加载配置、判断 repository 是否已有记录，排除该循环不处理的来源，再调用 `fetch_batches`；每批经过 `monitor.process`，最后心跳并等待默认 600 秒。

- `MultiAnnouncementProvider.fetch_batches`（2379 起）并发为启用来源建立 task，完成后执行 localize 并产出模型列表。局部 tasks 列表保留已经完成任务及其结果，直到整批生成器退出后才释放，因此慢来源和数据库处理会延长其他批次的存活时间。
- 各 provider 拉取分类列表、必要的文章详情、网页嵌入 JSON 或备用页面，再生成公告模型。列表响应字节、解码 JSON、网页文本、详情文本映射和模型可能同时存在。Binance 串行详情时仍持有整份列表 payload；Bitget 分类 gather 的 results 同时保留多份响应，之后再进行详情；其他来源存在受限并发详情。Gate 路径对同一页面先解析列表，再带详情重新解析。具体生产响应大小无法从代码静态确定。
- `monitor.process` 的排序列表、去重集合、watermark、candidate 副本、created 列表仅在该调用中存活；数据库持久化不等于 Python 常驻缓存。默认 main 没有挂载 asset_researcher，因此不能把此循环的生产增量归因于 AnnouncementResearchService._cache。
- provider 实例长期保留 localization._cache、_missing_until、来源摘要表；localization._cache 刷新替换，_missing_until 仅命中翻译时删除，对长期未命中的标识没有全表过期清理。当前富化循环局部 announcements 在最后一批处理完后仍跨 600 秒等待保留；停止或下一批赋值后可释放。fetch_one 闭包和任务结果的保留限于生成器活动期，不是模块全局 JSON 缓存。

### 离线单轮测量

`tests/test_cache_census.py::test_enrichment_round_allocation_experiment` 用真实 Binance provider 的 JSON 解码、列表/详情解析、localize、真实 MultiAnnouncementProvider 和 AnnouncementMonitor、原富化循环跑连续两轮。HTTP 为 MockTransport，repository 模拟已有持久化记录且不缓存模型，避免把 fake 数据库计成服务常驻。三份同类型 provider、每份 40 条；这是受控压力样本，不是生产所有来源的回放。tracemalloc 只在本机测试函数中启动/停止，fixture 字节构造与代表性 decoded JSON 的 deep_size 测量都在启动 tracing 前完成，不把普查临时记账成本混入业务单轮峰值。

最终全量测量：单份 feed **332,286 B**，详情 **4,384 B**，解码 feed 的 deep_size **345,196 B**。首轮峰值增量 **1,181,921 B**，轮后当前净增 **138,326 B**；第二轮相对第一轮等待点的峰值增量 **960,930 B**，新增常驻 **7,226 B**；两轮结束等待时总 tracing current **145,552 B**，退出实际循环后 **64,995 B**。数字包含 Python/HTTP/正则等首次使用成本，GC 后仍存活不表示永不释放。第二轮增量与首轮分开报告，避免把预热成本当稳定泄漏。证据位于 `backend/.pytest_m2_2_full/test_enrichment_round_allocati0/enrichment_metrics.json`，不同进程预热状态下数字会变化。

结论：本机可重现明显大于轮后保留量的临时峰值，也可看到最后一批跨等待保留。**这不证明生产 +8MB/次或最大 +73MB 全由该循环造成**；RSS 与 tracing 字节不同，其他任务、分配器、响应尺寸均需后续线上 census 与心跳时间联合验证。

### 最小修改建议（仅建议，未改业务）

1. M2-3 优先把富化单轮放入独立 async helper，或在进入间隔等待前明确释放最后一批引用；同时对 SnapshotConsumer 的旧代局部做同类处理。预期收益为实际测得的末批/旧代可达体积，不能预先承诺固定 RSS 降幅。
2. 对 fetch_batches 逐个完成任务释放结果持有，保留错误隔离、处理顺序语义、取消与 gather 收尾；用慢来源/慢数据库测试验证峰值下降。分类 gather 的大 payload 可在解析后逐个释放，但仍需保持原行为。
3. 对 localization._missing_until 做过期删除与有界容量，保持原公告精确身份匹配；先用 census 确认其规模，不能只因“无上限”就判断其为主因。
4. 生产先采一小时 census，重点看截断、missing、耗时与心跳相关性；不能将 RSS 减去 unique 当成严格可加减的“泄漏量”。是否立项和上线由后续 Claude 任务决定。

### samples=0 调查

现有 supervisor.enable_memory_probe 会把实例 beat 替换为 _beat_with_memory；模块级 beat 通过 ContextVar 到同一实例，再调用 TaskRSSDeltas.record。已调用 beat 的任务没有发现绕过采样的独立路径。静态检查发现多个工作循环没有显式 beat（例如目录刷新、negative-basis、funding-recorder、trade-availability、Astro preadd、oil-news、premium、可选 squeeze 等），supervisor 的生命周期管理不自动给业务迭代打点。没有两次有效 RSS 读数同样会 samples=0；本机 Windows 的 RSS 字段为 null。未连接生产，不能将线上约 14 个零样本逐个确认为某一原因。本任务未修改 supervisor 或业务心跳。

## Delivery / next task

Local only. No push, production backup or deployment. A separate task will decide M2-3 and production acceptance. Fake local measurements cannot establish production RSS attribution.

保留本任务生成的未跟踪测试目录 `backend/.pytest_m2_2/`、`backend/.pytest_m2_2_full/`、`backend/.pytest_m2_2_evidence/`；均不暂存。没有覆盖其他任务产物。依照用户六文件范围限制，交接集中在本文件，不修改 docs/next-task.md。

## 验收打回修复

本轮基于 `codex/m2-2-cache-census` 的 `f81ea09`，按用户 2026-10-25 的打回要求修复普查自身的临时内存风险。本节替代前文关于 own/unique 双口径、默认 200 万访问上限、排序和配置的旧说明；前文测试与测量保留为上一次提交的历史证据。仅修改 `cache_census.py`、`memory_probe.py`、`config.py`、`test_cache_census.py`、本交接，以及 `main.py` 一行新增传参。本轮仅新建本地提交，不 amend、不 push、不连接或部署生产。

### 实现

- 删除逐目标 local 集合和独立 own 测量；整轮只创建一个 shared visited 集合。命中已访问 id 时直接跳过其子图，共享对象仅归属第一个目标。体积只输出 `unique_kb`，删除 `own_bytes`、`own_kb`、`unique_bytes`，按 `unique_kb` 降序。LRU 仍只输出条目数、`unique_kb=null`。
- 登记表稳定调整为全部 `snapshot_store.*`、全部 `market_collector.*`（含 adapters），再到其他目标；相同优先级保留原顺序，使快照优先归属原始持有者。展示排序发生在遍历后，不改变归属。
- `run_census` 和 `MemoryProbe` 默认上限均为 **600,000**；新增 `Settings.memory_census_max_objects` / `MEMORY_CENSUS_MAX_OBJECTS`，默认 600000，必须为正整数，拒绝零、负数、小数及布尔值。环境变量用 int 解析后同样校验；`main.py` 仅新增 `census_max_objects=app_settings.memory_census_max_objects` 一行，探针再传给 `run_census`。
- 保留整轮访问次数预算（重复引用也消耗）及 3 秒时间预算，因此 visited 集合最多有 max_objects 个 id。每目标 `objects` 为该目标新增的不同对象数；整轮 `visited_objects` 为清理前共享集合大小，正常情况下等于各目标 objects 之和。
- 每轮先读 cgroup v2 的 `/sys/fs/cgroup/memory.max` 与 `memory.current`。余量严格小于 `max_objects * 80 + 64 * 1024**2` 字节时，在创建 visited、调用任何 getter 前返回 `census_skipped=low_headroom`、`headroom_mb`、`visited_objects=0`。默认阈值为 **115,108,864 B（109.7764 MiB）**，等于阈值时允许执行。读取失败、无效值或 `max` 退化为不检查。
- 探针在普查前后各读取一次 `rss_anon_mb`，新增 `census_rss_delta_mb=after-before`；任一读数缺失时为 null。低余量跳过也沿用 `_log` 输出 `memory_probe=1`、跳过原因、余量、visited 数及 RSS 字段。关闭普查时不执行这些读取。
- 共享集合及临时根引用在返回前释放；RSS delta 是释放后的净变化，**不代表轮内瞬时峰值**。余量门槛是预估保护，cgroup 不可读时按要求不拦截；本地测量不能代替 Linux 生产验收。

### 60 万对象集合开销实测

本机 **Windows 11 / CPython 3.12.10 / 64 位**。先创建并保持 600,000 个不同 `object()` 存活，再启动本地 tracemalloc，仅逐个向一个集合加入 `id(obj)`；对象列表及对象本体不计入记账成本。集合浅大小用 `sys.getsizeof(visited)`，整数大小为逐个 `sys.getsizeof(identity)` 求和。

| 项目 | 实测字节 |
|---|---:|
| 集合自身及哈希表 | 16,777,432 |
| 600,000 个 id 整数（每个 32 B） | 19,200,000 |
| 集合 + id 合计 | **35,977,432（34.3108 MiB）** |
| tracemalloc 当前 / 峰值增量 | 35,977,432 / 35,977,480 |
| 保护公式中的记账预估（600000 × 80） | 48,000,000（45.7764 MiB） |

该测量只覆盖 visited 记账集合及其增长过程，不含遍历栈、日志序列化、业务对象或其他进程/原生分配，不是生产 RSS 测量。tracemalloc 仅用于本机测量，没有加入生产代码。

### 测试与后续交接

新增/调整测试覆盖：只有一个集合、共享对象及子图只归属第一个目标、unique 排序、登记优先级、visited 计数、默认值与环境变量及 main 接线、正整数校验、两种对象上限下余量阈值的 -1/0/+1 字节边界、跳过前不构造集合或调用 getter、读取失败和无限额时继续执行，以及正常/跳过两条路径的实际 JSON 日志与 RSS 正/负/null 差值。

最终指定四文件回归：**131 passed in 22.93s**，退出码 0。cwd 为 `backend`：

```powershell
python -m pytest -q tests/test_cache_census.py tests/test_memory_probe.py tests/test_config.py tests/test_task_supervisor.py --basetemp=.pytest_m2_2b_final --tb=short
```

随后后端全量：**1852 passed, 12 warnings in 91.25s**，退出码 0；12 项均为既有 `datetime.utcnow()` 弃用警告。cwd 同为 `backend`：

```powershell
python -m pytest -q --basetemp=.pytest_m2_2b_full --tb=short
```

全部 pytest 都显式使用 worktree 内 basetemp 并保持默认离线护栏。RED 在仓库根目录运行时，除新行为预期失败外，两个既有 subprocess 导入测试因 cwd 无法导入 app 而失败；最终指定回归已在 backend 目录运行并全部通过，没有修改这些既有测试。

保留原有三个未跟踪目录及本轮新增的 `backend/.pytest_m2_2b_red/`、`backend/.pytest_m2_2b/`、`backend/.pytest_m2_2b_final/`、`backend/.pytest_m2_2b_full/`，不暂存测试产物。没有生产备份、部署或线上验证。后续请在独立验收任务验证 Linux 实际 RSS/余量、跳过日志、截断比例及 census_ms p95，再决定是否开启生产普查或进入 M2-3。

## 生产缺陷修复：预算与抽样

2026-10-26，分支 `codex/m2-2-cache-census`，修复基线 `8721fef445e7699e6383d166cf01fb042e2be260`。据用户提供的生产观察，该版本已经上线：60 万次访问预算在共享集合仅 **73,317** 个对象时用完，只有 `snapshot_store._markets`（9410 条）得到约 17 MB 的截断数据，后续目标均为零；每轮约 680 ms，释放后 `census_rss_delta_mb=0`。本轮未连接生产复核。这一节替代上一节关于“重复引用也消耗访问次数预算”的设计说明；旧测量保留作历史证据。

### 范围与实现

仅修改 `backend/app/runtime/cache_census.py`、`backend/tests/test_cache_census.py`、本交接。没有修改 memory_probe/config/main 或业务代码。用户要求新建本地提交，不 amend、不 push；没有运行 WSL、Docker、备份或部署。

- `_Budget.max_objects` 约束整轮共享集合的实际大小，只有新 id 才占用对象预算。重复引用只增加 `visits`，不扣减对象预算；集合已满时，仍能跳过已见 id，遇到下一个新 id 才截断，集合大小不会超过上限。默认仍为 **600,000**，原 cgroup 余量保护公式保持不变。
- dict/list/tuple/set/deque 直接元素数 **>2000** 时，深度测量 **500** 个元素，位置为 `floor(i * len / 500)`，`i=0..499`。dict 每个键值对算一个抽样元素，键和值均测量；同样适用于原来支持的 mappingproxy/frozenset。序列直接按索引取样，其他类型通过迭代器和 islice 前进，不复制完整容器。
- 容器自身 `sys.getsizeof` 精确计入；只有抽样元素的深度贡献乘以 `len / 500`。嵌套大容器继续抽样，倍率逐层相乘。实际测到的对象仍进入同一个共享集合，重复 id 及其子图不再计入。行内 `sampled=true` 表示遇到抽样容器；`sample_size` 是该目标内所有抽样容器**实际抽取的直接元素数之和**，正常根容器为 500，多个嵌套容器可大于 500，超时可小于计划抽样数。没有抽样的行保持原字段集合。
- 每个目标在调用 getter 前确定截止时间 `min(全局截止时间, 当前时间 + 0.5秒)`；单目标超时保留该行部分结果并置 `truncated=true`，后续目标使用自己的 0.5 秒窗口。全局仍为 **3 秒**，耗尽后后续目标直接记截断。getsizeof、单次迭代、getter 仍是协作式检查，不能强制抢占。
- 新增整轮 `visits`：成功从遍历迭代器取出的对象次数，包含重复引用和触及上限时未能入集合的对象，不含 islice 跳过的未测元素、迭代结束检查或被外推的假想对象。`visited_objects` 和每行 `objects` 仍为实际不重复对象数，前者等于各行后者之和；低余量跳过时二者均为零。`unique_kb` 仍是按共享集合归属的估算体积，排序和隐私约束保持不变。RSS 字段及其含义完全未改。

### 测试与误差验收

新增覆盖：100 万次共享引用在默认抽样及关闭抽样的完整遍历路径上均不提前耗尽对象预算，后续目标仍被测量；不重复对象达到上限时截断；五类容器 2000/2001/10003 元素边界；均匀步长、容器浅大小、嵌套倍率、样本加入共享集合；1 万个 60 字段 Pydantic 模型的外推误差；fake clock 下单目标超时、getter 耗时计入该目标、后续目标继续、全局超时仍终止后续目标。完整百万引用用 fake clock 单独验证对象预算，生产时重复遍历仍受时间限制。

本机 **Windows / CPython 3.12.10 / 64 位 / Pydantic 2.12.5**：1 万个同构 60 字段模型，抽样估算 **53,744,096 B**，关闭抽样后完整遍历 **53,688,122 B**，相对误差 **0.1043%**，满足 <15%。证据：`backend/.pytest_m2_2c/test_sampled_pydantic_estimate0/sampling_accuracy.json`。

指定四文件回归 **153 passed in 32.21s**，退出码 0，cwd 为 backend：

```powershell
python -m pytest -q tests/test_cache_census.py tests/test_memory_probe.py tests/test_config.py tests/test_task_supervisor.py --basetemp=.pytest_m2_2c --tb=short
```

随后后端全量 **1874 passed, 12 warnings in 121.20s**，退出码 0；12 条均为既有 `datetime.utcnow()` 弃用警告。cwd 同为 backend：

```powershell
python -m pytest -q --basetemp=.pytest_m2_2c_full --tb=short
```

所有 pytest 使用 worktree 内不同的 basetemp，保留默认离线网络护栏，不复用或清理旧任务的测试目录。新增行为在修复前指定四文件 RED 回归为 16 failed / 134 passed，之后补充嵌套倍率、getter 超时与生产规模基准，最终两轮验收均通过。

### 接近生产规模的本机性能验收

`test_production_scale_registered_targets_benchmark` 通过真实 `build_census_targets` 登记并选择四个压力目标：三个 SnapshotStore 列表分别 **9410 / 9500 / 4600** 条，共 **23,510** 个模型引用，底层 **12,000** 个不同的 60 字段 Pydantic 模型，列表间互相共享；`market_collector._exchange_snapshots` 持有嵌套 JSON dict，10 个分组各含 30,000 行，每行拥有不同 dict、str、float、int，共约 **120 万** 个不重复 JSON 对象，另有少量容器及共享字段名。构造数据不计入 census_ms。

三轮默认 60 万对象 / 3 秒预算下：

| 项目 | 第一轮 | 第二轮 | 第三轮 |
|---|---:|---:|---:|
| census_ms | 197.301 | 183.165 | 191.832 |
| visits | 303,510 | 303,510 | 303,510 |
| visited_objects | 111,503 | 111,503 | 111,503 |
| 非零体积目标 | 4/4 | 4/4 | 4/4 |
| truncated / census_errors | 0 / 0 | 0 / 0 | 0 / 0 |

三轮均满足 **census_ms <1500**。每轮只使用一个共享集合；清空前测得集合及哈希表 **4,194,536 B**，111,503 个 id 整数 **3,568,096 B**，合计 **7,762,632 B（约 7.403 MiB）**。使用测试专用 TrackingSet，在 clear 前计算浅大小及 id 大小之和，包含子类额外的少量固定开销；计数耗时也包含在上述 census_ms 内。这是共享集合当前占用，不是全进程 RSS 或集合扩容瞬时峰值，也不包含 fixture 本体。没有把 tracemalloc 引入运行时。证据：`backend/.pytest_m2_2c/test_production_scale_register0/production_scale_metrics.json`。

### 限制与下一步

抽样外推是估算：异构元素、按步长聚集的大对象或大量共享引用可能产生较大误差；跨目标只对**实际测到**的 id 去重，不能用 500 个样本推断所有未抽到对象的共享关系。因此，多个共享容器的外推体积之和不等于完整对象图的精确去重体积，不能当作 RSS 或严格可加减的泄漏量。`truncated=false` 表示抽样遍历完成，不表示所有元素均被深度测量。

保留原七个未跟踪 pytest 目录及本轮 `.pytest_m2_2c_red/`、`.pytest_m2_2c/`、`.pytest_m2_2c_full/`（均在 backend 内），均不暂存；按用户文件限制不修改 `docs/next-task.md`。本次只交付本地修复；生产版本仍按用户提供的信息记为 `8721fef`，未推送、未做生产备份或部署、未重新取得线上验收数据。后续在独立生产验收任务推送/部署并检查所有登记目标、sampled/sample_size、visits/visited_objects、截断及耗时分布，再继续 M2-3。

## 打回修复：一致抽样

2026-10-26，分支 `codex/m2-2-cache-census`，本轮基线 `e2662af7d33a54af40475874e6077395ac25b8cb`。验收发现：`markets` 的 9400 个 MarketSnapshot 同时出现在长度为 9530 的 `all_markets` 中，按各自长度均匀步长抽样会选中不同对象，后一个目标因而重复外推前一个目标已覆盖的模型。本节替代上一节的固定 500 元素、均匀步长抽样规则及其不同长度容器的去重限制。

### 实现与范围

仅修改 `backend/app/runtime/cache_census.py`、`backend/tests/test_cache_census.py` 和本交接文档；不修改 `docs/next-task.md`、配置、探针接线或业务行为。用户要求新建本地提交，不 amend、不 push；没有运行 WSL、Docker、连接生产、备份或部署。

- 直接元素数 **>2000** 的容器遍历全部直接元素，用 `(((id(elem) >> 4) * 2654435761) & 0xFFFFFFFF) % 16 == 0` 决定入选。同一个对象在不同长度或位置的容器中使用同一判定，不再因长度改变样本集合。dict/mappingproxy 根据 **value 的身份** 决定整个 key/value 对是否入选；两者均参与深度测量。list/tuple/set/frozenset/deque 使用元素本身。
- 只保留入选元素，不复制整个容器；完成扫描后，以 `元素数 / 入选数` 外推后代体积，容器自身浅大小仍精确计入。完整扫描但零入选时，回退测量首个元素（dict 为首个键值对），按元素数外推。嵌套大容器采用相同算法，倍率继续逐层相乘。
- 哈希扫描每个元素前检查既有的目标/全局截止时间；即使没有任何对象入选也会及时截断。超时保留已计入的部分结果并置 `truncated=true`，不为零入选执行额外回退扫描，后续目标仍可使用自己的时间窗口。
- `sample_size` 记录目标内各抽样容器**实际入选的直接元素数之和**（零入选正常回退记 1，扫描超时则记录截至中断时的入选数）；不再固定为 500。`visits` 仍只统计深度遍历实际取出的对象，包含重复引用，不包含仅做哈希判断的元素。共享集合、60 万对象上限、0.5 秒目标窗口、3 秒全局预算、余量保护、RSS 及隐私字段保持既有规则。

### 本机回归与误差

新增/更新覆盖：9400+130 个真实 MarketSnapshot 的共享前缀场景，要求 `all_markets.unique_kb <= markets.unique_kb * 5%`；同一列表由不同长度的大容器重复引用时，第二个目标只增加自身浅大小；七种容器的 2000/2001/10003 边界；入选数与外推倍率；嵌套倍率；list/dict 零入选回退；根级及嵌套 list/dict 在无入选元素时的扫描超时；100 万重复引用与对象预算；1 万同构模型的 <15% 误差要求。

本机 **Windows / CPython 3.12.10 / 64 位 / Pydantic 2.12.5**，指定两文件回归 **143 passed in 23.57s**，退出码 0，cwd 为 `backend`：

```powershell
python -m pytest -q tests/test_cache_census.py tests/test_memory_probe.py --basetemp=.pytest_m2_2d --tb=short
```

该 basetemp 对应仓库根目录的 `backend/.pytest_m2_2d`。每次复用前，将本轮上一阶段产物复制到 `backend/.pytest_m2_2d_evidence/`，保留 RED、指定回归和全量的日志/证据，未清理任何旧任务目录。实现前的指定两文件 RED 为 **23 failed / 118 passed**；之后补充了两个嵌套扫描超时用例。

随后后端全量 **1888 passed, 12 warnings in 104.97s**，退出码 0；12 条均为既有 `datetime.utcnow()` 弃用警告，cwd 同为 `backend`：

```powershell
python -m pytest -q --basetemp=.pytest_m2_2d --tb=short
```

两轮最终 pytest 均使用指定 basetemp 和默认离线网络护栏。额外对两个 Python 文件运行 Ruff，发现 1 个 `SIM102` 和 3 个 `I001`；通过对基线 `e2662af` 的原文件运行相同检查，确认四项全部既有，本轮未新增，也未扩大范围处理。

指定回归的共享前缀实测：`markets` 入选 **552**，unique **24,893.345 KiB**；`all_markets` 入选 **560**，unique **432.681 KiB（约 0.423 MiB）**，仅为前者的 **1.738%**，两行均未截断。1 万个 60 字段同构模型入选 **587**，外推 **53,735,363 B**，全量遍历 **53,688,122 B**，相对误差 **0.08799%**。证据分别位于 `.pytest_m2_2d_evidence/targeted/test_market_snapshot_shared_pr0/shared_markets_metrics.json` 和 `.pytest_m2_2d_evidence/targeted/test_sampled_pydantic_estimate0/sampling_accuracy.json`（均相对 backend）。

### 生产规模本机基准

沿用真实目标登记和既有压力 fixture：三个快照列表 **9410 / 9500 / 4600** 条，底层 **12,000** 个 60 字段模型；嵌套 JSON **300,000 行 / 约 120 万个不同对象**。数据构造不计入 census_ms。指定回归中连续三轮结果：

| 项目 | 第一轮 | 第二轮 | 第三轮 |
|---|---:|---:|---:|
| census_ms | 535.793 | 480.806 | 494.673 |
| visits | 650,275 | 650,275 | 650,275 |
| visited_objects | 341,161 | 341,161 | 341,161 |
| 非零体积目标 | 4/4 | 4/4 | 4/4 |
| truncated / census_errors | 0 / 0 | 0 / 0 | 0 / 0 |

三轮均满足 **census_ms <1500**，所有压力目标非零。每轮共享集合及 id 整数合计 **27,694,600 B（约 26.412 MiB）**，测量口径与上一节相同；不是全进程 RSS 或瞬时峰值。证据：`backend/.pytest_m2_2d_evidence/targeted/test_production_scale_register0/production_scale_metrics.json`。

### 验证边界与下一步

哈希抽样仍是估算，实际入选比例依赖对象地址分布；异构数据、重复引用、共享后代以及零入选时的首元素回退仍可能影响外推，不能将结果当作精确对象图大小或 RSS。本轮修复针对不同长度容器的样本错位，未调整抽样阈值、预算或业务缓存。

开始时已有十个未跟踪 pytest 目录全部保留，本轮只新增 `backend/.pytest_m2_2d/` 和 `backend/.pytest_m2_2d_evidence/`，均不暂存。线上状态本轮未核实；没有生产备份、部署或线上验收。下个独立任务继续生产验收或 M2-3，依本节规则检查样本数、共享前缀增量、截断和耗时。

## CI 失败修复：可配置时间预算

2026-10-26，分支 `codex/m2-2-cache-census`，本轮基线 `8da136a5146e4c4b29b9956f603fc759552676b4`。用户报告 GitHub Ubuntu runner 的唯一失败为生产规模基准中 JSON 目标超过既有 0.5 秒单目标窗口，导致 `truncated=true`；生产容器 Python 同一遍历约 1151 ms、本机约 199 ms（约 5.8 倍）。后者为用户提供的 Claude 实测，本轮未连接生产复测。

### 配置、传参和输出

- `core/config.py` 新增 `memory_census_budget_seconds` / `MEMORY_CENSUS_BUDGET_SECONDS`，默认 **3.0**；要求有限且 **0 < budget <= 10**。新增 `memory_census_target_seconds` / `MEMORY_CENSUS_TARGET_SECONDS`，默认 **0.5**；要求有限且 **0 < target <= budget**。非法值在配置构造时抛出 `ValueError`，环境变量同样校验。
- `main.py` 仅增加两个参数，经 `MemoryProbe` 传给 `run_census(budget_seconds=..., target_seconds=...)`。单目标截止时间仍取整轮 deadline 与 `目标开始时间 + target_seconds` 的较小值，getter 耗时仍计入目标窗口。默认预算、普查调度、抽样、对象上限及业务缓存行为保持原规则。
- 每轮普通结果和低内存跳过结果都带数字 `budget_seconds` / `target_seconds`；探针捕获异常时的计数日志也带这两个值，便于对照实际生效配置。未开启普查时不新增普查日志。
- 配置测试覆盖默认值、环境变量解析、正数下边界、10 秒上边界、target 等于 budget、零/负数/NaN/正负无穷/超上限/非法字符串，以及 target 大于自定义 budget。fake clock 验证自定义预算确实改变截断行为；真实 lifespan → MemoryProbe → fake run_census 验证默认和自定义配置传到底层；普通及低内存跳过日志验证两个数值字段。

### 墙钟测试与默认 CI

`backend/pyproject.toml` 只修改 pytest 配置：注册 `benchmark`，默认 `addopts = '-m "not network and not benchmark"'`。生产规模基准和真实服务登记基准均加 `@pytest.mark.benchmark`，保留所有既有断言，包括所有压力目标非零且不截断、`census_ms < 1500`。

逐项检查其余计时断言：`test_deadline_shared_by_all_targets` 的 monotonic 虽已伪造，但 `census_ms` 来自真实 perf_counter，所以其 `<200 ms` 断言对应测试也标记 benchmark。其余 fake clock 调度、目标超时和全局超时测试保留在默认回归；新增的纯 fake clock 用例继续覆盖全局截止和后续 getter 跳过。对象上限为零和已过期 deadline 的即时截断断言也保留，不是性能上限验收。未删除或放宽性能断言。

另发现 `backend/tests/test_memory_probe.py::test_sampling_cost_with_large_snapshot_is_below_five_ms` 使用真实 perf_counter 断言 `<5 ms`。该文件未列入用户允许修改的白名单，已单独请求允许只增加一行 benchmark 标记；本轮尚未获答复，故保持原样，仍在默认回归中运行并通过。这是要求 3a 的一个待确认范围项；不能声称默认 CI 已排除所有真实耗时断言。若获准，给该项加标记并依次重跑定向、全量和 benchmark，更新本节结果。

### 本机验证与证据

所有 pytest 都使用仓库根目录下的 `backend/.pytest_m2_2e` 作为 basetemp；每次复用前把上一阶段产物复制到 `backend/.pytest_m2_2e_evidence/` 对应子目录，保留原有全部未跟踪目录。以下命令从 worktree 根目录运行；PYTHONPATH 让既有子进程导入测试同样可以找到 `app`：

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath backend).Path
python -m pytest -q backend/tests/test_cache_census.py backend/tests/test_memory_probe.py backend/tests/test_config.py --basetemp=backend/.pytest_m2_2e --tb=short
python -m pytest -q backend/tests --basetemp=backend/.pytest_m2_2e --tb=short
python -m pytest -m benchmark -q backend/tests/test_cache_census.py backend/tests/test_memory_probe.py --basetemp=backend/.pytest_m2_2e --tb=short
```

初次定向运行未设置 PYTHONPATH，两个既有子进程导入测试失败；补齐本轮命令环境后重跑，不修改导入测试代码。初次日志及产物保存于 `targeted.txt` / `targeted_initial/`，重跑日志为 `targeted_rerun.txt`。最终验证均退出码 0：

| 顺序 | 验证 | 结果 |
|---|---|---|
| 1 | test_cache_census / test_memory_probe / test_config | **174 passed, 3 deselected in 24.24s** |
| 2 | 后端全量，默认排除 network / benchmark | **1913 passed, 3 deselected, 12 warnings in 121.36s** |
| 3 | 显式 `-m benchmark` | **3 passed, 145 deselected in 9.16s** |

12 条警告均为既有 `datetime.utcnow()` 弃用警告。日志分别为 `targeted_rerun.txt`、`full.txt`、`benchmark.txt`；产物分别归档到证据目录的 `targeted/`、`full/`、`benchmark/`。证据全部位于 `backend/.pytest_m2_2e_evidence/`，不加入提交。

生产规模基准三轮 `census_ms` 为 **578.758 / 593.612 / 582.301 ms**，4 个目标均非零且未截断，visited_objects 每轮 **341,159**、visits **650,423**。返回结果内预算均为默认 **3.0 / 0.5 秒**。详见 `benchmark/test_production_scale_register0/production_scale_metrics.json`；这是本机 Windows 结果，没有更改基准预算来取得通过。

额外运行 Ruff，并逐文件对比基线：config 和 memory_probe 无发现，cache_census 1 项、test_cache_census 3 项、test_config 3 项均为既有，未新增 lint 问题，也未扩大范围修复。

### 交付边界与下一步

本轮按用户要求只新建本地提交，不 amend、不 push；没有运行 WSL、Docker、连接生产、备份或部署。没有修改 `docs/next-task.md`、业务服务或 CI 工作流文件。本轮测试证据目录不暂存。线上版本与新配置的生产效果均未核实，不能把本机 benchmark 通过当作 Ubuntu CI 或生产验收通过；后续在独立生产验收任务选择有效预算，核对日志中的生效值、截断、耗时分布和业务延迟，再继续 M2-3。
