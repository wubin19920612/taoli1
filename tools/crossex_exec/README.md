# crossex-exec

独立的 Python 3.10+ 标准库服务，CrossEx 下单覆盖 Gate / Binance / Bybit / OKX / Aster，Bitget 使用 UTA v3。默认影子模式。此交付仅本地验证，没有部署、账户实测或实盘授权。

第五轮已按 PM 决定完成本地验收：不变量 1 拆为 1a/1b/1c/1d，全部严格断言，2–7 保持原规则。三个固定种子各跑 2448 回合（2400 原随机回合加 48 覆盖回合），执行器 195 项和 fill-sim 158 项 unittest 全部通过。定义、原始汇总、各不变量计数及边界见交接文档“第五轮 PM 决定补充验收”；本地结果不代表实盘或部署验收。

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
| `policy.py` | Decimal 步长公倍数、100 USDT 复核、15bp 门槛、退出与熔断规则 |
| `adapters.py` / `transport.py` | 两家签名、IOC、查单、撤单、分页成交、账户只读接口、限频与总超时 |
| `execution.py` | 意图落库、发送、ACK、终态、未知结果、恢复；两腿并发 |
| `accounts.py` | 持仓模式、3 倍杠杆、真实费率、保证金率及持仓归一化 |
| `background.py` | 账户快照缓存与有界后台刷新线程；发送路径不读取私有网络接口 |
| `limits.py` | 150/600/150 USD 硬上限、独立权益基线、每笔开仓发送前校验 |
| `engine.py` | 触发、持仓管理、最多三次补平、60 秒对账、风险停开/全平 |
| `stops.py` | 独立停开锁集合与按持仓归属的解除；`stop_reason` 仅为兼容展示 |
| `storage.py` / `security.py` | SQLite WAL/FULL、事务、文件权限与脱敏 |
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
| `CROSSEX_BASES` | 明确的基础币白名单，以逗号分隔；空表示不开新仓、不拉合约费率，已有仓位仍管理 |
| `TAOLI_BASE_URL` | `http://127.0.0.1:8000`，按实际服务端口配置 |
| `TAOLI_API_TOKEN` | 空；如 taoli1 需要认证则由用户在私有文件中配置 |
| `CROSSEX_API_KEY` / `CROSSEX_API_SECRET` | 空；仅 CrossEx 所需权限 |
| `BITGET_API_KEY` / `BITGET_API_SECRET` / `BITGET_API_PASSPHRASE` | 空；UTA 交易及账户管理读取权限，实盘才需交易写权限 |
| `CROSSEX_EXEC_API_TOKEN` | 空；非空才启动数据接口，与 `X-Api-Token` 常量时间比较；仍只从上述私有 env 文件读取 |
| `CROSSEX_EXEC_API_BIND` | `127.0.0.1:8795,172.18.0.1:8795`；逗号分隔，可只选其中一个；可省略 `:8795`，不允许其他地址或端口 |
| `FILL_SIM_DB` | 空；可选 fill-sim 主数据库路径，同时只读检索其同目录日期归档；未配置或无法匹配时 `sim=null` |

应用不修改账户模式、杠杆、资产模式或权限。CrossEx 必须为 `CROSS_EXCHANGE`，各目标合约杠杆必须已经是 3；Bitget 必须为 `accountMode=unified`、`assetMode=multi_assets` 和配置的 `holdMode`。逐合约 `symbolConfigList` 必须为 crossed、3 倍杠杆。模式检查失败直接拒绝启动，不用空仓推断模式。旧 `GATE_API_*` 配置会被拒绝。

## 只读数据接口

响应字段以 [`docs/live-exec-contract-2026-11-11.md`](../../docs/live-exec-contract-2026-11-11.md) 为准。`__main__` 在执行器成功启动后接线启动 API，退出时关闭监听；`report` 不启动 API。接口不调用账户、行情或交易 API，不使用执行器的数据库连接或锁。

| GET 路径 | 行为 |
|---|---|
| `/api/v1/summary` | UTC 生成时间、模式、停开状态、额度、最近持久化权益/对账、已平仓损益及真实费率 |
| `/api/v1/positions` | 活动持仓；影子意图不算实际仓位 |
| `/api/v1/trades?limit=200&before=<closed_at>` | `CLOSED` 记录按关闭时间倒序；同时间按 ID 倒序；`before` 为严格小于、必须带时区；`limit` 最大 500 |
| `/api/v1/stats?days=7` | 最近连续 N×24 小时内关闭的记录；`days` 为 1–365；未知收益不混入直方图 |

