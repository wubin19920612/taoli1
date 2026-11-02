# 不用功能删除清单（只读盘点，2026-11-02）

## 范围、基线与交付边界

- 模块：不用功能裁剪的第一阶段盘点；依据 `docs/remove-unused-audit-spec-2026-11-02.md`，覆盖 12 项功能（11 个独立页面和 1 个设置面板）。本文件中的“删除／修改”均为第二阶段建议，本阶段没有执行。
- 分支：`codex/remove-unused`；取证基线：`e432233a259d4b75d1391a49fe60e69b28556f35`。下列行号均指该提交，实施前应重新定位。
- 开始时 `git status --short` 和暂存区均为空，没有发现待保留的现有修改或未跟踪文件。本阶段唯一新增文件为本清单，仅本地提交，不 push、不部署、不连接生产库。
- 保留 Gate 定时减仓及任务书以外的所有功能，包括实时机会、资金费率套利、标的查询、价差查询及区间回测、历史统计、Astro／arb-mvp 建卡、浮窗、账户连接／持仓、公告、指数成分、溢价告警、电话告警和运维能力。
- 数据库只做源码清单：不删除数据库文件、表、索引或旧记录，第二阶段也不写 DROP。没有打开本地或线上业务数据库，不能据此判断生产表是否存在、行数或当前启用状态。
- 实盘实验的前端面板由另一分支删除；当前基线仍含面板和请求。后续必须先取得该分支的实际改动再整合，不把“已安排删除”写成“已删除”。

## 必须优先保护的共享依赖

| 共享对象 | 处理与实际保留使用方 | 文件:行号 |
|---|---|---|
| `exchange_formula_family`、`FORMULA_FAMILIES` | 保留行为并先迁出研究包；资金费率套利用它判定交易所公式差异。不能直接删除整个 `funding_research/` | `backend/app/services/funding_arbitrage.py:16`、`backend/app/services/funding_arbitrage.py:288`、`backend/app/services/funding_research/opportunity_types.py:13` |
| 前端 `FundingOpportunityType` | 保留；资金费率套利页面及其候选类型仍使用，虽然声明位于研究类型附近 | `frontend/src/api/types.ts:2341`、`frontend/src/pages/FundingArbitragePage.tsx:47`、`frontend/src/pages/FundingArbitragePage.tsx:135` |
| `/api/pair-spread/symbol-query`、`querySymbolExchangeSpreads`、`SymbolSpread*` | 保留；标的查询展开趋势后实际调用。删除的是独立跨所价差页面；应移除接口对已退役 `symbol_spread` 开关的依赖 | `frontend/src/pages/InstrumentLookupPage.tsx:53`、`frontend/src/pages/InstrumentLookupPage.tsx:1250`、`backend/app/api/routes_pair_spread.py:636` |
| `PairSpreadQueryService` 及四类实时缓存 | 保留；价差查询、标的趋势、标的历史统计都需要。不能因独立页面／事件榜退役删除服务或 symbol 缓存 | `backend/app/services/pair_spread_query.py:530`、`backend/app/services/pair_spread_query.py:1072`、`backend/app/services/instrument_statistics.py:17` |
| `fs_identity.py` | 保留；市场身份目录导入 `SPEC_URLS`、`reviewed_fs_bindings`，保留精确身份、单位、证据和有效期规则 | `backend/app/services/market_identity_catalog.py:14`、`backend/app/services/market_identity_catalog.py:390`、`backend/app/services/market_identity_catalog.py:409` |
| `AstroPairPlanner` 的 FS 映射、SDK、`AstroAlertService`、`AlertEngine` | 保留；手动逆向 SF 建卡、普通自动建卡、新币建卡仍使用。FS 观察桥和 live pilot 只是其中的独立调用方／分支 | `backend/app/services/astro_planner.py:262`、`backend/app/services/astro_planner.py:314`、`backend/app/services/astro_alerts.py:186`、`backend/app/services/astro_alerts.py:201`、`backend/app/services/astro_alerts.py:240` |
| `opportunity_history`、历史 recorder／repository | 保留；collector 持续记录，历史查询／统计继续读取；事件榜和研究回测只是读取方 | `backend/app/services/collector.py:300`、`backend/app/services/collector.py:323`、`backend/app/api/routes_history.py:16`、`backend/app/api/routes_history.py:136` |
| `spread_engine`、`SnapshotStore`、`pair_validation`、`quote_age` | 保留；实时机会、Astro、标的价差、身份校验仍使用；机会雷达／FS 对它们是单向依赖 | `backend/app/services/collector.py:33`、`backend/app/api/routes_opportunities.py:47`、`backend/app/api/routes_astro.py:41`、`backend/app/services/instrument_spreads.py:1` |
| Gate TWAP 客户端、账户连接／持仓服务 | 保留；Gate TWAP 客户端还为 Gate 持仓 provider 提供能力；FS 使用账户服务不意味着账户服务属于 FS | `backend/app/main.py:1046`、`backend/app/main.py:1514`、`backend/app/main.py:1519` |

## 1. 乌龙回测（fat-finger / fat_finger）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| `routes_second_level_sampling.run_second_level_fat_finger_backtest` | 删除 `POST /api/second-level-sampling/fat-finger-backtest` 及专用导入；其上同时有采样和回测两层 feature guard | `backend/app/api/routes_second_level_sampling.py:9`、`backend/app/api/routes_second_level_sampling.py:140` |
| `services/fat_finger_backtest.py`、`models/fat_finger_backtest.py` | 删除整文件：`run_fat_finger_backtest`、报价／挂单／持仓模拟、请求／结果模型仅服务该回测 | `backend/app/services/fat_finger_backtest.py:18`、`backend/app/services/fat_finger_backtest.py:378`、`backend/app/models/fat_finger_backtest.py:1` |
| 仓储／数据／后台任务 | 无独立仓储、表或 lifespan 后台任务；读取 `SecondLevelMarketSample`，随第 6 节停止该数据源；不删除旧样本 | `backend/app/api/routes_second_level_sampling.py:140`、`backend/app/services/fat_finger_backtest.py:18`、`backend/app/services/second_level_sampler.py:369` |
| settings／环境变量 | 无独立持久化 settings 或专用环境变量；移除 `fat_finger` 开关及“禁用采样连带禁用回测”逻辑，兼容旧部署值见配置节 | `backend/app/core/config.py:15`、`backend/app/core/config.py:120` |
| `FatFingerBacktestPage.tsx` 及 API／类型 | 删除页面、`runFatFingerBacktest`、`FatFingerMarketMode/MakerSide/ExitReason`、`FatFingerBacktestRequest/Trade/RouteSummary/Result` | `frontend/src/pages/FatFingerBacktestPage.tsx:26`、`frontend/src/pages/FatFingerBacktestPage.tsx:135`、`frontend/src/api/client.ts:1298`、`frontend/src/api/types.ts:10`、`frontend/src/api/types.ts:607` |
| AppShell | 修改 `PageKey`、lazy import、导航、`pageFeatures` 中 `fat-finger` 项 | `frontend/src/components/AppShell.tsx:61`、`frontend/src/components/AppShell.tsx:138`、`frontend/src/components/AppShell.tsx:186`、`frontend/src/components/AppShell.tsx:210` |
| CSS／测试 | 删除 `.fat-finger-*` 的基础和响应式选择器；删除专用后端／前端测试，修改共享 feature／API 用例 | `frontend/src/styles.css:6475`、`frontend/src/styles.css:7355`、`backend/tests/test_fat_finger_backtest.py:1`、`frontend/tests/FatFingerBacktestPage.test.tsx:1`、`backend/tests/test_feature_flags.py:33` |
| `pair_range_backtest`、`RangeBacktestPanel` | 保留；价差查询的新区间回测是另一套输入、算法与端点，不属于乌龙回测 | `backend/app/api/routes_pair_spread.py:525`、`backend/app/services/pair_spread_query.py:1181`、`frontend/src/components/RangeBacktestPanel.tsx:1` |

风险：仅删回测页面不会移除后端入口；仅删模型会让采样路由的模块级导入失败。必须与第 6 节同一后端工作包处理。

