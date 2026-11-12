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

## 第十轮增补（2026-11-12）

仅增加并发前提字段、读取字段及冲突结果；全部既有参数、响应字段、权限、业务限制保留。本节为当前请求体约定：第八轮示例须加上以下必填字段，原空 `{}` 写请求不再有效。`equity/rebaseline` 不变。页面及转发须把**用户看到的版本/状态**原样带回，不能在提交前自动取新状态覆盖旧前提，也不能收到 409 后自动重试。

### PUT /api/v1/config 请求体

`expected_version` 为必填顶层非负 JSON 整数；bool、字符串、浮点数（含 `0.0`）、null 均不接受。其余顶层字段仍为非空部分参数更新，没有 `values` 包裹层。例如先 GET 得到 version=3 后：

```json
{"expected_version":3,"order_usd_max":30,"min_quote_volume_usd":2000000}
```

比较和更新在同一个 `BEGIN IMMEDIATE` 事务内完成。相等时版本加一，200 成功体仍为 `{version,values,fallback}`；不等时参数/版本均不变，返回：

```json
{"error":"version_conflict","current_version":4}
```

该响应状态为 **409**。缺少字段、类型不合格、空更新、原有参数验证不合格仍为 400。冲突写 `CONFIG_CONFLICT`，成功仍写 `CONFIG_CHANGED`。`GET /api/v1/config/audit` 追加冲突条目，最近 500 条的范围包含成功和冲突；冲突沿用 `version/at/source/old/new`，其中 version=current_version，old/new 为 `{}`，另增 `error:"version_conflict"`、`expected_version`、`current_version`。冲突不会伪造一次配置更新。

### POST /api/v1/mappings/{id}/confirm 或 reject 请求体

`GET /api/v1/mappings` 每项新增 **`multiplier` 字符串**。定义为按 `market_key` 排序后的第一腿 `quantity_multiplier ÷` 第二腿 `quantity_multiplier`，用无多余末尾零的普通十进制表示，例如 `"1000"` 或 `"0.001"`。它是候选的单位换算倍数；原 `ratio` 仍是发现时的原生价格比，原各腿的数字 `quantity_multiplier` 均保留。客户端直接回传 multiplier，不要用数值重新换算、舍入或格式化。

confirm 和 reject 都恰好接受两个必填字段：

```json
{"expected_multiplier":"1000","expected_status":"pending"}
```

expected_status 取 `pending/confirmed/rejected`；expected_multiplier 为 1–80 字符的数字表示字符串（字符限 `0-9 . e E + -`），必须与当前 multiplier **逐字符相同**，`"1000.0"` 不等于 `"1000"`。两项中任一不等即 409，示例：

```json
{"error":"mapping_conflict","current_status":"confirmed","current_multiplier":"1000"}
```

成功返回原映射 schema 加 multiplier。缺字段/非法类型/额外键为 400；候选缺失、不可用或年龄不在 [0,300] 秒仍为 400。候选复核、比较、状态变更、`MAPPING_CHANGED` 在同一写事务内；后台发现也参与写事务，不能跨进程覆写已提交决定。冲突记录 `MAPPING_CONFLICT`，含 id、action、expected/current status 与 multiplier、at、source=admin_api，不改映射。

### POST /api/v1/universe/{base}/classify、exclude 或 unexclude 请求体

`GET /api/v1/universe` 每个 base 新增 `status`，格式 `u1:` 加 64 位小写十六进制。它是应原样回传的不透明状态标记，包含该 base 的完整管理决定、递增 revision 和原始合约目录规则；不随盘口或成交额变化。分类、所选市场、排除状态及目录规则变化都会使旧标记失效；管理决定改后又改回，也不会复用原标记。它不是 category、eligible 或 excluded 的别名。

三个路由均新增必填 `expected_status`。以下 status 仅演示格式，实际使用最新 GET 中用户已查看的值。

```json
{"expected_status":"u1:0000000000000000000000000000000000000000000000000000000000000000"}
```

上例为 exclude/unexclude 的完整请求体。classify 保留原全部业务字段并增加 expected_status：

```json
{"expected_status":"u1:0000000000000000000000000000000000000000000000000000000000000000",
 "category":"listed_equity","underlying_market":"KRX",
 "markets":["binance|future|SKHYNIXUSDT","bitget|future|SKHYNIXUSDT"]}
```

缺少或格式错误为 400；与事务内当前状态不等为 **409**：

```json
{"error":"status_conflict","current_status":"u1:1111111111111111111111111111111111111111111111111111111111111111"}
```

成功仍返回原管理决定对象，增加 `revision` 整数与更新后的 `status`。比较、决定及 `UNIVERSE_CHANGED` 同事务提交；冲突写 `UNIVERSE_CONFLICT`（base、action、expected_status、current_status、at、source），不改决定。原禁止类别/股票市场/精确所身份检查仍适用。不存在于列表的 base 没有可供页面选择的状态；不得因获得冲突体就静默重试排除或分类。

### 审计失败与后台诊断

409 只在冲突审计成功提交后返回；审计、事务或数据库失败为 503，不宣称冲突审计已经落库。成功变更的审计失败时整笔回滚。ADMIN/只读令牌隔离、无真实账户调用的配置/映射/交易范围接口边界均保留。

`BACKGROUND_FAILED` 内部事件新增 `location:"模块名:行号"`，与已有 worker、error_type 同时保存；后台错误日志同步增加 location。取异常最内层位置，不输出异常正文、完整 traceback、源码、绝对路径、请求/响应或局部变量。同一 worker 的同类型同位置每 60 秒最多记录一次，不同位置分别记录。本字段为内部诊断，不更改旧业务 API schema。

## 第十一轮增补（2026-11-12）

本节仅追加：允许用户明确登记、且本程序永久禁止交易的 CrossEx 外部合约。前文专用账户、远端全空等要求在本节明确覆盖的场景按以下条件执行，其余契约不变。登记不是忽略 ticker、解锁日亏或交易授权。

### 精确身份、永久禁单和归属

外仓逻辑登记表存于 `meta.external_contracts`，键为六项身份的 SHA-256。六项均必填且必须与原始完整 symbol、最新公开目录逐项一致：

```json
{"route":"crossex","exchange_type":"HYPERLIQUID","business":"FUTURE",
 "symbol":"HYPERLIQUID_FUTURE_io:OAI_USDC","quote":"USDC","dex":"io"}
```

`dex` 使用原始 DEX 字符串；没有 DEX 的普通合约显式传空字符串。`io:OAI`、`xyz:OAI`、其他交易所 OAI 与归一后的 OPENAI 都不能互代。当前范围仅 CrossEx FUTURE、USDT/USDC；这不扩大本程序交易市场集合。

每条记录包含 identity、raw_base、完整原始 rule、registered、恒为 true 的 forbidden、revision、registered_at、updated_at。首次登记即对该 route+原始 symbol 的两个方向永久禁止本程序发送任何订单，包括 reduce-only。取消登记保留记录和 forbidden 墓碑，重启、别名映射、初始准入、单腿发送及两腿 SENT 后共享发送关口都必须遵守。取消后若该精确合约重新出现远端持仓，视为不可核实，停开；不会自动平掉它。

任何本程序订单历史（包括孤单、INTENT/SENT/ACK/UNKNOWN、待记账、已终态），或 pair 历史/活动仓位/恢复义务与合约重叠，禁止登记。内存中仍有待持久化或正在处理的订单也拒绝管理写。身份缺失、原始/解析持仓不一致、目录缺失/超过六小时/规则改变均停开；保留原始风险快照与失败证据，不按 ticker 补字段。

