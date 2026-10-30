# taoli1 全量应用源码与接口索引

日期：2026-10-23，Asia/Seoul。配合 [docs/task-handoff-2026-10-23-system-design-review.md](C:/Users/wubin/Desktop/code/codex/taoli1/docs/task-handoff-2026-10-23-system-design-review.md) 阅读。

源码基线 `18348c4ae219a3d58c079f20c042f8b6ff8c2b8e`；当前工作区的 PairMonitor 页面和测试有既有布局 WIP。此索引不包含秘密配置、依赖、构建输出和 output 证据内容。

所有链接均使用本机绝对路径。Claude 若无本机文件权限，应在自己的 checkout 中按链接显示的仓库相对路径定位。行号只适用于本次核对版本。

## 1 关键调用锚点

| 职责 | 类或函数 | 代码位置 |
| --- | --- | --- |
| main | `create_app` | [backend/app/main.py:765](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:765) |
| main | `_start_background_task` | [backend/app/main.py:344](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:344) |
| main | `_run_alert_loop` | [backend/app/main.py:374](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:374) |
| main | `_latest_signal_validation_failure` | [backend/app/main.py:237](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:237) |
| collector | `default_exchange_adapters` | [backend/app/services/collector.py:113](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/collector.py:113) |
| collector | `MarketCollector.collect_once` | [backend/app/services/collector.py:175](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/collector.py:175) |
| collector | `run_collector_loop` | [backend/app/services/collector.py:506](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/collector.py:506) |
| spread_engine | `midpoint_spread_pct` | [backend/app/services/spread_engine.py:47](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/spread_engine.py:47) |
| spread_engine | `build_directional_opportunity` | [backend/app/services/spread_engine.py:122](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/spread_engine.py:122) |
| spread_engine | `build_all_opportunities` | [backend/app/services/spread_engine.py:256](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/spread_engine.py:256) |
| pair_spread_query | `PairSpreadQueryService` | [backend/app/services/pair_spread_query.py:976](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_query.py:976) |
| pair_spread_query | `PairSpreadQueryService.query` | [backend/app/services/pair_spread_query.py:1085](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_query.py:1085) |
| pair_spread_query | `PairSpreadQueryService.query_symbol_spreads` | [backend/app/services/pair_spread_query.py:1283](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_query.py:1283) |
| market_validation_review | `MarketValidationService.prepare` | [backend/app/services/market_validation_review.py:174](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_validation_review.py:174) |
| market_validation_review | `MarketValidationService.validate` | [backend/app/services/market_validation_review.py:500](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_validation_review.py:500) |
| market_validation_review | `MarketValidationService.qualify_opportunity` | [backend/app/services/market_validation_review.py:561](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_validation_review.py:561) |
| fs_economics | `scenario` | [backend/app/services/fs_economics.py:54](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_economics.py:54) |
| fs_economics | `evaluate` | [backend/app/services/fs_economics.py:84](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_economics.py:84) |
| fs_astro | `FsAstroBridge.process` | [backend/app/services/fs_astro.py:64](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_astro.py:64) |
| gate_twap | `GateTwapJobManager` | [backend/app/services/gate_twap.py:451](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/gate_twap.py:451) |
| gate_twap | `build_order_text` | [backend/app/services/gate_twap.py:443](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/gate_twap.py:443) |
| database | `connect_database` | [backend/app/db/database.py:35](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/database.py:35) |
| repositories | `OpportunityHistoryRepository.vacuum_if_beneficial` | [backend/app/db/repositories.py:1750](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/repositories.py:1750) |
| pair_spread_presets | `PairSpreadPresetRepository._transaction` | [backend/app/services/pair_spread_presets.py:75](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_presets.py:75) |
| routes_health | `health` | [backend/app/api/routes_health.py:36](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_health.py:36) |
| security | `verify_dashboard_password` | [backend/app/core/security.py:4](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/core/security.py:4) |

## 2 API 路由清单

由路由装饰器静态提取，拼接 main.py 注册的 `/api` 前缀。方法和路径是声明事实，不表示无需认证或没有副作用。GET 也可能触发外部查询或内部缓存更新。