## 2. 资金研究（funding-research / funding_research）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| `routes_funding_research.py` | 删除全部端点：GET `/api/funding-research/candidates`、`/candidate-snapshots`、`/paper-trades`、`/paper-trades/summary`、`/legacy-backtest`；POST `/run`、`/paper-trades/open/{candidate_id}`、`/paper-trades/{trade_id}/close` | `backend/app/api/routes_funding_research.py:22`、`backend/app/api/routes_funding_research.py:42`、`backend/app/api/routes_funding_research.py:61`、`backend/app/api/routes_funding_research.py:78`、`backend/app/api/routes_funding_research.py:97`、`backend/app/api/routes_funding_research.py:114`、`backend/app/api/routes_funding_research.py:128`、`backend/app/api/routes_funding_research.py:153`、`backend/app/api/routes_funding_research.py:195` |
| `services/funding_research/` | 先迁移下面的共享公式符号，再删除 `__init__.py`、`engine.py`、`formulas.py`、`models.py`、`opportunity_types.py`、`depth.py`、`repository.py`、`runner.py`、`paper.py`、`backtest.py`、`legacy_backtest.py`、`legacy_grid_search.py`、`live_run.py`；包含离线 CLI，不能只卸载 HTTP 路由 | `backend/app/services/funding_research/__init__.py:1`、`backend/app/services/funding_research/runner.py:39`、`backend/app/services/funding_research/live_run.py:100`、`backend/app/services/funding_research/legacy_grid_search.py:79` |
| `FORMULA_FAMILIES`、`exchange_formula_family` | 保留并迁移到公共公式分类模块（建议 `services/funding_formula_families.py`），修改资金费率套利 import；保留映射和 unknown 回退语义 | `backend/app/services/funding_research/opportunity_types.py:13`、`backend/app/services/funding_research/opportunity_types.py:35`、`backend/app/services/funding_arbitrage.py:16`、`backend/app/services/funding_arbitrage.py:288` |
| 包初始化的隐式依赖 | 修改顺序：先迁移共享 import，再删包。直接保留 `opportunity_types.py` 却删除其模型和 `__init__` 重导出目标仍会导入失败 | `backend/app/services/funding_research/opportunity_types.py:6`、`backend/app/services/funding_research/__init__.py:17`、`backend/app/services/funding_research/__init__.py:27` |
| `FundingResearchRepository`／三张表 | 删除仓储运行逻辑；保留旧表及 DDL：`funding_research_market_snapshots`、`funding_research_opportunity_snapshots`、`funding_research_paper_trades` | `backend/app/services/funding_research/repository.py:20`、`backend/app/db/schema.py:400`、`backend/app/db/schema.py:428`、`backend/app/db/schema.py:465` |
| main 创建／循环／注册 | 删除模块导入、`funding_research_repo`、`_run_funding_research_loop`、`funding-research-loop` 注册和路由；该循环由 supervisor 统一停止，无独立 close 条目 | `backend/app/main.py:105`、`backend/app/main.py:936`、`backend/app/main.py:1080`、`backend/app/main.py:1373`、`backend/app/main.py:1461`、`backend/app/main.py:1579` |
| `Settings` 字段／环境变量 | 删除 `funding_research_enabled` / `FUNDING_RESEARCH_ENABLED`、`funding_research_manage_paper_trades` / `FUNDING_RESEARCH_MANAGE_PAPER_TRADES`、`funding_research_snapshot_retention_hours` / `FUNDING_RESEARCH_SNAPSHOT_RETENTION_HOURS`；`funding_poll_interval_seconds` / `FUNDING_POLL_INTERVAL_SECONDS` 的生产调用方也仅为研究循环，可一并删除 | `backend/app/core/config.py:54`、`backend/app/core/config.py:62`、`backend/app/core/config.py:271`、`backend/app/main.py:1378`、`.env.example:8`、`docker-compose.yml:14` |
| `FundingResearchSettings`（持有／入场） | 删除 `target_holding_hours`、`snapshot_retention_hours`、`min_trade_ev_pct`、`min_small_trade_ev_pct`、`min_watch_ev_pct`、`min_trade_score`、`min_small_trade_score`、`min_watch_score`、`min_thin_depth_watch_ev_pct`、`min_thin_depth_watch_funding_pct`、`min_thin_depth_watch_score`、`min_volume_24h_usdt`、`notional_per_symbol_usdt`、`min_depth_multiple`；此对象不是 app_settings 的独立 key | `backend/app/services/funding_research/models.py:23` |
| `FundingResearchSettings`（成本／分类） | 删除 `open_fee_pct`、`close_fee_pct`、`base_slippage_pct`、`aligned_basis_capture_ratio`、`conflicted_basis_penalty_ratio`、`max_expected_basis_pct`、`basis_neutral_threshold_pct`、`basis_volatility_weight`、`low_liquidity_penalty_pct`、`thin_depth_penalty_pct`、`formula_uncertainty_penalty_pct`、`settlement_crowding_penalty_pct`、`settlement_crowding_minutes`、`min_minutes_to_settlement`、`max_minutes_to_settlement`、`strong_funding_pct`、`near_settlement_minutes`、`small_basis_threshold_pct`、`interval_mismatch_min_hours`、`formula_divergence_min_funding_pct`、`conflicted_basis_min_check_pct`、`min_conflicted_reward_risk_ratio` | `backend/app/services/funding_research/models.py:38` |
| `FundingResearchPage.tsx`、API、类型 | 删除页面和 `runFundingResearch`、`listFundingResearchCandidates/CandidateSnapshots/PaperTrades`、`open/closeFundingResearchPaperTrade`、`getFundingResearchPaperTradeSummary/LegacyBacktest`；删除 `FundingResearch*` 类型 | `frontend/src/pages/FundingResearchPage.tsx:414`、`frontend/src/api/client.ts:706`、`frontend/src/api/client.ts:726`、`frontend/src/api/client.ts:800`、`frontend/src/api/types.ts:2339`、`frontend/src/api/types.ts:2502` |
| `FundingOpportunityType` | 保留；资金费率套利页面和候选声明继续引用。不能整段删除 2339 之后的全部类型 | `frontend/src/api/types.ts:2341`、`frontend/src/pages/FundingArbitragePage.tsx:47`、`frontend/src/pages/FundingArbitragePage.tsx:135` |
| 导航／CSS／前端测试 | 修改 AppShell 的 `funding-research` 四处入口；删除 `.research-*`（含媒体查询）。基线没有 `FundingResearchPage.test.tsx`，共享导航／feature 测试仍须修改；公告 `.announcement-research-*` 保留 | `frontend/src/components/AppShell.tsx:51`、`frontend/src/components/AppShell.tsx:90`、`frontend/src/components/AppShell.tsx:189`、`frontend/src/components/AppShell.tsx:200`、`frontend/src/styles.css:3235`、`frontend/src/styles.css:6778`、`frontend/src/pages/AnnouncementsPage.tsx:380` |
| 后端测试／历史数据读者 | 删除 `test_funding_research.py`、`test_funding_research_{backtest,legacy_backtest,live_run,paper,repository,runner}.py`；修改 `test_api.py`、`test_config.py`、`test_feature_flags.py` 的研究用例；保留 `OpportunityHistoryRepository` 和资金费率套利测试 | `backend/tests/test_funding_research.py:1`、`backend/tests/test_funding_research_runner.py:1`、`backend/tests/test_api.py:1952`、`backend/tests/test_config.py:159`、`backend/app/db/repositories.py:1593`、`backend/tests/test_funding_arbitrage.py:1` |

风险：公式分类共享依赖会影响仍需保留的 `/api/funding-arbitrage/preview`，不是仅影响研究页。研究 CLI 使用通用 schema 和交易所适配器，不应连带删除数据库基础设施、ccxt/httpx 或交易所适配器。

## 3. 负基差埋伏（negative-basis / negative_basis）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| `routes_negative_basis_monitor.py` | 删除 `/api/negative-basis-monitor` 下 GET `/exchanges`、`/watchlist`、`/status`、`/auto-scan/settings`、`/samples`、`/events`、`/query`；POST `/watchlist`、`/watchlist/{item_id}/collect`、`/auto-scan`、`/auto-scan/block-symbol`、`/auto-scan/block-exchange`、`/auto-scan/block-exchange-symbol`；PUT `/auto-scan/settings`；DELETE `/watchlist/{item_id}` 和三个 block 端点 | `backend/app/api/routes_negative_basis_monitor.py:25`、`backend/app/api/routes_negative_basis_monitor.py:35`、`backend/app/api/routes_negative_basis_monitor.py:91`、`backend/app/api/routes_negative_basis_monitor.py:117`、`backend/app/api/routes_negative_basis_monitor.py:222` |
| `negative_basis_monitor.py`、`models/negative_basis.py` | 删除 `NegativeBasisMonitor`、`NegativeBasisMonitorRepository`、`GateContractStatsClient`、分析／自动筛选／告警及专用模型 | `backend/app/services/negative_basis_monitor.py:521`、`backend/app/services/negative_basis_monitor.py:838`、`backend/app/services/negative_basis_monitor.py:935`、`backend/app/services/negative_basis_monitor.py:1198`、`backend/app/models/negative_basis.py:1` |
| main 生命周期 | 删除实例、`negative-basis-monitor` 循环、关闭条目、默认 state、import、router 注册 | `backend/app/main.py:135`、`backend/app/main.py:1109`、`backend/app/main.py:1192`、`backend/app/main.py:1428`、`backend/app/main.py:1473`、`backend/app/main.py:1570` |
| settings／环境 | 删除 `negative_basis` 开关；无独立专用环境变量。停止使用 `app_settings['negative_basis_auto_scan_settings']`，保留旧行；扫描常量 60 秒在服务内部 | `backend/app/core/config.py:14`、`backend/app/services/negative_basis_monitor.py:62`、`backend/app/services/negative_basis_monitor.py:66`、`backend/app/services/negative_basis_monitor.py:942` |
| settings 字段 | 删除自动扫描 `enabled`、`feishu_notifications_enabled`、`strategy`、`blocked_exchanges/blocked_symbols/blocked_exchange_symbols`、`updated_at`；strategy／watch item 的 `interval_seconds/lookback_hours/retention_hours`、五个等级 watch/building/confirmed/strong/extreme 的 `*_threshold_pct/*_consecutive_hits`、`spot_volume_growth_threshold`、`oi_confirmed_growth_pct/oi_strong_growth_pct`、`min_spot_hourly_volume_usdt`、`alert_min_level/cooldown_seconds`；watch item 的 `id/auto_managed/enabled/symbol/spot_exchange/future_exchange/spot_symbol/future_symbol/future_multiplier/note/created_at/updated_at` 也仅属该功能 | `backend/app/models/negative_basis.py:70`、`backend/app/models/negative_basis.py:105`、`backend/app/models/negative_basis.py:158` |
| 数据表 | 保留旧 `negative_basis_watchlist`、`negative_basis_signal_samples`、`negative_basis_alert_events` 及索引，不执行清空或 DROP | `backend/app/db/schema.py:571`、`backend/app/db/schema.py:578`、`backend/app/db/schema.py:612` |
| 前端页面／API／类型 | 删除 `NegativeBasisMonitorPage.tsx`、client 中 `*NegativeBasis*` 函数和 `NegativeBasis*` 类型（含 level、strategy、watch/sample/event/status） | `frontend/src/pages/NegativeBasisMonitorPage.tsx:553`、`frontend/src/api/client.ts:1307`、`frontend/src/api/client.ts:1497`、`frontend/src/api/types.ts:9`、`frontend/src/api/types.ts:716` |
| 导航／CSS／测试 | 修改 `negative-basis` 的 PageKey/lazy/nav/pageFeatures；删除 `.negative-basis-*`，保留公共图表样式；删除专用两端测试；共享 health/feature 测试只替换相关场景 | `frontend/src/components/AppShell.tsx:58`、`frontend/src/components/AppShell.tsx:123`、`frontend/src/components/AppShell.tsx:182`、`frontend/src/components/AppShell.tsx:207`、`frontend/src/styles.css:6274`、`backend/tests/test_negative_basis_monitor.py:1`、`frontend/tests/NegativeBasisMonitorPage.test.tsx:1`、`backend/tests/test_health_routes.py:147` |
| PairSpread／告警发送器／采集行情 | 保留；负基差服务调用通用 PairSpread 查询，标的统计／价差查询仍使用；`text_alert_sender` 的同一发送链也供公告、指数、交易状态监控使用 | `backend/app/api/routes_negative_basis_monitor.py:23`、`backend/app/services/instrument_statistics.py:17`、`backend/app/main.py:1091`、`backend/app/main.py:1125`、`backend/app/main.py:1231`、`backend/app/main.py:1327` |

风险：不要把服务里的 Gate 合约统计客户端与 Gate 全局适配器／TWAP 混为一体；共享 health 和 runtime 的异常可见性测试应迁到保留任务，而非整份删除。

