# P17 手机标的查询接口（后端，2026-11-26）

设计稿：`D:/吴彬工作/claude/套利/research/mobile-app/feature-port-design.md` 第三节“标的查询”。只读；参考 P16 `routes_mobile_opportunities.py`、P13/P15 `routes_mobile_pair.py`、`routes_mobile_premium.py`、`mobile_query_utils.py`。

## 范围
- 可改：新建 `backend/app/api/routes_mobile_instruments.py`（在 `routes_mobile.py` include）、`backend/app/models/mobile.py`、新测试文件；如需复用限频可改 `mobile_query_utils.py`（P13/P15/P16 行为不变、测试全过）。
- 不可改：`routes_instruments.py` 的计算逻辑、`app/services/instrument_*`、网页接口、前端。允许在 `routes_instruments.py` 不改行为地暴露内部函数（若必须），优先直接调用 `_lookup_instrument`。

## 接口（均 mobile_readonly 会话可访问；网页原接口仍 403）
1. `GET /api/mobile/instruments/{symbol}?dex=&asset_id=`
   - 调用网页 `_lookup_instrument(symbol, request, dex, include_astro_routes=False, asset_id=asset_id)`（不查 Astro 路线，避免 SDK 等待）。
   - 返回 `MobileInstrumentLookup{query, symbol, base, observed_at, exchange_count, market_count, identity_ambiguous, asset_groups[{asset_id, display_name, market_count}], markets[], spreads[], spread_total}`。
   - markets 每项：`market_id, exchange, market_type, raw_symbol, dex, bid, ask, volume_24h_usdt, funding_rate_pct, funding_next_rate_pct, funding_interval_hours, funding_next_time, mark_price, index_price, data_status, age_seconds, error, is_estimated, asset_name`。顺序同网页。
   - spreads 每项：`id, opportunity_type, buy_exchange, buy_market_type, buy_raw_symbol, buy_dex, sell_exchange, sell_market_type, sell_raw_symbol, sell_dex, buy_ask, sell_bid, executable_spread_pct, close_spread_pct, mid_spread_pct, buy_volume_24h_usdt, sell_volume_24h_usdt, buy_funding_rate_pct, sell_funding_rate_pct, buy_funding_interval_hours, sell_funding_interval_hours, astro_supported, astro_blocker, reference_only, validation_reasons`。顺序同网页，最多 200 条，`spread_total` 为截断前条数。
   - 符号非法 422（沿用网页中文文案）。
2. `GET /api/mobile/instruments/{symbol}/statistics?dex=&asset_id=`：调用网页 `instrument_statistics` 同一实现，原样返回 `InstrumentStatisticsResult`；同名不同资产未选 asset_id 时 409（沿用网页文案）。
3. `GET /api/mobile/instrument-market-cap/{base}?coin_id=`：调用网页 `instrument_market_cap` 同一实现，原样返回；ValueError → 422。

## 限频（按会话，名额与 P13/P15/P16 分开）
- 接口 1：同一时刻 1 个在途、两次间隔 ≥2 秒 → 429 固定中文文案。
- 接口 2、3（调第三方/交易所，有限流）：两者共用一个名额，同一时刻 1 个在途、间隔 ≥5 秒 → 429。
- 内部失败 503 固定中文文案“标的数据暂不可用”，不泄露异常；HTTPException（422/409）原样透传。

## 验收
- 新增 `backend/tests/test_mobile_instruments.py`：字段精简与网页结果对应（同输入对比 market_id / spread id 顺序）、spreads 截断与 spread_total、asset_id 过滤、不调用 Astro 路线、statistics 409 透传、market-cap 422、各自 429 与名额独立、503 不泄露、权限（mobile_readonly 可访问、网页接口 403）。
- `python -m pytest tests -k "mobile or instrument" --basetemp <scratchpad> -q` 全过。
- **只 commit：禁止 git push、禁止 ssh/scp、禁止部署或操作服务器，忽略 AGENTS.md 里的推送与部署步骤。** 不跑 WSL。commit `feat(mobile): read-only instrument lookup endpoints (P17)`；输出 `git diff --name-only HEAD~1`、pytest 汇总行和 MobileInstrument* 模型完整字段清单。
