# P18 手机资金费率套利接口（后端，2026-11-26）

设计稿：`D:/吴彬工作/claude/套利/research/mobile-app/feature-port-design.md` 第三节“资金费率套利”。**全只读**；参考 P16 `routes_mobile_opportunities.py`、P17 `routes_mobile_instruments.py`、`mobile_query_utils.py`。

## 范围
- 可改：新建 `backend/app/api/routes_mobile_funding.py`（在 `routes_mobile.py` include）、`backend/app/models/mobile.py`、新测试文件；如需复用限频可改 `mobile_query_utils.py`（已有行为不变）。
- 不可改：`routes_funding_arbitrage.py`、`app/services/funding_arbitrage.py` 计算逻辑、设置读写、预加仓/建卡相关代码、网页接口、前端。
- **绝不暴露**：`PUT /funding-arbitrage/settings`、任何预加仓（preadd）/建卡/run 接口。手机端不新增任何写接口。

## 接口
`GET /api/mobile/funding-arbitrage/preview?decision=&type=&symbol=&limit=`
- **复用网页 `get_funding_arbitrage_preview` 的同一实现**（直接调用），候选和计数与网页完全一致，不复制计算。
- 手机侧过滤（在网页结果上做，不改排序）：`decision`（ENTER/HOLD/EXIT_SOON/EXIT_NOW/BLOCKED，可选）、`type`（SF/FF，可选）、`symbol`（可选，大小写不敏感的子串匹配）、`limit`（1–200，默认 100）。
- 返回 `MobileFundingPreview{observed_at, settings, total_pairs_evaluated, displayed_candidates, blocked_missing_funding, blocked_liquidity, blocked_adl_risk, blocked_expected_pnl, enter_count, hold_count, exit_count, blocked_count, filtered_total, candidates[]}`。`filtered_total` 是手机侧过滤后、截断前的条数；计数字段原样来自网页结果。
- `settings` 只返回展示用的只读子集：`enabled, max_candidates, min_entry_edge_pct, min_funding_edge_pct, min_volume_24h_usdt, min_minutes_to_settlement, max_minutes_to_settlement, notional_per_symbol_usdt, leverage`。
- candidates 每项精简为：`id, symbol, type, long_exchange, long_market_type, long_raw_symbol, short_exchange, short_market_type, short_raw_symbol, funding_source, long_current_funding_pct, short_current_funding_pct, long_next_funding_pct, short_next_funding_pct, current_funding_edge_pct, next_funding_edge_pct, long_funding_interval_hours, short_funding_interval_hours, funding_comparison_interval_hours, next_settlement_time, minutes_to_settlement, entry_basis_pct, exit_basis_pct, basis_width_pct, expected_cycle_pnl_pct, estimated_open_cost_pct, estimated_close_cost_pct, adl_risk_score, adl_risk_level, decision, decision_reasons, risk_labels, primary_opportunity_type, opportunity_types, opportunity_reasons, volume_24h_usdt, depth_usdt, uses_hyperliquid`。顺序同网页。
- 限频：按会话同一时刻 1 个在途、两次间隔 ≥3 秒 → 429 固定中文文案；名额与 P13/P15/P16/P17 分开。
- 错误：参数非法 422；设置库未就绪或内部失败 503 固定中文文案“资金费率数据暂不可用”，不泄露异常。
- mobile_readonly 会话可访问 preview；网页 `/api/funding-arbitrage/*`（含 settings GET/PUT）对 mobile_readonly 仍 403。

## 验收
- 新增 `backend/tests/test_mobile_funding.py`：与网页 preview 结果对应（同输入对比 id 顺序和计数字段）、settings 子集、decision/type/symbol 过滤与 filtered_total、limit 截断、429 与名额独立、422、503 不泄露、权限（mobile_readonly 访问网页 settings GET/PUT 均 403，且手机路由下不存在任何 PUT/POST）。
- `python -m pytest tests -k "mobile or funding" --basetemp <scratchpad> -q` 全过，再跑全量输出汇总行。
- **只 commit：禁止 git push、禁止 ssh/scp、禁止部署或操作服务器，忽略 AGENTS.md 里的推送与部署步骤。** 不跑 WSL。commit `feat(mobile): read-only funding arbitrage preview (P18)`；输出 `git diff --name-only HEAD~1`、pytest 汇总行和 MobileFunding* 模型完整字段清单。