## 4. 原油新闻（oil-news / oil_news）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| `routes_oil_news.py`、设置路由 | 删除 GET `/api/oil-news`、POST `/api/oil-news/refresh`、GET/PUT `/api/settings/oil-news` 及对应 import | `backend/app/api/routes_oil_news.py:13`、`backend/app/api/routes_oil_news.py:23`、`backend/app/api/routes_oil_news.py:37`、`backend/app/api/routes_settings.py:247`、`backend/app/api/routes_settings.py:252` |
| service／模型 | 删除 `services/oil_news.py`、`models/oil_news.py`：feed/provider、分类、翻译器、monitor、两条 loop、原油行情／新闻／设置模型 | `backend/app/services/oil_news.py:46`、`backend/app/services/oil_news.py:327`、`backend/app/services/oil_news.py:416`、`backend/app/services/oil_news.py:554`、`backend/app/services/oil_news.py:717`、`backend/app/services/oil_news.py:857`、`backend/app/services/oil_news.py:877` |
| 仓储／settings／表 | 从共享 repositories 中只删 `OilNewsRepository`、`get/set_oil_news_settings` 及专用 import；保留旧 `oil_news_items`、`app_settings['oil_news']` 和 `_ensure_oil_news_columns` 兼容旧库的 schema 处理 | `backend/app/db/repositories.py:1044`、`backend/app/db/repositories.py:1570`、`backend/app/db/schema.py:100`、`backend/app/db/schema.py:363` |
| settings 字段／环境变量 | 删除 `OilNewsSettings.enabled/poll_interval_seconds/feishu_notifications_enabled/alert_min_severity/alert_max_age_minutes/bootstrap_alerts_enabled`；删除 `oil_news_feishu_live_send_enabled` / `OIL_NEWS_FEISHU_LIVE_SEND_ENABLED` 和 `oil_news` 开关 | `backend/app/models/oil_news.py:84`、`backend/app/core/config.py:73`、`backend/app/core/config.py:299`、`.env.example:32` |
| main 生命周期／飞书初始化 | 删除 oil repo/monitor/sender、`oil-news-loop`、`oil-news-translation-loop`、关闭／state／router；修改 notifier 的 `feishu_text_send_enabled`，去掉 oil 开关的 OR 分支，保留通用飞书功能 | `backend/app/main.py:1085`、`backend/app/main.py:1096`、`backend/app/main.py:1354`、`backend/app/main.py:1433`、`backend/app/main.py:1523`、`backend/app/main.py:1574` |
| 前端页面／API／类型 | 删除 `OilNewsPage.tsx`、`get/updateOilNewsSettings`、`listOilNews`、`refreshOilNews`、`OilNews*` 和专用 `OilMarketSnapshot`（新闻项内含翻译状态字段） | `frontend/src/pages/OilNewsPage.tsx:184`、`frontend/src/api/client.ts:579`、`frontend/src/api/types.ts:1184`、`frontend/src/api/types.ts:1187` |
| 导航／CSS／测试 | 修改 `oil-news` 四处入口；删除 `.oil-news-*`（含媒体查询）；删除两端专用测试、修改共享 feature/API/repository 测试 | `frontend/src/components/AppShell.tsx:66`、`frontend/src/components/AppShell.tsx:161`、`frontend/src/components/AppShell.tsx:183`、`frontend/src/components/AppShell.tsx:215`、`frontend/src/styles.css:3007`、`backend/tests/test_oil_news.py:1`、`frontend/tests/OilNewsPage.test.tsx:1` |
| 公告／通知／市场数据 | 保留 `AnnouncementRepository`、`announcement_localization`、公告轮询／翻译／富化、`FeishuNotifier`、`_send_index_component_alert`、原油标的本身的行情／身份；删除新闻监控不删除 CL/WTI 市场支持 | `backend/app/db/repositories.py:715`、`backend/app/services/announcement_localization.py:1`、`backend/app/main.py:1334`、`backend/app/main.py:1344`、`backend/app/main.py:1527` |

风险：新闻功能有独立飞书总开关；移除它之后仍要保证普通飞书／电话／公告发送条件按各自配置工作。共享 repositories 和 routes_settings 整文件都不能删。

## 5. 机会雷达（opportunity-radar / opportunity_radar）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 路由 | 删除 GET/PUT `/api/opportunity-radar/settings`、POST `/test-notification`、GET `/preview`（后两者同一前缀） | `backend/app/api/routes_opportunity_radar.py:10`、`backend/app/api/routes_opportunity_radar.py:20`、`backend/app/api/routes_opportunity_radar.py:25`、`backend/app/api/routes_opportunity_radar.py:35`、`backend/app/api/routes_opportunity_radar.py:49` |
| service／模型 | 删除 `services/opportunity_radar.py` 的 `OpportunityRadarAlertEngine`、preview／评分／消息生成，以及 `models/opportunity_radar.py` | `backend/app/services/opportunity_radar.py:17`、`backend/app/services/opportunity_radar.py:68`、`backend/app/services/opportunity_radar.py:191`、`backend/app/models/opportunity_radar.py:1` |
| main／后台任务 | 删除 `_run_opportunity_radar_alert_loop`、`opportunity-radar-alert-loop`、引擎 state、import、router；命中／冷却缓存随引擎移除，由 supervisor 停止循环 | `backend/app/main.py:160`、`backend/app/main.py:843`、`backend/app/main.py:1302`、`backend/app/main.py:1486`、`backend/app/main.py:1562` |
| settings／仓储／表 | 删除 SettingsRepository 的 `get/set_opportunity_radar_settings`；保留 `app_settings['opportunity_radar']` 旧行和整张共享表，无专用数据表、无专用环境变量（只有功能开关） | `backend/app/db/repositories.py:1520`、`backend/app/db/repositories.py:1530`、`backend/app/db/schema.py:182`、`backend/app/core/config.py:14` |
| settings 字段 | 删除 `enabled`、`feishu_notifications_enabled`、`min_alert_score`、`alert_consecutive_hits`、`alert_cooldown_seconds`、`anchor_exchange`、`peer_exchanges`、`premium_direction`、`min_abs_premium_pct`、`min_relative_premium_gap_pct`、`max_abs_entry_spread_pct`、`require_funding_alignment`、`min_hourly_funding_edge_pct`、`min_volume_24h_usdt`、`notional_per_symbol_usdt`、`min_depth_multiple`、`max_data_age_seconds`、`max_candidates` | `backend/app/models/opportunity_radar.py:23` |
| 页面／API／类型 | 删除 `OpportunityRadarPage.tsx`、`get/updateOpportunityRadarSettings`、`getOpportunityRadarPreview`、`testOpportunityRadarNotification`、`OpportunityRadar*` 类型 | `frontend/src/pages/OpportunityRadarPage.tsx:198`、`frontend/src/api/client.ts:646`、`frontend/src/api/types.ts:2199` |
| 导航／CSS／测试 | 修改 `opportunity-radar` 四处入口，删除 `.radar-*` 专属样式；删除专用服务／alert loop／页面测试，修改共享 repository、API、quote_age、feature 测试里的雷达场景 | `frontend/src/components/AppShell.tsx:52`、`frontend/src/components/AppShell.tsx:95`、`frontend/src/components/AppShell.tsx:184`、`frontend/src/components/AppShell.tsx:201`、`frontend/src/styles.css:2928`、`backend/tests/test_opportunity_radar.py:1`、`backend/tests/test_opportunity_radar_alert_loop.py:1`、`backend/tests/test_quote_age.py:12`、`frontend/tests/OpportunityRadarPage.test.tsx:1` |
| 实时机会／告警／共用校验 | 保留 `/api/opportunities`、`/api/markets`、`spread_engine`、`AlertEngine`、snapshot store、行情年龄／身份校验。雷达从 markets 自行构造 preview，collector 的实时机会生成没有反向调用雷达 | `backend/app/api/routes_opportunities.py:47`、`backend/app/api/routes_opportunities.py:148`、`backend/app/services/collector.py:33`、`backend/app/services/opportunity_radar.py:13`、`backend/app/main.py:1297` |

风险：不要按“opportunity”关键词删除 `models/opportunity.py`、实时机会表格、store、history recorder 或普通 alert-loop；雷达通知和普通机会通知是不同后台循环。

## 6. 1s 采样（second-sampling / second_level_sampling）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 路由 | 连同第 1 节删除 `routes_second_level_sampling.py`：GET `/api/second-level-sampling/exchanges`、`/config`、`/status`、`/samples`、`/component-samples`；PUT `/config`；POST `/start`、`/stop`、`/fat-finger-backtest` | `backend/app/api/routes_second_level_sampling.py:21`、`backend/app/api/routes_second_level_sampling.py:31`、`backend/app/api/routes_second_level_sampling.py:36`、`backend/app/api/routes_second_level_sampling.py:51`、`backend/app/api/routes_second_level_sampling.py:80`、`backend/app/api/routes_second_level_sampling.py:109`、`backend/app/api/routes_second_level_sampling.py:140` |
| service／仓储／模型 | 删除 `second_level_sampler.py`（`SecondLevelSamplingRepository`、`SecondLevelMarketFetcher`、`SecondLevelSampler`、component signal helpers）及 `models/second_level_sampling.py`；唯一其他业务使用方乌龙回测也在删除范围 | `backend/app/services/second_level_sampler.py:369`、`backend/app/services/second_level_sampler.py:703`、`backend/app/services/second_level_sampler.py:1193`、`backend/app/services/second_level_sampler.py:1579`、`backend/app/services/fat_finger_backtest.py:18` |
| main／任务生命周期 | 删除构造、initialize、close、默认 state、import 和路由。任务不由 main 直接注册到 supervisor：initialize 读取 enabled，start 自行 `create_task(..., name='second-level-sampler')`；stop/cancel/aclose 也在服务内部 | `backend/app/main.py:1086`、`backend/app/main.py:1190`、`backend/app/main.py:1427`、`backend/app/main.py:1471`、`backend/app/main.py:1582`、`backend/app/services/second_level_sampler.py:1208`、`backend/app/services/second_level_sampler.py:1233`、`backend/app/services/second_level_sampler.py:1251` |
| settings／环境变量 | 停止使用 `app_settings['second_level_sampling']`，保留旧行；字段为 enabled、interval_seconds、retention_hours、exchanges、symbols、max_concurrent_requests、capture_index_components、component_signal_window_seconds。无独立环境变量；移除 feature 与 fat_finger 级联 | `backend/app/services/second_level_sampler.py:34`、`backend/app/models/second_level_sampling.py:26`、`backend/app/core/config.py:120` |
| 数据表／schema | 保留 `second_level_market_samples`、`second_level_index_component_samples` 及索引、旧库列升级逻辑；停止新增和采样留存清理，不清空历史 | `backend/app/db/schema.py:70`、`backend/app/db/schema.py:502`、`backend/app/db/schema.py:540` |
| 前端页面／API／类型 | 删除 `SecondLevelSamplingPage.tsx`、client 中 list exchanges/get-update config/start-stop/status/list samples/list component samples，以及所有 `SecondLevel*` 类型 | `frontend/src/pages/SecondLevelSamplingPage.tsx:294`、`frontend/src/api/client.ts:1230`、`frontend/src/api/client.ts:1281`、`frontend/src/api/types.ts:8`、`frontend/src/api/types.ts:496` |
| 导航／样式／测试 | 修改 `second-sampling` 四处入口；删除 `.second-sampling-*`、`.sampling-*`、该页专属 `.index-composition-*`（含媒体查询），公共 `.page-stack`／表单组件样式保留；删除两端专用测试、更新 feature/API 用例 | `frontend/src/components/AppShell.tsx:60`、`frontend/src/components/AppShell.tsx:133`、`frontend/src/components/AppShell.tsx:188`、`frontend/src/components/AppShell.tsx:209`、`frontend/src/styles.css:3867`、`frontend/src/styles.css:3890`、`backend/tests/test_second_level_sampling.py:1`、`frontend/tests/SecondLevelSamplingPage.test.tsx:1` |
| collector／指数成分 | 保留 `MarketCollector`、交易所适配器、指数 provider/monitor、基础 history recorder 和 `index_component_*` 表；collector 并不导入 SecondLevelSampler，独立通过 exchange adapters 抓行情 | `backend/app/services/collector.py:7`、`backend/app/services/collector.py:18`、`backend/app/main.py:1225`、`backend/app/main.py:1231`、`backend/app/main.py:1261`、`backend/app/db/schema.py:249` |

风险：不要把“1s 采样”当成整站行情采集器；删除 sampler 不应改变实时行情、指数监控或 history-recorder 的发布后处理。

