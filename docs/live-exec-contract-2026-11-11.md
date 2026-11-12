# 实盘数据接口约定（crossex-exec → taoli1）2026-11-11

本文件是 crossex-exec 数据接口与 taoli1“实盘”页面之间的唯一约定，两边都按它实现。

## 传输
- crossex-exec 只监听 `127.0.0.1:8795` 和 `172.18.0.1:8795`，taoli1 容器通过 `172.18.0.1` 访问；不对公网开放。
- 原四个业务接口仍仅支持 GET；第八轮增加下文管理写接口。`X-Api-Token` 使用只读 `CROSSEX_EXEC_API_TOKEN` 或独立管理 `CROSSEX_EXEC_ADMIN_TOKEN`。无效令牌 401，只读令牌写入 403。
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

## 第八轮接口（2026-11-12，本地实现，未部署）

本节取代之前的待实现提案，原四个 GET 路由及原字段全部保留。验收见 [第八轮交接](task-handoff-2026-11-12-crossex-exec-r8.md)。taoli1 页面和转发属于 `live-exec-page` 独立任务；线上未安装本轮改动。

### 配置与审计

- `GET /api/v1/config` 返回 `{version, values, fallback, schema}`。version 是单调递增整数，初值 0；values 是完整当前参数；fallback=true 表示参数不可读而使用默认值，执行器另写 CONFIG_DEFAULTS_ALERT。schema 每项包含 default/min/max/integer，max=null 表示无代码上限。
- `PUT /api/v1/config` body 为非空部分更新对象，例如 `{"order_usd_max":30,"min_quote_volume_usd":2000000}`，返回生效后的 `{version,values,fallback}`。未知参数、越界、bool、字符串数值、非有限数、整数参数的非整数、重复 JSON 键均 400。配置与审计在同一 SQLite 事务提交，不钳制、不部分生效。
- `GET /api/v1/config/audit` 返回最近最多 500 条，从旧到新排列。每项 `{version,at,source,old,new}`，at 为 UTC ISO8601，source=admin_api；old/new 只含本次提交的参数。

| 参数 | 默认 | 范围 |
|---|---:|---|
| min_quote_volume_usd | 1000000 | ≥100000 |
| min_venues_with_volume | 2 | 整数 ≥2；超过可用所数时不开仓 |
| sampling_min_quote_volume_usd | 200000 | ≥0 |
| order_usd_max | 50 | [5,150] |
| depth_fraction | 0.20 | [0.01,0.50] |
| oi_fraction | 0.0005 | [0.00001,0.005] |
| max_total_open_usd | 600 | [0,600]，双腿名义额之和 |
| max_open_pairs | 3 | 整数 [0,3] |
| max_equity_drop_usd | 150 | [10,150]，任一账户权益回撤 |
| daily_loss_stop_usd / total_loss_stop_usd | 20 / 100 | [1,20] / [1,100] |
| min_excess_bps | 15 | ≥15 |
| max_book_skew_ms | 300 | [0,300] |
| max_book_age_seconds | 1 | [0,1] |
| max_funding_diff_bps_h | 0.5 | [0,0.5] |
| max_funding_rate_8h | 0.0005 | [0,0.0005]，比例 |
| max_baseline_bps / max_baseline_mad_bps | 10 / 10 | 各 [0,10] |
| max_normal_deviation_bps | 10 | [0,10] |
| max_reference_age_seconds | 90 | [0,90] |
| min_outlier_share | 0.7 | [0.7,1] |
| min_venues | 3 | 整数 [3,6] |

每次开仓冻结完整配置与版本，发送共享关口核对同一版本。配置变化使尚未获准的新仓拒绝，两腿均不发送；已通过共享关口的批次沿用获准版本，后续修改对下一批次生效。管理写入与关口复核共用数据库路径锁，不会只影响一条腿。

