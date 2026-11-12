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