| 方法 | 完整路径 | 处理函数 | 源码 |
| --- | --- | --- | --- |
| GET | `/api/account-connections` | `list_account_connections` | [routes_account_connections.py:43](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py:43) |
| POST | `/api/account-connections` | `create_account_connection` | [routes_account_connections.py:51](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py:51) |
| POST | `/api/account-connections/test` | `test_draft_account_connection` | [routes_account_connections.py:63](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py:63) |
| PUT | `/api/account-connections/{connection_id}` | `update_account_connection` | [routes_account_connections.py:75](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py:75) |
| DELETE | `/api/account-connections/{connection_id}` | `delete_account_connection` | [routes_account_connections.py:88](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py:88) |
| POST | `/api/account-connections/{connection_id}/test` | `test_saved_account_connection` | [routes_account_connections.py:101](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py:101) |
| GET | `/api/account-positions` | `get_account_positions` | [routes_account_positions.py:17](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_positions.py:17) |
| GET | `/api/admin/service-control` | `get_service_control_status` | [routes_admin.py:27](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_admin.py:27) |
| POST | `/api/admin/service-control/{service}/restart` | `restart_service` | [routes_admin.py:36](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_admin.py:36) |
| GET | `/api/alerts/rules` | `list_rules` | [routes_alerts.py:74](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py:74) |
| POST | `/api/alerts/rules` | `create_rule` | [routes_alerts.py:79](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py:79) |
| PUT | `/api/alerts/rules/{rule_id}` | `update_rule` | [routes_alerts.py:89](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py:89) |
| DELETE | `/api/alerts/rules/{rule_id}` | `delete_rule` | [routes_alerts.py:101](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py:101) |
| GET | `/api/alerts/events` | `list_events` | [routes_alerts.py:112](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py:112) |
| POST | `/api/alerts/test` | `create_test_event` | [routes_alerts.py:122](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py:122) |
| GET | `/api/announcements` | `list_announcements` | [routes_announcements.py:22](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_announcements.py:22) |
| GET | `/api/announcements/exchanges` | `list_announcement_exchanges` | [routes_announcements.py:39](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_announcements.py:39) |
| GET | `/api/announcements/sources` | `announcement_sources` | [routes_announcements.py:44](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_announcements.py:44) |
| GET | `/api/astro/status` | `get_astro_status` | [routes_astro.py:381](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:381) |
| GET | `/api/astro/preadd/exchanges` | `preadd_exchanges` | [routes_astro.py:394](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:394) |
| GET | `/api/astro/preadd/settings` | `preadd_settings` | [routes_astro.py:399](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:399) |
| PUT | `/api/astro/preadd/settings` | `save_preadd_settings` | [routes_astro.py:404](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:404) |
| GET | `/api/astro/preadd/preview` | `preview_preadd` | [routes_astro.py:414](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:414) |
| POST | `/api/astro/preadd/run` | `run_preadd` | [routes_astro.py:419](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:419) |
| GET | `/api/astro/pairs` | `list_astro_pairs` | [routes_astro.py:432](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:432) |
| GET | `/api/astro/preview/{opportunity_id}` | `preview_astro_pair` | [routes_astro.py:445](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:445) |
| POST | `/api/astro/instrument/preview` | `preview_astro_instrument_pair` | [routes_astro.py:451](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:451) |
| POST | `/api/astro/opportunities/{opportunity_id}/card` | `create_astro_card_from_opportunity` | [routes_astro.py:530](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:530) |
| POST | `/api/astro/instrument/card` | `create_astro_card_from_instrument` | [routes_astro.py:542](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py:542) |
| GET | `/api/fs-monitor/report` | `report` | [routes_fs_monitor.py:21](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py:21) |
| GET | `/api/fs-monitor/settings` | `settings` | [routes_fs_monitor.py:26](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py:26) |
| PUT | `/api/fs-monitor/settings` | `update` | [routes_fs_monitor.py:31](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py:31) |
| POST | `/api/fs-monitor/refresh` | `refresh` | [routes_fs_monitor.py:43](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py:43) |
| GET | `/api/fs-monitor/history` | `history` | [routes_fs_monitor.py:50](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py:50) |
| GET | `/api/fs-monitor/events` | `events` | [routes_fs_monitor.py:66](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py:66) |
| GET | `/api/funding-arbitrage/settings` | `get_funding_arbitrage_settings` | [routes_funding_arbitrage.py:29](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_arbitrage.py:29) |
| PUT | `/api/funding-arbitrage/settings` | `update_funding_arbitrage_settings` | [routes_funding_arbitrage.py:35](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_arbitrage.py:35) |
| GET | `/api/funding-arbitrage/preview` | `get_funding_arbitrage_preview` | [routes_funding_arbitrage.py:46](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_arbitrage.py:46) |
| GET | `/api/funding-research/candidates` | `list_funding_research_candidates` | [routes_funding_research.py:41](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:41) |
| GET | `/api/funding-research/candidate-snapshots` | `list_funding_research_candidate_snapshots` | [routes_funding_research.py:60](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:60) |
| GET | `/api/funding-research/paper-trades` | `list_funding_research_paper_trades` | [routes_funding_research.py:77](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:77) |
| GET | `/api/funding-research/paper-trades/summary` | `get_funding_research_paper_trade_summary` | [routes_funding_research.py:96](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:96) |
| POST | `/api/funding-research/paper-trades/open/{candidate_id}` | `open_funding_research_paper_trade` | [routes_funding_research.py:113](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:113) |
| POST | `/api/funding-research/paper-trades/{trade_id}/close` | `close_funding_research_paper_trade` | [routes_funding_research.py:127](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:127) |
| POST | `/api/funding-research/run` | `run_funding_research_once` | [routes_funding_research.py:152](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:152) |
| GET | `/api/funding-research/legacy-backtest` | `run_legacy_opportunity_history_backtest` | [routes_funding_research.py:194](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py:194) |
| GET | `/api/gate-twap/market` | `get_gate_twap_market` | [routes_gate_twap.py:32](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py:32) |
| POST | `/api/gate-twap/preview` | `preview_gate_twap` | [routes_gate_twap.py:49](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py:49) |
| GET | `/api/gate-twap/jobs` | `list_gate_twap_jobs` | [routes_gate_twap.py:61](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py:61) |
| GET | `/api/gate-twap/jobs/{job_id}` | `get_gate_twap_job` | [routes_gate_twap.py:66](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py:66) |
| POST | `/api/gate-twap/jobs` | `start_gate_twap_job` | [routes_gate_twap.py:74](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py:74) |
| DELETE | `/api/gate-twap/jobs/{job_id}` | `cancel_gate_twap_job` | [routes_gate_twap.py:88](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py:88) |
| GET | `/api/health` | `health` | [routes_health.py:35](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_health.py:35) |
| GET | `/api/history/opportunities` | `list_opportunity_history` | [routes_history.py:16](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_history.py:16) |
| GET | `/api/history/opportunities/stats` | `get_opportunity_history_stats` | [routes_history.py:136](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_history.py:136) |
| GET | `/api/hyperliquid/trade-status/{symbol}` | `get_trade_status` | [routes_hyperliquid_trade_status.py:40](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_hyperliquid_trade_status.py:40) |
| GET | `/api/hyperliquid/trade-status/watches/list` | `list_watches` | [routes_hyperliquid_trade_status.py:55](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_hyperliquid_trade_status.py:55) |
| POST | `/api/hyperliquid/trade-status/watches` | `create_watch` | [routes_hyperliquid_trade_status.py:60](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_hyperliquid_trade_status.py:60) |
| DELETE | `/api/hyperliquid/trade-status/watches/{watch_id}` | `delete_watch` | [routes_hyperliquid_trade_status.py:110](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_hyperliquid_trade_status.py:110) |
| GET | `/api/index-components/notifications` | `get_index_component_notifications` | [routes_index_components.py:18](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:18) |
| PUT | `/api/index-components/notifications` | `update_index_component_notifications` | [routes_index_components.py:27](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:27) |
| GET | `/api/index-components/auto-watch` | `get_index_component_auto_watch` | [routes_index_components.py:44](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:44) |
| PUT | `/api/index-components/auto-watch` | `update_index_component_auto_watch` | [routes_index_components.py:53](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:53) |
| POST | `/api/index-components/auto-watch/sync` | `sync_index_component_auto_watch` | [routes_index_components.py:63](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:63) |
| GET | `/api/index-components/changes` | `list_index_component_changes` | [routes_index_components.py:87](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:87) |
| GET | `/api/index-components/snapshots` | `list_index_component_snapshots` | [routes_index_components.py:102](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:102) |
| GET | `/api/index-components/watchlist` | `list_index_component_watchlist` | [routes_index_components.py:117](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:117) |
| POST | `/api/index-components/watchlist` | `create_index_component_watch_item` | [routes_index_components.py:122](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:122) |
| DELETE | `/api/index-components/watchlist/{item_id}` | `delete_index_component_watch_item` | [routes_index_components.py:130](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py:130) |
| GET | `/api/instrument-market-cap/{base}` | `instrument_market_cap` | [routes_instruments.py:28](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_instruments.py:28) |
| GET | `/api/instruments/{symbol}` | `lookup_instrument` | [routes_instruments.py:278](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_instruments.py:278) |
| GET | `/api/instruments/{symbol}/statistics` | `instrument_statistics` | [routes_instruments.py:421](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_instruments.py:421) |
| GET | `/api/market-identities` | `bindings` | [routes_market_identities.py:24](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:24) |
| GET | `/api/market-identities/settings` | `settings` | [routes_market_identities.py:63](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:63) |
| PUT | `/api/market-identities/settings` | `update_settings` | [routes_market_identities.py:68](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:68) |
| POST | `/api/market-identities/binding-preview` | `binding_preview` | [routes_market_identities.py:92](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:92) |
| PUT | `/api/market-identities/binding` | `update_binding` | [routes_market_identities.py:117](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:117) |
| GET | `/api/market-identities/audit` | `audit` | [routes_market_identities.py:134](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:134) |
| GET | `/api/market-identities/incidents` | `incidents` | [routes_market_identities.py:139](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:139) |
| POST | `/api/market-identities/review` | `review` | [routes_market_identities.py:149](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:149) |
| GET | `/api/market-identities/card-audit` | `card_audit` | [routes_market_identities.py:172](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py:172) |
| GET | `/api/negative-basis-monitor/exchanges` | `list_negative_basis_exchanges` | [routes_negative_basis_monitor.py:34](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:34) |
| GET | `/api/negative-basis-monitor/watchlist` | `list_negative_basis_watchlist` | [routes_negative_basis_monitor.py:42](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:42) |
| POST | `/api/negative-basis-monitor/watchlist` | `upsert_negative_basis_watch_item` | [routes_negative_basis_monitor.py:47](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:47) |
| DELETE | `/api/negative-basis-monitor/watchlist/{item_id}` | `delete_negative_basis_watch_item` | [routes_negative_basis_monitor.py:57](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:57) |
| POST | `/api/negative-basis-monitor/watchlist/{item_id}/collect` | `collect_negative_basis_watch_item` | [routes_negative_basis_monitor.py:68](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:68) |
| GET | `/api/negative-basis-monitor/status` | `get_negative_basis_monitor_status` | [routes_negative_basis_monitor.py:85](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:85) |
| POST | `/api/negative-basis-monitor/auto-scan` | `refresh_negative_basis_auto_scan` | [routes_negative_basis_monitor.py:90](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:90) |
| GET | `/api/negative-basis-monitor/auto-scan/settings` | `get_negative_basis_auto_scan_settings` | [routes_negative_basis_monitor.py:99](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:99) |
| PUT | `/api/negative-basis-monitor/auto-scan/settings` | `update_negative_basis_auto_scan_settings` | [routes_negative_basis_monitor.py:106](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:106) |
| POST | `/api/negative-basis-monitor/auto-scan/block-symbol` | `block_negative_basis_auto_symbol` | [routes_negative_basis_monitor.py:116](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:116) |
| DELETE | `/api/negative-basis-monitor/auto-scan/block-symbol` | `unblock_negative_basis_auto_symbol` | [routes_negative_basis_monitor.py:129](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:129) |
| POST | `/api/negative-basis-monitor/auto-scan/block-exchange` | `block_negative_basis_auto_exchange` | [routes_negative_basis_monitor.py:139](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:139) |
| DELETE | `/api/negative-basis-monitor/auto-scan/block-exchange` | `unblock_negative_basis_auto_exchange` | [routes_negative_basis_monitor.py:152](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:152) |
| POST | `/api/negative-basis-monitor/auto-scan/block-exchange-symbol` | `block_negative_basis_auto_exchange_symbol` | [routes_negative_basis_monitor.py:162](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:162) |
| DELETE | `/api/negative-basis-monitor/auto-scan/block-exchange-symbol` | `unblock_negative_basis_auto_exchange_symbol` | [routes_negative_basis_monitor.py:176](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:176) |
| GET | `/api/negative-basis-monitor/samples` | `list_negative_basis_samples` | [routes_negative_basis_monitor.py:187](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:187) |
| GET | `/api/negative-basis-monitor/events` | `list_negative_basis_events` | [routes_negative_basis_monitor.py:204](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:204) |
| GET | `/api/negative-basis-monitor/query` | `query_negative_basis` | [routes_negative_basis_monitor.py:221](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py:221) |
| GET | `/api/oil-news` | `list_oil_news` | [routes_oil_news.py:22](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_oil_news.py:22) |
| POST | `/api/oil-news/refresh` | `refresh_oil_news` | [routes_oil_news.py:36](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_oil_news.py:36) |
| GET | `/api/opportunities` | `list_opportunities` | [routes_opportunities.py:45](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunities.py:45) |
| GET | `/api/markets` | `list_markets` | [routes_opportunities.py:129](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunities.py:129) |
| GET | `/api/opportunity-radar/settings` | `get_opportunity_radar_settings` | [routes_opportunity_radar.py:19](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunity_radar.py:19) |
| PUT | `/api/opportunity-radar/settings` | `update_opportunity_radar_settings` | [routes_opportunity_radar.py:24](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunity_radar.py:24) |
| POST | `/api/opportunity-radar/test-notification` | `test_opportunity_radar_notification` | [routes_opportunity_radar.py:34](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunity_radar.py:34) |
| GET | `/api/opportunity-radar/preview` | `get_opportunity_radar_preview` | [routes_opportunity_radar.py:48](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunity_radar.py:48) |
| GET | `/api/pair-spread/exchanges` | `list_pair_spread_exchanges` | [routes_pair_spread.py:181](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:181) |
| GET | `/api/pair-spread/presets` | `list_pair_spread_presets` | [routes_pair_spread.py:186](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:186) |
| PUT | `/api/pair-spread/presets/{preset_id}` | `upsert_pair_spread_preset` | [routes_pair_spread.py:191](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:191) |
| POST | `/api/pair-spread/presets/merge` | `merge_pair_spread_presets` | [routes_pair_spread.py:209](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:209) |
| DELETE | `/api/pair-spread/presets/{preset_id}` | `delete_pair_spread_preset` | [routes_pair_spread.py:224](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:224) |
| GET | `/api/pair-spread/hyperliquid-markets` | `list_hyperliquid_markets` | [routes_pair_spread.py:240](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:240) |
| GET | `/api/pair-spread/funding-records/watchlist` | `list_pair_spread_funding_records` | [routes_pair_spread.py:254](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:254) |
| GET | `/api/pair-spread/funding-records/status` | `get_pair_spread_funding_record_status` | [routes_pair_spread.py:287](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:287) |
| POST | `/api/pair-spread/funding-records/watch` | `start_pair_spread_funding_record` | [routes_pair_spread.py:319](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:319) |
| DELETE | `/api/pair-spread/funding-records/watch` | `stop_pair_spread_funding_record` | [routes_pair_spread.py:332](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:332) |
| GET | `/api/pair-spread/diagnostics` | `diagnose_pair_spread` | [routes_pair_spread.py:366](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:366) |
| GET | `/api/pair-spread/funding-history` | `query_pair_spread_funding_history` | [routes_pair_spread.py:461](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:461) |
| GET | `/api/pair-spread/query` | `query_pair_spread` | [routes_pair_spread.py:522](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:522) |
| GET | `/api/pair-spread/symbol-query` | `query_symbol_spread` | [routes_pair_spread.py:597](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py:597) |
| GET | `/api/phone-alerts/rules` | `list_rules` | [routes_phone_alerts.py:38](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py:38) |
| POST | `/api/phone-alerts/rules` | `create_rule` | [routes_phone_alerts.py:43](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py:43) |
| PUT | `/api/phone-alerts/rules/{rule_id}` | `update_rule` | [routes_phone_alerts.py:53](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py:53) |
| DELETE | `/api/phone-alerts/rules/{rule_id}` | `delete_rule` | [routes_phone_alerts.py:64](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py:64) |
| GET | `/api/phone-alerts/events` | `list_events` | [routes_phone_alerts.py:75](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py:75) |
| GET | `/api/phone-alerts/diagnostics` | `get_diagnostics` | [routes_phone_alerts.py:80](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py:80) |
| GET | `/api/premium-index/exchanges` | `list_premium_index_exchanges` | [routes_premium_index.py:31](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_premium_index.py:31) |
| GET | `/api/premium-index/query` | `query_premium_index` | [routes_premium_index.py:36](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_premium_index.py:36) |
| GET | `/api/premium-index/current` | `get_current_premium_index` | [routes_premium_index.py:74](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_premium_index.py:74) |
| GET | `/api/second-level-sampling/exchanges` | `list_supported_second_level_exchanges` | [routes_second_level_sampling.py:30](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:30) |
| GET | `/api/second-level-sampling/config` | `get_second_level_sampling_config` | [routes_second_level_sampling.py:35](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:35) |
| PUT | `/api/second-level-sampling/config` | `update_second_level_sampling_config` | [routes_second_level_sampling.py:40](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:40) |
| POST | `/api/second-level-sampling/start` | `start_second_level_sampling` | [routes_second_level_sampling.py:50](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:50) |
| POST | `/api/second-level-sampling/stop` | `stop_second_level_sampling` | [routes_second_level_sampling.py:63](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:63) |
| GET | `/api/second-level-sampling/status` | `get_second_level_sampling_status` | [routes_second_level_sampling.py:74](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:74) |
| GET | `/api/second-level-sampling/samples` | `list_second_level_samples` | [routes_second_level_sampling.py:79](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:79) |
| GET | `/api/second-level-sampling/component-samples` | `list_second_level_index_component_samples` | [routes_second_level_sampling.py:108](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:108) |
| POST | `/api/second-level-sampling/fat-finger-backtest` | `run_second_level_fat_finger_backtest` | [routes_second_level_sampling.py:139](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py:139) |
| GET | `/api/settings/floating-watch` | `get_floating_watch_settings` | [routes_settings.py:39](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:39) |
| POST | `/api/settings/floating-watch/items` | `mutate_floating_watch_settings` | [routes_settings.py:44](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:44) |
| POST | `/api/settings/floating-watch/positions` | `mutate_floating_watch_position_settings` | [routes_settings.py:57](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:57) |
| GET | `/api/settings/risk` | `get_risk_settings` | [routes_settings.py:70](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:70) |
| GET | `/api/settings/premium-index-alert-status` | `premium_index_alert_status` | [routes_settings.py:75](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:75) |
| PUT | `/api/settings/risk` | `update_risk_settings` | [routes_settings.py:88](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:88) |
| GET | `/api/settings/alert-message-template` | `get_alert_message_template` | [routes_settings.py:98](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:98) |
| PUT | `/api/settings/alert-message-template` | `update_alert_message_template` | [routes_settings.py:103](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:103) |
| GET | `/api/settings/astro-card` | `get_astro_card_settings` | [routes_settings.py:113](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:113) |
| PUT | `/api/settings/astro-card` | `update_astro_card_settings` | [routes_settings.py:123](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:123) |
| GET | `/api/settings/astro-automation` | `get_astro_automation_settings` | [routes_settings.py:133](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:133) |
| PUT | `/api/settings/astro-automation` | `update_astro_automation_settings` | [routes_settings.py:143](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:143) |
| GET | `/api/settings/live-pilot` | `get_live_pilot_settings` | [routes_settings.py:159](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:159) |
| GET | `/api/settings/live-pilot/preview` | `get_live_pilot_preview` | [routes_settings.py:164](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:164) |
| PUT | `/api/settings/live-pilot` | `update_live_pilot_settings` | [routes_settings.py:221](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:221) |
| GET | `/api/settings/announcements` | `get_announcement_settings` | [routes_settings.py:231](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:231) |
| PUT | `/api/settings/announcements` | `update_announcement_settings` | [routes_settings.py:236](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:236) |
| GET | `/api/settings/oil-news` | `get_oil_news_settings` | [routes_settings.py:246](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:246) |
| PUT | `/api/settings/oil-news` | `update_oil_news_settings` | [routes_settings.py:251](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py:251) |
| GET | `/api/squeeze-arbitrage/status` | `get_status` | [routes_squeeze_arbitrage.py:37](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:37) |
| GET | `/api/squeeze-arbitrage/watchlist` | `get_watchlist` | [routes_squeeze_arbitrage.py:53](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:53) |
| GET | `/api/squeeze-arbitrage/events` | `get_events` | [routes_squeeze_arbitrage.py:64](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:64) |
| GET | `/api/squeeze-arbitrage/routes` | `get_routes` | [routes_squeeze_arbitrage.py:75](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:75) |
| GET | `/api/squeeze-arbitrage/route-events` | `get_route_events` | [routes_squeeze_arbitrage.py:84](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:84) |
| GET | `/api/squeeze-arbitrage/paper/status` | `get_paper_status` | [routes_squeeze_arbitrage.py:96](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:96) |
| GET | `/api/squeeze-arbitrage/paper/positions` | `get_paper_positions` | [routes_squeeze_arbitrage.py:104](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:104) |
| GET | `/api/squeeze-arbitrage/paper/trades` | `get_paper_trades` | [routes_squeeze_arbitrage.py:116](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:116) |
| GET | `/api/squeeze-arbitrage/paper/report` | `get_paper_report` | [routes_squeeze_arbitrage.py:126](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py:126) |
| GET | `/api/trade-status/watches/list` | `list_watches` | [routes_trade_availability.py:38](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_trade_availability.py:38) |
| POST | `/api/trade-status/watches` | `create_watch` | [routes_trade_availability.py:43](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_trade_availability.py:43) |
| DELETE | `/api/trade-status/watches/{watch_id}` | `delete_watch` | [routes_trade_availability.py:107](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_trade_availability.py:107) |
| GET | `/api/trade-status/{symbol}` | `get_trade_status` | [routes_trade_availability.py:118](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_trade_availability.py:118) |
| GET | `/api/tradfi-perp-monitor/preview` | `get_tradfi_perp_monitor_preview` | [routes_tradfi_perp_monitor.py:24](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_tradfi_perp_monitor.py:24) |
| POST | `/api/tradfi-perp-monitor/refresh` | `refresh_tradfi_perp_monitor_preview` | [routes_tradfi_perp_monitor.py:46](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_tradfi_perp_monitor.py:46) |
| GET | `/api/stream` | `stream` | [stream.py:32](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/stream.py:32) |

