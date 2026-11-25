# 手机 App P8（后端）：曲线区间估算接口（2026-11-25）

App 曲线上拖选一段时间，估算“区间开头开仓、结尾平仓”的盈亏，复用网站已有的区间回测 `POST /api/pair-spread/range-backtest`（`routes_pair_spread.backtest_pair_spread_range` → `PairSpreadQueryService.query_range_backtest`）。

## 允许修改
`backend/app/api/routes_mobile.py`、`backend/app/models/mobile.py`、`backend/app/core/security.py`（白名单只加 `("POST", "/api/mobile/range-backtest")`）、新测试 `backend/tests/test_mobile_range_backtest.py`。不改其他文件，不放宽已有测试。

## 接口
`POST /api/mobile/range-backtest`，`Depends(require_session)`，body：
`{card_id: str(同 P5 校验), start_at: AwareDatetime, end_at: AwareDatetime, notional_usd: float | null}`
- 卡片：用 `list_astro_pairs`/缓存列表按 id 找，找不到 404「未找到卡片」。
- 两腿：与 `_history_for_pair` 完全相同的方式（`legs_for` + `_resolved_leg`，HL 腿 dex 默认 main）得到 leg1=A 腿、leg2=B 腿；识别失败 422。
- 方向固定 `long1_short2`（Astro 卡片 A 腿买入、B 腿卖出）。
- `notional_usd` 为空时：卡片有持仓用其持仓名义金额（与 summary 的 `notional_usdt` 同口径，取不到则 1000），否则 1000；范围 (0, 1e7]。
- 时间：`end_at > start_at`，区间 ≥ 1 分钟、≤ 72 小时，且不晚于现在；否则 422「区间无效」。
- 调 `query_range_backtest`，60 秒超时 → 504「区间估算超时」；`PairSpreadQueryError` → 502；手续费设置同网站接口。
- 返回网站同款 `PairRangeBacktestResult`，另加 `notional_usd`、`direction`、`leg1_label`、`leg2_label`（如 `gc-binance ANTHROPIC`）。可新建一个继承模型。
- 限频：每个会话同时最多 1 个进行中（第二个 429「上一次估算还在进行」），每分钟最多 6 次（429）。内存实现。
- 422 不回显输入（参照 `_MobileInputRoute`）。

## 测试（先写）
找不到卡 404；区间校验各种 422；默认金额（有持仓/无持仓）；方向与两腿传参正确（mock service 断言收到的请求）；超时 504；限频 429（并发、每分钟）；只读会话可访问、其他非白名单 POST 仍 403。

## 验收
`pytest backend/tests/test_mobile_range_backtest.py backend/tests/test_mobile_card_control.py backend/tests/test_mobile_pins.py -q --basetemp=<scratchpad>`，再跑全量 backend。`git commit -m "feat(mobile): range backtest endpoint for chart selection (P8)"`，不要 push。报告改动文件、测试命令与汇总行、未完成项。