## 7. 挤仓结构观察（squeeze-arbitrage / squeeze）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 路由 | 删除 `/api/squeeze-arbitrage` 下 GET `/status`、`/watchlist`、`/events`、`/routes`、`/route-events`、`/paper/status`、`/paper/positions`、`/paper/trades`、`/paper/report` | `backend/app/api/routes_squeeze_arbitrage.py:14`、`backend/app/api/routes_squeeze_arbitrage.py:38`、`backend/app/api/routes_squeeze_arbitrage.py:54`、`backend/app/api/routes_squeeze_arbitrage.py:76`、`backend/app/api/routes_squeeze_arbitrage.py:97`、`backend/app/api/routes_squeeze_arbitrage.py:105`、`backend/app/api/routes_squeeze_arbitrage.py:117`、`backend/app/api/routes_squeeze_arbitrage.py:127` |
| `services/squeeze_arbitrage/` 业务代码 | 删除 `__init__.py`、`discovery.py`、`features.py`、`liquidation.py`、`models.py`、`provider.py`、`runner.py`；`route_engine/provider/runner/tracker.py`；`paper_engine/funding/models/report/runner.py`。三个 repository 的 DDL 初始化先处理下面的 schema 依赖，再删除仓储业务 | `backend/app/services/squeeze_arbitrage/runner.py:29`、`backend/app/services/squeeze_arbitrage/route_runner.py:20`、`backend/app/services/squeeze_arbitrage/paper_runner.py:24`、`backend/app/services/squeeze_arbitrage/repository.py:104`、`backend/app/services/squeeze_arbitrage/route_repository.py:136`、`backend/app/services/squeeze_arbitrage/paper_repository.py:65` |
| main 状态／连接／任务 | 删除 squeeze repo/monitor states、路由独立 DB 连接、legacy route 数据复制、paper 初始化和三个任务：`squeeze-monitor`、`squeeze-route-monitor`、`squeeze-paper-monitor`；同步删除三项 close 和 route_db.close 专用分支 | `backend/app/main.py:1007`、`backend/app/main.py:1150`、`backend/app/main.py:1174`、`backend/app/main.py:1183`、`backend/app/main.py:1429`、`backend/app/main.py:1442`、`backend/app/main.py:1474`、`backend/app/main.py:1571` |
| schema 对 service 的反向导入 | 修改，不能直接删目录：`initialize_schema` 无条件导入 `initialize_squeeze_schema`，后者又导入 `initialize_route_schema`。建议把旧建表／加列函数迁至纯 db 兼容模块，再去除对 service 包的导入；不要执行新数据迁移、DROP 或清空 | `backend/app/db/schema.py:711`、`backend/app/services/squeeze_arbitrage/repository.py:16`、`backend/app/services/squeeze_arbitrage/repository.py:94`、`backend/app/services/squeeze_arbitrage/route_repository.py:19`、`backend/app/services/squeeze_arbitrage/paper_repository.py:12` |
| legacy route 复制 | 删除启动调用即可，不迁移、不清理已有源表／目标表。该函数会 ATTACH 旧库并复制数据，不是仅建表；不要误搬进通用 schema 每次启动继续运行 | `backend/app/services/squeeze_arbitrage/route_repository.py:65`、`backend/app/main.py:1014` |
| settings／环境变量 | 删除 squeeze feature 和 `squeeze_monitor_enabled/mode/symbols/exclude_symbols`、`squeeze_route_enabled`、`squeeze_paper_enabled`，对应 `SQUEEZE_MONITOR_ENABLED/MODE/SYMBOLS/EXCLUDE_SYMBOLS`、`SQUEEZE_ROUTE_ENABLED`、`SQUEEZE_PAPER_ENABLED`；内部 `SqueezeMonitorConfig`、`RouteMonitorConfig` 同业务退役 | `backend/app/core/config.py:56`、`backend/app/core/config.py:273`、`.env.example:10`、`backend/app/services/squeeze_arbitrage/runner.py:29`、`backend/app/services/squeeze_arbitrage/route_runner.py:20` |
| 内部 monitor／paper settings | 删除 `SqueezeMonitorConfig.mode/symbols/excluded_symbols/screen_limit/poll_seconds/max_active`、`RouteMonitorConfig.poll_seconds/max_pending_scans`；`PaperSettings.rule_version/initial_balance_per_exchange/entry_notional/delay_ms/limit_buffer_rate/capture_ratio/no_improvement_seconds/routine_exit_seconds/max_holding_seconds/recovery_seconds/max_recovery_attempts/max_simultaneous_positions/max_loss_fraction` 随 paper runner 退役，不删除表中保存的历史 settings JSON | `backend/app/services/squeeze_arbitrage/runner.py:29`、`backend/app/services/squeeze_arbitrage/route_runner.py:20`、`backend/app/services/squeeze_arbitrage/paper_models.py:13` |
| 原始观察表（11 张） | 保留 `squeeze_market_identity`、`squeeze_scan_runs`、`squeeze_hourly_candles`、`squeeze_positioning_samples`、`squeeze_feature_results`、`squeeze_watch_events`、`squeeze_liquidation_updates`、`squeeze_liquidation_coverage`、`squeeze_liquidation_gaps`、`squeeze_scan_state`、`squeeze_discovery_runs` | `backend/app/services/squeeze_arbitrage/repository.py:18`、`backend/app/services/squeeze_arbitrage/repository.py:31`、`backend/app/services/squeeze_arbitrage/repository.py:49`、`backend/app/services/squeeze_arbitrage/repository.py:64`、`backend/app/services/squeeze_arbitrage/repository.py:78` |
| route／paper 表（8 张） | 保留 `squeeze_route_state/latest/events/worker_state/meta` 五张表和 `squeeze_paper_run/accounts/trades` 三张表。route 表可能既在主库也在独立库；不按新代码不再连接推定旧库可删 | `backend/app/services/squeeze_arbitrage/route_repository.py:21`、`backend/app/services/squeeze_arbitrage/route_repository.py:35`、`backend/app/services/squeeze_arbitrage/paper_repository.py:14`、`backend/app/main.py:1010` |
| 页面／组件／API／类型 | 删除 `SqueezeArbitragePage.tsx`、`SqueezeRoutesView.tsx`、`SqueezePaperView.tsx`、`SqueezeArbitragePage.css`、client 的全部 `getSqueeze*` 和仅这些视图使用的 squeeze/route/paper 类型 | `frontend/src/pages/SqueezeArbitragePage.tsx:17`、`frontend/src/pages/SqueezePaperView.tsx:100`、`frontend/src/pages/SqueezeRoutesView.tsx:195`、`frontend/src/api/client.ts:124`、`frontend/src/api/types.ts:2646`、`frontend/src/api/types.ts:2878` |
| 导航／测试 | 修改 `squeeze-arbitrage` 四处入口；删除后端 `test_squeeze_arbitrage/paper/routes.py`、前端三个视图测试；修改共享 feature、cache census、DB 连接隔离的路径清单 | `frontend/src/components/AppShell.tsx:59`、`frontend/src/components/AppShell.tsx:128`、`frontend/src/components/AppShell.tsx:181`、`frontend/src/components/AppShell.tsx:208`、`backend/tests/test_squeeze_arbitrage.py:1`、`frontend/tests/SqueezeArbitragePage.test.tsx:1`、`backend/tests/test_db_connection_isolation.py:26`、`backend/tests/test_cache_census.py:835` |
| 独立 DB 与备份 | 保留 `/data/radar-squeeze-route.db` 的既有文件和条件备份／备份保留策略；服务退役不授权删除研究历史或旧备份 | `deploy/linux-update.sh:103`、`deploy/linux-update.sh:124`、`deploy/backup_retention.py:16`、`deploy/test_backup_retention.py:94`、`docs/linux-deployment.md:59` |

风险：这是最明显的启动／schema 反向依赖。即使 `squeeze` 已禁用，通用 schema 仍会导入 service 包；删除后必须对新库和已有库启动都验收。纸面实验与 arb_paper 是不同功能，不删 arb-mvp。

## 8. 跨所价差（symbol-spread / symbol_spread）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| `SymbolSpreadPage.tsx` | 删除独立页面及内部图表／表单函数 | `frontend/src/pages/SymbolSpreadPage.tsx:8`、`frontend/src/pages/SymbolSpreadPage.tsx:295`、`frontend/src/pages/SymbolSpreadPage.tsx:412` |
| AppShell／CSS | 修改 `symbol-spread` 的 PageKey/lazy/pageFeatures/nav；删除独立 `.symbol-spread-*` 选择器和响应式项 | `frontend/src/components/AppShell.tsx:56`、`frontend/src/components/AppShell.tsx:110`、`frontend/src/components/AppShell.tsx:185`、`frontend/src/components/AppShell.tsx:205`、`frontend/src/styles.css:5323`、`frontend/src/styles.css:7760` |
| `GET /api/pair-spread/symbol-query`、`_symbol_spread_exchanges_from_query` | 保留；标的查询趋势仍用此端点。修改 decorator，移除 `require_feature('symbol_spread')`，保留通用仪表盘鉴权和参数校验 | `backend/app/api/routes_pair_spread.py:356`、`backend/app/api/routes_pair_spread.py:636`、`backend/app/main.py:1588`、`frontend/src/pages/InstrumentLookupPage.tsx:1250` |
| `PairSpreadQueryService.query_symbol_spreads`、realtime 分支、symbol 点／统计 helpers | 保留；标的趋势按交易所比较价格和价差，不能删方法或其网络查询／历史分支 | `backend/app/services/pair_spread_query.py:909`、`backend/app/services/pair_spread_query.py:943`、`backend/app/services/pair_spread_query.py:1433`、`backend/app/services/pair_spread_query.py:1658` |
| `SymbolExchangePriceSnapshot`、`SymbolSpreadPoint/Series/QueryResult` | 两端均保留，标的查询图表和列声明仍用；不能按名字批量删 | `backend/app/models/pair_spread.py:417`、`backend/app/models/pair_spread.py:428`、`frontend/src/api/types.ts:1701`、`frontend/src/pages/InstrumentLookupPage.tsx:72`、`frontend/src/pages/InstrumentLookupPage.tsx:856`、`frontend/src/pages/InstrumentLookupPage.tsx:1881` |
| `querySymbolExchangeSpreads` | 保留；与被删页共用同一 client 函数，InstrumentLookup 的 import 和运行调用均存在 | `frontend/src/api/client.ts:1080`、`frontend/src/pages/InstrumentLookupPage.tsx:53`、`frontend/src/pages/InstrumentLookupPage.tsx:1250` |
| `_REALTIME_SYMBOL_SPREAD_CACHE`、预算／stats／census | 保留；symbol-query realtime 模式仍可调用；四类缓存共用 `RealtimeSeriesBudget`，不要一并裁剪 | `backend/app/services/pair_spread_query.py:530`、`backend/app/services/pair_spread_query.py:541`、`backend/app/services/pair_spread_query.py:570`、`backend/app/runtime/cache_census.py:395` |
| settings／任务／仓储／表 | 无独立后台任务、settings 仓储或专用表；移除独立页面 feature key 的有效开关语义，旧部署值用兼容策略消化；通用查询服务仍存在 | `backend/app/core/config.py:14`、`backend/app/api/routes_pair_spread.py:636`、`backend/app/services/pair_spread_query.py:1433` |
| 测试 | 修改 feature/导航断言；保留 symbol-query 的 API／query／缓存用例和标的页趋势测试。基线没有 `SymbolSpreadPage.test.tsx` | `backend/tests/test_api.py:1469`、`backend/tests/test_pair_spread_query.py:1`、`backend/tests/test_realtime_series_cache.py:237`、`frontend/tests/InstrumentLookupPage.test.tsx:367`、`frontend/tests/InstrumentLookupPage.test.tsx:1020`、`frontend/tests/FeatureFlags.test.tsx:26` |