合计 174 个 HTTP 方法与路径声明；不包含 FastAPI 自动生成的文档接口。

## 3 顶层后台任务启动位置

按 main.py 的 `_start_background_task` 调用提取。实际启动条件需阅读相邻 if，任务内部还可能读取持久化开关。秒级采样和 Gate 作业另在服务内部创建任务。

| task 名称 | 启动位置 |
| --- | --- |
| `squeeze-monitor` | [main.py:924](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:924) |
| `squeeze-route-monitor` | [main.py:931](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:931) |
| `squeeze-paper-monitor` | [main.py:936](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:936) |
| `negative-basis-monitor` | [main.py:942](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:942) |
| `pair-spread-funding-recorder` | [main.py:947](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:947) |
| `hyperliquid-trade-status-monitor` | [main.py:952](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:952) |
| `trade-availability-monitor` | [main.py:957](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:957) |
| `fs-monitor` | [main.py:965](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:965) |
| `market-identity-catalog` | [main.py:966](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:966) |
| `market-collector` | [main.py:1009](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1009) |
| `astro-preadd-loop` | [main.py:1014](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1014) |
| `alert-loop` | [main.py:1019](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1019) |
| `opportunity-radar-alert-loop` | [main.py:1024](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1024) |
| `multi-exchange-premium-loop` | [main.py:1044](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1044) |
| `announcement-loop` | [main.py:1055](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1055) |
| `announcement-enrichment-loop` | [main.py:1065](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1065) |
| `oil-news-loop` | [main.py:1075](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1075) |
| `oil-news-translation-loop` | [main.py:1084](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1084) |
| `phone-price-alert-loop` | [main.py:1034](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1034) |
| `funding-research-loop` | [main.py:1094](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py:1094) |

