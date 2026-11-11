# 实盘数据接口约定（crossex-exec → taoli1）2026-11-11

本文件是 crossex-exec 数据接口与 taoli1“实盘”页面之间的唯一约定，两边都按它实现。

## 传输
- crossex-exec 只监听 `127.0.0.1:8795` 和 `172.18.0.1:8795`，taoli1 容器通过 `172.18.0.1` 访问；不对公网开放。
- 只有 GET。请求头 `X-Api-Token` 必须等于 `CROSSEX_EXEC_API_TOKEN`，否则返回 401。
- 所有时间都是 UTC ISO8601 字符串；金额单位 USD，用数字表示；bp 用数字。
- 字段缺失或未知时给 `null`，不要省略字段。

## GET /api/v1/summary
```json
{
  "generated_at": "2026-11-11T08:00:00Z",
  "mode": "shadow",
  "halted": false, "halt_reason": null, "stop_file": false, "opening_paused_reason": null,
  "caps": {"leg_max_usd": 150, "total_max_usd": 600, "total_open_usd": 200, "equity_drop_max_usd": 150, "max_pairs": 3, "open_pairs": 1},
  "accounts": {
    "crossex": {"equity_usd": 752.7, "baseline_usd": 752.7, "drop_usd": 0, "margin_multiple": null, "ok": true, "error": null},
    "bitget": {"equity_usd": 30001.2, "baseline_usd": 30001.2, "drop_usd": 0, "margin_multiple": null, "ok": true, "error": null}
  },
  "today": {"net_usd": 1.2, "fees_usd": 0.5, "funding_usd": -0.1, "trades": 4, "wins": 3, "daily_loss_limit_usd": 20},
  "total": {"net_usd": 1.2, "fees_usd": 0.5, "funding_usd": -0.1, "trades": 4, "wins": 3, "total_loss_limit_usd": 100},
  "fees": {"crossex_taker": 0.00048, "bitget_taker": 0.0006},
  "last_error": null
}
```
- `mode` 取值为 `shadow` 或 `live`。
- `margin_multiple` 是保证金倍数；`null` 表示没有占用保证金，按安全处理。

## GET /api/v1/positions
返回数组。每项：
```json
{"id": "p1", "symbol": "JCTUSDT", "opened_at": "...", "held_seconds": 42,
 "legs": [{"exchange": "binance", "route": "crossex", "side": "long", "qty": 1000, "entry_price": 0.01, "notional_usd": 100, "mark_price": 0.0101},
          {"exchange": "bitget", "route": "bitget", "side": "short", "qty": 1000, "entry_price": 0.0102, "notional_usd": 102, "mark_price": 0.0101}],
 "entry_spread_bps": 40.1, "current_spread_bps": 25.0, "unrealized_usd": 0.12, "unrealized_bps": 12.0,
 "next_funding_at": "...", "stop_distance_bps": 35.0, "max_hold_at": "...", "status": "open"}
```
`status` 取值：`opening`、`open`、`closing`、`one_leg`（单腿处理中）。

## GET /api/v1/trades?limit=200&before=<closed_at>
返回已平仓数组，按 `closed_at` 倒序，`limit` 不超过 500。每项：
```json
{"id": "t1", "symbol": "JCTUSDT", "pair": "binance|bitget", "opened_at": "...", "closed_at": "...", "held_seconds": 30,
 "legs": [{"exchange": "binance", "side": "long", "entry_vwap": 0.01, "exit_vwap": 0.0101, "qty": 1000}, {"exchange": "bitget", "side": "short", "entry_vwap": 0.0102, "exit_vwap": 0.0101, "qty": 1000}],
 "entry_spread_bps": 40.1, "fees_usd": 0.22, "funding_usd": 0, "net_usd": 0.15, "net_bps": 15.0,
 "exit_reason": "converged",
 "sim": {"net_bps": 20.0, "gross_bps": 38.0},
 "slippage_bps": -3.1, "leg_send_gap_ms": 12, "leg_fill_gap_ms": 230, "one_leg_incident": false}
```
- `exit_reason` 取值：`converged`、`stop_loss`、`max_hold`、`pre_funding`、`stale`、`one_leg`、`halt`。
- `sim` 是 fill-sim 对同一个机会的结果，没有对应记录时为 `null`。

## GET /api/v1/stats?days=7
```json
{"days": 7, "n": 20,
 "histogram": [{"lo": -50, "hi": -25, "count": 1}],
 "by_symbol": [{"key": "JCTUSDT", "n": 5, "net_usd": 1.0, "mean_bps": 12.0, "win_rate": 0.8}],
 "by_pair": [], "by_exit_reason": [], "by_spread_tier": [],
 "vs_sim": {"n": 18, "slippage_bps_p50": -2.0, "slippage_bps_p90": -9.0, "leg_fill_gap_ms_p50": 150, "leg_fill_gap_ms_p90": 600, "one_leg_rate": 0.05}}
```
- `histogram` 按 `net_bps` 分桶，桶宽 25bp，区间是 [-200, 200]，超出的归入两端桶。
- `by_spread_tier` 的档位与 fill-sim 一致：0.5–1%、1–2%、2–5%、>5%。