风险：若以“删除页面”等同于“删除端点”，会破坏保留的标的查询。验收必须实际展开标的趋势，不能只看导航里已无“跨所价差”。

## 9. 价差事件榜（spread-events，无 feature key）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 路由／模型／service | 删除 `GET /api/spread-events`、`routes_spread_events.py`、`models/spread_events.py`、`services/spread_events.py`，包含聚合、只读 SQLite 查询、并发 semaphore 和取消等待逻辑 | `backend/app/api/routes_spread_events.py:13`、`backend/app/models/spread_events.py:1`、`backend/app/services/spread_events.py:108`、`backend/app/services/spread_events.py:171`、`backend/app/services/spread_events.py:250` |
| main 注册／后台／配置 | 删除局部 import 和 router 项。无独立 state、lifespan 后台任务或持久 settings；params 是请求筛选，当前读取 HistorySettings 的 sample_seconds，不应删除后者 | `backend/app/main.py:1551`、`backend/app/main.py:1554`、`backend/app/services/spread_events.py:99` |
| 表／仓储 | 无事件榜专属表／仓储；保留 `opportunity_history`、`OpportunityHistoryRepository`、`PERCENT_SCALE`、`market_identity_bindings`，仍供历史查询和身份服务使用 | `backend/app/services/spread_events.py:14`、`backend/app/services/spread_events.py:46`、`backend/app/db/schema.py:207`、`backend/app/db/repositories.py:1593`、`backend/app/db/market_identity_repository.py:1` |
| 历史写入链 | 保留 main 的 `OpportunityHistoryRecorder`、`history-recorder` consumer、collector.record_history 和历史留存维护；事件榜只是读者，不是历史来源 | `backend/app/main.py:1227`、`backend/app/main.py:1265`、`backend/app/main.py:1277`、`backend/app/services/collector.py:323`、`backend/app/services/history.py:1` |
| 历史统计／PairSpread | 保留 `/api/history/opportunities`、`/stats`、`pair_spread_query`、标的历史统计、配对查询页面。事件榜的跳转 URL 使用价差查询，但后者没有依赖事件榜 | `backend/app/api/routes_history.py:16`、`backend/app/api/routes_history.py:136`、`backend/app/services/instrument_statistics.py:17`、`frontend/src/api/spreadEvents.ts:77`、`backend/app/api/routes_pair_spread.py:561` |
| 页面／API／类型 | 删除 `SpreadEventsPage.tsx` 与独立 `api/spreadEvents.ts`（含类型和 URL／倍率 helpers，基线仅该页和其测试导入） | `frontend/src/pages/SpreadEventsPage.tsx:8`、`frontend/src/pages/SpreadEventsPage.tsx:48`、`frontend/src/api/spreadEvents.ts:4`、`frontend/src/api/spreadEvents.ts:47` |
| 导航／样式／测试／基准 | 修改 PageKey/lazy/nav；无 pageFeatures 项、无专属 CSS 文件／前缀，公共 `.page/.toolbar` 保留。删除专用两端测试和 benchmark 脚本 | `frontend/src/components/AppShell.tsx:55`、`frontend/src/components/AppShell.tsx:115`、`frontend/src/components/AppShell.tsx:204`、`frontend/src/pages/SpreadEventsPage.tsx:86`、`backend/tests/test_spread_events.py:1`、`frontend/tests/SpreadEventsPage.test.tsx:1`、`backend/scripts/benchmark_spread_events.py:22` |

风险：`HISTORY_ENABLED`、采样间隔、保留期、写入及历史倍率／DEX 身份证据都必须保持原样；不要因事件榜退役清理它读取的历史表。

## 10. TradFi 价差（tradfi-perp，无 feature key）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 路由 | 删除 GET `/api/tradfi-perp-monitor/preview`、POST `/api/tradfi-perp-monitor/refresh` | `backend/app/api/routes_tradfi_perp_monitor.py:9`、`backend/app/api/routes_tradfi_perp_monitor.py:24`、`backend/app/api/routes_tradfi_perp_monitor.py:46` |
| service／模型 | 删除 `services/tradfi_perp_monitor.py`、`models/tradfi_perp_monitor.py`，包括 `TradfiPerpLiveFetcher`、匹配／别名／preview 专用逻辑 | `backend/app/services/tradfi_perp_monitor.py:1`、`backend/app/models/tradfi_perp_monitor.py:1`、`backend/app/api/routes_tradfi_perp_monitor.py:4` |
| main 生命周期 | 删除 route import/register 和 `tradfi_perp_live_fetcher` 关闭项。fetcher 由第一次 live/refresh 请求懒创建，不是 main 里的定时 worker | `backend/app/api/routes_tradfi_perp_monitor.py:12`、`backend/app/main.py:50`、`backend/app/main.py:1426`、`backend/app/main.py:1581` |
| settings／仓储／表 | 无专属持久 settings／环境变量、仓储、表；请求参数 live、min_volume_24h_k、max_mark_index_deviation_pct、max_rows 随路由移除 | `backend/app/api/routes_tradfi_perp_monitor.py:25` |
| 前端页面／API／类型 | 删除 `TradfiPerpMonitorPage.tsx`、`get/refreshTradfiPerpMonitorPreview` 和 `TradfiPerp*` 类型 | `frontend/src/pages/TradfiPerpMonitorPage.tsx:240`、`frontend/src/api/client.ts:670`、`frontend/src/api/client.ts:685`、`frontend/src/api/types.ts:2272` |
| 导航／CSS／测试 | 修改 PageKey/lazy/nav，无 pageFeatures 项；删除 `.tradfi-*`（含媒体查询）；删除后端 `test_tradfi_perp_monitor.py`。基线无该页专用前端测试，导航测试仍须更新 | `frontend/src/components/AppShell.tsx:62`、`frontend/src/components/AppShell.tsx:143`、`frontend/src/components/AppShell.tsx:211`、`frontend/src/styles.css:3186`、`backend/tests/test_tradfi_perp_monitor.py:1`、`frontend/tests/AppShell.test.tsx:27` |
| 交易所／TradFi 行情与建卡 | 保留 Binance/Hyperliquid adapter、市场身份、标签／会话、DEX 精确路由、GC catalog。collector 与 Astro 等仍使用这些通用模块，不能按 TradFi 市场类别全删 | `backend/app/services/tradfi_perp_monitor.py:9`、`backend/app/services/collector.py:10`、`backend/app/services/collector.py:15`、`backend/app/main.py:1500`、`backend/app/api/routes_astro.py:39` |

风险：独立 TradFi 页面不是所有 TradFi 标的的开关；移除它不应改变实时机会、标的查询与 Astro 的传统资产支持。

## 11. 负费率 FS 观察（fs-monitor，无 feature key）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 路由 | 删除 GET `/api/fs-monitor/report`、`/settings`、`/history`、`/events`；PUT `/settings`；POST `/refresh` | `backend/app/api/routes_fs_monitor.py:7`、`backend/app/api/routes_fs_monitor.py:21`、`backend/app/api/routes_fs_monitor.py:26`、`backend/app/api/routes_fs_monitor.py:31`、`backend/app/api/routes_fs_monitor.py:43`、`backend/app/api/routes_fs_monitor.py:50`、`backend/app/api/routes_fs_monitor.py:66` |
| 专用 service／模型 | 删除 `fs_monitor.py`、`fs_bybit.py`、`fs_public.py`、`fs_economics.py`、`fs_astro.py`、`fs_repository.py`、`models/fs_monitor.py`；这些模块的业务消费者限定于 FS 观察／其 Astro bridge，`fs_identity.py` 另行保留 | `backend/app/services/fs_monitor.py:5`、`backend/app/services/fs_monitor.py:76`、`backend/app/services/fs_economics.py:7`、`backend/app/services/fs_public.py:11`、`backend/app/services/fs_astro.py:58`、`backend/app/services/fs_repository.py:7` |
| `fs_identity.py` | 保留整个模块；市场身份目录直接依赖，且 `reviewed_fs_bindings` 在函数内部反向引用 catalog 的 `_evidence`。不要顺手搬动或放宽过期检查 | `backend/app/services/market_identity_catalog.py:14`、`backend/app/services/market_identity_catalog.py:390`、`backend/app/services/market_identity_catalog.py:409`、`backend/app/services/fs_identity.py:31` |
| main 生命周期 | 删除独立 FsRepository DB 连接／initialize、FsMonitor/FsAstroBridge 构造、`fs-monitor` worker、关闭和路由；monitor.aclose 原来负责 provider/repo 收尾，不能只删其 close 条目而留下创建 | `backend/app/main.py:79`、`backend/app/main.py:1054`、`backend/app/main.py:1216`、`backend/app/main.py:1437`、`backend/app/main.py:1578`、`backend/app/services/fs_monitor.py:117`、`backend/app/services/fs_repository.py:121` |
| settings 字段／环境 | 无 `DISABLED_FEATURES` 的 fs key、无 FS 专属环境变量；删除 FsSettings 的 enabled、astro_auto_create、exchanges、account_id、target_usdt、holding_hours、scan_interval_seconds、max_candidates、min_volume_usdt、min_net_pct、spot_fee_pct、future_fee_pct、execution_buffer_pct、basis_stress_pct 的读写入口 | `backend/app/models/fs_monitor.py:7`、`backend/app/services/fs_repository.py:30`、`backend/app/core/config.py:13` |
| 数据表（在仓储内建表） | 保留旧 `fs_settings`、`fs_snapshots`、`fs_events`、`fs_astro_attempts`；它们不在 schema.py，不能因那里搜不到就漏记。停止初始化调用后，新空库可以不再创建这些退役表，已有库不删表／行 | `backend/app/services/fs_repository.py:16`、`backend/app/services/fs_repository.py:17`、`backend/app/services/fs_repository.py:18`、`backend/app/services/fs_repository.py:20` |
| 与资金费率套利的边界 | 保留 FundingArbitrageSettings/preview/service/页面。当前 `funding_arbitrage.py` 没有导入 fs_*；其真实跨功能依赖是研究包的公式分类，见第 2 节 | `backend/app/api/routes_funding_arbitrage.py:7`、`backend/app/api/routes_funding_arbitrage.py:46`、`backend/app/services/funding_arbitrage.py:16`、`frontend/src/pages/FundingArbitragePage.tsx:1` |
| 与 Astro／告警的边界 | 删除 bridge 发起的 FS 观察建卡；保留 SDK、planner 的 FS 路线映射、普通 `AstroAlertService` 和 `AlertEngine`。bridge 自行通过 client 建卡，不是它们的被调用模块；保留手动 FS 能力 | `backend/app/services/fs_astro.py:7`、`backend/app/services/fs_astro.py:42`、`backend/app/services/fs_astro.py:52`、`backend/app/services/fs_astro.py:75`、`backend/app/services/fs_monitor.py:415`、`backend/app/services/astro_planner.py:262`、`backend/app/services/alert_engine.py:4` |
| 账户／风控／身份校验 | 保留 `AccountConnectionService`、密钥存储、account positions、risk settings、`validate_pair`、market_validation 和 catalog；账户连接和持仓仍为独立保留功能 | `backend/app/main.py:1041`、`backend/app/main.py:1046`、`backend/app/main.py:1051`、`backend/app/services/fs_monitor.py:11`、`frontend/src/pages/AccountConnectionsPage.tsx:1` |
| 前端页面／API／类型／样式 | 删除 `FsMonitorPage.tsx/.css`、`api/fsTypes.ts`、`getFsReport/Settings/History/Events`、`updateFsSettings`、`refreshFsMonitor`；只移除 FS import，不删同页使用的通用 listAccountConnections/saveDashboardPassword | `frontend/src/pages/FsMonitorPage.tsx:4`、`frontend/src/pages/FsMonitorPage.tsx:66`、`frontend/src/pages/FsMonitorPage.tsx:146`、`frontend/src/pages/FsMonitorPage.css:1`、`frontend/src/api/fsTypes.ts:1`、`frontend/src/api/client.ts:115` |
| 导航／测试 | 修改 `fs-monitor` PageKey/lazy/nav，无 pageFeatures 项；删除 `test_fs_astro/borrow_reference/monitor/multi/public_spec_cache/threading.py`、`FsMonitorPage.test.tsx`；共享 supervisor/census/DB 隔离测试需移除 FS 依赖，保留通用覆盖 | `frontend/src/components/AppShell.tsx:50`、`frontend/src/components/AppShell.tsx:87`、`frontend/src/components/AppShell.tsx:199`、`backend/tests/test_fs_monitor.py:1`、`backend/tests/test_fs_public_spec_cache.py:1`、`backend/tests/test_fs_threading.py:1`、`backend/tests/test_task_supervisor.py:14`、`frontend/tests/FsMonitorPage.test.tsx:1` |