## 4 数据表定义位置

从应用源码 SQL 静态提取 CREATE TABLE 声明。包括启动创建、迁移兼容表和模块独立初始化表；该表不是线上 SQLite 实际表清单。归属哪个连接以 main.py 组装和对应 repository 为准。

| 表名 | 声明位置 |
| --- | --- |
| `asset_identities` | [backend/app/db/market_identity_repository.py:19](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:19) |
| `market_identity_bindings` | [backend/app/db/market_identity_repository.py:22](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:22) |
| `market_identity_audit` | [backend/app/db/market_identity_repository.py:27](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:27) |
| `market_validation_incidents` | [backend/app/db/market_identity_repository.py:33](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:33) |
| `market_validation_incident_events` | [backend/app/db/market_identity_repository.py:38](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:38) |
| `market_validation_config` | [backend/app/db/market_identity_repository.py:42](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:42) |
| `market_invalid_opportunities` | [backend/app/db/market_identity_repository.py:45](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py:45) |
| `alert_rules` | [backend/app/db/schema.py:117](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:117) |
| `alert_events` | [backend/app/db/schema.py:124](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:124) |
| `phone_price_alert_rules` | [backend/app/db/schema.py:134](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:134) |
| `phone_price_alert_events` | [backend/app/db/schema.py:141](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:141) |
| `app_settings` | [backend/app/db/schema.py:161](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:161) |
| `account_connections` | [backend/app/db/schema.py:166](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:166) |
| `opportunity_history` | [backend/app/db/schema.py:186](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:186) |
| `index_component_snapshots` | [backend/app/db/schema.py:228](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:228) |
| `index_component_changes` | [backend/app/db/schema.py:239](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:239) |
| `index_component_price_samples` | [backend/app/db/schema.py:262](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:262) |
| `index_component_trend_followups` | [backend/app/db/schema.py:274](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:274) |
| `index_component_watchlist` | [backend/app/db/schema.py:287](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:287) |
| `index_component_auto_watchlist` | [backend/app/db/schema.py:297](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:297) |
| `exchange_announcements` | [backend/app/db/schema.py:304](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:304) |
| `announcement_provider_state` | [backend/app/db/schema.py:336](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:336) |
| `oil_news_items` | [backend/app/db/schema.py:342](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:342) |
| `funding_research_market_snapshots` | [backend/app/db/schema.py:379](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:379) |
| `funding_research_opportunity_snapshots` | [backend/app/db/schema.py:407](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:407) |
| `funding_research_paper_trades` | [backend/app/db/schema.py:444](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:444) |
| `second_level_market_samples` | [backend/app/db/schema.py:481](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:481) |
| `second_level_index_component_samples` | [backend/app/db/schema.py:519](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:519) |
| `negative_basis_watchlist` | [backend/app/db/schema.py:550](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:550) |
| `negative_basis_signal_samples` | [backend/app/db/schema.py:557](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:557) |
| `negative_basis_alert_events` | [backend/app/db/schema.py:591](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:591) |
| `pair_spread_funding_watchlist` | [backend/app/db/schema.py:612](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:612) |
| `pair_spread_funding_samples` | [backend/app/db/schema.py:628](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:628) |
| `pair_spread_presets` | [backend/app/db/schema.py:644](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:644) |
| `hyperliquid_trade_status_watchlist` | [backend/app/db/schema.py:655](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:655) |
| `trade_availability_watchlist` | [backend/app/db/schema.py:668](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py:668) |
| `fs_settings` | [backend/app/services/fs_repository.py:16](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_repository.py:16) |
| `fs_snapshots` | [backend/app/services/fs_repository.py:17](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_repository.py:17) |
| `fs_events` | [backend/app/services/fs_repository.py:18](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_repository.py:18) |
| `fs_astro_attempts` | [backend/app/services/fs_repository.py:20](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_repository.py:20) |
| `squeeze_paper_run` | [backend/app/services/squeeze_arbitrage/paper_repository.py:14](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_repository.py:14) |
| `squeeze_paper_accounts` | [backend/app/services/squeeze_arbitrage/paper_repository.py:26](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_repository.py:26) |
| `squeeze_paper_trades` | [backend/app/services/squeeze_arbitrage/paper_repository.py:30](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_repository.py:30) |
| `squeeze_market_identity` | [backend/app/services/squeeze_arbitrage/repository.py:18](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:18) |
| `squeeze_scan_runs` | [backend/app/services/squeeze_arbitrage/repository.py:25](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:25) |
| `squeeze_hourly_candles` | [backend/app/services/squeeze_arbitrage/repository.py:31](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:31) |
| `squeeze_positioning_samples` | [backend/app/services/squeeze_arbitrage/repository.py:36](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:36) |
| `squeeze_feature_results` | [backend/app/services/squeeze_arbitrage/repository.py:44](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:44) |
| `squeeze_watch_events` | [backend/app/services/squeeze_arbitrage/repository.py:49](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:49) |
| `squeeze_liquidation_updates` | [backend/app/services/squeeze_arbitrage/repository.py:57](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:57) |
| `squeeze_liquidation_coverage` | [backend/app/services/squeeze_arbitrage/repository.py:64](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:64) |
| `squeeze_liquidation_gaps` | [backend/app/services/squeeze_arbitrage/repository.py:69](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:69) |
| `squeeze_scan_state` | [backend/app/services/squeeze_arbitrage/repository.py:73](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:73) |
| `squeeze_discovery_runs` | [backend/app/services/squeeze_arbitrage/repository.py:78](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py:78) |
| `squeeze_route_state` | [backend/app/services/squeeze_arbitrage/route_repository.py:21](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_repository.py:21) |
| `squeeze_route_latest` | [backend/app/services/squeeze_arbitrage/route_repository.py:24](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_repository.py:24) |
| `squeeze_route_events` | [backend/app/services/squeeze_arbitrage/route_repository.py:28](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_repository.py:28) |
| `squeeze_route_worker_state` | [backend/app/services/squeeze_arbitrage/route_repository.py:35](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_repository.py:35) |
| `squeeze_route_meta` | [backend/app/services/squeeze_arbitrage/route_repository.py:42](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_repository.py:42) |

## 5 全部应用源码

共 227 个文件、93,797 行，含空行/注释。目录按职责分组，完整绝对路径在链接目标内。


### backend/app

| 文件 | 行数 |
| --- | ---: |
| [backend/app/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/__init__.py) | 1 |

### backend/app/api

