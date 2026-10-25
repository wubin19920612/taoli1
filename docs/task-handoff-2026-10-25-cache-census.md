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