风险：删除 `fs_astro` 不等于删除 FS 卡片类型。不会也不应删除／暂停／更新 Astro 实例中已经存在的卡片；本任务只裁剪 taoli1 功能。`fs_identity` 的固定审核有效期即使已过，也仍是保留身份目录的一部分，不能据当前不产出绑定就认定可删。

## 12. 正差价正费率实盘实验（live_pilot，设置页面板）

| 文件/符号 | 处理（删除 / 保留并说明谁在用 / 修改） | 文件:行号 |
|---|---|---|
| 后端真实入口 | 删除 GET/PUT `/api/settings/live-pilot`、GET `/api/settings/live-pilot/preview`；它是 live_pilot，不是 arb_live。无独立 feature key 和专属环境变量 | `backend/app/api/routes_settings.py:160`、`backend/app/api/routes_settings.py:165`、`backend/app/api/routes_settings.py:222`、`backend/app/core/config.py:13` |
| service／模型／仓储 | 删除 `services/live_pilot.py` 的候选／matches 选择和 preview；从共享 settings 模型删 `LivePilotSettings/PreviewItem/Preview`，从 SettingsRepository 删 `get/set_live_pilot_settings`，保留 app_settings 旧 `live_pilot` 行 | `backend/app/services/live_pilot.py:78`、`backend/app/services/live_pilot.py:149`、`backend/app/services/live_pilot.py:163`、`backend/app/models/settings.py:214`、`backend/app/db/repositories.py:1452` |
| settings 字段 | 删除 enabled、max_symbols、notional_per_symbol_usdt、min_next_funding_edge_pct、prefer_hyperliquid、exclude_ss、create_cards_enabled 的实验读写；普通 AstroCardSettings、AstroAutomationSettings 独立保留 | `backend/app/models/settings.py:214`、`backend/app/services/astro_alerts.py:140` |
| main 告警链路 | 修改 `_refresh_astro_runtime_settings`、`_process_alert_match` 和 `_run_alert_loop`：删除实验设置加载、候选优先排序／match keys、实验资金覆盖和 handle_live_pilot 分支；所有普通 matches 继续经过原规则、身份／新鲜度／盘口检查、隔离并发、投递与建卡链路 | `backend/app/main.py:221`、`backend/app/main.py:243`、`backend/app/main.py:444`、`backend/app/main.py:538`、`backend/app/main.py:564`、`backend/app/main.py:591`、`backend/app/main.py:752`、`backend/app/main.py:769`、`backend/app/main.py:795` |
| `AstroAlertService` | 修改共享类，仅删 live_pilot_card_settings、构造字段／参数、handle_live_pilot、内部 live_pilot 参数／资金和 enabled 覆盖；保留普通 handle_alert、手动建卡、新币建卡、去重、attempt store 与失败恢复 | `backend/app/services/astro_alerts.py:140`、`backend/app/services/astro_alerts.py:162`、`backend/app/services/astro_alerts.py:186`、`backend/app/services/astro_alerts.py:193`、`backend/app/services/astro_alerts.py:201`、`backend/app/services/astro_alerts.py:240`、`backend/app/services/astro_alerts.py:364`、`backend/app/services/astro_alerts.py:394` |
| 旧数据库记录与 source | 无专用实验表；保留 `astro_card_attempts`、`alert_events`、`alert_delivery_state`、`app_settings`。schema 中允许历史 `source='live_pilot'` 的约束可保留，不重建表来缩减 CHECK 枚举 | `backend/app/db/schema.py:124`、`backend/app/db/schema.py:133`、`backend/app/db/schema.py:136`、`backend/app/db/schema.py:145`、`backend/app/db/schema.py:182` |
| `arb_paper`／`arb_live`／Astro | 保留 CardTarget、配置 `arb_paper_url/api_key`、`arb_live_url/api_key`、arb_mvp 路由／client／planner、Astro 目标分流、前端目标选择器。它们支持独立手动建卡，不依赖 live_pilot 实验选择器 | `backend/app/models/astro.py:9`、`backend/app/core/config.py:88`、`backend/app/api/routes_arb_mvp.py:10`、`backend/app/api/routes_astro.py:517`、`backend/app/api/routes_astro.py:549`、`frontend/src/components/AstroCardVariantSelector.tsx:1` |
| 设置页（由另一分支删除） | 接受并核对外部分支对 LivePilot 面板、默认值／normalize、form/state、load/save、columns 和 API 请求的删除；本实施包不与其重复改写面板。尤其 Promise.all 中残留请求会使整个设置加载失败 | `frontend/src/pages/SettingsPage.tsx:137`、`frontend/src/pages/SettingsPage.tsx:324`、`frontend/src/pages/SettingsPage.tsx:401`、`frontend/src/pages/SettingsPage.tsx:453`、`frontend/src/pages/SettingsPage.tsx:529`、`frontend/src/pages/SettingsPage.tsx:681`、`frontend/src/pages/SettingsPage.tsx:1049` |
| API／类型／CSS／导航 | 外部分支整合后删除 getLivePilotSettings/Preview、updateLivePilotSettings、LivePilot* 类型和 `.live-pilot-*`；无独立 PageKey、导航或 pageFeatures 项 | `frontend/src/api/client.ts:539`、`frontend/src/api/types.ts:1868`、`frontend/src/styles.css:2893`、`frontend/src/styles.css:6804` |
| 测试 | 删除 `test_live_pilot.py`；修改 API、astro_alerts、alert_loop、alert_loop_isolation、market_validation_astro、astro_card_attempts 及 SettingsPage 测试／fake 设置仓储，保留普通投递／幂等／并发隔离／手动建卡／preadd 覆盖 | `backend/tests/test_live_pilot.py:1`、`backend/tests/test_api.py:2657`、`backend/tests/test_alert_loop_isolation.py:14`、`backend/tests/test_market_validation_astro.py:7`、`backend/tests/test_astro_card_attempts.py:17`、`frontend/tests/SettingsPage.test.tsx:1` |

风险：没有独立 live-pilot worker；实验是普通 alert-loop 的一条分支。不能停止整个告警循环或删 AstroAlertService，也不能为了去掉实验优先级破坏同标的串行、其他标的并发、超时与异常隔离。已经创建的外部卡片和历史 attempt 记录不在删除范围。

## 数据库与兼容性总表

| 对象 | 第二阶段处理 | 源码证据 |
|---|---|---|
| 资金研究 3 表、原油 1 表、负基差 3 表、秒级采样 2 表 | 全部留库，停止对应读写入口；现有 schema 的建表／索引／加列兼容逻辑可保留，不做数据清理 | `backend/app/db/schema.py:363`、`backend/app/db/schema.py:400`、`backend/app/db/schema.py:502`、`backend/app/db/schema.py:571` |
| squeeze 19 个表名及主库／route 库的重复旧 route 表 | 全部留库；迁移 schema 代码位置时保持定义，不再运行 legacy 数据复制；保留独立库的条件备份 | `backend/app/services/squeeze_arbitrage/repository.py:16`、`backend/app/services/squeeze_arbitrage/route_repository.py:19`、`backend/app/services/squeeze_arbitrage/paper_repository.py:12`、`deploy/linux-update.sh:103` |
| FS 4 表 | 留在已有主库；不再通过 FsRepository 初始化、写入、prune。若迁移旧 DDL，只迁定义，不为删除功能新增数据迁移 | `backend/app/services/fs_repository.py:16` |
| `app_settings` 旧 key | `second_level_sampling`、`negative_basis_auto_scan_settings`、`opportunity_radar`、`oil_news`、`live_pilot` 均保留旧 JSON 行；只删除相应 getter/setter。其他设置读写和整张表保留 | `backend/app/services/second_level_sampler.py:34`、`backend/app/services/negative_basis_monitor.py:66`、`backend/app/db/repositories.py:1452`、`backend/app/db/repositories.py:1520`、`backend/app/db/repositories.py:1570` |
| 共享机会历史／身份／投递／账户／指数／pair 表 | 全部保留；事件榜／研究／FS 的删除不构成删除这些共享数据的理由 | `backend/app/db/schema.py:117`、`backend/app/db/schema.py:187`、`backend/app/db/schema.py:207`、`backend/app/db/schema.py:249`、`backend/app/db/schema.py:633`、`backend/app/db/market_identity_repository.py:1` |

上述退役专属定义合计 32 个表名，不含共享表，也不表示已核实生产有 32 张表。乌龙回测、机会雷达、跨所价差、事件榜、TradFi、live pilot 均没有另外的专属结果表。

## 横向修改：开关、部署、样式与测试