| 文件 | 行数 |
| --- | ---: |
| [backend/app/api/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/__init__.py) | 1 |
| [backend/app/api/routes_account_connections.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_connections.py) | 110 |
| [backend/app/api/routes_account_positions.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_account_positions.py) | 19 |
| [backend/app/api/routes_admin.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_admin.py) | 49 |
| [backend/app/api/routes_alerts.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_alerts.py) | 136 |
| [backend/app/api/routes_announcements.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_announcements.py) | 59 |
| [backend/app/api/routes_astro.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_astro.py) | 560 |
| [backend/app/api/routes_fs_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_fs_monitor.py) | 68 |
| [backend/app/api/routes_funding_arbitrage.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_arbitrage.py) | 55 |
| [backend/app/api/routes_funding_research.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_funding_research.py) | 224 |
| [backend/app/api/routes_gate_twap.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_gate_twap.py) | 99 |
| [backend/app/api/routes_health.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_health.py) | 45 |
| [backend/app/api/routes_history.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_history.py) | 170 |
| [backend/app/api/routes_hyperliquid_trade_status.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_hyperliquid_trade_status.py) | 118 |
| [backend/app/api/routes_index_components.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_index_components.py) | 132 |
| [backend/app/api/routes_instruments.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_instruments.py) | 439 |
| [backend/app/api/routes_market_identities.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_market_identities.py) | 183 |
| [backend/app/api/routes_negative_basis_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_negative_basis_monitor.py) | 277 |
| [backend/app/api/routes_oil_news.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_oil_news.py) | 46 |
| [backend/app/api/routes_opportunities.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunities.py) | 146 |
| [backend/app/api/routes_opportunity_radar.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_opportunity_radar.py) | 55 |
| [backend/app/api/routes_pair_spread.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_pair_spread.py) | 677 |
| [backend/app/api/routes_phone_alerts.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_phone_alerts.py) | 167 |
| [backend/app/api/routes_premium_index.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_premium_index.py) | 104 |
| [backend/app/api/routes_second_level_sampling.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_second_level_sampling.py) | 161 |
| [backend/app/api/routes_settings.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_settings.py) | 258 |
| [backend/app/api/routes_squeeze_arbitrage.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_squeeze_arbitrage.py) | 132 |
| [backend/app/api/routes_trade_availability.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_trade_availability.py) | 136 |
| [backend/app/api/routes_tradfi_perp_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/routes_tradfi_perp_monitor.py) | 60 |
| [backend/app/api/stream.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/api/stream.py) | 34 |

### backend/app/core

| 文件 | 行数 |
| --- | ---: |
| [backend/app/core/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/core/__init__.py) | 1 |
| [backend/app/core/config.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/core/config.py) | 232 |
| [backend/app/core/security.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/core/security.py) | 12 |

### backend/app/db

| 文件 | 行数 |
| --- | ---: |
| [backend/app/db/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/__init__.py) | 1 |
| [backend/app/db/database.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/database.py) | 45 |
| [backend/app/db/market_identity_repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/market_identity_repository.py) | 327 |
| [backend/app/db/repositories.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/repositories.py) | 1894 |
| [backend/app/db/schema.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/db/schema.py) | 696 |

### backend/app/exchanges

| 文件 | 行数 |
| --- | ---: |
| [backend/app/exchanges/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/__init__.py) | 1 |
| [backend/app/exchanges/arcus.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/arcus.py) | 183 |
| [backend/app/exchanges/aster.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/aster.py) | 147 |
| [backend/app/exchanges/base.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/base.py) | 201 |
| [backend/app/exchanges/binance.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/binance.py) | 157 |
| [backend/app/exchanges/bitget.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/bitget.py) | 252 |
| [backend/app/exchanges/bybit.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/bybit.py) | 83 |
| [backend/app/exchanges/gate.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/gate.py) | 134 |
| [backend/app/exchanges/htx.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/htx.py) | 118 |
| [backend/app/exchanges/hyperliquid.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/hyperliquid.py) | 396 |
| [backend/app/exchanges/lighter.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/lighter.py) | 419 |
| [backend/app/exchanges/okx.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/exchanges/okx.py) | 199 |

### backend/app

| 文件 | 行数 |
| --- | ---: |
| [backend/app/main.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/main.py) | 1271 |

### backend/app/models

| 文件 | 行数 |
| --- | ---: |
| [backend/app/models/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/__init__.py) | 1 |
| [backend/app/models/account_connection.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/account_connection.py) | 144 |
| [backend/app/models/account_position.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/account_position.py) | 137 |
| [backend/app/models/alert.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/alert.py) | 97 |
| [backend/app/models/announcement.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/announcement.py) | 186 |
| [backend/app/models/astro.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/astro.py) | 100 |
| [backend/app/models/astro_preadd.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/astro_preadd.py) | 74 |
| [backend/app/models/fat_finger_backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/fat_finger_backtest.py) | 142 |
| [backend/app/models/fs_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/fs_monitor.py) | 116 |
| [backend/app/models/funding_arbitrage.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/funding_arbitrage.py) | 111 |
| [backend/app/models/gate_twap.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/gate_twap.py) | 121 |
| [backend/app/models/history.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/history.py) | 82 |
| [backend/app/models/hyperliquid_trade_status.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/hyperliquid_trade_status.py) | 93 |
| [backend/app/models/index_component.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/index_component.py) | 182 |
| [backend/app/models/instrument.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/instrument.py) | 157 |
| [backend/app/models/market.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/market.py) | 61 |
| [backend/app/models/market_identity.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/market_identity.py) | 201 |
| [backend/app/models/negative_basis.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/negative_basis.py) | 382 |
| [backend/app/models/oil_news.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/oil_news.py) | 99 |
| [backend/app/models/opportunity.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/opportunity.py) | 79 |
| [backend/app/models/opportunity_radar.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/opportunity_radar.py) | 108 |
| [backend/app/models/orderbook.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/orderbook.py) | 44 |
| [backend/app/models/pair_spread.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/pair_spread.py) | 525 |
| [backend/app/models/phone_alert.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/phone_alert.py) | 102 |
| [backend/app/models/premium_index.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/premium_index.py) | 119 |
| [backend/app/models/second_level_sampling.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/second_level_sampling.py) | 173 |
| [backend/app/models/service_control.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/service_control.py) | 24 |
| [backend/app/models/settings.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/settings.py) | 280 |
| [backend/app/models/trade_availability.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/trade_availability.py) | 200 |
| [backend/app/models/tradfi_perp_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/models/tradfi_perp_monitor.py) | 72 |

### backend/app/services

| 文件 | 行数 |
| --- | ---: |
| [backend/app/services/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/__init__.py) | 1 |
| [backend/app/services/account_connections.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/account_connections.py) | 484 |
| [backend/app/services/account_position_providers.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/account_position_providers.py) | 504 |
| [backend/app/services/account_positions.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/account_positions.py) | 431 |
| [backend/app/services/alert_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/alert_engine.py) | 242 |
| [backend/app/services/alert_messages.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/alert_messages.py) | 511 |
| [backend/app/services/alert_metrics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/alert_metrics.py) | 35 |
| [backend/app/services/announcement_localization.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/announcement_localization.py) | 202 |
| [backend/app/services/announcement_polling.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/announcement_polling.py) | 131 |
| [backend/app/services/announcement_public_pages.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/announcement_public_pages.py) | 81 |
| [backend/app/services/announcement_research.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/announcement_research.py) | 742 |
| [backend/app/services/announcements.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/announcements.py) | 2693 |
| [backend/app/services/astro_alerts.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/astro_alerts.py) | 554 |
| [backend/app/services/astro_client.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/astro_client.py) | 193 |
| [backend/app/services/astro_planner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/astro_planner.py) | 447 |
| [backend/app/services/astro_preadd.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/astro_preadd.py) | 372 |
| [backend/app/services/collector.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/collector.py) | 519 |
| [backend/app/services/data_filters.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/data_filters.py) | 87 |
| [backend/app/services/fat_finger_backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fat_finger_backtest.py) | 704 |
| [backend/app/services/feishu.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/feishu.py) | 215 |
| [backend/app/services/fs_astro.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_astro.py) | 133 |
| [backend/app/services/fs_bybit.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_bybit.py) | 224 |
| [backend/app/services/fs_economics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_economics.py) | 257 |
| [backend/app/services/fs_identity.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_identity.py) | 95 |
| [backend/app/services/fs_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_monitor.py) | 619 |
| [backend/app/services/fs_public.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_public.py) | 434 |
| [backend/app/services/fs_repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/fs_repository.py) | 122 |
| [backend/app/services/funding_arbitrage.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_arbitrage.py) | 605 |
| [backend/app/services/funding_edge.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_edge.py) | 64 |

### backend/app/services/funding_research