已有仓位保留开仓版本。账户级三项损失阈值按活动仓位冻结值取最严格值；空仓用当前配置，升级前无快照的持仓用原默认值。代码/环境硬上限继续叠加。新版本比旧仓严格且当前账户已超过新门槛时，拒绝新仓，不借修改参数重写旧仓策略。日亏停开锁、累计亏损/独立权益回撤全平停机、保证金等原规则保留；没有新增解锁接口。

### 交易范围

`GET /api/v1/universe` 返回数组，每项：

```json
{"base":"SKHYNIX","category":"listed_equity","excluded":false,"underlying_market":"KRX",
 "markets":[{"market_key":"binance|future|SKHYNIXUSDT","raw_symbol":"SKHYNIXUSDT","exchange":"binance",
 "category":"listed_equity","effective_category":"listed_equity","underlying_market":"KRX",
 "volume_usd":3000000,"volume_basis":"quote_volume","at":"2026-11-12T00:00:00Z",
 "selected":true,"eligible":true,"needs_classification":false}]}
```

- market.category 是单所目录分类，effective_category 为人工判断后的类别。eligible 只代表类别与所选择允许参与，不代表全部开仓风控已通过。selected 只表示人工选择，自动 crypto 可以 eligible=true、selected=false。
- needs_classification 表示还需人工判断。未知永不默认 crypto。股票/ETF 必须选 US 或 KRX，只选定同一标的的精确所；未选中的所也不能充当第三家乌龙指参考；不加交易时段门禁。
- volume_usd 是该精确合约 24h USDT 报价成交额，超过 300 秒或缺失为 null。OKX 的 volume_basis=base_times_low24h_lower_bound，值为 volCcy24h×low24h 保守下界；其他为 quote_volume。
- pre_ipo 和任务指定的 OPENAI/ANTHROPIC 不进入候选。commodity 不交易，PAXG/XAUT/XAU/XAG 不能按普通 crypto 通过。未知币可采样预热，确认类别前不开仓。
- `POST /api/v1/universe/{base}/exclude|unexclude` body 为 `{}`，返回当前决策对象。排除原生及公共 base；取消排除不自动确认类别或映射。
- `POST /api/v1/universe/{base}/classify` 的股票 body 示例：

```json
{"category":"listed_equity","underlying_market":"KRX",
 "markets":["binance|future|SKHYNIXUSDT","bitget|future|SKHYNIXUSDT"]}
```

人工 crypto 用 `{"category":"crypto","markets":[...]}`；人工标记不交易用 pre_ipo 或 commodity。非股票类别不接受 underlying_market。markets 必须是该精确 base 已存在且无重复的目录键。不能把已识别 pre_ipo/commodity 或已识别股票改为 crypto 来启用。返回决策对象，另写 UNIVERSE_CHANGED 审计。

两条腿都须达到门槛；min_venues_with_volume>2 时，只统计同类别/股票市场/已确认映射的所，一所一次。不在股票选择内、排除、未知类别、未确认映射均不凑家数。CROSSEX_BASES 非空仅为额外开仓限制，空值不限制；采样不受该兼容名单限制。

### 映射

- `GET /api/v1/mappings` 返回可用候选数组，每项 `{id,base,legs,status,at,ratio}`；at 为 UTC ISO8601，ratio 为发现时排序后第一腿原生中间价除以第二腿原生中间价。legs 按 market_key 排序，每项 `{market_key,raw_base,quantity_multiplier}`；倍率为数字，一个原生数量单位对应该倍数的公共基础币。
- `POST /api/v1/mappings/{id}/confirm|reject` body 为 `{}`，返回同 schema 的映射；缺失、不可用或候选超过 300 秒为 400。状态持久化并写 MAPPING_CHANGED，只有 confirmed 能新开仓。
- 前缀 1000/10000/100000/1000000/1M/K 与价格比例须相符，偏差 >1% 不生成候选。KO≠1000×O，KAT≠K×AT。开仓和共享发送关口使用新鲜盘口再次复核，偏差 1.01% 拒绝。
- 公共数量=原生数量×倍率，公共价格=原生价格÷倍率。下单、实仓、流水、补平、重启、平仓和 API 全链路保留原始身份与该换算。已持仓不会因之后拒绝映射而失去减仓能力。