| 文件/符号 | 处理与约束 | 文件:行号 |
|---|---|---|
| `OPTIONAL_FEATURES`／`DISABLED_FEATURES` | 当前允许 9 个 key，代码默认空集合，环境默认空字符串；删除范围占 8 个，保留 `gate_twap`。FS／TradFi／事件榜／live pilot 原本无 key | `backend/app/core/config.py:13`、`backend/app/core/config.py:35`、`backend/app/core/config.py:242` |
| 旧环境值兼容 | 建议显式区分 active `gate_twap` 与 8 个 retired key：只把退休 key 作为已知兼容输入忽略，不恢复功能；未知拼写仍报错。否则直接缩减枚举会使旧 DISABLED_FEATURES 在 Settings 初始化抛错；路由 decorator 还会在模块导入时校验 key，必须同包去除退役 guard | `backend/app/core/config.py:117`、`backend/app/core/features.py:9`、`docs/feature-flags-spec-2026-10-26.md:47` |
| Gate 开关与 feature API | 保留 `require_feature('gate_twap')`、`/api/features`、前端 OptionalFeature/FeaturesResponse 和加载／失败隐藏行为；只收窄有效 feature 类型为 gate_twap。保留开关不等于本阶段打开线上 Gate | `backend/app/api/routes_gate_twap.py:1`、`backend/app/api/routes_features.py:6`、`frontend/src/api/types.ts:2987`、`frontend/src/components/AppShell.tsx:187`、`frontend/src/components/AppShell.tsx:343` |
| `.env.example` | 删除研究、squeeze、oil 专属变量及 FUNDING_POLL_INTERVAL_SECONDS；保留 ARB_*/ASTRO_*/HISTORY_*/账户/FEISHU_* 等共享项。基线文件没有声明 DISABLED_FEATURES，不能声称默认列入 9 项 | `.env.example:8`、`.env.example:32`、`backend/app/core/config.py:88`、`backend/app/core/config.py:96`、`backend/app/core/config.py:242` |
| `docker-compose.yml` | 删除 FUNDING_POLL_INTERVAL_SECONDS 显式 environment；保留 backend.env_file（其他研究开关原本由 .env 透传）、数据卷、健康检查、FEISHU 等。此阶段不读取实际 .env | `docker-compose.yml:5`、`docker-compose.yml:14`、`docker-compose.yml:23` |
| prod/local compose、Dockerfiles、锁文件 | 保留；没有本清单功能的独立容器或专属依赖。prod 使用不可变预构建镜像覆盖；不能因 Python imports 减少就删除通用 httpx/ccxt/cryptography 等依赖 | `docker-compose.prod.yml:1`、`docker-compose.local.yml:1`、`backend/Dockerfile:1`、`frontend/Dockerfile:1`、`backend/pyproject.toml:5` |
| CI | 保留现有后端离线 pytest、前端测试／构建、镜像构建 gate。工作流监听的分支并非本审计分支；没有 push，也未触发 CI。删除过时测试后仍需保留共享功能回归 | `.github/workflows/build-production-images.yml:3`、`.github/workflows/build-production-images.yml:40`、`.github/workflows/build-production-images.yml:57` |
| `deploy/linux-update.sh`、backup_retention | 保留备份、校验、ff-only 更新与启动流程，包括旧 squeeze-route.db 存在时备份及其历史备份保留规则；不要在功能删除时顺手清理旧文件 | `deploy/linux-update.sh:103`、`deploy/linux-update.sh:124`、`deploy/backup_retention.py:16`、`deploy/test_backup_retention.py:94` |
| `runtime/cache_census.py` | 修改：去除 fs_monitor、radar engine、negative basis、oil、squeeze 的目标路径；保留通用 census，以及 symbol-query 仍用的 `_REALTIME_SYMBOL_SPREAD_CACHE` 和其余三类 pair 缓存 | `backend/app/runtime/cache_census.py:257`、`backend/app/runtime/cache_census.py:270`、`backend/app/runtime/cache_census.py:277`、`backend/app/runtime/cache_census.py:357`、`backend/app/runtime/cache_census.py:395` |
| runtime／service_control／健康接口 | 保留 TaskSupervisor、watchdog、GC/memory probe、service controller、health；它们无退役服务的反向业务依赖，修改监控对象和测试即可 | `backend/app/main.py:1135`、`backend/app/main.py:1505`、`backend/app/api/routes_health.py:1`、`backend/app/services/service_control.py:1` |
| 前端 CSS | 按完整选择器删专属前缀，包含媒体查询和逗号组内单项；不得用删除整段响应式规则的方式伤及 funding、instrument、pair、gate、announcement、account。保留公共 page/panel/toolbar/table、`.funding-*`、`.announcement-research-*` 等 | `frontend/src/styles.css:2912`、`frontend/src/styles.css:7388`、`frontend/src/styles.css:7399`、`frontend/src/styles.css:7432`、`frontend/src/styles.css:7535` |
| AppShell 持久导航／URL | 删除 11 页 PageKey/lazy/nav；仅删除有对应映射的 8 个 pageFeatures key，保留 gate。旧 navigation-order 与 URL 参数必须落到保留页面，不允许白屏或残留 lazy import | `frontend/src/components/AppShell.tsx:47`、`frontend/src/components/AppShell.tsx:78`、`frontend/src/components/AppShell.tsx:180`、`frontend/src/components/AppShell.tsx:192`、`frontend/src/components/AppShell.tsx:196`、`frontend/src/components/AppShell.tsx:357` |
| 前端共享组件／状态 | 保留 OpportunityTable、RiskTags、QuoteAgeTag、FloatingWatchPanel、AstroCardVariantSelector、MarketIdentityPanel、RangeBacktestPanel、useRadarStore、pageVisibility、queryLimits、通用 fetchJson/buildUrl。退役页只是调用者 | `frontend/src/components/OpportunityTable.tsx:1`、`frontend/src/pages/OpportunityRadarPage.tsx:32`、`frontend/src/pages/SymbolSpreadPage.tsx:16`、`frontend/src/pages/FsMonitorPage.tsx:8`、`frontend/src/state/useRadarStore.ts:1` |
| 混合测试的处理 | 修改而非整删：test_api、test_feature_flags、test_config、test_repositories、test_quote_age、test_health_routes、test_task_supervisor、test_cache_census、test_db_connection_isolation、test_alert_loop、test_alert_loop_isolation、test_astro_alerts、test_market_validation_astro、test_astro_card_attempts；前端 AppShell/FeatureFlags/SettingsPage。fake settings、patch 目标和 import 也需同步 | `backend/tests/test_api.py:1952`、`backend/tests/test_feature_flags.py:18`、`backend/tests/test_repositories.py:72`、`backend/tests/test_quote_age.py:12`、`backend/tests/test_db_connection_isolation.py:23`、`backend/tests/test_task_supervisor.py:14`、`frontend/tests/AppShell.test.tsx:20`、`frontend/tests/FeatureFlags.test.tsx:6` |

### 文档引用盘点与处理

文档需要区分当前操作说明与历史证据。第二阶段更新当前入口／说明；已完成任务书、历史交接与基准不批量删改，在新交接标记“已退役、旧表保留”即可。以下是直接引用及关联历史引用位置；包含只在历史测试／部署命令中提到退役功能的记录，不代表这些文档本身也要删除。

| 文档/类别 | 建议处理 | 文件:行号 |
|---|---|---|
| 当前 README | 修改资金研究模块说明、运行／loop／grid search 命令；保留普通运行和交易所支持 | `README.md:33`、`README.md:49`、`README.md:55`、`README.md:62`、`README.md:84` |
| 当前下一任务入口 | 更新最前面的阶段状态，说明 FS 观察已退役后的入口；保留旧生产验证证据和链接 | `docs/next-task.md:9`、`docs/next-task.md:15`、`docs/next-task.md:17` |
| 运维说明 | 修改 squeeze worker 已退役的描述；保留旧库备份／恢复说明 | `docs/linux-deployment.md:59`、`docs/linux-deployment.md:94`、`docs/linux-deployment.md:101` |
| 当前任务书 | 保留本阶段原始范围和审计基线 | `docs/remove-unused-audit-spec-2026-11-02.md:6` |
| FS 设计与交接 | 保留为历史设计／验收证据，由新交接说明退役；不要把旧线上状态当成当前状态 | `docs/fs-borrow-monitor-design.md:5`、`docs/task-handoff-2026-10-21-fs-borrow-monitor.md:13`、`docs/task-handoff-2026-10-22-fs-astro-signals.md:16`、`docs/task-handoff-2026-10-22-fs-borrow-reference.md:27`、`docs/task-handoff-2026-10-22-fs-multi-exchange.md:12` |
| 实验、squeeze、事件榜历史 | 保留；新实施交接链接本清单及删除后的验证 | `docs/task-handoff-2026-08-18-live-pilot-experiment-isolation.md:1`、`docs/task-handoff-2026-09-25-squeeze-arbitrage.md:221`、`docs/spread-events-spec-2026-11-01.md:6`、`docs/task-handoff-2026-11-01-spread-events.md:7` |
| 开关任务书／交接 | 保留历史说明；旧 disabled 示例含 gate_twap，不能原样作为新部署启用策略 | `docs/feature-flags-spec-2026-10-26.md:11`、`docs/feature-flags-spec-2026-10-26.md:47`、`docs/task-handoff-2026-10-26-feature-flags.md:17` |
| 架构索引／旧设计 | 作为基线历史保留，不把旧模块列表当新架构；当前索引若要更新，统一归文档包 | `docs/system-code-map-2026-10-23.md:25`、`docs/task-handoff-2026-10-23-system-design-review.md:34`、`docs/own-arbitrage-system-design-2026-10-02.md:284`、`docs/superpowers/specs/2026-05-26-funding-rate-arbitrage-design.md:16` |
| FS 性能／共享连接规格 | 保留历史，后续实施只迁移仍需验证的共享条件 | `docs/loop-stall-gc-fs-spec-2026-11-01.md:5`、`docs/db-shared-conn-rollback-spec-2026-11-01.md:5`、`docs/m2-3a-fs-spec-cache-spec-2026-10-26.md:3`、`docs/task-handoff-2026-10-26-fs-spec-cache.md:50` |
| 告警／建卡／鉴权规格 | 保留；实验特例退役后，普通投递隔离、建卡去重和全局鉴权仍有效 | `docs/s0-route-auth-spec-2026-10-30.md:42`、`docs/s7b-astro-card-attempts-spec-2026-10-30.md:4`、`docs/s7c-alert-isolation-spec-2026-11-01.md:13` |
| 缓存／内存／测试规格 | 保留；symbol-query 缓存和通用 runtime 继续验收 | `docs/m1-memory-profiling-spec-2026-10-25.md:18`、`docs/m1c-memory-fix-spec-2026-10-25.md:68`、`docs/s3b-realtime-cache-bound-spec-2026-10-30.md:4`、`docs/s8a-polling-visibility-spec-2026-11-02.md:19`、`docs/s9-test-gate-spec-2026-10-25.md:11` |
| 历史备份／运维／内存交接 | 保留；不删除其描述的备份与诊断产物 | `docs/task-handoff-2026-09-24-server-backup-retention.md:20`、`docs/task-handoff-2026-10-23-runtime-supervision.md:16`、`docs/task-handoff-2026-10-24-s1-acceptance.md:16`、`docs/task-handoff-2026-10-25-cache-census.md:22`、`docs/task-handoff-2026-10-25-collector-decoupling.md:96`、`docs/task-handoff-2026-10-25-memory-fix.md:106`、`docs/task-handoff-2026-10-25-memory-probe.md:49` |
| 历史新币／平台／交易状态交接 | 保留，其中只是交付命令或保留边界提及旧模块 | `docs/task-handoff-2026-08-06-new-listing-fast-monitor.md:64`、`docs/task-handoff-2026-09-20-arbitrage-platform.md:133`、`docs/task-handoff-2026-09-20-lighter-hood.md:85`、`docs/task-handoff-2026-09-21-astro-spread-navigation.md:61`、`docs/task-handoff-2026-09-21-hyperliquid-trade-status.md:88`、`docs/task-handoff-2026-09-21-new-listing-master-switch.md:89`、`docs/task-handoff-2026-09-21-trade-availability.md:113` |
| 历史行情与身份交接（一） | 保留 | `docs/task-handoff-2026-09-22-rh-lighter-data-provenance.md:177`、`docs/task-handoff-2026-09-22-trade-availability.md:93`、`docs/task-handoff-2026-09-23-rh-lighter-public-market.md:53`、`docs/task-handoff-2026-09-25-architecture-optimization.md:227`、`docs/task-handoff-2026-09-25-astro-card-variants.md:14`、`docs/task-handoff-2026-09-25-astro-lighter-routes.md:15`、`docs/task-handoff-2026-09-25-monitor-pruning.md:16` |
| 历史行情与身份交接（二） | 保留 | `docs/task-handoff-2026-09-28-instrument-rh-lighter-status.md:40`、`docs/task-handoff-2026-09-28-pair-cl-auto-dex.md:32`、`docs/task-handoff-2026-09-29-announcement-notifications.md:40`、`docs/task-handoff-2026-09-29-announcement-source-recovery.md:54`、`docs/task-handoff-2026-09-29-exchange-spread-sorting.md:44`、`docs/task-handoff-2026-09-29-okx-announcement-resilience.md:23` |
| 历史建卡／pair 交接（一） | 保留 | `docs/task-handoff-2026-10-01-astro-aster-gc-routes.md:14`、`docs/task-handoff-2026-10-01-own-arbitrage-research.md:157`、`docs/task-handoff-2026-10-01-pair-workbench-custom-presets.md:45`、`docs/task-handoff-2026-10-01-pair-workbench-rh-lighter-oai.md:41`、`docs/task-handoff-2026-10-04-lighter-openai-priority.md:50`、`docs/task-handoff-2026-10-06-pair-preset-mobile-auth.md:48`、`docs/task-handoff-2026-10-07-astro-rh-lighter-manual-create.md:8` |
| 历史建卡／pair 交接（二） | 保留 | `docs/task-handoff-2026-10-07-instrument-history-statistics.md:53`、`docs/task-handoff-2026-10-08-instrument-spread-labels-lock.md:40`、`docs/task-handoff-2026-10-08-spread-arcus.md:37`、`docs/task-handoff-2026-10-11-instrument-lookup-query-refresh.md:38`、`docs/task-handoff-2026-10-13-instrument-spread-range.md:41`、`docs/task-handoff-2026-10-14-announcement-delayed-delivery.md:53`、`docs/task-handoff-2026-10-14-astro-hl-gc-markets.md:5` |
| 历史性能交接 | 保留 | `docs/task-handoff-2026-10-09-collector-pipeline-performance.md:34`、`docs/task-handoff-2026-10-09-history-maintenance-performance.md:36`、`docs/task-handoff-2026-10-09-instrument-statistics-performance.md:29`、`docs/task-handoff-2026-10-09-market-preprocessing-performance.md:35`、`docs/task-handoff-2026-10-09-public-quote-transfer-performance.md:29`、`docs/task-handoff-2026-10-09-spread-engine-performance.md:54`、`docs/task-handoff-2026-10-09-static-assets-performance.md:26` |
| 后续身份／历史／通知交接 | 保留 | `docs/task-handoff-2026-10-15-market-identity-validation.md:46`、`docs/task-handoff-2026-10-19-okx-history-statistics.md:32`、`docs/task-handoff-2026-10-19-pair-save-database-lock.md:51`、`docs/task-handoff-2026-10-21-aster-premium-alerts.md:11`、`docs/task-handoff-2026-10-21-instrument-spread-fixed-order.md:39`、`docs/task-handoff-2026-10-23-pair-price-ratio.md:48`、`docs/task-handoff-2026-11-01-zuudot-quote-age.md:16` |

