# DISABLED_FEATURES 依赖盘点交接（2026-10-26）

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