### 定量、观测字段与计数

每腿名义预算取 `min(order_usd_max, depth_fraction×较薄腿容忍带内深度, oi_fraction×有效腿最小OI, 剩余总额度分配)`，再按两腿公共步长公倍数向下取整。剩余额度按两腿最终限价分配，双腿之和不得突破总额。最终任一腿低于最小数量或最小名义额即 size_too_small，不向上补步长。

OI 必须单位可核实、匹配原生市场且不超过 300 秒。PM 裁决允许只用一条有效腿；两腿都缺失才 oi_missing。Aster 无已核实来源时为缺失。该规则不跳过深度、手续费、成交额、余额或硬上限；各所字段证据见交接。

summary 在原六项 opening_skip_counts 上追加 size_too_small/oi_missing/mapping_ratio_mismatch/volume_too_low/volume_unknown/category_unconfirmed，均缺省 0，触发 ID 去重，初始或共享关口失败只计一次。另加 config_version、effective_risk_limits（三项账户损失门槛）。today/total 的损失限额反映生效值；原 caps 的 max 字段继续表示硬上限。

positions/trades 新增以下字段，旧记录未知为 null（turnovers_usd 为两元素 null 数组）：

```json
{"config_version":2,"config_at_open":{"order_usd_max":50},
 "turnovers_usd":[4999999,9000000],
 "sizing":{"quantity":0.49,"depth_usd":[1000,1020],"oi_usd":[10000000,null],
 "constraints":{"order_usd_max":50,"depth":200,"oi":5000,"remaining_total":302.8},
 "remaining_total_usd":600,"budget_usd":50,"limiting":"order_usd_max"}}
```

config_at_open 实际包含全部参数，示例缩写。sizing 为定量快照，limiting 取 order_usd_max/depth/oi/remaining_total；USD 与数量均为数字，深度/OI 是估算依据。turnovers_usd 按 long/short 顺序保留开仓读取值。

legs 追加 raw_symbol/native_base/quantity_multiplier/mapping_id/native_qty/native_entry_price；trades 另加 native_exit_vwap。原 qty、entry_price、entry_vwap、exit_vwap 为公共基础币口径，native_* 为原生口径；未映射倍率为 1，原字段数值不变。symbol 为确认后的公共 base，实际费用和净损益不因展示换算而改变。

### 管理请求边界

写入仅接受独立且不同于只读令牌的 CROSSEX_EXEC_ADMIN_TOKEN。未设置则没有写权限，同令牌配置拒绝启动 API。写 body 必须 Content-Type: application/json、Content-Length 1–8192 字节；拒绝重复长度头、Transfer-Encoding、重复 JSON 键、非法 UTF-8 与非对象。非法输入 400，未认证 401，只读令牌写 403，不支持方法 405，数据库不可用/繁忙 503。

GET 使用独立只读连接，2 秒与 8 MiB 读取预算保留；写操作另开连接并原子提交。旧四个业务路由继续拒绝非 GET。管理请求不调用真实账户或交易接口。taoli1 转发的会话、CSRF 和私有令牌配置由另一任务实现。

## 第九轮增补（2026-11-12）

本节只增加接口、字段和限制。原四个 GET、参数范围、ADMIN/只读令牌隔离及请求大小边界全部保留。下面的账户管理动作是第八轮“管理请求不调用真实账户”说明的明确增补：仅新 `equity/rebaseline` 路由会重新读取两边账户及未完成订单，所有交易所请求均为 GET；不下单、撤单、划转或修改账户设置。此路由绑定正在运行的 Engine，由协调器串行执行，不能使用另一个数据库连接绕过执行状态检查。

### POST /api/v1/equity/rebaseline

仅接受独立 `CROSSEX_EXEC_ADMIN_TOKEN`，body 必须恰好包含两个键：

```json
{"confirm":true,"reason":"verified_transfer"}
```