### GET /api/v1/external-positions

沿用独立只读令牌或 ADMIN 令牌。新增返回对象，不删除现有端点或字段：

```json
{"status":"x1:<64 hex>","contracts":[],"income":null,"flatten_status":null,"projection":null}
```

`status` 是整个登记表和原始目录规则的不透明版本；时间刷新不改变版本，内容改变会改变版本。页面须让用户查看本次 GET 的登记状态，再原样提交它。`contracts` 是上述记录列表；`income.verified=false` 不表示贡献为零。`projection` 是已成功核实的本程序投影，可能是旧值，必须结合 income、UTC 日、captured_at 和当前账户状态判断，不能单凭它展示“可交易”。

### POST /api/v1/external-positions/register 或 unregister

只接受 ADMIN 令牌。完整请求体如下，三个顶层字段均必填，不接受额外字段：

```json
{"identity":{"route":"crossex","exchange_type":"HYPERLIQUID","business":"FUTURE",
             "symbol":"HYPERLIQUID_FUTURE_io:OAI_USDC","quote":"USDC","dex":"io"},
 "expected_status":"x1:<用户实际读取的64位标记>","reason":"Astro_manual_position"}
```

reason 为 1–200 字符非空说明，不含控制字符。成功 200 返回 `{record:登记记录,status:新标记}`。取消登记必须读取新鲜完整持仓，确认该合约两个方向均无仓位，且没有该合约远端活动订单；读不到即拒绝。登记也拒绝该合约的远端活动订单，避免登记采样期间归属变化。

比较当前 status、更新登记、递增 attribution revision、`EXTERNAL_CHANGED` 审计在同一 `BEGIN IMMEDIATE` 事务内完成。审计包含 old/new、source、reason、expected_status、at、全账户原始快照和远端活动订单。运行中由 Engine 协调器串行管理，读取账户期间停开；停机 CLI 持有原服务生命周期 flock。

- 400：缺字段、格式/类型错误或多余字段。
- 403：只读令牌请求写接口。
- 409：expected_status 冲突，返回 `{error:"external_conflict",current_status:"x1:..."}`；`EXTERNAL_CONFLICT` 成功落库后才返回。不得自动获取新状态并重试。
- 409：归属、持仓、活动订单、目录、时效等业务前提不满足，返回固定拒绝原因，如 program_ownership、program_ownership_unknown、local_orders_pending、external_remote_position、external_remote_order、external_identity_unverifiable、external_catalog_unavailable、account_snapshot_stale。
- 503：审计/事务故障、数据库不可用，或 API 未绑定运行中的 Engine 管理回调。成功审计失败整笔回滚，不退回离线直接写库。

### CLI 与重基线前提

新增 `external-list --db <db>`，只读且不加载凭据；`external-register` / `external-unregister` 接受 `--env-file`、`--route crossex`、`--exchange-type`、`--business`、`--symbol`、`--quote`、`--dex`、`--expected-status` 和 `--reason`。写命令默认 reason 为 manual_external_ownership。具体示例见执行器 README。

运行中 CLI 调用本机 ADMIN 写接口；停机时保持原 env 所有权/0600 校验与 flock，交易传输强制 live=False，刷新公开目录并只读鉴权账户/订单。CLI 不自动重试冲突；目录刷新产生冲突后，重新查看 external-list 再人工决定。停机时目录未初始化的首次操作同样可能先冲突，不能跳过前提。

每次登记或取消登记改变收益归属域，旧收益核实结果失效，必须显式 rebaseline 建立新归属计量起点。运行中沿用 POST equity/rebaseline；停机沿用 rebaseline/release-halt CLI。允许仅剩已登记外仓，但必须满足：

1. 本程序本地流水、pair、两边远端程序仓位均为空，无 INTENT/非终态/UNKNOWN/待记账/内存待写，无原仓位/恢复类锁。
2. 两边均无远端活动订单，原始身份、目录及外仓归属可核实；双快照间现金/数量/累计结算没有变化。
3. 采样不超过十秒、完整当日流水可读取，本程序费用/已实现与不可变订单流水一致，资金费可核实。
4. 两边权益为正、无负债；带外仓时两边保证金倍数均至少 3。原确认规则和 release-halt 的 HALTED/精确 halt_reason 前提不变。

审计 `EQUITY_REBASELINED` / `HALT_RELEASED` 新增 external_snapshot，包含全户原始账户/持仓、流水、外仓与本程序投影；新基线记录 external_equity_anchor、program_daily_upnl、program_last_upnl 和已核实资金费偏移。原历史、登记墓碑、当日日亏锁、STOP 文件和其他原因锁不删除；release-halt 仍只解除原权益停机原因。证据不足 external_income_unverifiable、身份/目录不可核实和 margin_open_limit 以 409 返回。重基线成功本身不代表允许开仓，还需后续收益核实及所有既有门禁通过。

### 全户风险与程序损益的两个口径

全户 account_risk_snapshot 保留完整 raw_accounts、raw_positions、equity/equity_by_venue、margins、liabilities、notionals、upnl。原 HardLimits 始终使用这份全户数据：外仓名义额计入 600 总额度，权益基线/单所回撤、保证金、负债、累计亏损均维持原规则。任意正负债仍停开告警，无外仓借款豁免。

程序 program_projection 仅剔除已登记的精确外仓持仓。未登记同名合约、另一 DEX 合约或不明远端仓位仍进入 mismatch；原三次新鲜一致观测恢复条件保留。日亏取本程序已核实的已实现、费用、当日资金费和当日浮亏差；本程序现金流水须与不可变成交记录一致。

原整户日权益差兜底保留，改为在同一计量起点计算 `全户权益变化 - 外仓完整净贡献`，并继续与程序日亏取较保守值。外仓净贡献 = 已核实账本现金变化（已实现、手续费、资金费、强平费）+ 外仓浮亏变化。成交历史校验开/平/增减数量、方向以及逐订单已实现/手续费，不能重复把成交金额再加到账本现金中。

CrossEx account_book/history_trades 与 Bitget financial-records 使用固定毫秒窗口、有界分页、唯一流水 ID；校验历史不能消失或被改写，逐交易所/币种现金余额变化必须与账本吻合。迟到的起点之前流水、缺页、缺资金费、结算中的活动订单、未知流水类型/归属、转入转出/借还款/利息/换币等无法归属变化都停开。不能将空列表、余额总和不变或当前 upnl 抵消视作完整收益证明。证据更新失败同样不能发布已核实状态。

跨 UTC 日先核对完整跨日持有区间，再以当日首次一致观测同时滚动全户日权益兜底与外仓贡献起点；程序浮亏保持上一份有效跟踪快照作为当日差分基础，本程序当日现金按 UTC 流水归属。重启保留这些起点、历史 ID 和登记版本，不伪造午夜价格，不忽略停机期间结算。

**USDC 限制：**公开 CrossEx 文档没有提供可核实的历史 USDC 到整户权益计价币折算证据。本轮支持精确登记并永久禁单 `HYPERLIQUID_FUTURE_io:OAI_USDC`，但该合约非零浮亏或新增非零 USDC 现金贡献不能按 1:1 假定折算，收益核实失败即停开。逐仓/逐币种零值且现金连续性证明完整才可能通过；不同币种盈亏抵消成零不算证明。不能将此交付描述为已经恢复该真实账户交易。

每个新账户缓存发布前撤销旧 external_income；开仓、最终发送需当前版本、当前 UTC 日且十秒内的核实结果。收益不可核实仍执行全户硬风险检查，必要时平本程序仓位，不能因为归属失败跳过硬止损。