每个请求必须携带一个 `X-Api-Token`。缺失、错误或重复 token 返回 401；鉴权成功的非 GET 请求返回 405。参数错误为 400，未知路径为 404，读取失败或繁忙为 503；响应均不缓存。错误响应和日志使用固定原因，不回显 URL、请求头、数据库内容或异常文本。

默认启动两个独立 IPv4 监听，直接绑定数字 IP，不执行标准库 HTTPServer 默认的反向 DNS 查询；本机没有 `172.18.0.1` 时记录 `api_bind_unavailable`，保留 `127.0.0.1`。地址配置仅接受上表中的两个地址，禁止 wildcard/公网/主机名和改端口。未配置 token 时记录 `api_disabled_no_token`，不创建监听或数据库读取器。绑定失败、请求异常、监听线程异常和日志异常均隔离，不修改执行状态。

执行库及模拟库均采用 URI `mode=ro`、`query_only=ON` 和 SQLite authorizer：拒绝 DML、DDL、ATTACH 及关闭只读限制。执行库每次请求独立短事务快照，完成读取后立即关闭连接；不创建缺失数据库。每个监听只有一个 daemon 工作线程，全服务只允许一个数据查询，忙时立即返回 503；SQLite 等锁最多 50ms，读查询预算 2 秒、累计 JSON 输入预算 8 MiB。预算超限返回不可用，不截断统计后冒充完整结果；这是资源保护，不是 Linux 80M 内存或交易延迟验收。

字段口径：

- 数值以 JSON 数字返回，时间统一 UTC ISO8601，缺失字段保留为 `null`。仅输出契约列出的字段，并对配置中的凭据/token 和已注册签名再次脱敏。
- `today/total` 为已平仓交易的汇总，今日按 UTC 关闭日期划分；不包含未平仓费用和浮盈亏，不替代执行器的日亏/累计亏风控计算。任一成员记账未知时，相应合计及胜数为 `null`。跨资金费结算尚未确认的资金费和净收益保持未知；确认未跨期的资金费是 0。
- `entry_vwap/exit_vwap` 来自真实成交，平仓多笔按数量加权；不使用意图限价。`net_bps` 分母为多头开仓实际名义额；两腿时间差来自开仓订单 `sent_ms/fill_ms`。
- `caps.total_open_usd` 是本地剩余量 × 开仓成交均价的两腿名义额之和，不含未确认订单预留及交易所未入本地的仓位，不等同于发送前硬风控总敞口。账户 `drop_usd=max(0,baseline-current)`，两边独立。`HALTING/HALTED` 均令 `halted=true`，此字段不证明已经全部平仓。
- 账户 `ok` 表示最近对账数据是否可用，不表示可开仓；对账超过 60 秒或失败时不报告健康。原执行器无保证金占用的 `1e30` 哨兵投影为 `margin_multiple=null`。多市场费率一致且 1 小时内刷新成功才给单个 route 的 taker 值；不一致返回 `null`，不掩盖 special 费率。
- 库里未保存真实标记价格，因此 `mark_price=null`。当前价差和浮动毛收益按两腿各自 60 秒内的缓存盘口、剩余量和深度 VWAP 计算，盘口不足/过期则为 `null`；不是即时交易所标记收益，未扣未实现平仓费和资金费。`stop_distance_bps` 是距离“开仓价差 +50bp”价差止损的距离；独立的浮亏 1% 止损规则未改变。
- `sim` 严格匹配 `trigger_id == fill-sim.event_id`、`measured`、100 USD、相同方向的两个完整 Market 身份，以及已平仓结果。不会拿同 ticker、300 USD、延迟版本或执行器开仓复核估值替代。重复匹配视为不确定。`sim.gross_bps` 由模拟开平仓名义额重建往返毛收益，不是开仓价差；`sim.net_bps` 使用已知资金费口径的模拟结果。
- `slippage_bps=实际 net_bps−sim.net_bps`，负数表示实际结果更差，包含两种执行轨迹、费用及持有期的综合差异，不是单笔撮合滑点。为对应契约示例中的负向尾部，`slippage_bps_p90` 取有符号差异的下十分位；成交时间差 p90 取上九十分位，均线性插值。`vs_sim.n` 只统计可计算差异的匹配交易；时间差和单腿率统计所有关闭记录，未知标志使单腿率为 `null`。
- `by_spread_tier` 使用匹配模拟记录保留的原 shadow 档位。执行库未单独保存该字段，无法取得时用 `key=null` 组，不从事后实际价差倒推。其余分组存在未知收益时，相应合计/均值/胜率为 `null`。