`confirm` 必须是 JSON 布尔值 true，数字 1 和字符串 "true" 均不接受；reason 必须为非空文字，最长 200 字符，不允许控制字符。页面调用应先显示旧基线、账户状态及不可撤销的审计记录说明，再取得用户二次确认后发送。页面和 taoli1 会话/CSRF 转发按既有 `live-exec-page` 交接接入，执行服务不向浏览器分发 ADMIN 令牌。

前提与效果：

- 本地没有实仓、活动 pair、非终态/未知状态/待记账订单、尚未落库的订单观察或在途发送；position_mismatch/unhedged_position/repair_stopped 等仓位、订单锁存在时拒绝。
- 重新读取两边真实账户、持仓及两边所有未完成订单；任一读取失败、结构不明、非空持仓或非空订单均拒绝。没有正负债，两边权益为有限正数，浮动盈亏为零。整个读取、历史核对及提交前检查仍受 10 秒时效限制。
- HALTING/HALTED 不接受本接口，必须用下文 `release-halt`。二次检查本地状态和基线版本后，原子保存两边独立权益基线、总权益/当日权益、当日浮盈亏基线及历史已实现损益的风险计量偏移，并写 `EQUITY_REBASELINED`。原订单、费用、交易与事件历史不改写；历史收益展示不清零。此后的新损失仍按原风控限额判定。
- 操作完成即更新运行中账户缓存；操作开始前仍在途的旧账户读取不能覆盖新基线。先置运行风险为 STOP，由正常对账恢复。STOP 文件、当日日亏锁、其他原因停开锁保持原规则，重设基线不能解除它们。
- 审计或任一数据库写入失败，整次变更回滚，内存基线也不提前更新。

成功返回 200，包含完整审计投影（下例省略的旧嵌套值以 null 表示尚未建立，不删除字段）：

```json
{
  "old": {
    "account_equity_baseline":{"crossex":753,"bitget":1514},
    "equity_baseline":2267,"daily_equity":null,"daily_upnl":null,"risk_pnl_baseline":null
  },
  "new": {
    "account_equity_baseline":{"crossex":29753,"bitget":517},
    "equity_baseline":30270,
    "daily_equity":{"day":"2026-11-12","equity":30270},
    "daily_upnl":{"day":"2026-11-12","upnl":0,"positions":{},"captured_at":"2026-11-12T01:00:00Z"},
    "risk_pnl_baseline":{"day":"2026-11-12","total":-4.2,"daily":-1.3}
  },
  "at":"2026-11-12T01:00:01Z","captured_at":"2026-11-12T01:00:00Z",
  "reason":"verified_transfer","source":"admin_api","halt_reason":null,"revision":1
}
```

金额保持 JSON 数字；`at/captured_at` 为 UTC ISO8601，`day` 为 UTC 日期。SQLite 审计内部保留 Decimal 字符串和 epoch 秒，CLI 直接执行的 JSON 日志使用内部审计格式。revision 单调增加并跨重启保留。

状态码：400 表示 body/字段不合规；401 未认证；403 只读令牌；409 为安全前提不成立，返回 `{"error":"<固定原因>"}`；503 为运行 Engine 未绑定、数据库或其他操作不可用。409 原因包括 `local_orders_pending`、`position_or_order_lock`、`local_positions`、`halted_requires_release`、`remote_positions`、`remote_orders`、`remote_orders_unavailable`、`account_read_unavailable`、`account_snapshot_stale`、`account_liability`、`equity_invalid`、`equity_revision_changed`。未知异常正文不返回。超时或响应丢失不意味着没有提交，应先检查 summary 中的 revision 及事件审计，不自动重试管理动作。

### CLI 人工管理

```sh
python -m tools.crossex_exec rebaseline --env-file /private/crossex-exec.env --confirm --reason verified_transfer
python -m tools.crossex_exec release-halt --env-file /private/crossex-exec.env --confirm account_equity_drop --reason verified_transfer
```

继续使用原 env 文件所有权/0600 校验及同一个数据库 `.lock`。运行中 `rebaseline` 通过本机 ADMIN 接口转交当前 Engine，审计 source=admin_api；停机后持有排他锁时执行，source=cli。CLI 不启动第二个执行服务，账户客户端即使 env 配置 LIVE=1 也禁用写交易所请求。

