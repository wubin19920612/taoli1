# crossex-exec

独立的 Python 3.10+ 标准库服务，CrossEx 下单覆盖 Gate / Binance / Bybit / OKX / Aster，Bitget 使用 UTA v3。默认影子模式。此交付仅本地验证，没有部署、账户实测或实盘授权。

**2026-11-12 最新状态：第八轮已合并终审修复。** `3b1d056` 为 `b24f5af` 与 `abf8dba` 的 no-ff 合并；后续 `8e95ea7` 修复共享锁顺序。SENT 两行原子提交后，在共享屏障内再次检查版本、映射、定量及年龄/硬风控；SQL 分页和第八轮采样优化共存。最新验收、原始汇总及逐项接口适配见 [合并终审修复](../../docs/task-handoff-2026-11-12-crossex-exec-r8.md#合并终审修复)。下列各轮数量为各自历史状态。

2026-11-12 第八轮：已接入参数、独立管理 API、动态成交额/类别、公开 OI 定量和人工确认的代码映射。保留并继续了 f125c4d 的全部 WIP；本轮仅本地交付。验收、性能实测、提交和边界见 [本轮交接](../../docs/task-handoff-2026-11-12-crossex-exec-r8.md)，最新请求/响应见 [接口契约](../../docs/live-exec-contract-2026-11-11.md)。下面第五至七轮的测试数字为历史记录。

第五轮已按 PM 决定完成本地验收：不变量 1 拆为 1a/1b/1c/1d，全部严格断言，2–7 保持原规则。三个固定种子各跑 2448 回合（2400 原随机回合加 48 覆盖回合），执行器 195 项和 fill-sim 158 项 unittest 全部通过。定义、原始汇总、各不变量计数及边界见交接文档“第五轮 PM 决定补充验收”；本地结果不代表实盘或部署验收。

第六轮已完成本地验收：增加持久化常态价差基准、超额开平仓、每小时资金费 ×2h、亏损退出冷却及只读观测字段。执行器 229 项、fill-sim 158 项测试通过；三个原种子各 2448 回合保留全部严格不变量，并实际覆盖基准准入和冷却。当前规则与证据见交接文档“第六轮交付”。仅核实官方公开文档，没有请求真实行情或账户接口，没有部署。

第七轮历史停点：新增两腿盘口同时性与 v1“乌龙指”开仓准入，以及时间差、放弃原因计数。当时 41 项新增测试通过，执行器全套 270 项有 8 项因旧模拟器 40bp 基准夹具被新上限 10bp 拒绝而失败；fill-sim 158 项通过。当时保留 `baseline_at_open == 40` 并等待 PM 裁决，没有绕过新准入条件；原始证据见旧交接“第七轮交付”。该段是历史状态，本轮两版本完整 323 项已通过，当前验收以第八轮交接为准。

**合入分支的历史状态：第七轮本地验收通过。** 已按用户批准使用非零 **5bp** 模拟器基准，执行器 270 项、fill-sim 158 项及三个种子各 2448 回合全部通过；保留 1a–1d、2–7 严格不变量和六类开仓拒绝的非空覆盖。此前 40bp 夹具造成的 8 项失败是已解决的历史记录，0bp 是未采用的历史提案。

2026-11-12 **终审修复本地验收通过：执行器 289 项、fill-sim 158 项、三种子各 2448 回合全过**，新增 19 项专项。修复包含整对发送前持久化、提交后的最终年龄/硬风控复查、原子意图准备、运行期孤立意图恢复和有界历史读取，并同步独立归档部署布局。验收原文、提交及内存实测见 [交接文档“终审修复”](../../docs/task-handoff-2026-11-11-crossex-exec.md#终审修复2026-11-12)。历史各轮结果保留作证据；本地通过不代表 Linux 实机、账户或实盘验收。

## 入口

在仓库根目录运行：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
python -m tools.crossex_exec report --db /home/ubuntu/wubin/crossex-exec/var/crossex-exec.db
```

未来经独立验收后，Linux 服务入口是：

```text
python3 -m tools.crossex_exec run --env-file /home/ubuntu/wubin/crossex-exec/crossex-exec.env
```

`run` 会读账户鉴权接口，因此不属于本次会话的允许运行范围。没有真实费率或账户核对资料时拒绝开仓；不会采用公开默认手续费替代真实手续费。`report` 只读业务数据，不访问网络，但 SQLite 打开时会检查已有表结构。

## 模块

| 文件 | 责任 |
|---|---|
| `market.py` | 公开目录、精确映射、50 档盘口、成交额、当前/24h 历史资金费 |
| `admission.py` | 第七轮开仓专用时间/资金费/平坦基准/单所异常门槛与不可放松的配置 |
| `baseline.py` | 六所批量买卖报价解析、6h 中位数/MAD、预热/结构性拉黑、无方向组合键和冷却条件 |
| `policy.py` | Decimal 步长公倍数、定量后的 VWAP 复核、超额门槛、退出与熔断规则 |
| `adapters.py` / `transport.py` | 两家签名、IOC、查单、撤单、分页成交、账户只读接口、限频与总超时 |
| `execution.py` | 两腿 INTENT 原子准备；共享关口原子写 SENT、最终复查后并发发送；未知结果与孤立意图恢复 |
| `accounts.py` | 持仓模式、3 倍杠杆、真实费率、保证金率及持仓归一化 |
| `background.py` | 账户快照缓存与有界后台刷新线程；发送路径不读取私有网络接口 |
| `limits.py` | 150/600/150 USD 硬上限、独立权益基线、每笔开仓发送前校验 |
| `engine.py` | 触发、持仓管理、最多三次补平、60 秒对账、风险停开/全平 |
| `stops.py` | 独立停开锁集合与按持仓归属的解除；`stop_reason` 仅为兼容展示 |
| `storage.py` / `security.py` | SQLite WAL/FULL、整对事务、SQL 状态/订单筛选和 rowid 分页、文件权限与脱敏 |
| `reporting.py` / `__main__.py` | 模拟与真实成交比较、CLI、300 秒 JSON 状态、即时 JSON 告警 |
| `api.py` / `api_data.py` | 可选的鉴权只读 HTTP 服务、独立 SQLite 只读连接、契约字段投影和同事件 fill-sim 对照 |

复用 `fill_sim` 的 `Market/Spec/Book`、`opening/closing/walk/common_step/track_interval`、原始盘口/规格/资金费解析、`ShadowAPI`、`PublicClient/Limiter/RequestQueue/DaemonPool` 及有总时限的 DNS/HTTP 基础设施。唯一的 fill-sim 修改是允许校验 Gate 的列表响应（目录、ticker、资金费历史）。

## 配置

只读取 `--env-file` 指定的文件，默认 `/home/ubuntu/wubin/crossex-exec/crossex-exec.env`，不继承进程环境中的密钥。文件必须为服务用户所有、普通文件、POSIX 权限 **0600**；Linux 禁止符号链接。Windows 不能提供这组 POSIX 保证，因此只用于离线测试，不运行服务。

| 变量 | 默认值/要求 |
|---|---|
| `CROSSEX_LIVE_ENABLED` | `0`；只有用户本人可在后续验收后设为 `1` |
| `CROSSEX_DB` | `/home/ubuntu/wubin/crossex-exec/var/crossex-exec.db` |
| `CROSSEX_STOP_FILE` | `/home/ubuntu/wubin/crossex-exec/var/STOP`；**存在即停开** |
| `CROSSEX_POSITION_MODE` | `DUAL`，另支持 `SINGLE`，必须与账户一致 |
| `BITGET_POSITION_MODE` | `hedge_mode`，另支持 `one_way_mode`，必须与账户一致 |
| `BITGET_RISK_RATE_CONFIRMED` | `0`；核实 UTA `effEquity / mmr` 倍数口径后方可设 `1`；实盘启动要求此确认 |
| `CROSSEX_MAX_LEG_USD` | `150`；只可调低，必须为正数 |
| `CROSSEX_MAX_TOTAL_USD` | `600`；只可调低，两腿总和，不取对冲净额 |
| `CROSSEX_MAX_EQUITY_DROP_USD` | `150`；只可调低，任一账户独立触发 |
| `CROSSEX_MAX_BOOK_SKEW_MS` | `300`；两腿交易所盘口时间戳绝对差，毫秒，只可调低 |
| `CROSSEX_MAX_BOOK_AGE_SECONDS` | `1`；每腿交易所时间及收到时间最大年龄，秒，只可调低 |
| `CROSSEX_MAX_FUNDING_DIFF_BPS_H` | `0.5`；两腿每小时资金费率差的绝对值，bp/h，只可调低 |
| `CROSSEX_MAX_FUNDING_RATE_8H` | `0.0005`；每腿折合 8 小时资金费率绝对值，比例（0.05%），只可调低 |
| `CROSSEX_MAX_BASELINE_BPS` | `10`；方向基准绝对值，bp，只可调低 |
| `CROSSEX_MAX_BASELINE_MAD_BPS` | `10`；方向基准 MAD，bp，只可调低 |
| `CROSSEX_MAX_NORMAL_DEVIATION_BPS` | `10`；正常腿及独立参考所中间价对 M 的最大偏离，bp，只可调低 |
| `CROSSEX_MAX_REFERENCE_AGE_SECONDS` | `90`；最新批量报价轮次的最大年龄，秒，只可调低 |
| `CROSSEX_MIN_OUTLIER_SHARE` | `0.7`；异常腿偏离至少占开仓超额的比例，只可在 `[0.7,1]` 内调高 |
| `CROSSEX_MIN_VENUES` | `3`；同币有效行情所数，只可设为 3–6 的整数，必须包含两腿及至少一家其他所 |
| `CROSSEX_BASES` | 兼容旧名单：非空仅作额外开仓限制，逗号分隔；空值不限制。采样始终按动态成交额预筛 |
| `TAOLI_BASE_URL` | `http://127.0.0.1:8000`，按实际服务端口配置 |
| `TAOLI_API_TOKEN` | 空；如 taoli1 需要认证则由用户在私有文件中配置 |
| `CROSSEX_API_KEY` / `CROSSEX_API_SECRET` | 空；仅 CrossEx 所需权限 |
| `BITGET_API_KEY` / `BITGET_API_SECRET` / `BITGET_API_PASSPHRASE` | 空；UTA 交易及账户管理读取权限，实盘才需交易写权限 |
| `CROSSEX_EXEC_API_TOKEN` | 空；非空才启动数据接口，与 `X-Api-Token` 常量时间比较；仍只从上述私有 env 文件读取 |
| `CROSSEX_EXEC_API_BIND` | `127.0.0.1:8795,172.18.0.1:8795`；逗号分隔，可只选其中一个；可省略 `:8795`，不允许其他地址或端口 |
| `FILL_SIM_DB` | 空；可选 fill-sim 主数据库路径，同时只读检索其同目录日期归档；未配置或无法匹配时 `sim=null` |

应用不修改账户模式、杠杆、资产模式或权限。CrossEx 必须为 `CROSS_EXCHANGE`，各目标合约杠杆必须已经是 3；Bitget 必须为 `accountMode=unified`、`assetMode=multi_assets` 和配置的 `holdMode`。逐合约 `symbolConfigList` 必须为 crossed、3 倍杠杆。模式检查失败直接拒绝启动，不用空仓推断模式。旧 `GATE_API_*` 配置会被拒绝。

第七轮所有 `MAX_*` 上限来自 `admission.py` 常量；配置只接受有限、非负且不高于默认值的数字，调松直接拒绝启动。两个 `MIN_*` 下限也由常量约束，不能调低；负数、NaN/Inf、无效文字均拒绝。配置仍只来自用户指定私有文件。重拉次数固定为一次，不提供调大或跳过校验的开关。

## 只读数据接口

响应字段以 [`docs/live-exec-contract-2026-11-11.md`](../../docs/live-exec-contract-2026-11-11.md) 为准。`__main__` 在执行器成功启动后接线启动 API，退出时关闭监听；`report` 不启动 API。接口不调用账户、行情或交易 API；GET 使用独立只读连接，管理写入使用独立连接并与发送关口共享数据库路径锁。

| GET 路径 | 行为 |
|---|---|
| `/api/v1/summary` | UTC 生成时间、模式、停开状态、额度、最近持久化权益/对账、已平仓损益及真实费率 |
| `/api/v1/positions` | 活动持仓；影子意图不算实际仓位 |
| `/api/v1/trades?limit=200&before=<closed_at>` | `CLOSED` 记录按关闭时间倒序；同时间按 ID 倒序；`before` 为严格小于、必须带时区；`limit` 最大 500 |
| `/api/v1/stats?days=7` | 最近连续 N×24 小时内关闭的记录；`days` 为 1–365；未知收益不混入直方图 |

每个请求必须携带一个 `X-Api-Token`。缺失、错误或重复 token 返回 401；原四个业务接口的非 GET 请求返回 405；第八轮管理写路由使用独立管理令牌，见契约。参数错误为 400，未知路径为 404，读取失败或繁忙为 503；响应均不缓存。错误响应和日志使用固定原因，不回显 URL、请求头、数据库内容或异常文本。

默认启动两个独立 IPv4 监听，直接绑定数字 IP，不执行标准库 HTTPServer 默认的反向 DNS 查询；本机没有 `172.18.0.1` 时记录 `api_bind_unavailable`，保留 `127.0.0.1`。地址配置仅接受上表中的两个地址，禁止 wildcard/公网/主机名和改端口。未配置 token 时记录 `api_disabled_no_token`，不创建监听或数据库读取器。绑定失败、请求异常、监听线程异常和日志异常均隔离，不修改执行状态。

执行库及模拟库均采用 URI `mode=ro`、`query_only=ON` 和 SQLite authorizer：拒绝 DML、DDL、ATTACH 及关闭只读限制。执行库每次请求独立短事务快照，完成读取后立即关闭连接；不创建缺失数据库。每个监听只有一个 daemon 工作线程，全服务只允许一个数据查询，忙时立即返回 503；SQLite 等锁最多 50ms，读查询预算 2 秒、累计 JSON 输入预算 8 MiB。预算超限返回不可用，不截断统计后冒充完整结果；这是资源保护，不是 Linux 内存或交易延迟验收。summary 在 SQL 排除 SHADOW/SKIPPED；各接口逐仓读取相关订单，投影后释放历史订单与盘口，只保留响应/统计所需字段。

字段口径：

- 数值以 JSON 数字返回，时间统一 UTC ISO8601，缺失字段保留为 `null`。仅输出契约列出的字段，并对配置中的凭据/token 和已注册签名再次脱敏。
- `today/total` 为已平仓交易的汇总，今日按 UTC 关闭日期划分；不包含未平仓费用和浮盈亏，不替代执行器的日亏/累计亏风控计算。任一成员记账未知时，相应合计及胜数为 `null`。跨资金费结算尚未确认的资金费和净收益保持未知；确认未跨期的资金费是 0。
- `entry_vwap/exit_vwap` 来自真实成交，平仓多笔按数量加权；不使用意图限价。`net_bps` 分母为多头开仓实际名义额；两腿时间差来自开仓订单 `sent_ms/fill_ms`。
- `baseline_bps/excess_bps/funding_cost_bps` 是开仓复核时的估算快照，关闭后继续保留；旧记录为 null。summary 增加 `baseline_ready_pairs/structural_spread_pairs/cooldown_pairs`，按无方向组合计数，详见接口约定。
- 第七轮 positions/trades 增加 `open_book_skew_ms/close_book_skew_ms`，旧记录为 null；summary 增加持久化 `opening_skip_counts`，六类原因按机会 ID 各计一次，重拉成功不计数。字段含义及平仓缓存盘口的时间差口径见接口约定。
- `caps.total_open_usd` 是本地剩余量 × 开仓成交均价的两腿名义额之和，不含未确认订单预留及交易所未入本地的仓位，不等同于发送前硬风控总敞口。账户 `drop_usd=max(0,baseline-current)`，两边独立。`HALTING/HALTED` 均令 `halted=true`，此字段不证明已经全部平仓。
- 账户 `ok` 表示最近对账数据是否可用，不表示可开仓；对账超过 60 秒或失败时不报告健康。原执行器无保证金占用的 `1e30` 哨兵投影为 `margin_multiple=null`。多市场费率一致且 1 小时内刷新成功才给单个 route 的 taker 值；不一致返回 `null`，不掩盖 special 费率。
- 库里未保存真实标记价格，因此 `mark_price=null`。当前价差和浮动毛收益按两腿各自 60 秒内的缓存盘口、剩余量和深度 VWAP 计算，盘口不足/过期则为 `null`；不是即时交易所标记收益，未扣未实现平仓费和资金费。`stop_distance_bps` 是距离“开仓价差 +50bp”价差止损的距离；独立的浮亏 1% 止损规则未改变。
- `sim` 严格匹配 `trigger_id == fill-sim.event_id`、`measured`、100 USD、相同方向的两个完整 Market 身份，以及已平仓结果。不会拿同 ticker、300 USD、延迟版本或执行器开仓复核估值替代。重复匹配视为不确定。`sim.gross_bps` 由模拟开平仓名义额重建往返毛收益，不是开仓价差；`sim.net_bps` 使用已知资金费口径的模拟结果。
- `slippage_bps=实际 net_bps−sim.net_bps`，负数表示实际结果更差，包含两种执行轨迹、费用及持有期的综合差异，不是单笔撮合滑点。为对应契约示例中的负向尾部，`slippage_bps_p90` 取有符号差异的下十分位；成交时间差 p90 取上九十分位，均线性插值。`vs_sim.n` 只统计可计算差异的匹配交易；时间差和单腿率统计所有关闭记录，未知标志使单腿率为 `null`。
- `by_spread_tier` 使用匹配模拟记录保留的原 shadow 档位。执行库未单独保存该字段，无法取得时用 `key=null` 组，不从事后实际价差倒推。其余分组存在未知收益时，相应合计/均值/胜率为 `null`。

`before` 只有关闭时间游标；同一关闭时间超过单页容量时，严格小于游标会跳过同时间的其余项，这是现有契约的边界。接口不会自动改契约增加复合游标。

## 安全语义与运营

- CrossEx v1.0.8 数量统一为基础币。废弃的 `contract_size` 不参与 CrossEx 下单量计算；Gate/OKX 原生盘口张数由 fill-sim 换算为基础币。
- 基准采样独立于 taoli1/CrossEx 行情：每 30 秒按全市场目录与批量成交额预筛精确 USDT 永续合约，不受 `CROSSEX_BASES` 限制。六所批量 ticker 加 Binance/Aster 的 bookTicker，每轮最多 8 个公开 GET；成交额缓存最多 300 秒，但用于新样本的中间价必须在 90 秒内。复用公开请求队列（预热优先级 2）和每所 2/s、全局 8/s 限频，不新增请求通道。Aster 使用 v3 bookTicker；其余路径及字段见本轮交接。
- SQLite 新增 `baseline_samples`，按币种及字典序交易所组合每 30 秒桶最多一条，以 `(mid_b/mid_a-1)*10000` 采样，保留 24h。最近 6h 中位数为基准，MAD 为绝对偏差中位数；至少 300 点且跨度 ≥3h 才预热完成。最新点超过 90 秒拒绝开仓，断网不补造样本。反向基准先对每个样本做倒数变换再取中位数。
- 预热未完成拒绝原因为 `baseline_warming`；结构性拉黑为 `structural_spread`（规范方向 `abs(baseline) ≥80bp` 或 `MAD ≥40bp`），每 10 分钟重新评估，可自动恢复，缺数据不解除旧拉黑。拉黑状态及样本均跨重启保留。
- 按深度/OI/单笔/剩余额度确定的最终合法数量，以 50 档 VWAP 求开仓价差 `S`。`excess=S-baseline`；净超额门槛为 `excess−2*(taker_long+taker_short)*10000−funding_cost_bps ≥15bp`。资金费成本为 `max(0,rate_long*3600/interval_long_seconds−rate_short*3600/interval_short_seconds)*2*10000`。周期来自公开接口，缺费率或周期拒绝；收款合计按零，不补贴收益。保存周期和预估标记，不假设 8 小时。两个准入检查都使用这套公式，缺基准不回退到 0。
- 第七轮在初始复核和共享发送前复核都增加：两腿交易所时间戳差 ≤300ms、每腿年龄 ≤1 秒，收到时间也须在 1 秒内；负年龄/缺失或非有限时间不放行。时间检查失败立即通过既有公开队列并发重拉**两腿**一次，再检查；仍失败放弃并记 `book_skew/book_stale`。定价、基准读取、限频、写库耗时均计入复核年龄；重拉后仍检查原限价、净超额、最新真实费率和原硬风控。当前六所永续深度均提供交易所时间戳；缺字段只阻止开仓，不给平仓添加新限制，详见交接的官方字段表。
- v1 仅接受资金费差绝对值 ≤0.5bp/h 且每腿折合 8h 的绝对费率 ≤0.05%，读不到记 `funding_diff`；方向基准绝对值和 MAD 均 ≤10bp，否则 `baseline_not_flat`。原 24h 历史完整性、0.5% 黑名单、近结算禁开等条件继续保留。
- 基准模块每轮保存完整报价集合，失败/空轮替换旧集合，不跨轮拼接。至少三家有效参考所且包含两腿与外部参考，轮次年龄 ≤90 秒，否则 `too_few_venues`。用该轮所有有效所中间价的中位数 M，对照当前两腿深度中间价：只能买被砸低的一腿或卖被拉高的一腿，异常幅度 ≥净扣费前开仓超额的 70%，正常腿偏离 M ≤10bp，所有外部参考所也须在 M 的 10bp 内，否则 `not_single_venue_outlier`。M 可包含两腿，但至少有一家独立参考。30 秒参考采样与上述形态判定不能证明报价异常的实际成因。
- 开仓冻结 `baseline_at_open/excess_at_open`；止盈阈值为当前可平仓价差 `≤baseline_at_open+excess_at_open/2`，持有中不追随新基准。反向 50bp、浮亏 1%、2h、结算前 5 分钟、陈旧盘口退出规则保留。升级前缺快照的旧持仓保留原来半价差退出规则，API 不伪造历史基准。
- `stop_loss/max_hold/pre_funding/stale/one_leg` 退出且完整已知净收益 <0 时，按无方向币种/交易所组合冷却至 `closed_at+24h`。关闭记录与冷却在同一个 SQLite 事务落盘；重复刷新、延迟记账和重启不延长截止时间。盈利、零收益、正常止盈及熔断退出不触发；收益未知时继续由既有记账停开锁保护，确认后再写冷却。冷却在复核前及共享发送关口均检查，拒绝原因为 `pair_cooldown`。
- 不足 24h 资金费历史覆盖、历史有缺口、资料过期或倍率/代码不一致都不交易。OKX 成交额使用基础币成交量 × 24h 最低价的保守下界。
- 两腿意图及 client ID、对应事件在同一事务持久化，任一准备失败全部回滚。开仓两腿完成限频与建连后到达共享关口：原准入复核 → 两腿 `SENT` 及事件原子提交 → 重新检查硬风控、停开状态和最终盘口年龄 → 同时释放发送线程。最后复查后不再写库或重拉；1.01s 慢提交使整对拒发，1.00s 等号仍放行。超时/5xx/重复 ID 先查原单；**不会自动重发未知结果**。CrossEx text 查不到已终结订单不是未下单的证据。
- 影子模式从协调器和传输层双重禁止下单/撤单。影子意图不伪造成已成交持仓，不产生实盘收益；报告分开展示。
- 订单确认默认每 200ms 查询，最多 10s（限频可延迟查询）；超时保留 `UNKNOWN` 和敞口。成交记录延迟则保留 `accounting_pending`，60s 再补拉，不用 ACK 价格代替成交价。
- 恢复先查原单，再撤仍未确认的订单并复查，随后通过独立的 `Accounts.position_snapshot` 读取两腿实仓；此路径不依赖账户余额或 settings 检查。每次 tick 检查 SENT/ACK/UNKNOWN，即使 pair 状态或跟踪时间尚未更新也恢复。启动及运行期只将没有执行线程持有、且仍为 INTENT 的记录转成 UNSENT；正在等待发送关口的意图不被清理。崩溃后留下的 SENT/UNKNOWN 必须查原单，绝不作为未发送意图清理；未确认时即使远端暂时为空也不报 HALTED。
- `remaining` 始终为成交累计的本地流水，`remote_remaining/remote_captured_at` 单独记录远端数量。减仓每轮读取实仓并按其限量，远端数据不覆盖流水。旧 `position_anchor` 只迁移到远端字段，不再用于重建流水；成交/费用未记齐时不推进对账解锁，迟到成交不会导致重复平仓。已关闭历史不接受新持仓的远端快照。
- SENT 提交失败则不发送，状态退为 UNSENT；发送之后的 UNKNOWN/ACK/成交状态写失败保留在内存待写集合，下一轮重试持久化，开仓门同时检查待写集合。两腿共享放行后先完成各自发送，再并发确认，避免一腿的确认故障在另一腿发送期间提前触发恢复。
- 单腿补平一轮最多三笔，额外容差依次为 13/23/33bp，进度持久化。先读两腿真实持仓，每次只发送一笔减仓；确认成交并重读两腿持仓后，重新计算下一笔。优先按本腿步长向下取整；不足一步时，只有单独执行粗步长也能严格缩小裸露量才采用，不能依赖另一腿未来成交。零成交、裸露量未变小、读不到持仓或没有合法减仓量时，记录 `UNHEDGED`、持久化 `repair_stopped` 并停开；停止当前轮次后，后续 tick 按退避进入退出恢复，继续减掉剩余仓位。所有订单仍校验本腿最小数量、名义额、最大量和 tick。
- PM 的 1a 要求任何单腿补平/减仓不增净裸露；双腿平仓的 1b 允许有上界且不穿零的暂时裸露。部分平仓后重读两腿实仓，并优先逐单补平差额。1c 沿用 N=1：下一协调器 tick 进入恢复路径，失败的实际减仓按持久化 `next_close/close_attempts` 在轮末开始 2/4/8/16/32/60 秒退避，重启不重置期限；退避期间仍查询、撤销并确认未知订单。残余裸露触发独立 `unhedged_position` 锁和告警。1d 的开仓门禁直接检查流水/实仓的差额和未确认快照，即使锁尚未写入也不放行新开仓批次。
- 平仓发送前持久化 `remote_pending`，发送后必须取得新的两腿持仓快照才能清除。读取失败时继续恢复，不使用旧快照宣布平仓完成或清除补平锁；`UNHEDGED` 的数量、剩余量、超额腿暂记为 `null`，不把旧数值冒充当前实仓。
- 退出判断使用实际剩余量；用于止损的开仓成交价按真实成交均价、订单查询均价、意图限价依次取值，后两项只用于保守风控，不填入真实 VWAP 或收益。第六轮止盈使用上文冻结的超额阈值。订单均价字段按官方文档为 CrossEx `executed_avg_price`、Bitget UTA `avgPrice`。fill-sim `closing()` 只记录估值，异常不会阻断真实平仓；原 fill-sim 尚未实现新基准策略，不把其估值冒充相同策略回测。
- 盘口丢失时采用**该腿自己的最后盘口**，不把另一所价格冒充该腿可成交价；扩大限价仍不保证能成交。剩余仓位一直保留并告警。
- Bitget 私有接口只有 UTA v3，签名传输层拒绝非 v3 路径。公开深度/资金费沿用 v2 market，第八轮成交额/采样批量 ticker 使用 v2 的 usdtVolume/bidPr/askPr；旧 baseline_quotes 兼容辅助方法保留 v3。双向开平仓由实际 `side` + `posSide` 表达；`reduceOnly=yes` 仅用于单向模式。clientOid 最长 32 字符。
- CrossEx 保证金率使用 `maintenance_margin_rate` 并核对余额/维持保证金口径；Bitget 使用账户 `effEquity / mmr`，分母为 0 视为安全。两者 <3 停开、<2 全平。
- `HardLimits.check_order` 统一校验三项硬上限：整对进入适配器前预检，限频和建连后的共享发送关口再检查两腿；单笔入口也保留适配器前和实际发送前检查。总额度按远端标记名义额与本地确认敞口逐市场取较大值，再计入待发及未知订单剩余量。减仓不受单腿及总额上限限制，也不因风险读取失败而被阻断。
- 账户后台任务按 5 秒周期刷新，发送路径只读账户缓存；按读取开始时刻计龄，超过 10 秒不开仓。两个开仓工作线程都通过限频/建连后，在同一个关口复核两腿盘口 ≤1 秒、时间差 ≤300ms、v1 模式、净超额 ≥15bp、限价和资金费/费率有效性。时间检查不合格允许一次公共盘口并发重拉；完整复核统一通过后并发发送，不再各自否决。任一线程在关口前失败，两腿都不发送；统一放行后的网络/撮合失败仍需实际敞口恢复。
- 按第八轮 PM 裁决，最小数量和最小名义额按向下取整后的数量及最终 `quantity × limit_price` 检查；任一腿不足即 `size_too_small`，不再向上补一步长。交易所最大数量、单腿和总名义额硬上限继续保留。
- 首次启动保存 CrossEx `margin_balance` 和 Bitget `usdtEquity` 的独立基线；重启记录当前值并保留原基线，避免清除损失。任一边下降达到限额即进入 HALTING，全平确认后 HALTED。存在实盘历史而缺少独立基线时拒绝启动，需要人工核对迁移。
- CrossEx 任意正 liability 写入事件日志和每 300 秒状态 JSON。费率刷新失败会清除对应旧费率，读不到就不允许该市场开仓。
- 启动先恢复订单、重建持仓并对账，目录初始化与每小时费率刷新由后台完成。目录单所失败只移除该所开仓映射，30 秒后重试；已有持仓使用持久化的原市场身份继续管理。费率按触发所需的精确合约刷新，兼容名单非空时也预取其中合约，每个合约的费率和读取起始时间封装为不可变 `FeeSnapshot`，一次替换发布并按整个版本读取；单个失败只撤销该合约。
- 风险计算同时使用逐笔真实费用/已实现损益/浮动损益和账户权益变化的较差结果。使用用户选定的 UTA 账户；运行期间应避免其他策略交易，账户间转账/提现也会改变权益基线并可能触发停机。
- `HALTING` 表示累计亏损或独立账户回撤触发、仍在处理剩余或未知敞口；确认本地和远端均为空后才写 `HALTED` 并退出。`HALTED` 阻止自动重启。不得删除整个数据库“复位”。
- HALTING 每轮结束（包括持仓查询失败）后再开始 2/4/8/16/32/60 秒退避，之后持续以 60 秒重试；启动账户暂不可读也不会放弃已有 HALTING 恢复。
- 日损益为当天已实现损益加浮盈亏相对当日基准的变化。账户/持仓快照和持仓跟踪持续保存 `last_upnl` 的各仓数值、总额及采样时间；跟踪使用本腿可成交盘口估值。UTC 换日先用上日最后保存值冻结 `daily_upnl`，再记录新日观测，即使平仓早于新日首个账户快照也不丢基准。首次启动无历史时才使用首个有效观测。旧账户缓存仍不能放行开仓；这些观测不是精确午夜的交易所报价。当日日亏锁经盈亏回升或重启保留，跨日只清理它自身。
- 持仓对账只接受读取起点严格晚于最近本地成交观察/成交时间的快照。旧快照暂不用于比较，避免误报；真实 `position_mismatch` 之后连续三次不同的新快照一致，自动清除该锁并写 `POSITION_MATCH_RECOVERED`。重复读取同一缓存不累计，新的差异重置计数，其他停开原因保留。
- `stop_locks` 是独立原因及归属集合；`stop_reason` 只是只读接口和日志的兼容展示，不参与覆盖其他锁。`position_mismatch` 只能通过三个不同且流水记账完整的匹配快照解除自身；`unhedged_position/repair_stopped` 按 pair 解除，要求两腿实仓相等、与流水一致、无非终态/待记账订单且补平结束。其他 pair 或其他原因的锁不随之解除。HALTED 只能人工核对后解除，后台权益更新也不能将其改回 HALTING。
- 资金费跨期、非 USDT 手续费或优惠券扣费不会被猜成零成本。跨期成交报告保留 `trading_net_usd`，完整 `net_usd=null`，停开等待账单核对。
- 告警写本地事件表，并由 CLI 输出即时 JSON 到 journal；同一 HALTING 周期内按原因及数值去重，`EQUITY_HALT`/`UNHEDGED` 与 `ALERT` 共用一次判断，首次或数值变化才输出。后台线程最外层异常记录固定工作线程名和异常类型，同类型每 60 秒最多一次，不输出异常正文或 traceback。没有发送飞书、邮件等外部通知。状态 JSON 每 300 秒输出。
- `flock` 防止两个服务实例共享同一数据库；停机尝试减仓，无法清完则以失败退出供恢复处理。进程崩溃、OOM 或交易所不可用时仍可能留下真实敞口。

## 部署模板（本次未安装）

独立目录为 `/home/ubuntu/wubin/crossex-exec`。模板使用 `User=ubuntu`、`Group=ubuntu`；`WorkingDirectory` 就是此目录，`ExecStart=/usr/bin/python3 -m tools.crossex_exec run --env-file /home/ubuntu/wubin/crossex-exec/crossex-exec.env`。env、SQLite、STOP、锁文件及 `ReadWritePaths` 均使用该目录下的既有布局。保留 `Restart=on-failure`、`RestartSec=10`、`StartLimitIntervalSec=0`、`UMask=0077`。

以后单独授权部署时，按固定提交归档，包内必须同时包含两个模块，保留顶层 `tools/`：

```sh
# 在发布 checkout 中，用已经验收的完整提交号替换 <FULL_SHA>。
git archive --format=tar --output=crossex-exec.tar <FULL_SHA> tools/crossex_exec tools/fill_sim

# 将归档放到目标机器后，在独立目录解包；不要改变 tools/ 层级。
sudo install -d -o ubuntu -g ubuntu -m 0750 /home/ubuntu/wubin/crossex-exec
sudo -u ubuntu tar -xf /path/to/crossex-exec.tar -C /home/ubuntu/wubin/crossex-exec
sudo install -d -o ubuntu -g ubuntu -m 0700 /home/ubuntu/wubin/crossex-exec/var
cd /home/ubuntu/wubin/crossex-exec
sudo -u ubuntu /usr/bin/python3 -m tools.crossex_exec --help
```

首次配置才从 `tools/crossex_exec/deploy/crossex-exec.env.example` 创建 `crossex-exec.env`；已有配置保留。由 `ubuntu` 持有，文件权限必须 **0600**，目录只向需要的服务用户开放。没有 systemd `EnvironmentFile`，应用自己打开文件验证权限。初始 `CROSSEX_LIVE_ENABLED=0`、空白名单不产生开仓意图；`BITGET_RISK_RATE_CONFIRMED` 和真实费率等仍按原验收要求处理。代码依赖 `tools/fill_sim`，但不要求启动 fill-sim 服务；`FILL_SIM_DB` 只是可选对照库。

确认配置、资源和只读账户访问已获授权后，才安装 `tools/crossex_exec/deploy/crossex-exec.service` 到 `/etc/systemd/system/`，执行 `systemctl daemon-reload` 并启动影子服务。`run` 即使 LIVE=0 也会读取鉴权接口，本次未执行这些步骤。`ProtectHome=read-only` / `ProtectSystem=strict` 仅放行 `var` 写入；`PYTHONDONTWRITEBYTECODE=1` 避免在代码目录写缓存。

内存建议：模板暂设 **`MemoryMax=192M`**，用于后续影子实机验收。Windows 离线探针含 2000 条 SHADOW 历史、4000 订单、每腿每侧 50 档：活动仓位零结果的 Python 分配峰值由 **92.06 MiB 降至 0.0022 MiB**；完整分页遍历仓位/订单分别约 **0.72/0.20 MiB**；增加三条活动仓位后查询约 **0.16 MiB**，探针进程峰值工作集约 **41 MiB**。复现：`python -m tools.crossex_exec.tests.memory_probe`。相对约 500MB 可用余量，192MiB 留出约 300MB 给宿主机与其他服务；它是有余量的起始建议，不是 Linux RSS 上限证明。原 80M 没有完整负载验收依据，不再使用。

运行路径按 SQL 筛选活动仓位、pair ID、非终态/待记账状态和最近发送窗口；历史按仓位 64 条、订单 128 条的 rowid 页读取，聚合保留 Decimal 口径，不丢历史亏损、敞口或未确认订单。`all_orders()` 只保留为显式物化导出兼容入口，运行期不调用。只读 API 仍保留 2 秒/8MiB 预算，过量历史返回不可用，不将部分结果冒充完整统计。

实际安装、Ubuntu/Python 3.10/SQLite 3.37、systemd 权限和 cgroup 内存、实际白名单/API 查询并发下的 24h 影子验收、账户核对及开实盘开关均留待新任务。届时记录 `MemoryCurrent/MemoryPeak`、OOM/重启、主机剩余内存及跟踪延迟；自动重启不代表仓位管理连续性。详细证据见 [交接文档](../../docs/task-handoff-2026-11-11-crossex-exec.md)。

## 第八轮运行说明

参数存执行数据库，通过独立管理令牌更新。默认单所 24h 成交额 100 万、单笔上限 50、总名义额上限 600、持仓最多 3 对；参数范围、冻结版本与账户损失阈值见契约。固定 100U 只保留在独立旧估算辅助函数中，Engine 必须传入 sizing 结果。未知类别待人工判断，股票需明确 US/KRX 与参与市场，pre_ipo/commodity 不交易。

基准样本、状态、最新报价与成交额按 SQLite 行存储。升级迁移旧 baseline_status/baseline_quotes/market_tickers meta 值并保留结构性锁；之后公共行情不走递归凭据脱敏或整块 meta JSON 重写。订单、事件、配置与对外 API 保留原脱敏。样本 24h 留存、6h 窗口、300 点且 3h 预热、90s 新鲜度、10 分钟结构性重查均未改变。

`python -m tools.crossex_exec.benchmark_sampling --output <结果路径>` 为离线实测入口，默认 84 币种×6 所=504 原生合约、24h 历史；`--bases 500 --history-hours 6` 为放大检查，不使用网络或凭据。前后比较用起始 WIP 备份的 performance-before 模块，复现与结果见交接。

管理令牌 CROSSEX_EXEC_ADMIN_TOKEN 与只读令牌必须不同，默认不设置。真实 env、账户和系统服务未修改；本次合并保留终审分支的 service 布局与 `MemoryMax=192M`，尚未安装。

## 第九轮运行说明

完整接口与二次确认格式见 [第九轮契约增补](../../docs/live-exec-contract-2026-11-11.md#第九轮增补2026-11-12)，本地验收和线上待核实事项见 [第九轮交接](../../docs/task-handoff-2026-11-12-crossex-exec-r8.md#第九轮)。

```sh
# 运行中交给本机 ADMIN API；服务停止时持有同一个 flock 后执行。
python -m tools.crossex_exec rebaseline --env-file /private/crossex-exec.env --confirm --reason verified_transfer
# 服务已因 HALTED 退出后，confirm 必须精确匹配当前 halt_reason。
python -m tools.crossex_exec release-halt --env-file /private/crossex-exec.env --confirm account_equity_drop --reason verified_transfer
```

两项操作均要求本地与两边远端空仓、无非终态/待记账订单、无仓位类锁、无负债及两边新鲜正权益。读不到即拒绝；不发送订单、撤单或划转。失败输出固定拒绝原因，不输出交易所异常正文。运行中 rebaseline 要求原有独立 ADMIN 令牌；停机操作沿用 env 所有权/0600 和 flock，仍是 POSIX 运行路径。划转前先置 STOP 文件，核对后显式重设基线；不得通过删除数据库或修改历史费用复位。

重设后保留历史交易/费用统计，以审计中的风险计量偏移开启新的损失基线；新损失阈值不变。当日日亏锁、STOP 文件及其他原因锁不会被清除。release-halt 只能解除已完成全平的权益停机，不代替仓位/未知订单恢复，也不启动服务。

账户刷新使用三个不同的新鲜快照恢复 account_snapshot_unavailable；任何失败、陈旧或超过 10 秒的采样间隔重置计数。新 summary 的 account_snapshot 同时给出当前年龄与该次完整刷新耗时，重复读取缓存不会让观测“变新”。CrossEx 正负债现在会停开并告警，保留 coin/exchange_type/liability；连续三个新鲜无负债快照才解除这一原因，既有平仓/停机恢复继续可用。

告警、HardLimits、StopLocks 和账户生命周期统一使用 Store 的重入锁；需要组合时为协调器→Executor→Store。发送共享屏障在 SENT 提交后再次只读成交额缓存，检查两腿金额、时效、所数及其他原门禁。实际 sent_ms 由各腿传输在请求写出前采集到内存，随后随原有 ACK/UNKNOWN 等记录落库；sent_persisted_ms 单独保留写入阶段时间。观察不到实际时刻时不伪造 send_delta_ms，最近订单风险统计仍保守计入持久化发送阶段。

公开行情、基准状态按行比较，只写变化或删除当轮缺失数据；基准样本查询使用原主键的 bucket 范围，同时保留精确时间边界。采样仍为 30 秒，6 小时窗口/24 小时留存和所有开仓、持仓风险门槛不变。离线等价稳态测量：

```sh
python -m tools.crossex_exec.benchmark_steady --bases 187 --dynamic-bases 40 --history-hours 24 --warmup 30 --seconds 180 --output steady.json
```

该探针禁止外部 DNS/连接，合成 I/O 驱动实际公共解析、请求队列、Engine、账户/基准后台调度及并发 API 投影；以进程 CPU 秒除以实测墙钟秒计算单核占用。准备 187 币的 24 小时历史，另外 40 币随动态成交额进入/退出采样；额外币在此短测中尚未完成 3 小时预热。准备、30 秒调度预热和单独 cProfile 不计入 180 秒稳态区间。结果不替代 Linux/systemd、真实网络和 24h 影子验收。

## 第十轮：采样故障和管理并发

最新本地验收见[第十轮交接](../../docs/task-handoff-2026-11-12-crossex-exec-r8.md#第十轮)与[完整验收数据](../../docs/crossex-exec-r10-validation-2026-11-12.json)。副本加真实公开 GET 复现了不支持的非 ASCII 基础币导致整轮 baseline 中断；现在在动态候选和记录入口隔离这些身份，原 ASCII 校验、交易资格、采样/预热/风险门槛保持不变。公开目录保留原名。后台日志和 BACKGROUND_FAILED 增加模块:行号，仍不输出异常正文、源码或完整 traceback。

管理写请求的新必填字段：PUT config 顶层 `expected_version`；mappings confirm/reject 的 `expected_multiplier` 与 `expected_status`；universe classify/exclude/unexclude 的 `expected_status`。GET mappings 新增字符串 multiplier，GET universe 新增不透明 status；从用户所见读取结果原样回传。比较、更新和审计由同一写事务保护，冲突返回 409 并持久化审计；不要自动更新前提重试。完整 JSON 和 400/409/503 语义见[契约第十轮](../../docs/live-exec-contract-2026-11-11.md#第十轮增补2026-11-12)，此前不带前提的写入示例需要增加字段。

Python 3.12.10/指定 3.10.20 各执行器 415 项、fill-sim 158 项全过，三种子各 2,568 回合；原 1,934 个断言保留。仅本地 test/feat/docs 提交，没有推送或部署。HL `io:OAI` 外部持仓只有方案比较，当前仍要求专用账户，没有新增忽略名单或解除任何风控锁。

## 第十一轮：手动登记外仓

本轮实施方案 (b)，取代上节“外仓尚未实现”的历史状态。完整前提、返回字段和错误码见[第十一轮追加契约](../../docs/live-exec-contract-2026-11-11.md#第十一轮增补2026-11-12)，验收与限制见[第十一轮交接](../../docs/task-handoff-2026-11-12-crossex-exec-r8.md#第十一轮)。

先只读查看状态，核对完整原始合约，再提交用户实际查看的 status；以下为 POSIX 部署端示例，并非本次已经运行的鉴权操作：

```sh
python -m tools.crossex_exec external-list --db /private/crossex-exec.db
python -m tools.crossex_exec external-register --env-file /private/crossex-exec.env \
  --route crossex --exchange-type HYPERLIQUID --business FUTURE \
  --symbol HYPERLIQUID_FUTURE_io:OAI_USDC --quote USDC --dex io \
  --expected-status 'x1:<刚才查看的64位标记>' --reason Astro_manual_position
```

运行中走 ADMIN API，停机时保留原 env 0600/flock 并只读查询账户，交易传输强制关闭。目录刷新或另一用户修改造成冲突时，重新查看并决定，不自动重试。普通无 DEX 合约需要 `--dex ''`。取消登记使用同样参数的 `external-unregister`，须该合约远端两个方向无仓位且无活动订单；**取消后永久禁单仍在**。

登记、取消均不能解除既有损失/恢复锁。修改归属后需在程序空仓、无未知/待记账/恢复义务、远端无活动订单、无负债且保证金至少 3 等前提下显式 rebaseline；允许仅剩已登记外仓，审计保留其完整快照。release-halt 仍要求停机及精确确认原因，保留原当日日亏锁和其他锁。

程序日亏剔除经现金余额、完整账本、成交数量与费用核对的外仓净贡献；取不到证据即停开。外仓仍计入 600 总名义额、整户权益回撤、累计亏损、保证金和负债检查，程序不会自动平外仓。停机报告分 program_flat 与 account_flat，有外仓时必须明确全账户未平。

当前 `io:OAI_USDC` 可以登记和永久禁单，但**非零 USDC 损益缺少可核实的历史整户权益折算证据时仍停开**；没有使用 USDC=USDT=1 的假设。原副本中的 daily_loss 也不会因登记自动清除。此实现不承诺登记后该账户立即恢复交易。

双版本各执行器 457 项、fill-sim 158 项通过，三个种子各 2,664 回合，含外仓亏损、结算、目录变化和保证金/权益硬限制；[验收 JSON](../../docs/crossex-exec-r11-validation-2026-11-12.json)保留详细计数与日志指纹。本轮仅本地提交，没有 push、SSH、WSL、amend、真实鉴权或部署。

## 第十二轮：外仓结算币保守折算

本节取代上节非零 USDC 一律缺少历史估值而拒绝的描述。按 PM 指定的当前公开估值模型，OKX/Bitget/Bybit 的直接 `COIN/USDT` 现货报价经原公开 GET 限频队列获取，至少两家有效、源时间不超过 60s，取买一/卖一中点的中位数 `p`。外仓原币净贡献 `x` 折算为 `x*p + abs(x)*p*0.002` 后从整户同起点日权益差中扣除：盈利用上界、亏损用下界；分别折算币种后求和。报价不齐、过期、极端分歧或任一旧账务证据不足仍停开，最终发送时复查报价年龄。

USDT/USD 保留原报告口径。全户硬风控、任何正负债停开并告警、精确 DEX 永久禁单和旧日亏锁保持；FX 成功不等于准许开仓。此估值不是历史交易所权益/抵押折扣的重建。

公式、证据字段及离线验证见[第十二轮契约](../../docs/live-exec-contract-2026-11-11.md#第十二轮增补2026-11-12)；登记后的三次匹配快照、HALTED 维护、release-halt/rebaseline 和日亏周期操作见[第十二轮交接](../../docs/task-handoff-2026-11-12-crossex-exec-r8.md#第十二轮)。线上操作交由 Claude，本轮仅 test/feat/docs 本地提交，无 push/SSH/WSL/amend/鉴权/部署。
