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
  "baseline_ready_pairs": 12, "structural_spread_pairs": 2, "cooldown_pairs": 1,
  "opening_skip_counts": {"book_skew": 2, "book_stale": 1, "funding_diff": 3, "baseline_not_flat": 4, "not_single_venue_outlier": 5, "too_few_venues": 6},
  "last_error": null
}
```
- `mode` 取值为 `shadow` 或 `live`。
- `margin_multiple` 是保证金倍数；`null` 表示没有占用保证金，按安全处理。
- 第六轮新增三个整数计数，按「基础币 + 无方向交易所组合」去重：`baseline_ready_pairs` 为最近 6 小时至少 300 点、样本跨度至少 3 小时、最新样本及状态均不超过 90 秒的组合数（包含其中被拉黑或冷却的组合）；`structural_spread_pairs` 为当前锁存的结构性拉黑组合数，只有重新取得有效预热数据并到达 10 分钟评估时点才能解除；`cooldown_pairs` 为冷却截止时间仍晚于当前时间的组合数。无记录为 0。三项可能重叠，不表示可立即开仓，原有全局风控及交易对检查继续适用。
- 第七轮新增 `opening_skip_counts`，包含示例中的全部六个整数键，缺记录为 0。统计启用第七轮后该执行数据库内累计放弃的机会数（含 shadow），跨 UTC 日界线及重启保留。按触发机会 ID 去重，初始复核或共享发送前复核最终失败才计一次；时间检查重拉成功不计数，两个发送线程不会重复计数。不回填升级前的历史事件，也不代替成交/胜率统计。多个条件同时失败时只计首先判定的原因；时间检查同时陈旧和不同步时优先 `book_stale`。

## GET /api/v1/positions
返回数组。每项：
```json
{"id": "p1", "symbol": "JCTUSDT", "opened_at": "...", "held_seconds": 42,
 "legs": [{"exchange": "binance", "route": "crossex", "side": "long", "qty": 1000, "entry_price": 0.01, "notional_usd": 100, "mark_price": 0.0101},
          {"exchange": "bitget", "route": "bitget", "side": "short", "qty": 1000, "entry_price": 0.0102, "notional_usd": 102, "mark_price": 0.0101}],
 "entry_spread_bps": 40.1, "current_spread_bps": 25.0, "unrealized_usd": 0.12, "unrealized_bps": 12.0,
 "baseline_bps": 20.0, "excess_bps": 20.1, "funding_cost_bps": 1.0,
 "open_book_skew_ms": 250, "close_book_skew_ms": null,
 "next_funding_at": "...", "stop_distance_bps": 35.0, "max_hold_at": "...", "status": "open"}
```
`status` 取值：`opening`、`open`、`closing`、`one_leg`（单腿处理中）。

第六轮新增的 `baseline_bps`、`excess_bps`、`funding_cost_bps` 都是开仓复核时冻结的快照，单位 bp。前两项分别来自持仓的 `baseline_at_open`、`excess_at_open`；`excess_bps = 开仓目标数量盘口 VWAP 价差 − 该方向基准`，尚未扣费用。`baseline_bps` 保留 long→short 的方向，反向按原始价格比的倒数计算，不简单取负。`funding_cost_bps` 为当前费率按两腿各自实际结算秒数折算每小时、合计应付款取非负后乘 2 小时的预估成本。它不是实际已付资金费，不替代 `funding_usd` 或损益字段。历史记录缺少快照时三项保留 `null`，不事后用当前基准补写。

第七轮新增 `open_book_skew_ms`、`close_book_skew_ms`，均为两腿用于定价的盘口时间戳绝对差，单位毫秒，使用交易所时间戳；只有无交易所时钟的接口才使用收到时间。当前六所永续深度均有交易所时间戳，开仓响应缺失该字段时拒绝，不能伪装成接收时间回退。开仓值取最后成功的共享发送前复核（shadow 取初始复核）；若重拉更换盘口，保存新值及重新计算的超额估算。平仓值取最近一次减仓定价使用的两腿盘口；单腿补平或行情缺失时可能包含另一腿的自身缓存，故可能远超 300ms。尚未减仓、时间信息未知或旧记录均为 `null`。这两个字段是行情时间差，不是订单发送/成交时间差；平仓不受第七轮时间或模式准入限制。示例中第六轮的基准数值继续兼容历史持仓，不表示第七轮允许新开这样的仓位。

## GET /api/v1/trades?limit=200&before=<closed_at>
返回已平仓数组，按 `closed_at` 倒序，`limit` 不超过 500。每项：
```json
{"id": "t1", "symbol": "JCTUSDT", "pair": "binance|bitget", "opened_at": "...", "closed_at": "...", "held_seconds": 30,
 "legs": [{"exchange": "binance", "side": "long", "entry_vwap": 0.01, "exit_vwap": 0.0101, "qty": 1000}, {"exchange": "bitget", "side": "short", "entry_vwap": 0.0102, "exit_vwap": 0.0101, "qty": 1000}],
 "entry_spread_bps": 40.1, "fees_usd": 0.22, "funding_usd": 0, "net_usd": 0.15, "net_bps": 15.0,
 "baseline_bps": 20.0, "excess_bps": 20.1, "funding_cost_bps": 1.0,
 "open_book_skew_ms": 250, "close_book_skew_ms": 2300,
 "exit_reason": "converged",
 "sim": {"net_bps": 20.0, "gross_bps": 38.0},
 "slippage_bps": -3.1, "leg_send_gap_ms": 12, "leg_fill_gap_ms": 230, "one_leg_incident": false}
```
- `exit_reason` 取值：`converged`、`stop_loss`、`max_hold`、`pre_funding`、`stale`、`one_leg`、`halt`。
- `sim` 是 fill-sim 对同一个机会的结果，没有对应记录时为 `null`。
- 第六轮新增的三个 bp 字段与 `/positions` 含义相同，持仓关闭后继续保留开仓快照。`entry_spread_bps` 仍来自实际开仓成交，`net_usd/net_bps` 仍为实际已知损益，均不被预估超额价差替换。
- 第七轮两个盘口时间差字段与 `/positions` 含义相同，关闭后保留；缺失值保留 `null`，不从订单发送时间或当前行情倒推。

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

## 第八轮扩展状态（2026-11-12，待实现）

第八轮任务书提出参数、白名单候选、代码映射确认和独立管理令牌写接口；**本次尚未实现或开放这些接口**，当前实现仍是上面的第七轮契约。交接文档“第八轮交付”记录了新增 summary 字段与旧完整字典断言的冲突，按用户要求等待 PM 决定。本节只追加状态说明。

待实现接口为 `GET/PUT /api/v1/config`、`GET /api/v1/config/audit`、`GET /api/v1/universe`、`POST /api/v1/universe/{base}/enable|exclude`、`GET /api/v1/mappings`、`POST /api/v1/mappings/{id}/confirm|reject`。写操作使用与只读令牌不同的 `CROSSEX_EXEC_ADMIN_TOKEN`；参数越界原子拒绝并返回 400，只读令牌调用写接口返回 403。正式实现时补齐请求/响应、版本和审计字段定义。

第八轮拟在现有 `opening_skip_counts` 中追加 `size_too_small`、`oi_missing`、`mapping_ratio_mismatch`，旧六项保留，新项缺记录为 0，按触发机会去重并跨重启保留。已有六项完整字典断言尚未获准适配，因此此扩展目前只有两个先失败测试，尚未进入生产实现。