| 文件 | 行数 |
| --- | ---: |
| [backend/app/services/funding_research/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/__init__.py) | 70 |
| [backend/app/services/funding_research/backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/backtest.py) | 50 |
| [backend/app/services/funding_research/depth.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/depth.py) | 156 |
| [backend/app/services/funding_research/engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/engine.py) | 489 |
| [backend/app/services/funding_research/formulas.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/formulas.py) | 86 |
| [backend/app/services/funding_research/legacy_backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/legacy_backtest.py) | 130 |
| [backend/app/services/funding_research/legacy_grid_search.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/legacy_grid_search.py) | 109 |
| [backend/app/services/funding_research/live_run.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/live_run.py) | 153 |
| [backend/app/services/funding_research/models.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/models.py) | 202 |
| [backend/app/services/funding_research/opportunity_types.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/opportunity_types.py) | 156 |
| [backend/app/services/funding_research/paper.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/paper.py) | 315 |
| [backend/app/services/funding_research/repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/repository.py) | 358 |
| [backend/app/services/funding_research/runner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/funding_research/runner.py) | 139 |

### backend/app/services

| 文件 | 行数 |
| --- | ---: |
| [backend/app/services/gate_crossex_hyperliquid.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/gate_crossex_hyperliquid.py) | 88 |
| [backend/app/services/gate_twap.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/gate_twap.py) | 585 |
| [backend/app/services/history.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/history.py) | 73 |
| [backend/app/services/hyperliquid_trade_status.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/hyperliquid_trade_status.py) | 546 |
| [backend/app/services/index_components.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/index_components.py) | 915 |
| [backend/app/services/instrument_market_cap.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/instrument_market_cap.py) | 136 |
| [backend/app/services/instrument_spreads.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/instrument_spreads.py) | 189 |
| [backend/app/services/instrument_statistics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/instrument_statistics.py) | 234 |
| [backend/app/services/live_pilot.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/live_pilot.py) | 203 |
| [backend/app/services/market_identity.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_identity.py) | 96 |
| [backend/app/services/market_identity_catalog.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_identity_catalog.py) | 482 |
| [backend/app/services/market_identity_seeds.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_identity_seeds.py) | 123 |
| [backend/app/services/market_labels.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_labels.py) | 135 |
| [backend/app/services/market_sessions.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_sessions.py) | 168 |
| [backend/app/services/market_validation_review.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/market_validation_review.py) | 699 |
| [backend/app/services/multi_exchange_premium.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/multi_exchange_premium.py) | 373 |
| [backend/app/services/negative_basis_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/negative_basis_monitor.py) | 1739 |
| [backend/app/services/oil_news.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/oil_news.py) | 900 |
| [backend/app/services/okx_history.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/okx_history.py) | 114 |
| [backend/app/services/opportunity_radar.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/opportunity_radar.py) | 380 |
| [backend/app/services/opportunity_trade_availability.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/opportunity_trade_availability.py) | 399 |
| [backend/app/services/orderbook_validator.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/orderbook_validator.py) | 313 |
| [backend/app/services/pair_spread_diagnostics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_diagnostics.py) | 225 |
| [backend/app/services/pair_spread_funding_recorder.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_funding_recorder.py) | 370 |
| [backend/app/services/pair_spread_presets.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_presets.py) | 118 |
| [backend/app/services/pair_spread_query.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_spread_query.py) | 4407 |
| [backend/app/services/pair_validation.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/pair_validation.py) | 217 |
| [backend/app/services/phone_price_alerts.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/phone_price_alerts.py) | 129 |
| [backend/app/services/premium_index_query.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/premium_index_query.py) | 902 |
| [backend/app/services/risk_labels.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/risk_labels.py) | 134 |
| [backend/app/services/second_level_sampler.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/second_level_sampler.py) | 1648 |
| [backend/app/services/service_control.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/service_control.py) | 200 |
| [backend/app/services/snapshot_store.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/snapshot_store.py) | 46 |
| [backend/app/services/spread_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/spread_engine.py) | 375 |

### backend/app/services/squeeze_arbitrage

| 文件 | 行数 |
| --- | ---: |
| [backend/app/services/squeeze_arbitrage/__init__.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/__init__.py) | 1 |
| [backend/app/services/squeeze_arbitrage/discovery.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/discovery.py) | 94 |
| [backend/app/services/squeeze_arbitrage/features.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/features.py) | 145 |
| [backend/app/services/squeeze_arbitrage/liquidation.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/liquidation.py) | 54 |
| [backend/app/services/squeeze_arbitrage/models.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/models.py) | 81 |
| [backend/app/services/squeeze_arbitrage/paper_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_engine.py) | 566 |
| [backend/app/services/squeeze_arbitrage/paper_funding.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_funding.py) | 128 |
| [backend/app/services/squeeze_arbitrage/paper_models.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_models.py) | 173 |
| [backend/app/services/squeeze_arbitrage/paper_report.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_report.py) | 186 |
| [backend/app/services/squeeze_arbitrage/paper_repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_repository.py) | 281 |
| [backend/app/services/squeeze_arbitrage/paper_runner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/paper_runner.py) | 260 |
| [backend/app/services/squeeze_arbitrage/provider.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/provider.py) | 133 |
| [backend/app/services/squeeze_arbitrage/repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/repository.py) | 517 |
| [backend/app/services/squeeze_arbitrage/route_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_engine.py) | 410 |
| [backend/app/services/squeeze_arbitrage/route_provider.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_provider.py) | 201 |
| [backend/app/services/squeeze_arbitrage/route_repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_repository.py) | 351 |
| [backend/app/services/squeeze_arbitrage/route_runner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_runner.py) | 254 |
| [backend/app/services/squeeze_arbitrage/route_tracker.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/route_tracker.py) | 233 |
| [backend/app/services/squeeze_arbitrage/runner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/squeeze_arbitrage/runner.py) | 282 |

### backend/app/services

| 文件 | 行数 |
| --- | ---: |
| [backend/app/services/symbol_aliases.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/symbol_aliases.py) | 490 |
| [backend/app/services/trade_availability.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/trade_availability.py) | 1669 |
| [backend/app/services/tradfi_perp_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/app/services/tradfi_perp_monitor.py) | 546 |

### frontend/src/api

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/api/client.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/api/client.ts) | 1599 |
| [frontend/src/api/fsTypes.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/api/fsTypes.ts) | 56 |
| [frontend/src/api/types.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/api/types.ts) | 2953 |

### frontend/src/components

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/components/AppShell.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/AppShell.tsx) | 633 |
| [frontend/src/components/AstroCardVariantSelector.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/AstroCardVariantSelector.tsx) | 53 |
| [frontend/src/components/FloatingWatchPanel.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/FloatingWatchPanel.tsx) | 1459 |
| [frontend/src/components/MarketIdentityPanel.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/MarketIdentityPanel.tsx) | 137 |
| [frontend/src/components/OpportunityTable.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/OpportunityTable.tsx) | 523 |
| [frontend/src/components/RiskTags.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/RiskTags.tsx) | 43 |
| [frontend/src/components/TopFilters.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/components/TopFilters.tsx) | 183 |

### frontend/src/constants

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/constants/alertRules.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/constants/alertRules.ts) | 32 |
| [frontend/src/constants/marketLabels.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/constants/marketLabels.ts) | 37 |
| [frontend/src/constants/queryLimits.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/constants/queryLimits.ts) | 19 |
| [frontend/src/constants/riskLabels.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/constants/riskLabels.ts) | 73 |

### frontend/src

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/main.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/main.tsx) | 30 |

### frontend/src/pages

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/pages/AccountConnectionsPage.css](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/AccountConnectionsPage.css) | 141 |
| [frontend/src/pages/AccountConnectionsPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/AccountConnectionsPage.tsx) | 555 |
| [frontend/src/pages/AlertHistoryPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/AlertHistoryPage.tsx) | 89 |
| [frontend/src/pages/AnnouncementsPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/AnnouncementsPage.tsx) | 754 |
| [frontend/src/pages/DashboardPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/DashboardPage.tsx) | 1103 |
| [frontend/src/pages/FatFingerBacktestPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/FatFingerBacktestPage.tsx) | 480 |
| [frontend/src/pages/FsMonitorPage.css](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/FsMonitorPage.css) | 18 |
| [frontend/src/pages/FsMonitorPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/FsMonitorPage.tsx) | 268 |
| [frontend/src/pages/FundingArbitragePage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/FundingArbitragePage.tsx) | 933 |
| [frontend/src/pages/FundingResearchPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/FundingResearchPage.tsx) | 668 |
| [frontend/src/pages/GateTwapPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/GateTwapPage.tsx) | 560 |
| [frontend/src/pages/IndexComponentChangesPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/IndexComponentChangesPage.tsx) | 796 |
| [frontend/src/pages/InstrumentLookupPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/InstrumentLookupPage.tsx) | 2404 |
| [frontend/src/pages/NegativeBasisMonitorPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/NegativeBasisMonitorPage.tsx) | 1615 |
| [frontend/src/pages/OilNewsPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/OilNewsPage.tsx) | 347 |
| [frontend/src/pages/OpportunityRadarPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/OpportunityRadarPage.tsx) | 419 |
| [frontend/src/pages/PairMonitorPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/PairMonitorPage.tsx) | 6253 |
| [frontend/src/pages/PhonePriceAlertsPanel.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/PhonePriceAlertsPanel.tsx) | 235 |
| [frontend/src/pages/PremiumIndexPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/PremiumIndexPage.tsx) | 1516 |
| [frontend/src/pages/SecondLevelSamplingPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SecondLevelSamplingPage.tsx) | 1078 |
| [frontend/src/pages/SettingsPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SettingsPage.tsx) | 1362 |
| [frontend/src/pages/SqueezeArbitragePage.css](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SqueezeArbitragePage.css) | 23 |
| [frontend/src/pages/SqueezeArbitragePage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SqueezeArbitragePage.tsx) | 278 |
| [frontend/src/pages/SqueezePaperView.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SqueezePaperView.tsx) | 172 |
| [frontend/src/pages/SqueezeRoutesView.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SqueezeRoutesView.tsx) | 230 |
| [frontend/src/pages/SymbolSpreadPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/SymbolSpreadPage.tsx) | 733 |
| [frontend/src/pages/TradfiPerpMonitorPage.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/pages/TradfiPerpMonitorPage.tsx) | 371 |