运行中登记/取消、带外仓重基线在读取前也撤销旧证明并停开；管理读取或业务前提失败后，不能沿用操作前的 RUN/收益证明，必须重新对账。完整管理观测保存为 external_admin_snapshot，失败读取已取得的原始部分保存为 external_failed_snapshot；这些证据写入不改变登记版本或旧权益基线。

### 平仓和停机报告

`flatten_all` 只处理程序归属仓位；最终禁单关口也拒绝已登记合约的 reduce-only。停止完成要求程序远端仓位、本地活动 pair、待写、INTENT/非终态及待记账义务均为空，外仓可以仍在。

新增 flatten_status：`{program_flat,account_flat,external_positions,captured_at}`。有外仓时 program_flat=true、account_flat=false，写入 `PROGRAM_FLATTENED_EXTERNAL_REMAINS`；HALTED 与正常退出只证明程序仓位处理完成。shutdown 日志显式输出这两个状态和精确外仓快照。身份/目录不能核实时不报告成功全平。

公开接口依据：[CrossEx 文档](https://www.gate.com/docs/developers/crossex/en/)、[Bitget UTA 账务流水](https://www.bitget.com/docs/catalog/account/assets-balance)、[Bitget UTA 枚举](https://www.bitget.com/docs/uta/enum)。本轮只核实公开文档，没有调用真实鉴权接口；真实字段时延、资金费及计价证明仍需另行授权验证。

## 第十二轮增补（2026-11-12）

本节按 PM 裁决追加外仓结算币的公开现货折算，优先于第十一轮“非零 USDC 无历史估值即拒绝”的限制描述。原 API、CLI、请求体、返回键与风控规则均保留；不新增管理豁免或解锁入口。

### 币种、报价和上界公式

- 原始合约/DEX、账本/成交/现金余额/数量连续性仍先按原生币种核实；只改外仓贡献的估值环节。原生 USDT/USD 沿用执行器现有 USD 报告口径；其他结算币仅查直接 `COIN/USDT` 现货，不做 ticker 别名、反向币对或间接路径猜测。
- 登记身份白名单本轮不扩展，现有 `symbol_identity` 只接收 USDT/USDC 合约，因此本轮新增的实际登记路径为 USDC；通用币种 FX 解析/数学函数不构成允许其他结算币登记的豁免。USD 是报告单位兼容分支，不意味着新增 USD 合约准入。
- 固定查询 OKX、Bitget、Bybit 三个公开 GET，复用同一个 `MarketData.batch`、RequestQueue、PublicClient、Limiter 和线程池，优先级 0。没有签名、私有 Transport、凭据或新限频通道。每币种正常最多三次请求/30s；失败可能随账户刷新重试，仍受原限频约束。
- 使用至少两家不同交易所的有效买一/卖一中点；两家时中位数为两者均值，三家时为中间值。精确校验币对、现货类型、成功码、正数、有限数、买价不高于卖价与交易所时间。`0 <= now - source_at <= 60s`，未来时间、过期、报价家数不足或无法解析都不能作为证明。接收时间不能早于源时间或晚于当前时间。
- 报价中位数为 `p_c`，不确定带固定 `L_c = 0.998*p_c`、`H_c = 1.002*p_c`。所有参与来源的买一/卖一必须落在此带内，超出则整币种不可核实；不删掉极端偏离的有效来源来拼合成功。不会把 USDC 报价钳制为 1；各家一致的脱锚报价按实际中位数计算。
- 缓存最多 30s，源时间超过 60s 立即失效；失败刷新不续期、不回填旧价或 1。收益证明仍受原账户快照 10s 时限约束，开仓及最终共享发送关口另复查原 FX 证据的 60s 时限，不在发送关口联网。

对币种 `c`，完整同起点外仓原生贡献为：

```text
x_c = 新增外仓账本净现金_c + 当前外仓浮盈亏_c - 起点外仓浮盈亏_c
C_c_upper = x_c * H_c  (x_c >= 0)
          = x_c * L_c  (x_c < 0)
          = x_c*p_c + abs(x_c)*p_c*0.002
external_contribution = sum_c C_c_upper
equity_daily = (当前全户权益 - 同一起点全户权益) - external_contribution
最终日损益 = min(既有已核实程序日损益, equity_daily)
```

USDT/USD 的 `C_c_upper=x_c`。不同币种分别折算后才能求和。任一起点/当前浮盈亏或新现金项非零的非 USD 币种都必须有报价，即使同币种净贡献抵消为零；只有所有相关原生项本来就是零才不需要折算证据。

在 PM 指定的当前现货区间内，对任意 `r_c ∈ [L_c,H_c]` 都有 `C_c_upper >= x_c*r_c`，故扣除后的程序损益只会更低、更保守。示例：`p=1`，外仓盈利 `+50` 扣 `50.1`；外仓亏损 `-50` 扣 `-49.9`。若整户变化分别为 `+30/-70`，两种情况的程序权益兜底都为 `-20.1`，不会把原 `-20` 自身亏损变轻。

这是 PM 授权的**当前公开估值模型**，不是交易所历史权益折算/抵押折扣的精确重建，不能解释为区间之外或历史各时点价格的保证。整户累计亏损、分所权益回撤、保证金、总名义额、负债和原程序损益的 min 保护继续独立生效。

### 只追加的收益证据字段

既有 `GET /api/v1/external-positions` / `external-list` 的 `income` 保留所有键，成功结果追加：

| 字段 | 内容 |
|---|---|
| `external_cash_by_currency` | 各结算币新增外仓净现金，十进制字符串字典，原生单位 |
| `external_contribution_by_currency` | 各币种 `x_c`，十进制字符串字典，原生单位 |
| `external_fx` | 非 USD 币种 → `{median,lower,upper,expires_at,sources}`；每个 source 含 `venue,base,quote,market_type,symbol,bid,ask,at,received_at` |
| `valuation` | `public_spot_upper_bound` 或 `native_usd` |

原 `external_cash` 和 `external_contribution` 在新支持的币种上为 USD 口径的保守上界；前者单独对现金净额取上界，不能把它和未经折算的浮盈亏直接相加。USDT 旧结果不变。跨 UTC 日的 `carried_interval` 追加原币净现金、贡献及 FX 证据；验证完整旧区间后新起点的现金/贡献字典清空。失败仍是 `verified=false / external_income_unverifiable`，失败快照保留所读公开来源与账户证据。

### 锁与负债保持原规则

任何正负债仍停开并告警，无外仓借款豁免；解除负债原因仍需三次不同的新鲜无负债观测。登记、FX 成功、rebaseline 和 release-halt 均不清当日日亏锁，不删除 STOP 文件。position_mismatch 仅经既有三次不同匹配快照解除；release-halt 仍要求 HALTED、停止服务、精确确认 halt_reason、程序空仓/无单/无恢复义务、无远端订单、无负债、正权益与新鲜证据。仅移除既有权益停机原因，不移除 daily_loss。

原 UTC 次日成功对账的日亏周期规则不变；本轮不补任何即时或自动清旧锁路径。详细线上 CLI 顺序和 HALTED+mismatch 维护分支见[第十二轮交接](task-handoff-2026-11-12-crossex-exec-r8.md#第十二轮)，由 Claude 在服务器执行。

公开文档核实日期 2026-11-12：[OKX ticker](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-ticker)、[Bitget spot tickers](https://www.bitget.com/legacy-docs/classic/spot/market/Get-Tickers)、[Bybit spot tickers](https://bybit-exchange.github.io/docs/v5/market/tickers)。本轮只联网核实这些公开文档；接口行为用受 DNS/socket 守护的离线响应验证，未调用真实鉴权或宣称线上行情验收。

## 第十三轮增补（2026-11-12）

本节只追加契约。原有接口、字段、登记身份白名单、ADMIN 权限、永久禁单和风控阈值继续有效。修复终审 5P1/1P2，并新增供外仓登记页面读取的全远端仓位视图；未新增交易或管理写入口。

### GET /api/v1/remote-positions

使用既有 `X-Api-Token` 只读令牌；ADMIN 令牌仍按既有规则兼容读取。无查询参数，无请求体，支持空查询串 `?`。未授权 401，非 GET 405，未知/重复查询参数 400；数据库不可用、损坏、不完整或超过既有读取/响应预算返回 503。响应带 `Cache-Control: no-store`。

只读取执行器数据库中最近一次**已发布的完整账户快照**，包含快照里的所有非零远端仓位，不按程序交易集合或已登记外仓过滤。覆盖范围沿用当前账户适配器：CrossEx `/crossex/positions` 与 Bitget UTA `current-position?category=USDT-FUTURES`；不会在 HTTP GET 中另外查询未订阅的产品类别。该接口不联网、不刷新账户、不初始化数据库、不提交订单、不登记或取消登记。账户余额、用户 ID、订单原文、密钥及其他原始账户字段均不返回。

顶层响应是对象：

| 字段 | 类型与含义 |
|---|---|
| `available` | boolean；已有完整快照为 true。尚无快照时 false，不能解读为已核实空仓 |
| `snapshot_at` | UTC ISO 8601 字符串或 null；采集开始时间，不是 API 读取时间 |
| `snapshot_version` | 正整数或 null；每次成功发布独立递增并持久化，相同采集时间仍有不同版本，重启不重用版本 |
| `stale` | boolean；采集时间在未来、年龄超过 10 秒或后续账户刷新失败时 true；没有快照也为 true |
| `positions` | 全部非零远端仓位数组，按 `position_key` 排序；有完整空仓快照时为 `[]` 且 `available=true` |

每个仓位的字段如下。数值使用十进制**字符串**保留原精度；未知数值为 null，不填 0。

| 字段 | 类型与含义 |
|---|---|
| `position_key` | `route|symbol|side`，包含原始 symbol，不用规范化 ticker |
| `route` | `crossex` / `bitget` |
| `exchange_type` | CrossEx 原始 symbol 内的交易所，如 `HYPERLIQUID`；Bitget 为 `BITGET` |
| `business` | CrossEx 为 `FUTURE`；Bitget 保留产品类别，如 `USDT-FUTURES` |
| `symbol` | 原始交易合约，如 `HYPERLIQUID_FUTURE_io:OAI_USDC` 或 `BTCUSDT` |
| `quote` | 原币计价币，如 `USDC` / `USDT` |
| `dex` | 原始 DEX 前缀，如 `io` / `xyz`，普通市场为 `""`；不得互相合并 |
| `identity_verified` | boolean；无法解释的身份仍保留该行及原 symbol，其未知身份项为 null；false 不可用于登记 |
| `side` | `long` / `short`；CrossEx `NONE` 按有符号数量转换方向 |
| `quantity` | 正数量，原始合约数量单位，不套规范化 ticker 的倍率 |
| `entry_price` | CrossEx `entry_price` / UTA `avgPrice`；缺失或无效为 null |
| `mark_price` | CrossEx `mark_price` / UTA `markPrice`；缺失或无效为 null |
| `unrealized_pnl_native` | CrossEx `upnl` / UTA `unrealisedPnl`，币种为 `quote` |
| `unrealized_pnl_usd` | 保守 USD 浮盈亏**下界**；有盈利用 `L`，亏损用 `H`。来源不足/过期/身份或原值未知为 null |
| `valuation` | `native_usd` 或 `public_spot_lower_bound`；沿用 USDT/USD 报告口径，不把 USDC 当作 1 |
| `valuation_verified` | boolean；本次读取时原来源仍能支持折算；与账户 `stale` 独立，不能单凭它决定数据新鲜 |
| `valuation_error` | 折算可用为 null，否则固定 `position_fx_unavailable`，不返回异常正文 |
| `ownership` | `program` / `external_registered` / `unregistered`，语义见下文 |
| `snapshot_at` / `snapshot_version` / `stale` | 与顶层相同，方便逐行展示 |

归属读取与快照读取处于同一个 SQLite 只读事务。与登记禁止规则保持一致：只要精确 route/symbol 有程序订单或 pair 历史，即 `program`，包括已平历史、孤单、未知单，不以相似 ticker 推断。完整精确身份与当前有效登记一致为 `external_registered`；其余为 `unregistered`。取消登记后的永久禁单墓碑不代表仍已登记，不会把未登记仓位隐藏。

示例（省略仓位可选数值展示，不代表删减返回字段）：

```json
{"available":false,"snapshot_at":null,"snapshot_version":null,"stale":true,"positions":[]}
```

前端必须区分“无快照”“过期快照”和“已核实空仓”。登记按钮至少要求最新非过期快照、完整身份及 `unregistered`；`program` 不允许登记。原 ADMIN 服务仍会重新核对完整目录、程序历史和写前提，视图的归属标签不授予写权限。取消登记按钮结合既有 `GET /api/v1/external-positions` 的有效登记列表与本接口的**完整非过期空仓证据**判断，合约任一方向仍有持仓时禁用；因此已无仓位的登记仍要在登记列表中显示。写入继续使用既有接口的 `expected_status`（`x1:...`），不能拿 `snapshot_version` 替代；409 后重读并人工复核，不能自动重试。Bitget 行可展示，但本轮没有扩展外仓登记白名单。

### 快照、收益证明与流水连续性

`meta.account_snapshot` 保存完整发布快照及 `captured_at/snapshot_version`，`account_snapshot_version` 保存递增序号。发布、撤销旧收益证明和版本更新在同一 Store RLock / SQLite 事务下完成；写失败不发布新内存缓存。失败刷新保留上一份完整快照供诊断，同时记录 `account_snapshot_error` 并使缓存失效。

`external_income` 成功结果只追加 `snapshot_version`、`equity_revision`。核实提交时在同一事务比较当前快照版本、归属 revision、权益 revision、原外仓起点、已核实流水索引及新鲜度；旧结果不能覆盖新观测。开仓与 SENT 后最终共享发送检查要求证明和当前缓存版本一致，并重新检查 FX 原始来源时钟。

新增 `meta.external_verified_history`，按 `crossex_book/crossex_trades/bitget_book` 保存所有已核实 ID 对应的 `{at,hash}`。每次成功核实只增加已见证据，不滚动原计量起点；请求窗口内已核实 ID 消失或内容变化即失败，同一 ID 再出现也必须原内容。窗口外旧索引保留；原 UTC 日界线规则不变。索引、原始成功流水、收益证明和需要跨日更新的起点同事务提交。rebaseline/release-halt 不得通过丢弃已核实索引绕过缺失检查。旧数据库第一次使用时从仍保存的 anchor history 与最后 `external_activity` 合并迁移，无法重建旧版本已经丢弃且数据库中不再保留的历史证据。

### USD 名义额、负债与停机审计

原 `snapshot.notionals` 保留原币金额。追加 `notionals_usd`、`notional_valuation_verified`，失败追加 `notional_valuation_error=position_fx_unavailable`。全户远端仓位、本地已成交和待发送订单全部按精确计价币分别计量，再做 USD 求和；非 USD 正名义额乘第十二轮 FX 区间上界 `H`，不使用中位价低估额度。最终发送重新计算并检查来源，不能信任一份旧的 USD 字段或“外仓净收益为零”跳过折算。

例：500 USDC × 1.01 × 1.002 = 506.01 USD，另加两条各 50 USD 的腿为 606.01 USD，超过 600 必须拒绝。原单腿 150、全户 600、分所权益回撤 150、累计亏损、保证金和减仓可用性不放宽；缺价不遮蔽权益/保证金硬退出。

`Accounts.account_liabilities` 统一读取 CrossEx `assets[].liability` 与 Bitget UTA `assets[].debt`，不以正余额抵销债务。每项保留 `coin/exchange_type/liability`，追加 `route/source_field`；金额仍为对应原币。缺失、非有限、负值、重复身份或非法资产集合拒绝账户证据。开仓、管理前提和三次独立新鲜无负债恢复共用这份汇总。无借款豁免。

`flatten_all` 每次生成独立 `attempt_id`，追加 `verified/started_at/reason`，启动时先撤销旧成功；返回本次状态对象。shutdown 必须重新读取远端仓位，即使没有外仓登记或处于 HALTING 退避中。任何读取、核实或审计失败返回未核实状态，退出码非 0；CLI 只消费本次返回值，不再读取历史 `flatten_status` 决定退出。`verified=true,program_flat=true,account_flat=false` 表示程序义务已清、登记外仓仍在，可正常退出并明确报告，不会向外仓发单。

rebaseline 与 release-halt 的审计 `old` 在同一写事务中保存全部原基线，补齐 `external_equity_anchor/program_daily_upnl/program_last_upnl/external_verified_history/last_upnl/account_loss_current/external_income`；审计写失败回滚。本轮不增加清日亏、删 STOP、跳过三快照或更改 HALTED 释放条件的入口。

2026-11-12 官方字段核实：[Bitget UTA assets/debt](https://www.bitget.com/docs/catalog/account/assets-balance)、[Bitget UTA current-position/avgPrice](https://www.bitget.com/docs/catalog/trading/position-management)、[CrossEx positions/entry_price](https://www.gate.com/docs/developers/crossex/en/)。只查阅公开文档，未做真实账户鉴权。

## 第十四轮增补（2026-11-12）

本节按用户明确指示及 PM 第十四轮任务书改变风险归属口径，覆盖前文全户回撤、全户累计亏损、外仓占用 600 额度和任何负债一律停开的描述；其余门槛、证据要求和接口保持。未删除任何历史契约、字段或端点。本轮仅本地实现和离线验收，未部署。

### 默认风险口径：本程序

- `max_equity_drop_usd` 仍受代码/env/冻结配置中的最严阈值约束，硬上限 150 USD；现在比较本程序累计已实现损益、手续费、资金费和当前浮盈亏之和。达到负阈值进入 HALTING，只平本程序仓位。沿用 `account_equity_drop` / `account_equity_halt` 原因码；名称不再表示整户权益差。
- `daily_loss_stop_usd` 与 `total_loss_stop_usd` 同样只看本程序，原硬上限分别 20 / 100 USD 不变。累计亏损 100 的较严门槛仍可先于 150 触发；150 不是新允许亏损额度。日亏使用程序 UTC 浮盈亏基线差，累计使用当前程序浮盈亏。旧 daily_loss、HALTED、STOP 文件和其他恢复锁不会因登记、升级、外仓盈利或出入金自动清除。
- 单腿 150 / 总额 600 的硬上限及配置中的更小额度不变。总额仅剔除当前可核实且已登记的精确外仓；本地已成交与远端程序敞口取较大值，再加未决订单剩余名义额。INTENT/SENT/ACK/UNKNOWN 继续占用额度。未登记远端持仓仍保守计入并触发 `position_mismatch`，不提供忽略未知持仓的通道。
- 整户 `equity/equity_by_venue/account_equity_current` 及 API `accounts.*.equity_usd/baseline_usd/drop_usd` 保留为观测值；出入金和外仓盈亏不参与默认程序损失判定。`external_income.equity_daily` 也保留旧公式的观测结果，不能再用于判断程序损失或作为程序损益展示。
- 唯一保留的整户风险门槛是两边维持保证金覆盖倍数：任何一边低于 3 停开，低于 2 减本程序仓位；等于 3 可以通过该门槛，等于 2 停开而不因该门槛减仓。缺值/未知仍停开。外仓永久禁单在减仓和最终发送关口仍有效。

为原专用账户回归保留显式 `CROSSEX_RISK_SCOPE=dedicated_account` 的旧整户保守模式；默认是 `program`。该兼容模式仅在登记表完全为空时适用：一旦存在登记或永久墓碑，强制使用程序隔离及完整收益证明，不能靠该参数绕过外仓证据。旧测试明确设置专用账户口径，当前共享账户不应配置该兼容选项。未增加 ADMIN 配置字段或解除锁的接口。

### 可核实程序收益与新增字段

默认模式即使没有外仓登记，也使用第十一至十三轮的双账户快照、完整固定窗口流水、逐交易所/币种现金连续性、不可变历史 ID/哈希、快照版本和基线版本机制。缺页、旧流水消失/改写、缺费用/成交均价、活动订单、数量/现金不符、未知资金流、过期 FX 或新快照替换旧证明，都停开；不把不可核实收益记为零。

程序已实现/手续费仍由本地不可变成交计算，并与完整账本核对；逐仓浮盈亏必须覆盖程序全部远端仓位且金额汇总一致。资金费新增累计计量：`external_equity_anchor.program_funding_carry` 保存跨 UTC 日已核实累计值，当日起点之后新 ID 的资金费加到累计值；同毫秒新增 ID 不漏计，重启和滚日不清零，显式重基线才建立新风险计量期。

原 `GET /api/v1/external-positions` 的 `income` 成功对象只追加：

| 字段 | 类型/语义 |
|---|---|
| `scope` | `"program"` |
| `program_daily` | 十进制字符串；当前计量期内的程序 UTC 日损益 |
| `program_total` | 十进制字符串；程序累计已实现、费用、资金费和当前浮盈亏之和 |
| `program_funding_total` | 十进制字符串；本风险计量期的程序累计资金费 |
| `program_upnl` | 十进制字符串；当前程序浮盈亏，非 USD 按下界估值 |
| `program_journal` | 本地已成交/终态订单的内容摘要，用于证据绑定，不是授权令牌 |

原 `verified/day/captured_at/snapshot_version/revision/equity_revision` 继续适用。计算开始记录订单摘要，提交与最终发送重复比较；新 INTENT/SENT 的未成交计划由原共享屏障处理，不会替代已成交损益证明。证明更新失败时旧 `account_loss_current` 可保留作诊断，不能将其解释为当前已核实值；查询方必须同时检查最新 income 和账户快照版本/新鲜度。`account_loss_current` 内部增加 `scope/snapshot_version`。

全新且无任何本程序订单历史、远端程序空仓的数据库可在首次完整核实后建立初始起点。已有历史而缺少新累计资金费证据的旧库保守停开，需原有显式 rebaseline/release-halt 路径；不得默认旧累计为零，也不自动改写原亏损锁。管理操作仍要求程序空仓、无非终态/待记账/恢复义务、无远端活动订单、两边正权益、新鲜证据和原确认规则。

明确 `TRANSFER_IN/TRANSFER_OUT` 的非合约资金流可在完整历史及逐币现金连续性核对后排除：要求方向符号正确，symbol 为空；Bitget 转入转出另要求 fee=0，非零费用的归属无法核实即停开。不认识的出入金枚举、借还款、利息、换币或其他未知流水继续停开。不会为了兼容出入金跳过现金连续性；两边本金变化及外仓收益不能抵消本程序已核实损失。所有原非 USD 外仓非零贡献的第十二轮 FX 证明仍保留；被排除的外仓名义额不再单独要求 FX 授权占额。

### 外仓保证金借款与告警

债务仍保存 route/exchange_type/coin/liability/source_field 的原生单位。只有以下条件全部成立，才列为外仓负债并只告警：

1. 来源是 CrossEx `assets[].liability`；精确交易所/币种对应有效登记外仓，身份、目录和原始仓位核对成功。
2. 同一交易所/币种的非零远端仓位全部归属已登记外仓，无程序或未登记仓位，也无本程序待发/UNKNOWN 等未决订单占用该域。
3. 各外仓 `initial_margin` 有限、非负；汇总与该资产 `futures_initial_margin` 一致，原资产 liability 与汇总债务一致，且正负债不超过该原生币种的外仓初始保证金需求。不跨币抵销、不按 ticker 归并、不猜保证金。

缺字段、币种/交易所不符、混合归属、超过需求或 Bitget debt 均继续 `account_liability` 停开。外仓债务可核实不代表程序收益可核实，两个门禁独立。原已阻断负债锁仅在三份不同、新鲜且没有阻断债务的快照后恢复；可核实外仓债务算作“无阻断债务”。带外仓显式重基线可接受同样核实的债务，但不能清旧日亏锁。

内部 `liability_attribution` 保存 `external/blocking/snapshot_version/at`；外仓告警事件为 `EXTERNAL_LIABILITY` 和 `ALERT(reason=external_liability)`，不添加 stop lock。归属状态/资产身份变化时告警，同一状态每小时最多一次；解除后再次出现是新事件。该状态和告警审计同事务提交。程序模式下其他阻断告警按 reason/owner 独立持久化去重，数值/时间刷新或交替原因不再刷屏，恢复后再次发生可告警，重启保留去重记录。sink 失败保留 `ALERT_SINK_FAILED`，不宣称外部通知必达。

公开字段依据仅用于解析和合成测试：[CrossEx 账户、持仓及账本](https://www.gate.com/docs/developers/crossex/en/)、[Bitget UTA 枚举](https://www.bitget.com/docs/uta/enum)。没有真实鉴权、借还款、划转、订单或线上验收。

## 第十五轮增补（2026-11-12）

本节只追加。沿用第十四轮程序风险口径、全部额度/亏损/保证金阈值、ADMIN 权限、乐观并发、永久禁单和人工解除停机前提；不新增交易开关或管理写端点。本轮仅本地实现与离线验证，未推送、部署或调用真实鉴权接口。

### 首次资金费完整性

资金费证明同时使用账本和独立仓位累计值。CrossEx 读取当前 `funding_fee/position_id/create_time` 与 `/crossex/history_positions` 的完整关闭记录；Bitget UTA 读取当前 `totalFunding/createdTime` 与 `/api/v3/position/history-position` 的完整历史记录。历史读取沿用固定毫秒窗口和有界分页；缺字段、重复页/游标、超过窗口/页数预算或读取失败不能作为空历史。

内部 `external_equity_anchor` 追加 `funding_positions`：以精确 route、原始 symbol、方向、生命周期标识保存原生累计值和创建/关闭毫秒时间。CrossEx 用 position_id 区分生命周期；UTA 当前接口没有 positionId，使用精确合约、方向和 createdTime，历史响应必须匹配。双方向保持独立，不按 ticker 或净数量合并。部分平仓历史不重复加总，需当前累计值或完整关闭的最终累计值支持。

对每个 `route|原始symbol|quote`，独立累计值相对起点的变化必须等于该合约新增资金费流水总和；不能用同所同币种的现金净变化为零替代。已结束仓位缺最终累计值、生命周期无法接续、创建时间在起点前但起点没有记录、或本程序已成交订单在该区间没有对应生命周期证据时，均 `external_income_unverifiable` 停开。起点已记录的终结生命周期不得改写。原已见账本 ID/摘要、现金与成交核对、十秒新鲜度和发送前版本检查继续独立生效。

双账户采样一致性检查追加 UTA totalFunding/createdTime 和 CrossEx funding_time/create_time。本地采样时间保留秒的小数精度，与账本比较时采用同一向下取整的整数毫秒边界，不要求本机时钟恰好落在整数毫秒上。

`GET /api/v1/external-positions` 的成功 `income` 追加 `funding_verified: true`。它表示本次已完成独立累计资金费核对；旧证明缺少该字段不允许开仓。其他成功/失败字段和错误码不变，失败不可解释为资金费零。旧库缺 `funding_positions` 不自动补零或滚动起点，须保持停开，按原程序空仓、完整新证据及审计要求执行显式 rebaseline/release-halt。已有新起点的重基线必须先核对原区间，不能绕过尚未补齐的资金费证据；旧日亏锁和停机原因仍保留。

### 外仓身份与 HL 空订单上限

登记、风险投影、负债归属和 `remote-positions` 展示共用 `position_identity` 解析。原始仓位中的 exchange_type、business_type/business、tag/dex、quote、route 若存在，必须与原始 symbol 解析出的完整身份及登记一致；冲突时登记拒绝、不得排除名义额或豁免负债，展示为 identity_verified=false、unregistered。官方仓位行未提供 exchange/tag 等字段时，由精确原始 symbol 和当前唯一目录记录共同核对，不臆造 ticker 别名。

外仓目录仍要求唯一原始合约、exchange/business/tag 一致、live、support_cross=true、delist_time=0，以及正的 lot_size/min_size/tick_size。`max_limit_size` 必须有字段；显式 null 保留为未知订单上限，可用于外仓身份登记。提供非空值时仍要求有限正数。没有把 null 改成无限大，没有修改交易目录、定量或发送校验。

该行为覆盖目录内 `HYPERLIQUID_FUTURE_io:OAI_USDC` 的订单上限为 null、CrossEx DUAL 同时存在 long/short 的情况。登记仍按完整合约施加双方向永久禁单，任一方向尚有持仓不能取消登记。目录缺失、重复、过期、错误 DEX、身份矛盾、程序历史所有权或远端活动订单仍按原 409 拒绝。

### HALTING 后台与明确零成交拒单

HALTING 重启仍启动账户刷新、目录/参考数据刷新和基线采样线程；启动账户读取失败也保留这些线程。协调器继续执行有界减仓重试，刷新线程阻塞不阻止协调器尝试收尾。开仓继续被状态及停机原因锁禁止；成功刷新不清 halt_reason，核实程序已平后进入 HALTED，释放仍需原人工流程。正常启动对非本程序 Bitget 活动订单的拒绝规则保留；HALTING 分支记录该阻断原因并保留后台及程序减仓机会，不接管这些订单。

已持久化发送后收到明确 placement rejection、没有订单接受或成交证据的记录，原 REJECTED 事件直接保存完整零成交记账：filled="0"、fee="0"、vwap=null、fill_ms=null、trade_ids=[]、accounting_pending=false，并追加内部 `zero_fill_evidence={source,identity}`。identity 为订单身份/发送记录摘要，非授权凭据。拒单率统计继续包含该记录。

原有缺 fee 的 REJECTED 在 executor recover 时仅可通过匹配的持久化 SENT → REJECTED 审计修复；需原始事件和当前记录完全一致、明确拒绝、零成交、无 oid/成交价/成交时间/交易 ID 等冲突。存在 ACK、UNKNOWN、其他终态、身份改变、缺失审计或正成交时不适用。恢复与 `REJECTION_ACCOUNTING_RECOVERED` 审计同事务提交，失败回滚，重复执行不重复记账；不提交或重发订单，不清其他锁。

公开字段核实：[CrossEx 当前/历史持仓与合约目录](https://www.gate.com/docs/developers/crossex/en/)、[Bitget UTA 当前/历史持仓](https://www.bitget.com/docs/catalog/trading/position-management)。公开文档定义 max_limit_size 为限价下单数量上限；本次 null 的存在来自用户提供的只读数据库证据，不宣称文档承诺所有交易所均返回 null。真实账户的新历史接口权限、生命周期语义、延迟、分页量和十秒预算仍待后续只读/影子验证，证据不足持续停开。

## 第十六轮增补（2026-11-12）

本节仅追加，补强第十五轮累计净额无法证明 UTC 分日归属的缺口；第十四轮程序风险口径、金额阈值、归属、永久禁单、UNKNOWN 不重发、发送屏障、十秒快照时效及人工管理前提均保留。本轮只有本地提交和离线验收。

### UTC 分日资金费证明

`income` 成功对象新增 `funding_daily_verified: true`。最终开仓关口同时要求它和原 `funding_verified`，旧成功证明缺此字段不能开仓。生命周期累计净额相等、账户余额不变，以及补回一组相反流水，均不能独自证明跨日资金费完整。

内部 `funding_checkpoint` 保存上次**全部核验成功**的 captured_at、归属 revision、equity_revision、独立 funding_positions 和历史 ID/摘要。它与 external_income、历史索引及 UTC 起点在同一事务提交，提交前再次比较原检查点、原起点、快照版本、订单摘要和时效。失败不推进检查点或滚动日界；重启继续使用持久化检查点。rebaseline/release-halt 必须先验证已有区间，新检查点和旧值进入原审计事务，失败回滚。

每个生命周期的累计增量在按合约净额相加之前，必须能确定落在一个 UTC 日内：下界为检查点时间与创建时间的较大者；上界为可核实的收取时间或关闭/观测时间。然后按 `route|完整原始symbol|quote|UTC日` 与新增资金费流水精确比较。资金费时间为毫秒，不按服务器本地时区分日。

CrossEx 当前仓位的 `funding_time` 是独立收取时间，0 明确表示尚未收取；归一化保存为 `settled_ms`。0 与非零累计值冲突、非法/未来/创建前时间或时间倒退均拒绝。午夜之后取得的当前仓位，如果该时间仍在午夜之前，可将其累计值作为该日界的独立累计证据。不能仅从 funding_fee=0 推导无结算，也不能用“最新一条流水出现”推导所有中间结算完整。[CrossEx 官方字段定义](https://www.gate.com/docs/developers/crossex/#query-contract-positions)

完整关闭仓位及 Bitget UTA 没有同等收取时钟时，仅使用生命周期和观测边界；跨日区间无法归入单日就以 `funding_utc_boundary_unproven` 停开。各自创建并结束于单日的不同生命周期，可提供逐日独立累计证据。相同单日内相反金额允许净额核对；不同日不能先互相抵销。缺少日界证据的区间即使全部账本后来补回，仍保持停开；不自动重基线或伪造日界快照。

旧已终结生命周期缺可选 settled_ms 时按 unknown 比较其原字段；不是填 0，原累计值、创建/关闭时间及身份仍不可改写。旧库缺 funding_positions 的规则不变。Bitget 当前/历史 totalFunding 仍仅视为生命周期累计值，不因它为 0 就假定跨日未发生相反结算。[Bitget 官方仓位接口](https://www.bitget.com/docs/catalog/trading/position-management)

### 完整历史分页与失败诊断

CrossEx account_book/history_trades/history_positions 继续固定毫秒窗口、每页 1000、page 从 1 开始递增；Bitget financial-records/history-position 每页 100，下一页原样使用响应 cursor。Bitget 短页带非空 cursor 时必须继续；空页仍带 cursor、满页无 cursor、缺/非法 cursor、重复 ID 或重复 cursor 均失败。没有按资金费、ticker 或当前仓位过滤全账本。[CrossEx 历史接口](https://www.gate.com/docs/developers/crossex/#query-account-asset-change-history)、[Bitget 财务流水](https://www.bitget.com/legacy-docs/uta/account/Get-Financial-Records)

取消旧固定 20 页截断。完整区间按最多 30 天切分，每个子窗为闭区间，下一窗从前窗结束毫秒 +1 开始；跨窗继续检查唯一 ID。每个接口最多 1024 次请求、250000 行、8 秒读取期限，原传输限频及两账户整体十秒新鲜度不提高。达到任一预算、权限/限频/网络失败或窗口内账户结算变化，都仍 complete=false。大账户分页可完成不等于其所有现金流已可归属；后续独立资金费、现金、成交、费用和身份核对仍必须通过。

`external_failed_snapshot.activity` 保留 from_ms/to_ms/complete，并新增 `failure`，失败时保留此前成功完成的接口列表。固定诊断字段如下，不保存响应正文、异常正文、账户 ID、密钥、签名或原始 cursor：

```json
{"route":"crossex","endpoint":"/api/v4/crossex/account_book",
 "from_ms":1794441600000,"to_ms":1794478452271,"page":2,
 "rows_read":1000,"cursor_used":false,
 "condition":"request_failed","error_kind":"rate_limited"}
```

历史失败具体 condition 包括 `page_budget_exhausted`、`row_budget_exhausted`、`read_deadline_exceeded`、`duplicate_id`、`cursor_repeated`、`cursor_missing_or_invalid`、`full_page_without_cursor`、`empty_page_with_cursor`、`response_shape_invalid`、`response_list_invalid`、`row_identity_missing/invalid`。传输失败可追加 HTTP 状态和白名单 api_code；未知 code 不照抄。若失败发生在活动订单或第二份账户快照，记录对应 stage。双快照不一致、跨 UTC 日、远端活动订单及超时分别记录固定条件，不能再折叠成无原因的 false。

失败 `income` 可新增 `activity_failure` 和 `income_failure`，经现有 external-positions GET 返回；ALERT 审计和服务标准输出同样携带这些安全诊断。去重比较接口/页/条件/错误码，忽略窗口时间和行数变化，维持同一事件每小时提醒及恢复后再告警。归属缺失与历史读取失败分别记录，不把二者混为已核实零收益。

### 停机仍以程序仓位为准

`finish_shutdown` 只消费本次 flatten_all 返回值。已登记外仓、程序空仓、无活动 pair/未决订单/待记账/内存待写，且新鲜原始身份核实成功时，退出 0；无须收益证明为 true。程序未平、未登记远端仓位、身份/读取失败或上述义务未清仍退出 1，外仓永不被减仓。

成功和有返回状态的失败停机都会输出 `shutdown_positions`，保留 `program_flat/account_flat/external_positions/attempt_id/verified`，追加 `remaining_positions`（剩余程序或未登记远端仓位）和 `local_active_pairs`。失败仍另外输出 shutdown_residual_exposure。旧 flatten_status 不代表新的 systemctl stop 结果，必须比对 attempt_id 与 captured_at。

## 第十七轮增补（2026-11-12）

本节只追加。原金额阈值、资金费逐日核对、完整性、十秒快照时效、流水消失/改写检测、发送屏障、UNKNOWN 不重发、外仓永久禁单和人工管理条件均保留。本轮仅本地离线验证，不包含线上鉴权、部署或实盘许可。

### 起点与检查点的分日证明来源

内部 `external_equity_anchor` 和 `funding_checkpoint` 增加 `funding_daily_proof`：`version=1`、`source`、`equity_revision`、`payload_hash`。摘要绑定该起点/检查点除证明自身以外的全部内容，包括累计资金费 carry、offset、生命周期与历史索引。它是本地证明来源及内容一致性标记，不是交易所签名。

source 只允许 `initial_flat`（真正新程序且无登记、无订单历史、程序空仓的首次起点）、`audited_baseline`（原显式带审计管理流程）、`daily_continuity`（完整跨区间和逐日核验成功）。创建证明与原起点/检查点、收益及审计同事务提交；写入失败不留下新证明。

使用起点和检查点前必须验证版本、允许来源、当前 equity_revision 和内容摘要。缺检查点或检查点不属于当前起点/基线时，只能回退到已具有有效分日证明的起点；不自动给旧净额起点或旧无版本检查点补标记。已有适用检查点的证明损坏也停止核验。旧 r15 错误滚日、以及无来源证明的 r16 状态不能因净额为零、最新收取早于起点而自动认证。无法补足来源/日界证据时，继续以 `funding_utc_boundary_unproven` / `external_income_unverifiable` 停开，不滚日起点，不自动重基线，也不清旧损失锁。

### 历史接口的明确空响应

Transport 先验证 HTTP 和 Bitget 外层 `code="00000"`，之后才解析 data。只有 `/api/v3/account/financial-records` 同时包含 `list`、`cursor` 两个键且二者均明确为 null 时，接受为终止空页；首末页均适用。这是用户报告的 f559f95 影子实测形态，不能扩展为所有接口的通用缺省值。`list=null,cursor=""` 的原拒绝断言保留；缺键、非成功 code、null data、错误 list 类型、非终止 cursor 等仍失败。

| 接口 | 本轮接受的空形态 | 依据和边界 |
|---|---|---|
| Bitget financial-records | `list=[],cursor=""`；另接受明确的 `list=null,cursor=null` | 数组/字符串是文档 Schema；null/null 另有本次影子实测证据 |
| Bitget history-position、trade/fills | `list=[],cursor=""` | 文档定义数组/字符串，未查到专门 null 空页承诺，null/null 仍拒绝 |
| CrossEx account_book、history_trades、history_positions | `[]` | 文档响应为数组；null、对象、缺失结构仍拒绝 |

官方公开来源：[Bitget 财务流水](https://www.bitget.com/legacy-docs/uta/account/Get-Financial-Records)、[仓位历史](https://www.bitget.com/legacy-docs/uta/trade/Get-Position-History)、[成交历史](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Fills)、[CrossEx 历史接口](https://www.gate.com/docs/developers/crossex/)。数组空页处理是按 Schema 的解释，不冒称这些页面提供了 null 的专门示例。

分页解析共用 `page_rows`。Bitget 成交/活动订单读取也必须消费短页非空 cursor，不能先按短页返回；缺/非法/重复 cursor、空页带 cursor、满页无 cursor 继续拒绝。CrossEx 订单成交读取同样显式验证数组。无成交明细不能冲销已确认的正成交量，原数量、费用及身份核对保留。

### 增量计算缓存与资源边界

单轮核算共用一份完整历史索引；下一轮只有规范 JSON 字节完全相同的已读取行可以复用摘要。不同 JSON 类型（例如 1 与 true）必须重新计算。缓存最多保留 25,000 行和 8 MiB 行内容，超限只降低缓存命中，不扩大读取预算、不减少核验。它不提供缺失的远端行、不提供已认证收益，也不替代持久化历史。已核实历史完全未变时，不重复序列化/写入其整份索引；有新增时仍在原事务中提交。

脱敏器每份文档只建立一次秘密/签名集合，保留最长优先替换和敏感键过滤；按 token 长度查找，仅保存实际匹配项，长文本不分配全部候选子串。新密钥/签名在下一次脱敏生效，签名环仍为原 2,048 条，敏感键缓存最多 1,024 项。

**网络读取仍完整覆盖原窗口。** 查阅的历史接口没有可用于证明旧页未变化的版本、摘要或删除游标；单纯只拉最新时间段会漏掉旧流水撤回。为满足“不降低风控”，本轮实现计算/写入缓存，未实施纯追加网络拉取或降低旧窗口复核频率。所有历史失败、缓存失效、流水消失/改写与迟到回填仍走原停开条件。若后续要求减少历史网络请求，须先获得可证明历史完整性的独立机制，不能只缩短窗口。

资源数字只作为指定本地合成负载的实测，不能替代线上 f559f95 的 CPU、Linux RSS、systemd 或持续影子验收。现有 service 的 `MemoryMax=192M` 本轮不调整；测量和剩余验收边界见第十七轮交接。

## 第十八轮增补（2026-11-12）

### 分页终止与续读

本节更新第十七轮空页约定。Bitget Transport 必须先验证 HTTP 成功和 `code="00000"`，才能把 data 交给解析器。四个分页接口为 unfilled-orders、trade/fills、history-position、financial-records。

| data 形态 | 处理 |
|---|---|
| `list=[],cursor=null`，两个键完整 | 四个接口均正常结束 |
| `list=[],cursor=""` | 保留正常结束 |
| 非空数组（满页或短页）+非空字符串 cursor | 继续读取，游标不得重复 |
| 短非空数组+`cursor=""` | 保留正常结束 |
| `list=null,cursor=null`，两个键完整 | 仅 financial-records 正常结束，保持已核实的接口范围 |
| 缺键、null data、非列表、布尔/数字/对象游标、空页带非空游标、满页无游标、非空页+null 游标 | 仍拒绝，不返回部分成功 |

分页数量、总行数、截止时间、完整历史窗口、跨段/跨页唯一 ID、账户新鲜度和资金费证明保持原约束。`Bitget.paged` 的活动订单/单笔成交读取仍最多 20 页；共享 history 的账户历史读取仍受 1024 请求/250000 行/8 秒预算限制。CrossEx account_book、history_trades、history_positions 和单笔 fills 仍使用顶层数组及页码：满页继续，短页/空数组结束；open_orders 仍要求完整顶层数组。正成交必须核对实际数量、费用、币种和原始市场身份，空页不代表正成交费用为零。

公开文档核实：[Bitget 活动订单](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Pending)、[成交](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Fills)、[历史仓位](https://www.bitget.com/legacy-docs/uta/trade/Get-Position-History)定义按返回游标续读；[CrossEx](https://www.gate.com/docs/developers/crossex/)定义顶层数组和 page/limit。明确 null 形态依据用户本轮实测报告，不冒称文档提供 null 终止示例，也没有进行真实鉴权复测。

### 致命故障的安全诊断

CLI 保留 `reason=startup_or_runtime_failed_closed` 和退出码 1；标准输出 `kind=fatal` 增加 `error_type`（异常类名）及 `location`（最内层模块:行号）。在数据库已打开且可写时，同一诊断在关闭前落为 `FATAL` 事件，value 仅含 reason/error_type/location。类型/模块名经过字符及长度校验，无 traceback 时 location 为 `unknown:0`。

从不格式化异常正文或异常链，不记录源码、绝对路径、局部变量、响应体、账户数据、请求头或游标。若数据库未能打开或 FATAL 写入失败，仍输出原异常的安全诊断并退出 1；不保证不可写数据库中存在事件。现有可恢复周期告警与后台工作线程的处理语义保持。