## 风险与本阶段验证结论

1. **启动导入**：研究包共享函数、schema → squeeze repository → route schema、catalog → fs_identity、共享测试中的已删除 import／monkeypatch 是必须处理的闭包，不能靠禁用开关掩盖。
2. **保留接口**：跨所独立页面退役后 `/api/pair-spread/symbol-query` 继续服务 InstrumentLookup；`/api/opportunities`、funding-arbitrage、history、pair-spread、Astro／arb-mvp、Gate 等保持可用。对“被删端点都应 404”的测试需显式排除这个共享接口。
3. **已有数据**：保留全部旧表／JSON／attempt source 和 route DB；不重建表、不改 CHECK、不执行旧研究数据复制或自动清理。不要把删除旧数据当成本次实施验收步骤。
4. **告警与建卡**：只移除 oil/radar/negative/FS 的专用生成器和 live-pilot 分支；保留普通 alert-loop、FeishuNotifier、phone alert、preadd、溢价告警、身份／盘口校验和投递去重。删除功能不会自动处理外部平台已有卡片。
5. **前端跨分支**：设置面板删除需和外部分支整合；后端先删接口而前端 Promise.all 仍请求 live-pilot 会破坏整个设置页。旧导航／URL 必须回退到保留页；TS 构建不能替代实际趋势／设置加载验收。
6. **部署旧配置**：旧 DISABLED_FEATURES 含退休 key 会触发严格校验，须做已知退役 key 兼容；未知拼写仍拒绝。旧 gate_twap 禁用状态要单独确认，本盘点不调整线上值。
7. **本阶段检查**：逐项核对了 tracked 源码、模块导入、main 启停、schema／仓储、前端调用方、导航／样式、测试、Compose／CI／部署脚本与文档。环境无 rg，使用 git grep／git ls-files 和 PowerShell 只读检索。
8. **验证边界**：没有运行应用、pytest、npm install/test/build、数据库迁移、镜像构建或生产操作，避免生成本阶段禁止的缓存／构建／数据库文件。旧交接中的通过数字只是历史证据，不作为本次测试结果。交付检查仅为文档引用、清单结构、`git diff --check` 和仅暂存本文件。

## 建议的实施拆分

按文件所有权拆成 **3 个顺序执行、可分别测试的工作包**。不按功能并行修改 main/client/types/styles 等共享文件；每个路径只属于一个包。下面命令是第二阶段验收建议，本阶段未执行。依赖应事先安装于实施环境；测试／构建允许的临时产物由第二阶段任务另行约定。

### A. 前端入口与调用清理

- **独占文件集合**：`frontend/**`。所有 11 个退役页面、squeeze 的 2 个子视图及 CSS、fsTypes/spreadEvents API、client/types/styles/AppShell、相关测试均由此包整合；SettingsPage 的面板删除从外部分支接入，由本包统一验收，不让另一包再次编辑该文件。
- **范围**：删 UI 及其专有调用／类型；保留 InstrumentLookup 的 symbol-query、FundingOpportunityType、Astro／arb 目标、Gate、浮窗和公共组件。现阶段后端旧接口仍可存在，前端先完成后再做 B。
- **验收命令**（仓库根目录的 PowerShell）：

```powershell
Push-Location frontend
npm test -- tests/AppShell.test.tsx tests/FeatureFlags.test.tsx tests/SettingsPage.test.tsx tests/InstrumentLookupPage.test.tsx tests/FundingArbitragePreadd.test.tsx tests/FloatingWatchPanel.test.tsx tests/RangeBacktestPanel.test.tsx
npm test
npm run build
Pop-Location
git diff --check
```

- **行为验收**：菜单无 11 页；旧页 URL 和持久导航不白屏；设置页不再请求 live-pilot；标的趋势仍请求 symbol-query 且出图；资金费率套利、手动建卡目标和 Gate 导航可用。应保留／更新上述共享测试来覆盖这些行为。

### B. 后端卸载与共享契约保护

- **独占文件集合**：`backend/**`，包括全部 route/service/model/db/runtime/config、后端测试和事件榜 benchmark；必要的新公共公式分类模块、旧 schema 兼容模块、退役接口回归测试也只归本包。
- **范围与内部顺序**：先迁出公式分类、保留 fs_identity 和 symbol-query；处理 schema 对 squeeze 的导入；再删除专属模块、main 实例／任务／close／路由；裁剪 live-pilot 分支；最后统一更新 Settings／guard／census／测试。旧表／数据定义和历史 source 兼容性维持。
- **验收命令**：

```powershell
Push-Location backend
python -m pytest tests/test_feature_flags.py tests/test_config.py tests/test_api.py tests/test_repositories.py tests/test_history.py tests/test_funding_arbitrage.py tests/test_pair_spread_query.py tests/test_realtime_series_cache.py tests/test_instrument_statistics.py tests/test_instrument_statistics_routes.py tests/test_astro_alerts.py tests/test_astro_card_attempts.py tests/test_astro_preadd.py tests/test_arb_mvp_routes.py tests/test_alert_loop.py tests/test_alert_loop_isolation.py tests/test_market_identity_catalog.py tests/test_gate_crossex_hyperliquid.py tests/test_account_positions.py tests/test_collector.py tests/test_snapshot_consumers.py tests/test_task_supervisor.py tests/test_health_routes.py tests/test_cache_census.py tests/test_db_connection_isolation.py -q
python -m pytest -q
Pop-Location
git diff --check
```

- **必须加入／调整的回归场景**：新临时库与已有库副本的 create_app lifespan 启停／重复启动；旧退休开关兼容、未知开关拒绝和 gate 开关有效；退役专属端点 404、保留 symbol-query 200；已删除任务不注册；正常 collector/history/alert/preadd/账户服务仍构造与关闭；保留旧表和旧 live_pilot attempt 可读；普通告警超时／失败隔离与投递去重仍成立。测试仅使用临时库和 mock 外部建卡／通知。
- **跨包验证**：以 A 的前端配 B 的后端跑设置加载、标的趋势、资金费率套利、pair 查询／区间回测和 Gate 展示。不得调用真实交易或通知来制造通过结果。

### C. 部署配置、当前说明与交接

- **独占文件集合**：`.env.example`、`docker-compose.yml`、`docker-compose.prod.yml`、`docker-compose.local.yml`、`.github/**`、`deploy/**`、`README.md`、`docs/**`（本任务书／本盘点和历史证据保留；新增实施交接归本包）。其中没有必要变化的 compose override、CI、备份脚本保持原样，不能为了凑改动删除旧库备份。
- **范围**：移除研究／squeeze／oil 示例变量及唯一 FUNDING_POLL Compose 项；说明旧开关处理、共享 symbol-query 例外、旧数据／备份保留、外部前端分支整合结果；更新 next-task 并新建第二阶段交接。此包不触碰 frontend/backend。
- **验收命令**（PowerShell；镜像名仅为配置解析占位，不启动服务）：

```powershell
docker compose --env-file .env.example -f docker-compose.yml config --quiet
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.local.yml config --quiet
$env:BACKEND_IMAGE = 'taoli1-backend:audit-config'
$env:FRONTEND_IMAGE = 'taoli1-frontend:audit-config'
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.prod.yml config --quiet
python -m unittest discover -s deploy -p 'test_backup_retention.py'
git diff --check
```

- **发布前条件**：A/B 全部通过后再按独立实施任务的授权完成提交／推送／部署。需要部署时仍按项目规则备份并校验主库和存在的旧 route 库、ff-only 更新、使用审核过的镜像／Compose 流程、核对容器／health／保留接口及页面；不删除旧表、卷、数据库或备份。本阶段到本清单的本地提交为止，交给 Claude 审核后再另开实施任务。