`release-halt` 要求原服务已退出、state=HALTED，`--confirm` 精确等于真实 halt_reason（旧记录回退 stop_reason）；其余空仓、订单、负债、权益和时效前提同上。成功写 `HALT_RELEASED`，重新建立基线，将 state 改为 RUNNING，仅移除 account_equity_drop/total_loss 的权益停机锁；不启动服务，不移除 STOP 文件或其他锁。仓位类锁一律拒绝，不能借此“确认”丢失的仓位/订单。HALTING 仍须完成原恢复流程。

### 新增 summary 字段及账户恢复

```json
{
  "equity_baseline_revision":1,
  "account_snapshot":{
    "captured_at":"2026-11-12T01:00:00Z","completed_at":"2026-11-12T01:00:00.500Z",
    "age_seconds":2,"refresh_seconds":0.5,"fresh":true
  },
  "liabilities":[{"coin":"USDC","exchange_type":"HYPERLIQUID","liability":28.5}],
  "stop_locks":{"account_liability":["global"]}
}
```

- account_snapshot 没有观测时各时间/时长为 null，fresh=false。age_seconds 从账户读取开始时刻到本次 API 投影时刻；fresh 当且仅当年龄在 [0,10] 秒。refresh_seconds 为该次读取开始到首次完整发布核验的时长，包含 I/O、等待锁及核验；协调器重读同一缓存不覆盖此时长。它与原 accounts.ok 的最近对账展示不同，后者不是开仓放行凭据。
- snapshot 失败/超时/陈旧时停开。`ACCOUNT_RECOVERY_SNAPSHOTS=3`：连续三个不同读取起点的新鲜完整快照自动解除自身 account_snapshot_unavailable 锁，并原子记录 ACCOUNT_SNAPSHOT_RECOVERED；相邻读取起点超过 10 秒、失败或陈旧会重置计数，重复缓存不累计。不会解除 HALTED、仓位锁或其他停开原因。
- CrossEx 任意正 liability 均停开并记录 ACCOUNT_LIABILITY/ALERT，保留币种与原始 exchange_type。负债本身不新增强制平仓策略，已有减仓/停机恢复通道保持可用；若同时触发权益、保证金或累计亏损条件，仍执行原全平/停机流程。连续三个不同且新鲜的无负债快照才解除自身 account_liability 锁并写 ACCOUNT_LIABILITY_RECOVERED。
- halt_reason 优先返回持久化的真实停机原因，不因后续另一条告警覆盖；stop_locks 为独立原因及归属集合，stop_reason 继续仅作兼容展示。

### OKX 目录与实际发送时间

- universe.markets 增加 `rule_type`（原始目录 ruleType，未知为 null）、`trading_blocked`（布尔）。OKX pre_market/rebase_contract 优先于 crypto/人工分类，盘前市场不展示为候选；其他未知非 normal 规则也禁止开仓、采样及映射。人工分类不能覆盖该禁止标志。已有持仓仍使用原始身份减仓。
- SENT 提交后，以开仓冻结配置只读已发布成交额缓存，复查两条精确合约的金额、300 秒时效及符合身份的交易所数量；失败整对拒发，此处不联网补数。
- 订单内部新增 sent_persisted_ms，表示写入 SENT 批次前采集的持久化阶段时间；sent_ms 改为各腿传输完成签名、即将调用请求写出前的内存时间，随后随 ACK/UNKNOWN/拒绝等原有落库流程保存。统一放行至写出前没有新增同步写库。
- 原 send_delta_ms 使用两腿实际 sent_ms 的绝对差。任一实际时刻未知时为 null，不用 sent_persisted_ms 补造指标；重启后无实际时刻的记录仍以持久化阶段时刻参与原有最近订单/拒单率风控。旧版本共享时间形成的历史指标不追溯重算，250ms 人工延迟和真实传输层 150ms 签名延迟均有回归覆盖。