### frontend/src/state

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/state/useRadarStore.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/state/useRadarStore.ts) | 115 |

### frontend/src

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/styles.css](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/styles.css) | 7848 |

### frontend/src/utils

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/utils/floatingWatch.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/utils/floatingWatch.ts) | 38 |
| [frontend/src/utils/pairRatio.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/utils/pairRatio.ts) | 32 |

### frontend/src

| 文件 | 行数 |
| --- | ---: |
| [frontend/src/vite-env.d.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/src/vite-env.d.ts) | 1 |

## 6 测试 构建 部署与研究脚本

测试文件用于确定回归范围；列出文件不代表本轮运行或通过。未跟踪研究脚本和 output 目录不进入本索引。

| 文件 | 用途 |
| --- | --- |
| [.github/workflows/build-production-images.yml](C:/Users/wubin/Desktop/code/codex/taoli1/.github/workflows/build-production-images.yml) | 项目约束或构建配置 |
| [AGENTS.md](C:/Users/wubin/Desktop/code/codex/taoli1/AGENTS.md) | 项目约束或构建配置 |
| [README.md](C:/Users/wubin/Desktop/code/codex/taoli1/README.md) | 项目约束或构建配置 |
| [backend/Dockerfile](C:/Users/wubin/Desktop/code/codex/taoli1/backend/Dockerfile) | 项目约束或构建配置 |
| [backend/pyproject.toml](C:/Users/wubin/Desktop/code/codex/taoli1/backend/pyproject.toml) | 项目约束或构建配置 |
| [backend/tests/conftest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/conftest.py) | 测试或测试支持 |
| [backend/tests/instrument_statistics_reference.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/instrument_statistics_reference.py) | 测试或测试支持 |
| [backend/tests/reference_market_sessions.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/reference_market_sessions.py) | 测试或测试支持 |
| [backend/tests/spread_engine_reference.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/spread_engine_reference.py) | 测试或测试支持 |
| [backend/tests/symbol_aliases_reference.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/symbol_aliases_reference.py) | 测试或测试支持 |
| [backend/tests/test_account_connections.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_account_connections.py) | 测试或测试支持 |
| [backend/tests/test_account_position_providers.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_account_position_providers.py) | 测试或测试支持 |
| [backend/tests/test_account_positions.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_account_positions.py) | 测试或测试支持 |
| [backend/tests/test_alert_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_alert_engine.py) | 测试或测试支持 |
| [backend/tests/test_alert_loop.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_alert_loop.py) | 测试或测试支持 |
| [backend/tests/test_alert_messages.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_alert_messages.py) | 测试或测试支持 |
| [backend/tests/test_alert_metrics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_alert_metrics.py) | 测试或测试支持 |
| [backend/tests/test_alert_rule_migrations.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_alert_rule_migrations.py) | 测试或测试支持 |
| [backend/tests/test_announcement_delayed_delivery.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_announcement_delayed_delivery.py) | 测试或测试支持 |
| [backend/tests/test_announcement_localization.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_announcement_localization.py) | 测试或测试支持 |
| [backend/tests/test_announcement_public_sources.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_announcement_public_sources.py) | 测试或测试支持 |
| [backend/tests/test_announcement_research.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_announcement_research.py) | 测试或测试支持 |
| [backend/tests/test_announcements.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_announcements.py) | 测试或测试支持 |
| [backend/tests/test_api.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_api.py) | 测试或测试支持 |
| [backend/tests/test_arcus_adapter.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_arcus_adapter.py) | 测试或测试支持 |
| [backend/tests/test_astro_alerts.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_astro_alerts.py) | 测试或测试支持 |
| [backend/tests/test_astro_client.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_astro_client.py) | 测试或测试支持 |
| [backend/tests/test_astro_instrument_routes.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_astro_instrument_routes.py) | 测试或测试支持 |
| [backend/tests/test_astro_planner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_astro_planner.py) | 测试或测试支持 |
| [backend/tests/test_astro_preadd.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_astro_preadd.py) | 测试或测试支持 |
| [backend/tests/test_bitget_adapter.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_bitget_adapter.py) | 测试或测试支持 |
| [backend/tests/test_bitget_website_announcements.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_bitget_website_announcements.py) | 测试或测试支持 |
| [backend/tests/test_collector.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_collector.py) | 测试或测试支持 |
| [backend/tests/test_collector_pipeline.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_collector_pipeline.py) | 测试或测试支持 |
| [backend/tests/test_config.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_config.py) | 测试或测试支持 |
| [backend/tests/test_data_filters.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_data_filters.py) | 测试或测试支持 |
| [backend/tests/test_database_snapshot_recovery.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_database_snapshot_recovery.py) | 测试或测试支持 |
| [backend/tests/test_exchange_orderbooks.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_exchange_orderbooks.py) | 测试或测试支持 |
| [backend/tests/test_exchange_resilience.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_exchange_resilience.py) | 测试或测试支持 |
| [backend/tests/test_fat_finger_backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_fat_finger_backtest.py) | 测试或测试支持 |
| [backend/tests/test_feishu_notifier.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_feishu_notifier.py) | 测试或测试支持 |
| [backend/tests/test_floating_watch.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_floating_watch.py) | 测试或测试支持 |
| [backend/tests/test_fs_astro.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_fs_astro.py) | 测试或测试支持 |
| [backend/tests/test_fs_borrow_reference.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_fs_borrow_reference.py) | 测试或测试支持 |
| [backend/tests/test_fs_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_fs_monitor.py) | 测试或测试支持 |
| [backend/tests/test_fs_multi.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_fs_multi.py) | 测试或测试支持 |
| [backend/tests/test_funding_arbitrage.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_arbitrage.py) | 测试或测试支持 |
| [backend/tests/test_funding_research.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research.py) | 测试或测试支持 |
| [backend/tests/test_funding_research_backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research_backtest.py) | 测试或测试支持 |
| [backend/tests/test_funding_research_legacy_backtest.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research_legacy_backtest.py) | 测试或测试支持 |
| [backend/tests/test_funding_research_live_run.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research_live_run.py) | 测试或测试支持 |
| [backend/tests/test_funding_research_paper.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research_paper.py) | 测试或测试支持 |
| [backend/tests/test_funding_research_repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research_repository.py) | 测试或测试支持 |
| [backend/tests/test_funding_research_runner.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_funding_research_runner.py) | 测试或测试支持 |
| [backend/tests/test_gate_crossex_hyperliquid.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_gate_crossex_hyperliquid.py) | 测试或测试支持 |
| [backend/tests/test_history.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_history.py) | 测试或测试支持 |
| [backend/tests/test_history_maintenance.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_history_maintenance.py) | 测试或测试支持 |
| [backend/tests/test_hyperliquid_trade_status.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_hyperliquid_trade_status.py) | 测试或测试支持 |
| [backend/tests/test_index_components.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_index_components.py) | 测试或测试支持 |
| [backend/tests/test_index_watch_filter_equivalence.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_index_watch_filter_equivalence.py) | 测试或测试支持 |
| [backend/tests/test_instrument_lookup.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_instrument_lookup.py) | 测试或测试支持 |
| [backend/tests/test_instrument_market_cap.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_instrument_market_cap.py) | 测试或测试支持 |
| [backend/tests/test_instrument_spreads.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_instrument_spreads.py) | 测试或测试支持 |
| [backend/tests/test_instrument_statistics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_instrument_statistics.py) | 测试或测试支持 |
| [backend/tests/test_instrument_statistics_equivalence.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_instrument_statistics_equivalence.py) | 测试或测试支持 |
| [backend/tests/test_instrument_statistics_routes.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_instrument_statistics_routes.py) | 测试或测试支持 |
| [backend/tests/test_lighter_adapter.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_lighter_adapter.py) | 测试或测试支持 |
| [backend/tests/test_live_pilot.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_live_pilot.py) | 测试或测试支持 |
| [backend/tests/test_market_identity_catalog.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_identity_catalog.py) | 测试或测试支持 |
| [backend/tests/test_market_identity_repository.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_identity_repository.py) | 测试或测试支持 |
| [backend/tests/test_market_identity_routes.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_identity_routes.py) | 测试或测试支持 |
| [backend/tests/test_market_identity_validation.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_identity_validation.py) | 测试或测试支持 |
| [backend/tests/test_market_labels.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_labels.py) | 测试或测试支持 |
| [backend/tests/test_market_session_cache.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_session_cache.py) | 测试或测试支持 |
| [backend/tests/test_market_sessions.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_sessions.py) | 测试或测试支持 |
| [backend/tests/test_market_validation_astro.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_validation_astro.py) | 测试或测试支持 |
| [backend/tests/test_market_validation_review.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_market_validation_review.py) | 测试或测试支持 |
| [backend/tests/test_multi_exchange_premium.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_multi_exchange_premium.py) | 测试或测试支持 |
| [backend/tests/test_negative_basis_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_negative_basis_monitor.py) | 测试或测试支持 |
| [backend/tests/test_oil_news.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_oil_news.py) | 测试或测试支持 |
| [backend/tests/test_okx_announcement_sources.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_okx_announcement_sources.py) | 测试或测试支持 |
| [backend/tests/test_okx_history.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_okx_history.py) | 测试或测试支持 |
| [backend/tests/test_opportunity_radar.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_opportunity_radar.py) | 测试或测试支持 |
| [backend/tests/test_opportunity_radar_alert_loop.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_opportunity_radar_alert_loop.py) | 测试或测试支持 |
| [backend/tests/test_opportunity_trade_availability.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_opportunity_trade_availability.py) | 测试或测试支持 |
| [backend/tests/test_orderbook_validator.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_orderbook_validator.py) | 测试或测试支持 |
| [backend/tests/test_pair_spread_diagnostics.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_pair_spread_diagnostics.py) | 测试或测试支持 |
| [backend/tests/test_pair_spread_presets.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_pair_spread_presets.py) | 测试或测试支持 |
| [backend/tests/test_pair_spread_query.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_pair_spread_query.py) | 测试或测试支持 |
| [backend/tests/test_phone_price_alert_loop.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_phone_price_alert_loop.py) | 测试或测试支持 |
| [backend/tests/test_phone_price_alerts.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_phone_price_alerts.py) | 测试或测试支持 |
| [backend/tests/test_premium_index_query.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_premium_index_query.py) | 测试或测试支持 |
| [backend/tests/test_repositories.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_repositories.py) | 测试或测试支持 |
| [backend/tests/test_risk_labels.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_risk_labels.py) | 测试或测试支持 |
| [backend/tests/test_second_level_sampling.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_second_level_sampling.py) | 测试或测试支持 |
| [backend/tests/test_spread_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_spread_engine.py) | 测试或测试支持 |
| [backend/tests/test_spread_engine_equivalence.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_spread_engine_equivalence.py) | 测试或测试支持 |
| [backend/tests/test_squeeze_arbitrage.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_squeeze_arbitrage.py) | 测试或测试支持 |
| [backend/tests/test_squeeze_paper.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_squeeze_paper.py) | 测试或测试支持 |
| [backend/tests/test_squeeze_routes.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_squeeze_routes.py) | 测试或测试支持 |
| [backend/tests/test_symbol_alias_index.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_symbol_alias_index.py) | 测试或测试支持 |
| [backend/tests/test_symbol_aliases.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_symbol_aliases.py) | 测试或测试支持 |
| [backend/tests/test_symbol_normalization.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_symbol_normalization.py) | 测试或测试支持 |
| [backend/tests/test_trade_availability.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_trade_availability.py) | 测试或测试支持 |
| [backend/tests/test_tradfi_perp_monitor.py](C:/Users/wubin/Desktop/code/codex/taoli1/backend/tests/test_tradfi_perp_monitor.py) | 测试或测试支持 |
| [deploy/backup_retention.py](C:/Users/wubin/Desktop/code/codex/taoli1/deploy/backup_retention.py) | 部署与备份 |
| [deploy/linux-update.sh](C:/Users/wubin/Desktop/code/codex/taoli1/deploy/linux-update.sh) | 部署与备份 |
| [deploy/test_backup_retention.py](C:/Users/wubin/Desktop/code/codex/taoli1/deploy/test_backup_retention.py) | 部署与备份 |
| [docker-compose.local.yml](C:/Users/wubin/Desktop/code/codex/taoli1/docker-compose.local.yml) | 项目约束或构建配置 |
| [docker-compose.prod.yml](C:/Users/wubin/Desktop/code/codex/taoli1/docker-compose.prod.yml) | 项目约束或构建配置 |
| [docker-compose.yml](C:/Users/wubin/Desktop/code/codex/taoli1/docker-compose.yml) | 项目约束或构建配置 |
| [docs/git-delivery-checklist.md](C:/Users/wubin/Desktop/code/codex/taoli1/docs/git-delivery-checklist.md) | 项目约束或构建配置 |
| [docs/linux-deployment.md](C:/Users/wubin/Desktop/code/codex/taoli1/docs/linux-deployment.md) | 项目约束或构建配置 |
| [frontend/Dockerfile](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/Dockerfile) | 项目约束或构建配置 |
| [frontend/nginx.conf](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/nginx.conf) | 项目约束或构建配置 |
| [frontend/package-lock.json](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/package-lock.json) | 项目约束或构建配置 |
| [frontend/package.json](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/package.json) | 项目约束或构建配置 |
| [frontend/tests/AccountConnectionsPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/AccountConnectionsPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/AlertHistoryPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/AlertHistoryPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/AnnouncementsPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/AnnouncementsPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/ApiClient.test.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/ApiClient.test.ts) | 测试或测试支持 |
| [frontend/tests/AppShell.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/AppShell.test.tsx) | 测试或测试支持 |
| [frontend/tests/DashboardPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/DashboardPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/FatFingerBacktestPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/FatFingerBacktestPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/FloatingWatchPanel.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/FloatingWatchPanel.test.tsx) | 测试或测试支持 |
| [frontend/tests/FsMonitorPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/FsMonitorPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/FundingArbitragePreadd.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/FundingArbitragePreadd.test.tsx) | 测试或测试支持 |
| [frontend/tests/IndexComponentChangesPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/IndexComponentChangesPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/InstrumentLookupPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/InstrumentLookupPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/MarketIdentityPanel.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/MarketIdentityPanel.test.tsx) | 测试或测试支持 |
| [frontend/tests/NegativeBasisMonitorPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/NegativeBasisMonitorPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/OilNewsPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/OilNewsPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/OpportunityRadarPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/OpportunityRadarPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/OpportunityTable.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/OpportunityTable.test.tsx) | 测试或测试支持 |
| [frontend/tests/PairMonitorPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/PairMonitorPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/PhonePriceAlertsPanel.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/PhonePriceAlertsPanel.test.tsx) | 测试或测试支持 |
| [frontend/tests/PremiumIndexPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/PremiumIndexPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/SecondLevelSamplingPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/SecondLevelSamplingPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/SettingsPage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/SettingsPage.test.tsx) | 测试或测试支持 |
| [frontend/tests/SqueezeArbitragePage.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/SqueezeArbitragePage.test.tsx) | 测试或测试支持 |
| [frontend/tests/SqueezePaperView.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/SqueezePaperView.test.tsx) | 测试或测试支持 |
| [frontend/tests/SqueezeRoutesView.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/SqueezeRoutesView.test.tsx) | 测试或测试支持 |
| [frontend/tests/pairRatio.test.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/pairRatio.test.ts) | 测试或测试支持 |
| [frontend/tests/setup.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/setup.ts) | 测试或测试支持 |
| [frontend/tests/useRadarStore.test.tsx](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tests/useRadarStore.test.tsx) | 测试或测试支持 |
| [frontend/tsconfig.json](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/tsconfig.json) | 项目约束或构建配置 |
| [frontend/vite.config.ts](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/vite.config.ts) | 项目约束或构建配置 |
| [script/analyze_gigadev_ah_perp.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/analyze_gigadev_ah_perp.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/benchmark_collector_pipeline.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/benchmark_collector_pipeline.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/benchmark_history_maintenance.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/benchmark_history_maintenance.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/benchmark_instrument_statistics.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/benchmark_instrument_statistics.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/benchmark_market_preprocessing.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/benchmark_market_preprocessing.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/benchmark_spread_engine.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/benchmark_spread_engine.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/gate_twap_sell.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/gate_twap_sell.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/minute_basis_signal_framework.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/minute_basis_signal_framework.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [script/test_minute_basis_signal_framework.py](C:/Users/wubin/Desktop/code/codex/taoli1/script/test_minute_basis_signal_framework.py) | 离线基准或独立研究工具，运行前检查副作用 |
| [frontend/index.html](C:/Users/wubin/Desktop/code/codex/taoli1/frontend/index.html) | HTML 挂载入口 |
