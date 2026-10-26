# DISABLED_FEATURES 实现交接（2026-10-26）

## 当前交付状态

本节为本次续做的有效状态；后面的历史盘点保留作为依赖证据，其中“未实现”“停止原因”“待决策”均已由任务书 §7 和本节取代。

- 模块与目标：九个可选功能的启动裁剪、统一接口停用和前端菜单/页面守卫；空配置保留启用行为。
- 工作目录：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\feature-flags`；分支 `codex/feature-flags`。
- 本次基线：`d309c77f3e2a2b580b721babae7b02a68f389a4b`；进入工作区时无修改或未跟踪文件。
- 已完成实现、后端全量、前端定向与全量复核、类型检查和生产构建；新建本地提交的哈希由最终编码报告给出，也可通过本文件的最新 Git 提交定位。
- 只改任务书 §4 白名单；各功能业务文件、FS、`cache_census.py`、PairMonitor 页面及其测试、compose、CI、部署脚本均未改。
- 按用户本次要求只新建本地提交，不 amend、不 push、不运行 WSL/Docker。不连接生产、不备份或部署、不修改线上 `.env`；§6 线上内存验收留给 Claude。

## §7 决策落实与关键入口

1. `backend/app/core/config.py`：新增 `disabled_features: frozenset[str]` 和九键白名单，解析逗号、空格、大小写、重复及空项；未知键配置加载直接报错。设置冻结，只读启动配置，路由不再读取环境变量。
2. 停用 `second_level_sampling` 自动加入 `fat_finger` 并记录 INFO 日志。单独停用 `fat_finger` 只拦回测接口，采样器仍构造并执行原有初始化路径。停采样则不构造 sampler/repo/fetcher，不调用 `initialize()`，因此不会创建内部 `second-level-sampler` 任务。
3. `backend/app/main.py`：裁剪对应 repo、monitor、engine 和 supervisor 注册；squeeze 专属 route/paper 数据库连接、初始化和迁移一起跳过。共享主数据库/schema 保留，无删表、删数据或迁移业务变更。`funding_research` 与原 `FUNDING_RESEARCH_ENABLED` 对后台 loop 取 AND，原开关关闭时仍保留原路由语义。
4. Gate client 作为核心资源始终构造并供 `GateAccountPositionProvider` 使用；停用时 manager 为 None。生命周期只选择一个关闭入口：manager 存在时通过其 `aclose()` 关闭 client，否则直接关闭保留在 state 的 client。测试验证两种情况下 client 均只关闭一次。
5. `backend/app/core/features.py` 为公共 `require_feature` 依赖；七个完整功能 router 加统一依赖。混合 router 只拦 symbol-query、fat-finger-backtest 和 oil-news settings GET/PUT；原路由继续挂载，停用返回 `404 {"detail":"feature_disabled"}`。
6. `backend/app/api/routes_features.py`：公开 `GET /api/features` 返回排序后的实际生效集合，不需要 Dashboard 密码，包含采样联动项。
7. `frontend/src/api/client.ts`、`types.ts` 和 `components/AppShell.tsx`：请求配置、九个 page key 映射、菜单和菜单排序弹窗隐藏；可选页面在结果返回前不挂载。停用直达页面显示“该功能已停用”，包括 URL、浏览器历史和内部导航；请求失败恢复全部启用。保留本地排序数据，重新启用不丢原顺序。
8. readiness 只验收 `/api/health/ready`，没有添加 `/ready` 或放宽就绪条件。全停用测试同时验证缺少快照仍为 503、核心任务运行且快照新鲜时为 200。
9. census 不改：移除的 state 服务对应 target 为 missing；symbol-query 被拦后模块缓存不再由该接口写入，启动空缓存的 `entries=0`。全停用时 health/detail、MemoryProbe 采样和实际 census 均通过。

## 本次验证证据

- RED 在实现前执行：后端 `tests/test_feature_flags.py` 14 项失败，原因是尚无配置字段；前端隔离页面 mock 后 12 失败、1 通过，无未处理异常。首轮前端 RED 的页面连带错误日志也保留。
- 后端定向 GREEN：14 passed（14.55 秒）。逐项/全停用时使用禁止构造的 mock，检查 state、sampler 初始化、全部相关路由/方法、supervisor 注册、独立数据库、核心接口和 Gate 关闭次数；测试 supervisor 不执行联网业务循环。
- 后端全量：**1926 passed, 4 deselected, 12 warnings**（187.51 秒）。4 项是项目默认 `not network and not benchmark` 的排除；warning 为既有 UTC datetime 弃用提示。
- 两次后端命令均在 `backend` 目录执行，明确指定 `--basetemp=C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/feature-flags/backend/.pytest_ff`：先 `python -m pytest -q tests/test_feature_flags.py ...`，再 `python -m pytest -q ...`。
- 前端先 `npm ci`；定向测试 13 passed。验证九项菜单、直接访问守卫、等待期间不挂载、失败/默认放行、全部停用和排序保留。
- 首次 `npm test`：277 passed / 2 failed（28 文件）。两个失败均在未改动的 PairMonitor 测试：密码取消流程超出 5000ms，随后 Hyperliquid 保存重试断言失败。单独复跑该文件 **53 passed**（45.30 秒），没有改文件或延长 timeout。
- `npm test -- --maxWorkers=2 --minWorkers=2`：**28 文件、279 项全部通过**（187.88 秒），没有改超时阈值。该轮也包含默认/失败时直接访问可选页面能正常挂载的补强断言。
- `npm run build`：TypeScript 与 Vite 均通过；新增测试已纳入类型检查。测试日志保留首次全量失败及所有复核结果。
- 已审阅代码差异与白名单、共享引用、关闭路径；`git diff --check` 通过。

## 未解决项、产物和下一步

- 功能依赖决策全部落实，无待用户决策的代码阻塞。前端默认高并发测试出现超时，须保留首次失败记录，不能只报告复跑成功。
- `npm ci` 审计报告 21 项依赖告警（3 low、5 moderate、11 high、2 critical）；未执行 audit fix，也未改依赖/锁文件。前端测试另有 React act 和 Ant Design 弃用 warning。
- 未跟踪产物：`backend/.pytest_ff/`（测试数据库、WAL 等证据，保留且不提交）。忽略产物：前后端 `feature-flags-*.log`、`frontend/node_modules`、`frontend/dist`、类型构建缓存及 pytest 缓存；均不提交。
- 本次没有新的线上版本、线上备份或线上验收结果；内存节省尚未实测。后续由 Claude 按 §6 先部署默认空配置验收，再备份 `.env`、启用全部停用项并采样对比。
- 本模块交付后，下一模块应新建任务；继续部署/内存验收可以直接引用本交接及任务书，不需恢复旧对话。

---

## 历史依赖盘点（本次实现前，保留原文）

## 状态、目标与范围

**状态：依赖盘点完成，发现核心共享依赖，按用户要求停止在 RED 之前；功能未实现。**

- 工作目录：`taoli1/.worktrees/feature-flags`；分支：`codex/feature-flags`。
- 开始时 HEAD：`4dc5ed069d4d116a85e3f681dcfd0d3ff701be1d`（任务书提交）；生产基线：`cfd79d6`。已检查祖先关系，两者之间只有任务书文档变化。
- 开始时 `git status --short` 为空，没有现有修改或未跟踪文件。
- 已完整阅读 `docs/feature-flags-spec-2026-10-26.md`、根目录 `AGENTS.md` 和 Git 交付清单。
- 目标：9 个可选模块启动时裁剪，默认空配置保持原行为；完成验收原需单项/全停用测试、前后端回归。
- 本次实际只新增本交接，属于任务书 §4 白名单；未修改代码、配置、数据库或业务逻辑。
- 用户本次明确要求不运行 WSL/Docker、不 push；优先于通用交付规则和任务书上线步骤。本次未连接生产、未部署。

## 9 项依赖盘点表

以下为当前源码静态盘点，不代表启动或线上验收。路由路径均包含 `/api` 前缀。所有页面均由 `frontend/src/components/AppShell.tsx` 懒加载。

| 功能键 / 前端 key | 服务、repo、数据库 | 后台任务与定时器 | 路由及写入入口 | 引用者、告警、探针及前端影响 |
| --- | --- | --- | --- | --- |
| `squeeze` / `squeeze-arbitrage` | `SqueezeRepository(db)`、`SqueezeMonitor`；`SqueezeRouteRepository(route_db)`、`SqueezeRouteMonitor`；`SqueezePaperRepository(route_db, route_repo._lock)`、`SqueezePaperMonitor`。主 repo 共用主库；route/paper 共用独立 `<主库stem>-squeeze-route<ext>` 连接，`:memory:` 时复用主连接。 | supervisor：`squeeze-monitor`、`squeeze-route-monitor`、`squeeze-paper-monitor`，受原有三个 enabled 开关及启动参数控制。内部还有 `squeeze-hourly-scan`、`squeeze-force-order-stream`、`squeeze-route-writer`；扫描、WebSocket 重连、队列写入及 paper 轮询。 | `routes_squeeze_arbitrage.py`，`/api/squeeze-arbitrage/*`。`main.py:788` 起在 lifespan 初始化 route schema、迁移和 paper schema；停用必须同时跳过专属连接、repo、monitor 和子任务。 | 路由及各 squeeze monitor 相互引用 repo；主库 `initialize_schema` 仍调用 squeeze schema 初始化，不能删除表。census 登记 `squeeze_monitor._verified_symbols`、`squeeze_route_monitor._queue._queue`、`tracker`、`provider._route`；paper 无专属 census target。`SqueezeArbitragePage` 15 秒刷新。未发现核心服务读取这些 state 实例。 |
| `negative_basis` / `negative-basis` | `NegativeBasisMonitor(NegativeBasisMonitorRepository(db), snapshot_store, alert_sender, risk_settings_loader)`；repo 共用主库，monitor 持有查询资源。 | supervisor：`negative-basis-monitor`，仅需 `start_background_workers`；内部轮询等待 1 秒，按 watch item interval 和自动扫描设置执行。 | `routes_negative_basis_monitor.py`，`/api/negative-basis-monitor/*`，包括 query、watchlist、samples、events、auto-scan。 | 使用核心 snapshot/settings 和共享飞书发送器，未发现核心反向依赖此 monitor。census：`_last_run_at`、`_last_sent_at`、`_last_sent_level`、`_auto_candidates`。`NegativeBasisMonitorPage` 8 秒刷新。 |
| `oil_news` / `oil-news` | `OilNewsRepository(db)`、`OilNewsMonitor`、`OilNewsProvider`、`OilNewsTranslator`，repo 共用主库。 | supervisor：`oil-news-loop`、`oil-news-translation-loop`，在 `start_collector` 时注册；新闻轮询按 settings，翻译按 `TRANSLATION_BACKFILL_INTERVAL_SECONDS`。 | `routes_oil_news.py`：`/api/oil-news`、`/refresh`；另有 `routes_settings.py:246` 起的 `/api/settings/oil-news` GET/PUT，不能漏加单接口停用依赖。 | 独立新闻告警开关使用共享 `FeishuNotifier`，不能删除共享 notifier。`OilNewsPage` 同时读取新闻及 settings，60 秒刷新。census：`oil_news_monitor._translation_retry_after`。未发现其他页面调用新闻 settings。 |
| `opportunity_radar` / `opportunity-radar` | `OpportunityRadarAlertEngine` 在 `create_app` 构造；复用 `SettingsRepository`、snapshot store 和飞书 notifier；无专属数据库连接。 | supervisor：`opportunity-radar-alert-loop`，随 `start_collector` 注册；间隔为 `poll_interval_seconds`。 | `routes_opportunity_radar.py`：`/api/opportunity-radar/{settings,test-notification,preview}`。 | `main.py:640` 的循环读取 engine、settings、行情，进行独立雷达告警；不能裁掉共享行情/配置/飞书资源。census：engine 的 `_hits`、`_alerted_active`、`_last_sent`。`OpportunityRadarPage` 60 秒刷新。未发现核心反向依赖 engine。 |
| `symbol_spread` / `symbol-spread` | 请求内构造 `PairSpreadQueryService`，与核心 pair 查询共用服务类；模块级 `_REALTIME_SYMBOL_SPREAD_CACHE`，无专属 repo/连接。 | 无专属 supervisor 或启动任务；前端实时查询定时触发请求。 | 只应保护 `routes_pair_spread.py:597` 的 `/api/pair-spread/symbol-query`。路径：handler → `query_symbol_spreads` → `_query_symbol_spreads_realtime` → `pair_spread_query.py:1611` 的 `setdefault` 和 append。静态搜索未发现其他 app 调用者。 | 不能禁用整个 pair-spread router 或共用服务类。`SymbolSpreadPage` 使用此接口；健康详情调用 `realtime_cache_stats()`；census 直接读模块字典，空字典仍是有效 target，**不会自然变成 missing**。需明确任务书 missing 是针对已移除的 state 服务，不能擅自改禁改的 census/业务模块。 |
| `fat_finger` / `fat-finger` | 无独立启动服务；请求中运行 `run_fat_finger_backtest`。**直接依赖 `SecondLevelSampler`、其 alias resolver 和 `sampler.repo`**，读取主库历史 samples。 | 无专属后台任务；前端用户触发。 | `routes_second_level_sampling.py:139`：`POST /api/second-level-sampling/fat-finger-backtest`，handler 先 `_sampler(request)`，再 resolve 和 list_samples。 | `FatFingerBacktestPage` 直接调用该接口；无独立 census target。仅停用 fat_finger 可在此接口加依赖；单停 second_level_sampling 时，仍启用的回测会失效，见下文。 |
| `gate_twap` / `gate-twap` | `GateTwapClient`、`GateTwapJobManager`；内存 jobs/tasks，无专属 DB。**同一 client 被核心 `GateAccountPositionProvider` 持有**。 | 无启动 supervisor 任务；manager.start 按请求 `asyncio.create_task(self._run(job_id))`，按执行计划 sleep；页面轮询 jobs。manager.aclose 取消任务并关闭共享 client。 | `routes_gate_twap.py`：`/api/gate-twap/{market,preview,jobs,...}`；核心 `/api/account-positions` 不属于此开关。 | census：`gate_twap_manager.jobs/tasks`；同时有核心 `account_position_service._cache`。`GateTwapPage` 使用减仓路由，**`FloatingWatchPanel` 经账户接口依赖相同 client**。不能将 client 置空或移除默认 provider；属于本次停止原因。 |
| `second_level_sampling` / `second-sampling` | `SecondLevelSampler(SecondLevelSamplingRepository(db))`，构造时创建 `SecondLevelMarketFetcher`；共用主库。 | lifespan 在 `start_background_workers` 时调用 `initialize()`；加载 DB config 后可能立即 `start()`；内部任务名 `second-level-sampler`，**不在 supervisor**，按 config interval 采样。 | `routes_second_level_sampling.py`：config、start、stop、status、samples、component-samples、exchanges；同一 router 还包含 fat-finger-backtest。 | `SecondLevelSamplingPage` 轮询；`FatFingerBacktestPage` 经回测接口反向依赖 sampler。census 当前未登记 sampler 专属路径。仅检查 supervisor 不足以证明采样没启动，需检查构造、initialize 和内部任务。 |
| `funding_research` / `funding-research` | `FundingResearchRepository(db)`，共用主库；候选计算、paper、回测函数按需调用。 | supervisor：`funding-research-loop`，现受 `start_collector` 和 `FUNDING_RESEARCH_ENABLED` 控制，间隔 `funding_poll_interval_seconds`。新开关须与原 loop 开关取 AND，默认不能顺便改变原路由可用性。 | `routes_funding_research.py`：`/api/funding-research/*`，包括 run、candidates、snapshots、paper-trades、legacy-backtest。 | `FundingResearchPage` 使用；census 无专属 state target。核心 `services/funding_arbitrage.py:16` 导入 `funding_research.opportunity_types.exchange_formula_family`，属于共享纯函数依赖：保留代码/import，只裁 repo/任务可避免破坏它。另有离线 `funding_research/live_run.py` 构造 repo，不在本次 Web app 生命周期范围。 |

## 停止原因及精确证据

### 1. Gate 客户端服务被核心持仓功能依赖

- `backend/app/main.py:1266-1270`：创建一个 `GateTwapClient()`，同时传给 `GateTwapJobManager` 和 `AccountPositionService([GateAccountPositionProvider(gate_twap_client)])`。
- `backend/app/services/account_positions.py:79-99`：provider 访问 `client.has_credentials`，调用 `client.list_positions()` 和 `client.list_contracts()`，是实际持仓和合约倍率查询，不是可用空对象替代的状态展示。
- `backend/app/api/routes_account_positions.py:17-19`：`GET /api/account-positions` 调用该服务的 `snapshot()`。
- `frontend/src/components/FloatingWatchPanel.tsx:717`：调用 `listAccountPositions()` 驱动浮窗持仓展示。
- `backend/app/services/gate_twap.py:463-474`：manager 负责关闭共享 client；若仅去掉 manager，还必须明确保留 client 的关闭归属。

任务书 §2 明确将 `GateTwapClient/JobManager` 列入停用范围，§3.2 要求不构造服务。继续保留完整 client 不等于仅保留轻量空占位；移除 client/provider 则会改变核心持仓行为。虽然 client 的 HTTP 会话是惰性创建，保留它仍需明确共享服务例外，不能自行当作已满足“不构造”验收。

因此按用户“发现核心依赖、无法安全停用就停下报告”要求，本次不实施裁剪或核心重构。建议后续先明确：是否允许把共享 Gate 客户端归为核心资源，只裁 manager、减仓接口和菜单，并在 `main.py` 明确关闭归属。此建议尚未实施。

### 2. 采样与回测的单项独立验收冲突

`routes_second_level_sampling.py:144-151` 的回测 handler 必须取得 sampler，再解析别名和读取历史样本。单独停用 `second_level_sampling` 并不构造 sampler 后：

- 若只加回测自身的 fat_finger guard，回测返回现有 503，无法满足“其他功能正常”。
- 若对整个采样 router 加 guard，回测一起返回 404，相当于隐式联动停用另一个键，但菜单和 `/api/features` 语义尚未定义。
- 若拆开回测只读 repo/alias 能力，需要改 handler/业务依赖，超出 §4“路由只加停用依赖”的边界；构造完整 sampler 又违背停用要求。

需后续明确联动停用规则，或调整允许范围以解耦只读回测。未采用任何一种方案。

## health、memory_probe 与恢复边界

- `routes_health.py` 的 health/live/ready 不直接访问上述可选服务；ready 检查核心 `market-collector`、`alert-loop` 和快照新鲜度。不能为测试强行放松 ready 条件。
- 当前实际就绪路径是 `/api/health/ready`（`routes_health.py:58`）；任务书写 `/ready`，源码没有根路径 `/ready`。应明确验收路径，不能假称该 URL 已存在或通过。
- `/api/health/detail` 返回 supervisor、collector、snapshot、lag、realtime cache stats；除 symbol cache 统计外，没有直接解引用这 9 个服务。此为静态结论，未进行运行验证。
- `cache_census.py` 的 `_path` 使用 `getattr(..., None)`，服务为 None/缺失可返回 missing；symbol cache 是模块级字典，停写后应为空而非 missing。没有修改此文件。
- 共享主库连接和 schema 必须保留；不能因停用模块删除数据表。squeeze 独享 route 连接应单独控制初始化、迁移和 shutdown。重新启用应保留原数据与配置。
- 默认配置行为保持与基线相同，因为本次没有修改运行代码；这不是功能实现后的回归通过证明。

## 改动、验证及未解决项

- 本次改动：仅本交接文档，记录全部 9 项依赖和停止证据。
- 已执行：分支/HEAD/祖先关系检查、初始工作区检查、源码引用搜索和调用链审阅。`rg` 在当前环境不可用，后续使用 `git grep` 和 PowerShell 只读读取。
- 未进入 RED：未创建 `backend/tests/test_feature_flags.py`，未实现配置、路由依赖、菜单或守卫。按照用户先盘点、遇阻停止的要求，未运行 pytest、npm ci/test/build；没有测试通过结果。
- 未运行 WSL 或 Docker；未 push、未备份或更改生产，生产状态未实时核验。
- 未解决：共享 Gate 客户端例外、采样/回测独立开关语义、ready 路径、symbol census missing 的验收含义。
- 本文档拟作为本分支单独 docs 提交；提交号由最终编码报告给出。未创建其他未跟踪产物。

## 继续时的顺序

先解决上述范围/语义冲突，再在本模块任务继续：更新盘点决策 → 新建 RED 测试 → 实现 → 指定测试 → 后端全量 → 前端测试和构建 → 仅提交本分支。不要在未解决依赖时直接完成其余 8 项并宣称模块完成。

后续后端 pytest 必须指定 worktree 内 basetemp。若从仓库根目录执行，遵守用户指定路径：`python -m pytest -q backend/tests/test_feature_flags.py --basetemp=backend/.pytest_ff`，随后 `python -m pytest -q backend/tests --basetemp=backend/.pytest_ff`；执行前检查项目 Python import/环境要求。若在 backend 目录执行，应将 basetemp 改为本 worktree 的绝对 `backend/.pytest_ff` 路径，避免多嵌套一级 backend。前端在 `frontend` 目录按需 `npm ci`，再 `npm test`、`npm run build`。这些是待办命令，本次没有执行。