`before` 只有关闭时间游标；同一关闭时间超过单页容量时，严格小于游标会跳过同时间的其余项，这是现有契约的边界。接口不会自动改契约增加复合游标。

## 安全语义与运营

- CrossEx v1.0.8 数量统一为基础币。废弃的 `contract_size` 不参与 CrossEx 下单量计算；Gate/OKX 原生盘口张数由 fill-sim 换算为基础币。
- 开仓手续费扣除完整往返两腿费用；资金费按每腿一个已知周期计算差额，不以预期收款改善不合格机会。保存周期和预估标记，不统一假设 8 小时。
- 不足 24h 资金费历史覆盖、历史有缺口、资料过期或倍率/代码不一致都不交易。OKX 成交额使用基础币成交量 × 24h 最低价的保守下界。
- 意图及两腿 client ID 先持久化，限频与建连后、实际请求前写 `SENT`。超时/5xx/重复 ID 先查原单；**不会自动重发未知结果**。CrossEx text 查不到已终结订单不是未下单的证据。
- 影子模式从协调器和传输层双重禁止下单/撤单。影子意图不伪造成已成交持仓，不产生实盘收益；报告分开展示。
- 订单确认默认每 200ms 查询，最多 10s（限频可延迟查询）；超时保留 `UNKNOWN` 和敞口。成交记录延迟则保留 `accounting_pending`，60s 再补拉，不用 ACK 价格代替成交价。
- 恢复先查原单，再撤仍未确认的订单并复查，随后通过独立的 `Accounts.position_snapshot` 读取两腿实仓；此路径不依赖账户余额或 settings 检查。每次 tick 检查 SENT/ACK/UNKNOWN，即使 pair 状态或跟踪时间尚未更新也恢复；启动先将从未发出的 INTENT 转成 UNSENT。未知单不重发，未确认时即使远端暂时为空也不报 HALTED。
- `remaining` 始终为成交累计的本地流水，`remote_remaining/remote_captured_at` 单独记录远端数量。减仓每轮读取实仓并按其限量，远端数据不覆盖流水。旧 `position_anchor` 只迁移到远端字段，不再用于重建流水；成交/费用未记齐时不推进对账解锁，迟到成交不会导致重复平仓。已关闭历史不接受新持仓的远端快照。
- SENT 提交失败则不发送，状态退为 UNSENT；发送之后的 UNKNOWN/ACK/成交状态写失败保留在内存待写集合，下一轮重试持久化，开仓门同时检查待写集合。两腿共享放行后先完成各自发送，再并发确认，避免一腿的确认故障在另一腿发送期间提前触发恢复。
- 单腿补平一轮最多三笔，额外容差依次为 13/23/33bp，进度持久化。先读两腿真实持仓，每次只发送一笔减仓；确认成交并重读两腿持仓后，重新计算下一笔。优先按本腿步长向下取整；不足一步时，只有单独执行粗步长也能严格缩小裸露量才采用，不能依赖另一腿未来成交。零成交、裸露量未变小、读不到持仓或没有合法减仓量时，记录 `UNHEDGED`、持久化 `repair_stopped` 并停开；停止当前轮次后，后续 tick 按退避进入退出恢复，继续减掉剩余仓位。所有订单仍校验本腿最小数量、名义额、最大量和 tick。
- PM 的 1a 要求任何单腿补平/减仓不增净裸露；双腿平仓的 1b 允许有上界且不穿零的暂时裸露。部分平仓后重读两腿实仓，并优先逐单补平差额。1c 沿用 N=1：下一协调器 tick 进入恢复路径，失败的实际减仓按持久化 `next_close/close_attempts` 在轮末开始 2/4/8/16/32/60 秒退避，重启不重置期限；退避期间仍查询、撤销并确认未知订单。残余裸露触发独立 `unhedged_position` 锁和告警。1d 的开仓门禁直接检查流水/实仓的差额和未确认快照，即使锁尚未写入也不放行新开仓批次。
- 平仓发送前持久化 `remote_pending`，发送后必须取得新的两腿持仓快照才能清除。读取失败时继续恢复，不使用旧快照宣布平仓完成或清除补平锁；`UNHEDGED` 的数量、剩余量、超额腿暂记为 `null`，不把旧数值冒充当前实仓。
- 退出判断使用实际剩余量，开仓基准按真实成交均价、订单查询均价、意图限价依次取值；后两项只用于保守风控，不填入真实 VWAP 或收益。订单均价字段按官方文档为 CrossEx `executed_avg_price`、Bitget UTA `avgPrice`。fill-sim `closing()` 只记录估值，异常不会阻断真实平仓。
- 盘口丢失时采用**该腿自己的最后盘口**，不把另一所价格冒充该腿可成交价；扩大限价仍不保证能成交。剩余仓位一直保留并告警。
- Bitget 私有接口只有 UTA v3，签名传输层拒绝非 v3 路径。复用的免鉴权公开行情仍使用 v2 market。双向开平仓由实际 `side` + `posSide` 表达；`reduceOnly=yes` 仅用于单向模式。clientOid 最长 32 字符。
- CrossEx 保证金率使用 `maintenance_margin_rate` 并核对余额/维持保证金口径；Bitget 使用账户 `effEquity / mmr`，分母为 0 视为安全。两者 <3 停开、<2 全平。
- `HardLimits.check_order` 统一校验三项硬上限：整对进入适配器前预检，限频和建连后的共享发送关口再检查两腿；单笔入口也保留适配器前和实际发送前检查。总额度按远端标记名义额与本地确认敞口逐市场取较大值，再计入待发及未知订单剩余量。减仓不受单腿及总额上限限制，也不因风险读取失败而被阻断。
- 账户后台任务按 5 秒周期刷新，发送路径只读缓存；按读取开始时刻计龄，超过 10 秒不开仓。两个开仓工作线程都通过限频/建连后，在同一个关口用同一个时刻只复核一次两腿盘口 ≤2 秒、净价差 ≥15bp、限价和资金费/费率有效性；统一通过后并发发送，不再各自否决。任一线程在关口前失败，两腿都不发送；统一放行后的网络/撮合失败仍需实际敞口恢复。
- 最小数量和最小名义额按最终的 `quantity × limit_price` 检查；不足时只上调一个两腿公倍数步长，重新计算深度/限价/净价差并校验数量与名义额上限，仍不合格即拒绝。
- 首次启动保存 CrossEx `margin_balance` 和 Bitget `usdtEquity` 的独立基线；重启记录当前值并保留原基线，避免清除损失。任一边下降达到限额即进入 HALTING，全平确认后 HALTED。存在实盘历史而缺少独立基线时拒绝启动，需要人工核对迁移。
- CrossEx 任意正 liability 写入事件日志和每 300 秒状态 JSON。费率刷新失败会清除对应旧费率，读不到就不允许该市场开仓。
- 启动先恢复订单、重建持仓并对账，目录初始化与每小时费率刷新由后台完成。目录单所失败只移除该所开仓映射，30 秒后重试；已有持仓使用持久化的原市场身份继续管理。费率仅处理白名单合约，每个合约的费率和读取起始时间封装为不可变 `FeeSnapshot`，一次替换发布并按整个版本读取；单个失败只撤销该合约。
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

`deploy/crossex-exec.service` 使用用户 `ubuntu`、代码目录 `/home/ubuntu/wubin/taoli1`、私有配置 `/home/ubuntu/wubin/crossex-exec/crossex-exec.env`、状态目录 `/home/ubuntu/wubin/crossex-exec/var`，设置 `MemoryMax=80M`、`Restart=on-failure`、`RestartSec=10`、`StartLimitIntervalSec=0`、`UMask=0077`。配置目录和文件应仅由服务用户读取，文件模式必须 0600。没有 systemd `EnvironmentFile`，由应用自己打开文件验证权限。安装前需确认实际代码位置、创建 var 目录并核对所有者；`ProtectHome=read-only` 配合只允许 var 写入，本次没有安装或启动模板。

实际安装、Linux 80M 内存验收、账户核对、24h 影子验收及开实盘开关均留待新任务。详细证据、接口 URL 和未确认事项见 [交接文档](../../docs/task-handoff-2026-11-11-crossex-exec.md)。
