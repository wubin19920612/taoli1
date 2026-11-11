# crossex-exec 本地实现交接 — 2026-11-11

> **当前状态：第五轮 5 项修复已实现，20 项专项测试通过；整体验收未通过，详见文末“第五轮本地修复与待决验收”。** 固定种子模拟器跑满 2400 回合，严格第 1 条不变量仍失败，平仓验收口径等待用户明确选择。历史轮次保留作证据；当前停开锁、流水/实仓分离、恢复顺序和日界基线以第五轮为准。第四轮曾联网核实官方均价字段，第五轮完全离线。

## 目标、范围与状态

按 `docs/crossex-exec-spec-2026-11-11.md` 和 `docs/crossex-live-design-2026-11-11.md` 实现独立的小额执行服务：CrossEx 五所永续 + Bitget Classic v2 USDT 永续，标准库、SQLite、默认影子模式。此任务的用户约束覆盖项目默认推送/上线流程。

**本次只交付本地代码、测试、部署模板和文档；没有 push、SSH、WSL、账户鉴权调用、生产备份或生产部署，也没有运行服务的 `run` 命令。** 测试使用 127.0.0.1 假服务器及虚构凭据；真实网络访问仅官方文档和免鉴权公开 GET。

分支：`codex/crossex-exec`。

基线：`d978ebb5389b3eeb50f1f5d5ef1fad0e297ba164`。开始时工作区干净。

| 类型 | 提交 |
|---|---|
| test：初始离线契约，先验证预期失败 | `faa4423488c8a0c7b63a203e022cfceb9b6b7898` |
| test：补充生命周期、恢复、退出、熔断、禁网与脱敏 | `1b06376bf00ddc2aaacf3af166a7468ba68823fa` |
| feat：执行器与部署模板 | `3ecd38225c843a239edde7352e0f097c132b9bf7` |
| docs：本交接及 README | 随本文件提交；用下列命令取得，不硬编码自引用 hash |

```text
git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md
```

全部提交均为新提交，没有 amend、回退或 force 操作。Git worktree 索引在父仓库，后两次提交经自动审批允许写入该 Git 元数据目录。

## 文件清单与关键入口

新建：

- `tools/crossex_exec/__init__.py`、`.gitignore`、`README.md`。
- `tools/crossex_exec/__main__.py`：`run --env-file`、`report --db`、flock、信号停机、状态/告警 JSON。
- `tools/crossex_exec/security.py`：0600/文件所有者校验、密钥入口和脱敏。
- `tools/crossex_exec/transport.py`：HMAC、固定私有域名、无重定向/隐式重试、限频、总时限。
- `tools/crossex_exec/adapters.py`：CrossEx/Bitget 下单、查单、撤单、成交分页和账户只读查询。
- `tools/crossex_exec/storage.py`：WAL/FULL、订单/持仓/事件/配置元数据。
- `tools/crossex_exec/policy.py`：数量/价格/手续费/资金费复核和退出/熔断条件。
- `tools/crossex_exec/market.py`：启动目录映射、公开行情、成交额、资金费历史完整性。
- `tools/crossex_exec/accounts.py`：真实费率、账户模式/杠杆/保证金率及持仓归一化。
- `tools/crossex_exec/execution.py`：订单状态机、并发发送、确认、未知状态、成交补拉、重启恢复。
- `tools/crossex_exec/engine.py`：触发、持仓限制、补平、跟踪、对账和停开/全平。
- `tools/crossex_exec/reporting.py`：逐笔滑点、两腿时间差、单腿失败率、净收益和未知记账标记。
- `tools/crossex_exec/tests/__init__.py`、`tests/test_exec.py`、`tests/test_engine.py`。
- `tools/crossex_exec/deploy/crossex-exec.service`、`deploy/crossex-exec.env.example`。
- 本交接文档。

唯一改动的已有代码：`tools/fill_sim/exchanges.py::check_response`，为 Gate 增加列表响应校验，与已有 Binance/Aster 路径一致。支持公开目录、ticker 和历史资金费；已有 158 项测试通过。

未改 backend、compose、已有部署脚本或任何 `.env`。测试产生的 `__pycache__` 被忽略。公开核查的临时 SQLite 已随临时目录清理；没有将凭据、行情抓取包或无关产物入库。

## 业务与安全实现

1. `ShadowAPI` 轮询 `/api/shadow/trades`，保留 fill-sim 原有业务过滤和 120s 时效，并增加两腿永续、精确基础币/原始市场代码/倍率检查、两所独立成交额 ≥500 万美元。
2. 启动读取 CrossEx rules 和六所原始合约目录，生成并保存精确映射。CrossEx 当前数量为**基础币数量**，`contract_size` 已废弃；原生 Gate/OKX 深度张数由 fill-sim 换算，禁止再乘一次。
3. 使用 Decimal、公倍数步长、两腿较小的 100 USDT 可成交数量；检查最小量/名义额/最大量。公开盘口并发获取 50 档，3s 时效，按最差档 ±3bp 并按真实 tick 向外量化。
4. 真实费率来自 CrossEx `/fee` 和 Bitget `/common/all-trade-rate`，另提供 `/trade-rate` 单市场方法。没有真实费率不放行，不使用公开默认费率。门槛扣完整往返费用和每腿一个周期的资金费差；预期资金费收入不救活不合格价差。保存每腿实际周期、下一结算时间和预估标记。
5. 资金费历史需要覆盖 24h，缺口或截断不放行；绝对费率超过 0.5% 拉黑；结算前 30min 不开、5min 平。持仓时定期刷新下一结算时刻。
6. 两腿 client ID 在启动发送前全部落库；通过限频和建连后才写 `SENT`；ACK 保存交易所 ID。每 200ms 查询、确认预算最多 10s，过期保留 `UNKNOWN`。成交价格/费用按成交 ID 去重、分页补拉，数量不齐或币种不支持保留未知。
7. **不自动重发未知订单。** CrossEx 完成单可能不能再按 text 查到，404/未找到也不能证明从未受理。重复 ID 也查询原单，不按普通拒单当成零成交。
8. 单币一对、最多三对；两腿成交不等时只减多余腿，三次容差 13/23/33bp，重试计数落库。仍不一致停开并 JSON 告警。平仓部分成交只继续减仓，不反向补开。
9. 退出覆盖收敛一半、反向扩大 50bp、浮亏 1%、2h、结算前 5min、盘口中断 60s。盘口缺失时用**本腿缓存盘口**扩大容差，避免将另一交易所价格错误当成本腿可成交价；无法成交保留真实敞口。
10. 风险规则：日亏 20 停开；累计亏 100 全平；两账户保证金率分别 <3 停开、<2 全平；连续三次单腿事件或一小时拒单率 >30% 停开；60s 调度账户/持仓对账。逐笔损益与账户权益变化取更差者，覆盖延迟费用和意外资金费扣收。
11. `HALTING` 是正在处理剩余/未知敞口的持久化状态。确认本地和远端都空仓才写 `HALTED`、退出。启动先检查 `HALTED`；人工不能用删除整个数据库代替复核。
12. 默认 `CROSSEX_LIVE_ENABLED=0`，协调器和传输层均禁止影子下单/撤单。影子模式保存真实复核与意图，不伪造成实盘持仓或收益。若已有实盘持仓，禁止切换到影子后继续运行。
13. `STOP` **存在即停开**，采用任务书明确规定，覆盖设计稿较早的“删除文件停开”表述。下单实际发送前再检查，不阻止减仓。
14. SQLite 事务、FULL 同步、唯一订单编号、重启只查原单并从日志重建持仓；未发送意图在重启时转为 `UNSENT`，不发送过期机会。
15. 所有密钥只来自用户指定的私有文件；检查普通文件/0600/所有者，不回退到进程环境。不保存原始请求头、私有响应正文或远端错误消息；测试覆盖异常、数据库和日志脱敏。

## 验证证据

执行环境：Windows，Python 3.12.10。所有新 Python 文件已通过 `ast.parse(..., feature_version=(3,10))` 语法检查。SQLite 语法采用目标 3.37 支持的基础事务/UPSERT/WAL，无需新版本特性。没有运行 Ubuntu、systemd 或 WSL，不能将本地结果称为 Linux 部署验收。

最终两条命令及原始汇总行：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 51 tests in 43.396s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 31.480s

OK
```

新增测试通过 socket 与 DNS 守护禁止外网。真实 HTTP 仅到 127.0.0.1，两家服务器独立核对签名，覆盖 ACK、终态、5xx 后查单、重复编号、部分成交、单腿补平、所有退出信号、保证金全平/日亏停开/累计 HALTED、对账差异、恢复、影子禁写及密钥隔离。

一轮加速测试曾因测试用 1s 确认时限小于真实 1.05s 滚动限频窗口而发生超时；测试预算改为 3s，生产仍使用 10s，专门的短超时测试继续覆盖到期 UNKNOWN。修正后最终全套通过。没有降低生产限频或放宽未知订单处理。

独立公开接口检查（非 unittest，不带任何鉴权）：六家 BTC 原始合约均成功完成目录映射、当前与历史资金费解析（`history_ok=true`）、成交额解析和公开盘口解析。此前 BTC/ETH 映射检查中 Gate ETH 未进入可交易映射；本服务不会绕过规则强行加入。该结果是本次时点证据，不是永久支持清单。

`python -m tools.crossex_exec --help` 成功；`git diff --check` 通过。

## 官方接口出处 URL

| 来源 | 用途与 URL |
|---|---|
| Gate CrossEx v1.0.8 | 签名、orders/DELETE/orders detail/history_trades/accounts/positions/fee/positions/leverage/rule/symbols：<https://www.gate.com/docs/developers/crossex/en/> |
| Gate Futures | contracts、order_book、tickers、funding_rate：<https://www.gate.com/docs/developers/apiv4/en/futures/> |
| Bitget Classic REST | 签名、毫秒时间戳和 30s 窗口：<https://www.bitget.com/docs/classic/rest-api> |
| Bitget Trade | place-order、detail、cancel-order、fills、单双向 side/tradeSide/reduceOnly：<https://www.bitget.com/docs/catalog/classic-contract-trade/classic-contract-trade> |
| Bitget Account | account/accounts、posMode、crossedMarginLeverage、crossedRiskRate：<https://www.bitget.com/docs/catalog/classic-contract-account/classic-contract-account> |
| Bitget Position | all-position、holdSide、total：<https://www.bitget.com/docs/catalog/classic-contract-position/classic-contract-position> |
| Bitget Common | trade-rate/all-trade-rate：<https://www.bitget.com/docs/catalog/classic-common-public/classic-common> |
| Bitget Market | contracts、merge-depth、ticker、current-fund-rate、history-fund-rate：<https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market> |
| Bitget 官方旧版完整错误码 | 45034 clientOid duplicate、43001/45057 未找到、40017/40757/40762 确定拒单：<https://www.bitget.com/legacy-docs/classic/broker/error-code/restapi> |
| Binance USD-M | exchangeInfo/depth/ticker/premiumIndex/fundingInfo/fundingRate：<https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data> |
| Bybit 深度 | <https://bybit-exchange.github.io/docs/v5/market/orderbook> |
| Bybit 规格 | <https://bybit-exchange.github.io/docs/v5/market/instrument> |
| Bybit ticker/当前资金费 | <https://bybit-exchange.github.io/docs/v5/market/tickers> |
| Bybit 资金费历史 | <https://bybit-exchange.github.io/docs/v5/market/history-fund-rate> |
| OKX | public/instruments、market/books、market/ticker、public/funding-rate、funding-rate-history：<https://www.okx.com/docs-v5/en/> |
| Aster 官方 V3 | exchangeInfo、depth、ticker/24hr、premiumIndex、fundingInfo、fundingRate：<https://github.com/asterdex/api-docs/blob/master/V3(Recommended)/EN/aster-finance-futures-api-v3.md> |
| CrossEx 实际免鉴权规则 URL | <https://api.gateio.ws/api/v4/crossex/rule/symbols> |

私有请求只是在代码中实现和在本地假服务验证，本次没有请求上表中的真实私有接口。Bitget 某些旧 `/api-doc/...` 地址重定向到 UTA 介绍页，已改用实际 Classic 文档；不据重定向页面推断 v2 账户语义。

Gate 有固定输入和输出签名向量，GET/POST 两个均直接验证。Bitget 本次官方 Classic 页面只给签名预映像和实现，没有提供完整的固定 secret + 输出摘要；测试用该预映像及独立 HMAC 计算，并由假 HTTP 服务复核实际发送字节，**不是官方输出向量验收**。

## 需账户只读确认的点

用户已确认两个账户开通；以下为进入 C1/C3 前的未验证清单，不是再次要求开户：

1. CrossEx 权限、`CROSS_EXCHANGE` 模式、SINGLE/DUAL、各目标合约实际杠杆 3、账户余额和所有已有仓位；不调用写接口更改配置。
2. CrossEx `position_side=NONE` 时空头 `position_qty` 的符号；基础币数量/lot_size/最小名义额在目标市场的一致性，尤其 Gate/OKX。本次公开规则证实基础币口径，但未见账户实际持仓回报。
3. CrossEx 真实各所费率和 `special_fee_list` 优先级；Bitget 真实逐市场费率、费率折扣、实际手续费是否 USDT。BGB/优惠券费用目前拒绝作为已知 USDT 成本。
4. Bitget 是否仍是 **Classic v2** 而非已经升级 UTA；USDT-FUTURES/crossed、posMode、各目标合约 3 倍杠杆。空仓时的模式也通过 account 读取，不用持仓列表为空推断。
5. Bitget `crossedRiskRate` 的确切公式、零值和极端余额语义；与页面/账户维持保证金、权益对照后才能启用 `BITGET_RISK_RATE_CONFIRMED=1`。代码使用倒数转为权益/维持保证金比例，不直接与 300/200 比较。
6. 已有订单/成交记录可用于确认 text/clientOid 的可查时效、订单终态字段、成交记录到达延迟、分页一致性、费用正负号与时间戳单位。新订单行为仍需用户开启实盘后的小额验收，本次只读不能证明真实撮合。
7. 本机/服务器时钟同步，Gate 秒时间戳与 Bitget 毫秒时间戳窗口；私有限频是否与其他程序共享。两账户应专用，禁止不明外部持仓和并行交易程序。
8. 单账户总权益和日权益基线在转账/提现时如何维护；本实现保守将权益下降计入风险，不自动把资金流解释为收益。

## env、部署和后续验收

env 变量全表在 `tools/crossex_exec/README.md`，模板含 14 个名称及默认值：`CROSSEX_LIVE_ENABLED`、`CROSSEX_DB`、`CROSSEX_STOP_FILE`、`CROSSEX_POSITION_MODE`、`BITGET_POSITION_MODE`、`BITGET_RISK_RATE_CONFIRMED`、`CROSSEX_BASES`、`TAOLI_BASE_URL`、`TAOLI_API_TOKEN`、`GATE_API_KEY`、`GATE_API_SECRET`、`BITGET_API_KEY`、`BITGET_API_SECRET`、`BITGET_API_PASSPHRASE`。密钥默认均为空。

模板具备 `MemoryMax=80M`、`Restart=on-failure`、专用用户、只写状态目录与 0077 umask。实际服务未安装、未启动，线上版本完全未变。本次无线上数据库备份，因为没有部署动作。

残余风险与验收边界：

- 仅在 Python 3.12/Windows 上执行测试，Python 3.10/SQLite 3.37 的实际 Ubuntu 22.04 运行、systemd 权限、80M 内存和长时间资源占用未验证。
- 网络/限频会延迟 3/15/60s 跟踪与 60s 对账；循环带有有界请求，但不是硬实时调度。需在 Linux 影子压力测试中量出最坏耗时；请求未知时停开不会消除已有仓位风险。
- CrossEx 没有已验证测试网。官方文档、公开行情成功和本地假服务均不能证明账户权限、真实 ACK/成交/补平可用。
- 跨资金费结算、非 USDT 手续费、优惠券、ADL/外部持仓等异常会使完整收益未知并停开；未实现账单级跨期资金费归因。默认策略力求不跨期，但宕机/网络故障仍可能跨期。
- 盘口故障、交易所拒绝 reduce-only、最小量造成的尘埃仓、进程 OOM/掉电都可能留敞口；`HALTING/UNKNOWN` 不会被误标为已清仓。需人工核对后恢复。
- 影子意图不模拟已成交持仓的占位；它用于比较触发和复核价格，完整持仓生命周期由本地假服务测试覆盖。24h C3 验收仍应结合 fill-sim 对照。
- 告警只有本地 JSON/journal，没有接入外部通知渠道。

下一任务应先做独立代码审查与 C1 只读账户核对，再在 **仍为 0 的实盘开关** 下完成 C3 的 24h 影子与资源/调度验收。C4 只能由用户本人启用。不得依据本交接直接开启实盘。

## 第二轮交付（2026-11-11）

### 范围、分支与提交

按 `docs/crossex-exec-round2-2026-11-11.md` 的 7 项修改。指定 worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支 `codex/crossex-exec`；本轮基线 `cd58156b94be7748e9b9020989b5872153a87e78`，开始时工作区干净。

本轮真实联网仅阅读官方文档；账户事实来自用户提供的脱敏 C1 文档，未由本会话重复核查。所有签名 HTTP 测试使用虚构凭据和 127.0.0.1 假服务，socket/DNS 守护禁止测试访问外网。未读取真实密钥文件，未运行服务 `run` 命令。

| 类型 | 完整提交 hash | 内容 |
|---|---|---|
| test | `2e44d69b09ea7939f9edf56103c01d77cae0c31d` | UTA 字段、签名示例输入、费用、模式和三项硬上限契约；先观察旧实现失败 |
| test | `6037d0a0d187346460c4a3e957ce0e9b6e7627f6` | 补充回撤全平、重启恢复、负债日志、真实签名的本地账户读取 |
| feat | `4825fc5879d6cb9e1007ff6c88ec90bd4951ddcb` | UTA v3、硬风控、配置和服务模板 |
| feat | `286721c791fdceae9fd968e0eae1ac581d58e0b0` | 删除新 limits.py 的末尾多余空行；不改变行为，未 amend |
| test | `fd0e4dd332e43f7e15b7514e1f3eba1687c892dc` | 删除新增测试文件的末尾多余空行；不改变行为，未 amend |
| docs | 随本节与 README 单独提交 | 用下面的命令取得自引用提交号 |

```text
git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md
```

只有本任务文件进入提交；未修改 backend、compose、既有部署脚本、真实 `.env` 或 fill-sim。没有 push、SSH、WSL、amend、生产备份或部署；线上版本未变。交付工作区无新增未跟踪产物，Python 缓存由既有 ignore 排除。

### 7 项验收映射

| 项目 | 实现与证据 |
|---|---|
| 1. Bitget UTA v3 | `adapters.py::Bitget` 全部私有路径改为 v3；下单 qty/timeInForce/posSide，查单 orderStatus/cumExecQty，成交 execId/execQty/execPrice/fee/createdTime，分页 list/cursor。空持仓 list=null 归一为空列表；缺少 list 不伪装为空。签名传输层拒绝非 v3 路径。 |
| 2. 持仓模式 | CrossEx 默认 DUAL，Bitget 默认 hedge_mode。`Accounts.validate_startup` 和 `Engine.startup` 先读账户与 settings，明确检查 unified/holdMode，不一致直接异常退出，不进入行情初始化或下单。外部 Bitget 挂单拒绝启动，本程序已落库待确认订单允许进入恢复。 |
| 3. 环境变量 | CLI 和示例使用 CROSSEX_API_KEY/CROSSEX_API_SECRET 及 BITGET_API_KEY/BITGET_API_SECRET/BITGET_API_PASSPHRASE；load_env 明确拒绝 GATE_API_*，无旧变量回退。 |
| 4. 路径 | 默认 DB 为 `/home/ubuntu/wubin/crossex-exec/var/crossex-exec.db`，STOP 为同目录 `/STOP`，env 为 `/home/ubuntu/wubin/crossex-exec/crossex-exec.env`；CLI、示例和 systemd 模板一致。 |
| 5. 硬上限 | 新增 `limits.py`：MAX_LEG_USD=150、MAX_TOTAL_USD=600、MAX_EQUITY_DROP_USD=150。配置只接受不超过常量的正数，NaN/Inf 不允许。整对预检、每笔调用 place 前、通过限频/建连后，均调用同一 `HardLimits.check_order` 重新读取并检查三项。 |
| 6. 保证金倍数 | CrossEx 使用 maintenance_margin_rate 并校验余额/maintenance_margin；maintenance_margin=0 安全。Bitget 使用账户 effEquity/mmr；mmr=0 安全。<3 停开、<2 请求全平。上线前仍须核实 Bitget 口径；BITGET_RISK_RATE_CONFIRMED 默认 0。 |
| 7. 实际费率 | CrossEx 精确匹配 exchange_type 和 symbol，special_fee_list 的 taker_fee_rate 优先，其次 future_taker_fee；Bitget 对每个精确合约请求 v3 fee-rate。刷新失败清除该市场旧值，读不到不开仓。Bitget fee-rate 按官方 3 次/秒限频。 |

关键调用链：`Engine.ingest → Executor.execute_pair → Executor.execute → adapter.place → Transport.request(before_send)`；整对和单笔所有开仓入口复用 `HardLimits.check_order`。两腿 ID 先落库，意图计入额度后才启动工作线程。已知超限在调用下单适配器前即返回 UNSENT；限频/建连期间变化在 before_send 阶段再次拒发，测试断言下单适配器/实际发送均没有越过对应边界。

总名义额按市场原始 symbol、venue、position side 区分：远端标记名义额和本地已确认剩余仓位的保守名义额逐键取大，再加 INTENT/SENT/ACK/UNKNOWN 的未确认剩余数量 × 限价。多空不相抵；并发待发和未知订单预留额度，可能保守重复计数而拒绝机会。减仓不消耗开仓额度；无需等待风险读取成功，仍可发出平仓意图。

权益分别取 CrossEx `margin_balance`、Bitget `usdtEquity`。首次启动持久化 `account_equity_baseline`，每次启动记录当前值并保留原基线；重启不抹掉亏损。有实盘订单历史但缺少该基线时拒绝启动。任一账户下降达到 150 USD（或更低配置）立即锁存 HALTING，等待并发工作线程结束再由协调器全平，确认本地与远端都无仓位才写 HALTED。无法平掉或订单未知时保留 HALTING，不假报清仓。启动时已知回撤会在目录和费率初始化前尝试全平。原有日亏 20、累计亏 100 风控继续有效，可能更早触发。

CrossEx `assets[]` 任何正 liability 都写入 `ACCOUNT_LIABILITY` 事件、状态元数据，并由每 300 秒状态 JSON 输出；日志同时带两边权益基线与最近权益。只保存必要的 coin/exchange_type/liability，不保存原始私有响应。

双向平仓使用 UTA 的 `side=sell,posSide=long` 或 `side=buy,posSide=short`；不能照搬 v2 tradeSide。UTA reduceOnly 仅适用于单向模式，因此双向减仓意图由上述字段表达。生成的 clientOid 已从 35 字符改为 32 字符，适配器拒绝超过上限的编号。

本轮代码文件：新增 `tools/crossex_exec/limits.py`；修改 `__main__.py`、`accounts.py`、`adapters.py`、`engine.py`、`execution.py`、`security.py`、`transport.py`、两个 deploy 模板。测试文件：修改 `tests/test_exec.py`、`tests/test_engine.py`，新增 `tests/test_round2.py`。文档：本交接和 `tools/crossex_exec/README.md`。

### 官方 UTA 接口出处

以下均在本轮阅读；只访问文档页面，没有执行页面中的 curl/API 示例。

| 路径 / 用途 | 官方 URL |
|---|---|
| POST `/api/v3/trade/place-order` | https://www.bitget.com/legacy-docs/uta/trade/Place-Order |
| GET `/api/v3/trade/order-info` | https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Details |
| POST `/api/v3/trade/cancel-order` | https://www.bitget.com/legacy-docs/uta/trade/Cancel-Order |
| GET `/api/v3/trade/fills` | https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Fills |
| GET `/api/v3/trade/unfilled-orders` | https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Pending |
| GET `/api/v3/position/current-position` | https://www.bitget.com/legacy-docs/uta/trade/Get-Position |
| GET `/api/v3/account/assets` | https://www.bitget.com/legacy-docs/uta/account/Get-Account |
| GET `/api/v3/account/fee-rate` | https://www.bitget.com/legacy-docs/uta/account/Get-Account-Fee-Rate |
| GET `/api/v3/account/settings` | https://www.bitget.com/docs/catalog/account/account-settings |
| 当前合并版订单文档 | https://www.bitget.com/docs/catalog/trading/order-management |
| UTA 签名与官方 GET/POST 示例输入 | https://www.bitget.com/docs/uta/quick-start |
| v2→v3 签名保持不变 | https://www.bitget.com/docs/classic/uta-api-upgrade-guide |
| CrossEx 账户、仓位、费率与 special 字段 | https://www.gate.com/docs/developers/crossex/en/ |

Bitget `legacy-docs/uta` 是官方旧版展示站点的 **UTA v3** 文档，并非保留 Classic v2 实现。复用的 Bitget **免鉴权公开行情**仍沿用 fill-sim 的 v2 market 接口；本轮要求的下单、查单、撤单、成交、仓位、资产、费用和设置全部已切 v3。

签名测试使用当前 UTA Quick Start 的固定 GET（fee-rate）和 POST（place-order）预映像、时间戳和请求体。页面未提供固定 secret 对应的官方输出摘要；使用虚构 secret 独立 HMAC 计算期望值，并由本地 HTTP 服务再次验证实际请求字节。不能将其表述成“官方输出摘要向量已验收”。Gate 原有官方 GET/POST 固定输出向量继续通过。

### 本轮测试与检查

最终原始汇总如下。新增执行服务测试含本地 HTTP 和 mock，外网守护启用；fill-sim 本轮未改动。

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 76 tests in 52.160s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.483s

OK
```

最初 15 项新增契约在旧实现下出现 3 failures、11 errors，确认覆盖了迁移缺口；实现后与后续补充测试全部通过。最终测试之后仅删除实现及测试 Python 文件末尾空行和更新文档，没有行为改动。Python 3.10 语法解析检查、CLI `--help`、最终完整差异 `git diff --check` 通过。运行环境仍为 Windows/Python 3.12.10，不是 Ubuntu/systemd 验收。

### 当前配置与上线前必须确认

示例现有 17 项：`CROSSEX_LIVE_ENABLED`、`CROSSEX_DB`、`CROSSEX_STOP_FILE`、`CROSSEX_POSITION_MODE`、`BITGET_POSITION_MODE`、`BITGET_RISK_RATE_CONFIRMED`、`CROSSEX_BASES`、`TAOLI_BASE_URL`、`TAOLI_API_TOKEN`、`CROSSEX_API_KEY`、`CROSSEX_API_SECRET`、`BITGET_API_KEY`、`BITGET_API_SECRET`、`BITGET_API_PASSPHRASE`、`CROSSEX_MAX_LEG_USD`、`CROSSEX_MAX_TOTAL_USD`、`CROSSEX_MAX_EQUITY_DROP_USD`。密钥默认均为空，实盘开关仍为 0。

1. **Bitget 保证金口径**：按 PM 要求用账户 `effEquity / mmr` 得倍数，与 CrossEx 使用相同 3/2 阈值。官方分别描述为 USD 有效权益和 USD 维持保证金，但实际 multi_assets 的折扣、借贷、极端余额及与网页风险指标的对应关系仍需上线前核对；不能混用 position 接口的 mmr 比率，也不能拿 mgnRatio 直接比较。确认后才可设置 `BITGET_RISK_RATE_CONFIRMED=1`。
2. **逐合约与实仓字段**：C1 已提供 unified/hedge_mode/DUAL 及空仓事实；仍需核对目标合约 crossed/3 倍杠杆、持仓非空时的 posSide/total/markPrice/unrealisedPnl，以及 CrossEx position_qty/mark_price/upnl 的账户实际返回。字段缺失或模式不符会停开/拒绝启动。
3. **成交和费用**：核实 UTA `feeDetail[].fee` 正负号、USDT 扣费币种、特殊优惠、special 费率和分页/成交延迟；官方例子 fee 为正成本，本轮按该例解析。BGB/优惠券等非 USDT 成本仍保留未知记账，不假定零费用。真实双向减仓及 IOC 撮合尚未验证。
4. **查询时效与恢复**：当前订单文档提示 API 下单的 cancelled 历史仅保留 2 小时；查不到不能证明没有成交，本程序不会重发未知单。旧 v2 实盘订单/旧数据库不能未经核对直接迁移；基线或订单格式不兼容时需要人工复核，不删除数据库绕过。
5. **3 万 USDT 账户的运行边界**：用户已决定使用该账户，不要求另开账户；运行期间仍应避免外部交易及转账，外部持仓/挂单会阻止正常启动或对账。两边权益不能互相抵销；资金流出会触发回撤，不自动解释为转账。硬额度在发送检查时约束新增风险，价格后续变化、并行外部交易和非原子快照无法由本地程序绝对锁住；发现残仓不会假报 HALTED。
6. **Linux 和延迟验收**：确认代码根目录 `/home/ubuntu/wubin/taoli1`、运行用户 ubuntu、env 所有者与 0600、预建 var 的权限、ProtectHome/ReadWritePaths、Python 3.10/SQLite 3.37、80M 内存。新增发送前风险读取会增加请求数和两腿时间差；每次读取和最终写请求有界，但整段复核并非原来的单次 2 秒总预算，必须在 C3 量出限频、调度和单腿风险。远端故障时停止开仓并保留真实敞口。

下一任务只做独立审查及后续获准的 C3 影子验收；C1 数据若要刷新须在新任务明确授权。当前工作没有部署或开启实盘，C4 开关仍由用户本人控制。

## 只读数据 API 交付（2026-11-11）

### 目标、范围、基线与提交

本轮模块是 `tools/crossex_exec` 的只读数据接口，按 `docs/live-exec-contract-2026-11-11.md` 实现 `GET /api/v1/summary|positions|trades|stats`。开始前已读任务书、第二轮要求、现有交接、契约及实际存储/调用链。

- 指定 worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-api`。
- 分支：`codex/crossex-exec-api`。
- 基线：`af5f88bac5b86d05dfdd656e022e26076a35cae1`；开始时工作区干净。
- 本轮用户要求只在本地交付，不联网、不 push、不 SSH、不 WSL、不 amend，覆盖项目默认上线流程。测试 HTTP 仅到 `127.0.0.1`，使用虚构凭据并保留 socket/DNS 禁外网守护。没有读取真实 env/凭据、调用真实账户接口或启动真实执行服务。

| 类型 | 完整提交 hash | 内容 |
|---|---|---|
| test | `f89dcbf1be2b11a2c17851d21f29b6e9d3df08b3` | 23 项只读接口契约、权限、故障隔离和数据口径测试 |
| feat | `3ae5e479015abe1ea0900b36c32cab7019767c8c` | HTTP 服务、独立只读投影、`__main__` 启停接线、三项 env 示例 |
| test | `680b0f8f26a8f0b68d1e3a2586322a21ae1b4ffe` | 补充监听禁止反向 DNS 的回归测试，合计 24 项 API 测试 |
| feat | `178989f8f63aa7be089cfbee0ea55b5e083f4201` | 直接绑定数字 IP，去掉 HTTPServer 默认的隐式反向 DNS 查询 |
| docs | 随本节及 README 单独提交 | 本轮证据、字段口径、配置和剩余验收边界 |

docs 提交号通过 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md` 取得，不硬编码自引用 hash。全部为新提交，无 amend。父仓库中的该 worktree Git 索引需要沙箱外写权限，已按用户的本地提交授权通过自动审批；Git hooks 在提交命令中禁用，确保不触发联网动作。

### 文件与关键调用链

| 文件 | 本轮变化 |
|---|---|
| `tools/crossex_exec/tests/test_api.py` | 新增隔离的临时 SQLite、loopback HTTP、故障注入及字段断言 |
| `tools/crossex_exec/api.py` | 新增 token 校验、监听白名单、仅 GET、固定错误响应、daemon 线程生命周期 |
| `tools/crossex_exec/api_data.py` | 新增只读连接、请求预算、显式字段投影、统计及精确 fill-sim 匹配 |
| `tools/crossex_exec/__main__.py` | 仅增加导入和 4 行启动/关闭接线；成功 `engine.startup()` 后启动，finally 关闭 |
| `tools/crossex_exec/deploy/crossex-exec.env.example` | 增加 `CROSSEX_EXEC_API_TOKEN`、`CROSSEX_EXEC_API_BIND`、`FILL_SIM_DB` |
| `tools/crossex_exec/README.md`、本交接 | 增加接口行为、公式、未知值和验证边界 |

调用链：`__main__.main → start_api → APIService`；HTTP 工作线程 `dispatch → JournalView.get → readonly → position/trade/statistics`。API 模块不接收 Engine、Accounts 或执行 Store，不调用下单、撤单、账户或行情接口。`engine.py/accounts.py/execution.py/limits.py/policy.py/adapters.py/transport.py/security.py/storage.py`、fill-sim、backend、compose、systemd 模板及真实 `.env` 本轮均未修改。

### 配置与行为

沿用现有 `load_env` 的私有文件入口、0600/所有者检查，不新增进程环境凭据回退。env 示例现在共 20 个变量；此前 17 个保留，新增：

```text
CROSSEX_EXEC_API_TOKEN=
CROSSEX_EXEC_API_BIND=127.0.0.1:8795,172.18.0.1:8795
FILL_SIM_DB=
```

1. token 为空/只有空白时，不创建 API 对象或监听，并记录 `api_disabled_no_token`。单个 `X-Api-Token` 与配置原值通过 `hmac.compare_digest(bytes,bytes)` 比较；缺失、错误、重复头返回 401。认证成功的所有非 GET 方法（含 HEAD/OPTIONS/未知 verb）返回 405。
2. 地址配置可选择默认两个地址中的一个或两个，可省略端口，端口固定 8795。拒绝 `0.0.0.0`、IPv6 wildcard、其他 IP、主机名和其他端口。直接数字 IP 绑定，不执行反向 DNS。默认 bridge 不存在时记录 `api_bind_unavailable`，保留 loopback；各监听故障互不传播。
3. 两种数据库都用独立 URI `mode=ro` 连接，启用 `query_only`，authorizer 只允许 SELECT/READ/受限函数/只读事务；禁止 INSERT/UPDATE/DELETE、建表、ATTACH、关闭 query_only。不存在的 DB 不创建。执行库读取结束即释放快照，不借用写连接、Store.lock 或交易执行线程。
4. API 请求/监听/启动/日志异常被隔离，不设置 STOP、HALTING/HALTED 或交易风险状态。连接与响应错误无 traceback、原始 URL、请求头或异常文本；字段白名单之后再脱敏已注册凭据及签名。未加入任何 API 写路由。
5. HTTP 只有两个有界的监听工作线程，不按请求无界创建线程；查询并发 1，繁忙返回 503。SQLite 等锁 50ms，查询读预算 2s、JSON 输入预算 8 MiB；预算不足返回不可用，不输出截断统计。关闭 API 不等待其 I/O 线程结束。
6. 时间均为 UTC ISO8601，金额/数量/bp 均为数字，所有契约字段保留，未知为 `null`。分页默认 200、上限 500；非法参数/重复参数 400，`before` 必须带时区。统计默认滚动 7 天，允许 1–365 天，16 个宽 25bp 的桶覆盖 [-200,200]，越界放两端。
7. 模拟结果只匹配同一 shadow 事件、`measured/100`、相同方向两个完整 Market 身份及已关闭 position；支持 fill-sim 的同目录日期归档。缺失、读不到、损坏、未关闭或重复匹配为 `sim=null`。不会退回到同 ticker、其他金额/延迟版本或执行器的复核估值。

详细公式见 README“只读数据接口”。对 UI 接入尤其重要：

- 当前 journal 没有交易所 mark，因此 `mark_price=null`；当前价差/浮动毛收益只按各自最近 60 秒内盘口深度计算，失效则 null。`stop_distance_bps` 只描述价差止损距离。
- `today/total` 汇总已关闭交易；`caps.total_open_usd` 是本地剩余量按开仓成交价计量，不含远端未知仓位/未确认订单预留。两者均不是执行器发送前风控函数的替代。
- 账户健康参考最近 60 秒内对账；过期标记 error/ok=false；无保证金占用哨兵映射为 null。费率仅在同 route 的已知有效市场费率一致时输出单值；special 导致不同则 null。
- 模拟 gross 为往返开平仓毛收益，`slippage_bps=实际净 bp−模拟净 bp`。为对应契约负向尾部示例，slippage p90 为下十分位，时间差 p90 为上九十分位；均线性插值。该差值包含持有期及费用差异，不是纯粹撮合滑点。
- 原 shadow 档位只从精确匹配的模拟记录读取，取不到用 `key=null` 分组；不根据事后实际价差猜档位。未知记账不冒充 0 或盈利。

### 验证与原始汇总

Windows / Python 3.12.10 / SQLite 3.49.1；没有 WSL、Ubuntu 或真实账户环境。所有新增 Python 与 `__main__` 通过 Python 3.10 AST 语法检查，CLI `--help` 和完整差异 `git diff --check` 通过。

先新增测试，观察 API 模块缺失产生预期 ImportError；随后实现并扩展为 23 项。开发中修正了一个测试的浮点精确比较和临时 SQLite 连接关闭问题。交付复核发现标准库 HTTPServer 绑定时会调用 `getfqdn`，先补测试观察失败，再改为直接绑定，合计 24 项 API 测试。最终执行服务完整回归 100 项通过；fill-sim 本轮未改动，完整回归 158 项通过。下列最终结果取得后未再修改实现或测试。

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 100 tests in 53.052s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.744s

OK
```

新测试具体覆盖：四端点字段完整、空/缺失字段 null、数字/UTC、实际 VWAP/费用/时间差、出场原因映射、分页限制与时间边界、统计桶和分位数、模拟事件/金额/版本/Market 身份及归档/缺失/损坏/重复、过期账户和费率、未知记账、401/重复头/405、常量时间比较被调用、错误与所有正常响应脱敏、协议解析错误不反射输入、只读连接 DDL/DML/ATTACH/pragma 被拒绝、不创建缺失 DB、WAL 快照下写事务仍能完成、API 读取异常后真实 `Engine.tick()` 继续对账、监听/绑定/线程启动/日志故障隔离、CLI 主循环继续，以及未配置 token 不启动。

### 线上状态、残余边界与下一步

未 push、SSH、WSL、amend、联网、生产备份或部署；线上版本未改变。没有启动真实 `run`，没有实盘开关或风控行为变化。交付仅包含上表文件，没有新增待保留的未跟踪产物；Python 缓存继续由既有 ignore 规则排除。

尚未验证 Ubuntu 22.04 / Python 3.10 / SQLite 3.37 的实际运行、systemd/数据库只读文件权限、真实 Docker bridge `172.18.0.1:8795` 可达性、Linux 80M 内存及并行交易下的延迟。缓存 journal 字段不是实时交易所快照；接口读取保护不证明整个进程绝不会发生资源竞争。契约只有 `before=closed_at`：同时间记录超过单页时，严格时间游标可能跳过同时间剩余项，未擅自增加复合游标。

下一模块请新建任务，从本交接及 `docs/live-exec-contract-2026-11-11.md` 开始进行 taoli1 实盘页面对接/独立验收；如需 Linux 部署、C1/C3 或联网检查，须由后续任务授权。当前 API 交付不构成开启实盘或部署授权。

## 第三轮修复交付（2026-11-11）

### 范围、基线与提交

严格按 `docs/crossex-exec-round3-2026-11-11.md` 的 5 项 P0、5 项 P1、1 项 P2 修复。先依次阅读第三轮清单和本交接，再读取任务书及代码；任务限于执行模块，不扩展到页面、生产运维或新策略。

- 指定 worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支 `codex/crossex-exec`。
- 基线：`0b94623f520ea5d1ca4ebb4a73c3def5f78ecdaa`；开始时工作区干净。
- 用户要求覆盖项目默认上线流程：不联网、不调用真实鉴权接口、不 push、不 SSH、不 WSL、不 amend，按 test/feat/docs 分开本地提交。
- 没有读取真实 env/凭据，没有运行真实 `run`。第三轮新增用内存交易所和临时 SQLite；完整旧回归中的 HTTP 仅到 127.0.0.1 假服务，虚构签名凭据，socket/DNS 外网守护保留。

| 类型 | 完整提交 hash | 内容 |
|---|---|---|
| test | `412a016cd7215c76d46a9e6f54c51be59ed61776` | 首批 20 项，11 项问题均先在旧实现复现 |
| test | `c4146ab1669ec7658421fe770f6d69313e7f902b` | 追加迟到成交、发送前等待、缓存、HALTING 账户故障等；旧 Engine 夹具显式发布账户缓存 |
| test | `f36bbf72eca487911eb4a5a7937beb4e8e9fc0d2` | 补足普通跟踪、目录重试、白名单、最小数量和逐合约费率隔离，共 33 项新测试 |
| feat | `2c463b80c87444d77711b91ff9c25d948bd82996` | 11 项实现及 systemd 模板 |
| docs | 随本节及 README 独立提交 | 用下列命令获取，不硬编码自身 hash |

```text
git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md
```

本地 Git worktree 索引在父仓库，普通沙箱写入被拒绝后，按已有提交授权通过自动审批写入对应 Git 元数据；所有 commit 禁用 hooks 和 GPG 签名，不触发外部动作。没有 amend、回退或强制操作。

### 11 项修复位置与测试映射

下列实现路径均相对 `tools/crossex_exec/`。新增测试全部在 `tests/test_round3.py`；`Round3PolicyTests` 覆盖目录分所、数量策略、回包解析和费率隔离，其余在 `Round3Tests`。以下逐项列出全部 33 个测试名。

| 项目 | 修复位置与行为 | 对应测试名 |
|---|---|---|
| 1. UNKNOWN 不阻塞实际平仓 | `engine.py:386 resolve_exposure`、`:325 refresh_pair`、`:515 close_pair`、`:548 track`，`execution.py:132 cancel`、`:142 recover`。先撤原单再查原订单号，随后读两腿真实持仓；持仓锚点不伪造订单成交，迟到回报不重复计数。HALTING 也可减仓，未确认订单继续保留。 | `test_r1_unknown_cancel_query_positions_then_close_both_legs`；`test_r1_halting_recovers_unknown_and_retains_unresolved_order`；`test_r1_late_entry_fill_after_remote_flat_never_reopens_or_double_closes`；`test_r1_missing_position_snapshot_does_not_guess_a_close_quantity`；`test_r1_regular_tracking_uses_unknown_recovery_path` |
| 2. 补平步长与最小额 | `engine.py:420 reduction_intent`、`:457 repair_intents`、`:487 balance`、`:451 unhedged`。先向下取整减多余腿；不足一步时仅用合法的两腿减仓计划降低最终净敞口。所有计划校验本腿最小数量/名义额、最大量、剩余量；无法处理记录裸露量并停开。 | `test_r2_repair_rounds_down_then_minimizes_residual_with_both_steps`；`test_r2_substep_residual_reduces_other_leg_without_opening`；`test_r2_untradable_dust_records_exposure_and_sends_nothing` |
| 3. 模拟异常不挡退出 | `engine.py:548 track`。真实退出计算使用实际剩余量；`closing()` 只记录，所有模拟异常被隔离，真实平仓仍执行。 | `test_r3_simulation_failure_never_blocks_actual_quantity_exit` |
| 4. 目录后台初始化/重试 | `engine.py:142 startup`、`:89 refresh_reference`、`:121 start_background`；`market.py:184 bootstrap`、`:231 publish_mapping`；`background.py:26 PeriodicWorker`。恢复/对账先于目录，单所目录失败只撤销该所开仓映射；30 秒后重试，不终止管理循环。公开请求泵加锁，目录低优先级，跟踪优先。 | `test_r4_catalog_failure_does_not_abort_startup_or_position_tracking`；`test_r4_blocked_catalog_runs_in_background_while_tracking`；`test_r4_one_catalog_failure_keeps_healthy_venue_and_retries`；`test_r4_failed_catalog_is_retried_after_backoff` |
| 5. HALTING 持续恢复 | `engine.py:142 startup`、`:623 flatten_all`、`:645 tick`；`deploy/crossex-exec.service:5`。失败不抛出退出，按 2/4/8/16/32/60 秒退避并持续重试；本地活动仓位/未知订单和远端都清空才 HALTED。模板 `StartLimitIntervalSec=0`，保留 `RestartSec=10`。 | `test_r5_halting_restart_retries_with_backoff_until_flat`；`test_r5_halting_startup_account_outage_stays_in_process`；`test_r5_service_never_exhausts_restart_budget` |
| 6. 最终限价最小额 | `policy.py:59 review`。初始模拟不提前拒绝可修正的最小量，最终 quantity × limit 检查不足时只加一个共同步长，重新计算深度/限价/净价差并校验数量上限、150 USD 硬顶；仍不满足即拒绝。配置中更低的硬额由发送前 HardLimits 再校验。 | `test_r6_final_sell_limit_minimum_bumps_one_common_step`；`test_r6_min_quantity_bumps_once_and_still_invalid_minimum_rejects` |
| 7. 缓存账户与发送前盘口 | `background.py:7 SnapshotCache`；`engine.py:76 refresh_accounts`、`:63 check_order`；`limits.py:85 check_order`；`policy.py:107 send_review`；`execution.py:22 execute_pair`、`:69 sent`。账户按 5 秒周期后台刷新，缓存按读取开始时计龄、超过 10 秒拒开；整对预检和实际发送前均检查两腿盘口 ≤2 秒、净价差 ≥15bp、限价可成交和资金费/费率时效；两腿并发。 | `test_r7_account_admission_uses_cache_and_rejects_older_than_ten_seconds`；`test_r7_twelve_second_books_send_neither_leg`；`test_r7_net_spread_rechecked_before_either_worker_sends`；`test_r7_two_workers_reach_send_barrier_without_account_io`；`test_r7_after_limiter_wait_rechecks_book_age`；`test_r7_slow_account_refresh_does_not_hold_admission_lock` |
| 8. 缺成交价仍做退出判断 | `adapters.py:25 checked_status`、`execution.py:96 confirm`、`engine.py:548 track`。成交均价缺失时优先用订单查询均价，再用限价保守估计；只用于风控，真实收益仍保留未知。超时和资金费退出仍先判断。 | `test_r8_checked_status_retains_venue_average_fields`；`test_r8_missing_fills_use_order_average_or_conservative_limit_for_exit` |
| 9. UTC 日亏锁持久化 | `engine.py:200 reconcile`、`:133 can_open`。SQLite meta 存 `daily_loss_day`，当天盈亏回升、重建 Engine 均不解锁，跨日自动清理当日日锁，不清理其他异常原因。 | `test_r9_daily_loss_latch_survives_recovery_restart_and_expires_utc` |
| 10. 费率后台、逐合约隔离 | `accounts.py:85 fees`、`engine.py:89 refresh_reference`、`:53 fees_for`、`__main__.py:26 main`。从主循环移除小时同步刷新；只传白名单合约，逐合约发布费率及时间戳，失败仅撤销该合约值。空白名单不拉费率/不开新仓。 | `test_r10_fee_refresh_background_whitelist_and_tracking`；`test_r10_fresh_contract_fees_survive_other_contract_refresh_delay`；`test_r10_empty_whitelist_reads_no_contract_fees`；`test_r10_failed_contract_revokes_only_its_fee_and_timestamp` |
| 11. 首次及新原因告警 | `engine.py:40 alert`、`execution.py:96 confirm`/`:120 account`、`limits.py:40 check_equity`。执行器与硬风控进入统一告警入口，去重依据本进程已输出原因及锁状态，不被提前写入 stop_reason 吞掉首次输出；清除后再次进入同原因也输出。 | `test_r11_first_executor_stop_and_new_reason_reach_journal_once`；`test_r11_same_reason_after_clearing_is_a_new_stop` |

### 验证证据

首批测试在修改实现之前执行：

```text
Ran 20 tests in 4.360s

FAILED (failures=15, errors=6)
```

这些失败覆盖全部 11 项原问题。增加恢复边界后，28 项测试还复现了 HALTING 启动账户故障、逐合约费率时效、清锁后同原因告警三个缺口（2 failures、1 error），再修复并补齐为 33 项。旧 Engine 测试夹具改为显式发布缓存和使用实际 Engine 发送检查；没有删减原有断言或放宽风险阈值。

最终两条命令及原始汇总如下；取得这些结果之后，没有再修改实现或测试，只更新 README 和本交接。

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 133 tests in 52.513s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.166s

OK
```

执行器的 133 项包含全部 24 项只读 API 测试。相对本轮基线，`api_data.py`、`api.py`、`tests/test_api.py` 和整个 `tools/fill_sim` 无差异。只读接口结构/权限/故障隔离保持原有契约。全部 22 个执行器 Python 文件通过 `ast.parse(feature_version=(3,10))`，CLI `--help` 与 `git diff --check` 通过。环境为 Windows / Python 3.12.10 / SQLite 3.49.1，不是 Ubuntu 或 systemd 验收。

### 关键运行规则与剩余边界

1. 恢复持仓锚点只说明交易所该次快照数量，不代表未知订单已终结。`position_anchor.unresolved` 的迟到成交不会再叠加到已包含它的仓位；下次恢复重新撤/查并读取持仓。恢复时拿不到真实持仓，不猜测数量、不重发原开仓订单。清零前不伪报 HALTED。
2. 小于粗步长的差额，可能需要先规划粗腿一步以及细腿相应减仓；两条都是减仓，只有计划后的绝对净敞口更小时才采用。仍是非原子的跨交易所 IOC，单腿拒绝/部分成交可能留下敞口；重试不保证消除所有尾差，最终裸露量持久化并告警。最小名义额按通用规则检查，未假定交易所有额外的 dust 豁免。
3. `CROSSEX_BASES` 空值语义改为不开新仓。必须明确白名单；不会默认为全市场逐合约拉费率。后台目录/费率失败记录固定诊断，按失败场景重试；已有仓位用自身持久化规格继续跟踪和减仓。
4. 账户任务按 5 秒周期调度，不代表远端慢请求能在 5 秒内完成。缓存超过 10 秒不准开；减仓不依赖账户缓存可用。盘口最终检查为纯本地复核，不在下单工作线程内重新发私有读取。两腿仍无法获得跨所原子成交保证。
5. 本轮订单均价解析的回包夹具覆盖 CrossEx `avg_price` / Bitget `avgPrice`，缺失或无效回退到限价用于退出判断。没有用本轮离线测试宣称真实交易所回包、权限、撮合或费率已获验证；不会把估值填成 API 的真实 `entry_vwap` 或净收益。
6. 后台账户、目录/费率线程是有界数量的 daemon，关闭时停止继续发布 journal；真实网络总时限、并行压力、Linux 80M 内存及长期运行仍需后续获准验收。schema 沿用原有 meta/orders/pairs，不修改只读 API 投影代码。

新增 `background.py`、`tests/test_round3.py`；实现修改 `engine.py/policy.py/execution.py/limits.py/accounts.py/adapters.py/market.py/__main__.py` 和 service 模板；测试夹具修改 `tests/test_engine.py`；文档修改本交接和 README。没有修改 backend、compose、真实 `.env` 或既有生产部署脚本。临时测试日志位于本机 `%TEMP%`，没有入库；没有待保留的新未跟踪工作区产物。

线上状态：未 push、未 SSH、未 WSL、未调用真实鉴权接口、未联网、未做生产备份或部署，线上版本未改变。没有 amend 或实盘开关变更。下一模块请新建任务，从第三轮清单和本节开始做独立复核；任何联网、账户、Linux/C3 或上线动作都须在后续任务明确授权，本轮本地通过不等于开启实盘验收。

## 第四轮修复交付（2026-11-11）

### 范围、基线与提交

按用户指定顺序先读 `docs/crossex-exec-round4-2026-11-11.md`，再读本交接；仅处理执行器第四轮 8 项。每项均先写复现测试，在未修复实现上观察失败，再修复。用户本轮允许联网核实 CrossEx/Bitget 官方文档字段，覆盖第四轮清单中较早的“不联网”；除此之外不联网，绝不调用真实鉴权接口。

- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支 `codex/crossex-exec`。
- 基线：`e0476b9afb8143172a986a5abc9da535824ed16f`；开始时工作区干净。
- 没有 push、SSH、WSL、amend、读取真实 env/密钥或运行服务 `run`，没有生产备份或部署。
- 所有测试只使用内存交易所或 `127.0.0.1` 假服务器，虚构凭据，继续保留 socket/DNS 外网守护。

| 类型 | 完整提交 hash | 内容 |
|---|---|---|
| test | `18a571451fa6f63488a414c7655e323a4d0ec738` | 首批 21 项覆盖全部 8 项问题；修正旧 CrossEx 均价夹具 |
| test | `c60ae0fd48b021fda305858577d881f001447187` | 持久化停止补平、共享放行、跨日缓存、部分成交观察、异常日志边界；共 28 项新测试及旧夹具适配 |
| test | `271f8048a7870c4f1b04e1c197036f0890fe61eb` | 补充对账执行到一半跨过 UTC 午夜的复现，合计 29 项 |
| feat | `c04ce7eac430ffc35fe7e4f4e875953044f6cef4` | 全部 8 项实现，6 个执行器文件 |
| docs | 随本节和 README 单独提交 | 本轮证据、官方字段出处、规则及边界；避免硬编码自引用 hash |

取得本节 docs 提交号：

```text
git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md
```

父仓库中的 worktree 索引不在可写根内；普通 `git add` 被文件沙箱拒绝后，按已获授权的本地提交范围通过自动审批写入 Git 元数据。所有提交均禁用 hooks 和 GPG 签名，无外部副作用，没有 amend 或回退。

### 8 项修复位置与测试名

实现路径相对 `tools/crossex_exec/`；下表全部新增测试均在 `tests/test_round4.py::Round4Tests`。行号对应本轮 feat 提交。

| 项目 | 修复位置与行为 | 对应测试名 |
|---|---|---|
| 1. 补平不扩大裸露敞口 | `engine.py:532 repair_intents`、`:551 balance`、`resolve_exposure`。先读两腿真实持仓；每次只发一笔，确认后再读两腿并重新计算。单笔理论执行也须降低绝对裸露量；实际量未严格下降、零成交或无法读取持仓就持久化 `repair_stopped`、停开和告警。 | `test_r1_coarse_step_zero_fill_never_exceeds_original_exposure`；`test_r1_zero_fill_stops_before_other_leg_or_retry`；`test_r1_partial_fill_replans_from_both_remote_positions`；`test_r1_snapshot_failure_after_send_stops_further_repair`；`test_r1_initial_repair_uses_current_remote_excess_leg` |
| 2. 两腿一次共享复核 | `execution.py:23 execute_pair`、`:70 execute`；`engine.py:84 check_pair`，复用 `policy.py::send_review`。两腿通过限频和建连后才进入有界屏障，action 在同一个时刻只做一次盘口/价差/费率/资金费复核，结论同时适用于两腿；工作线程只执行发送。硬额度仍在整对进入适配器前和共享关口检查。 | `test_r2_asymmetric_book_ages_reject_both_legs`；`test_r2_late_second_worker_rejects_entire_pair`；`test_r2_shared_review_runs_once_after_both_workers_ready`；`test_r2_failure_before_barrier_releases_peer_without_sending`；`test_r2_send_failure_does_not_revoke_shared_admission` |
| 3. 费率和时间戳同版本 | `accounts.py:79 FeeSnapshot`、`:99 fees`；`engine.py:70 fees_for`、`refresh_reference`。frozen dataclass 同时保存 rate/captured_at，逐合约复制映射后一次替换发布，读取整份版本后检查时间；旧 rates/times 属性仅为派生投影，不用于发送校验。 | `test_r3_old_rate_cannot_borrow_new_timestamp`；`test_r3_published_fee_version_is_immutable_and_replaced` |
| 4. 订单均价字段 | `adapters.py:25 checked_status`。CrossEx 使用 `executed_avg_price`，不接受旧错误字段 `avg_price`；Bitget UTA 仍使用核实后的 `avgPrice`。`test_round3.py` 及本地 HTTP 夹具同步修正。 | `test_r4_order_average_uses_documented_fields_only`；原有 `Round3PolicyTests.test_r8_checked_status_retains_venue_average_fields` 继续验证 |
| 5. 新一天只计算浮盈亏变化 | `engine.py:204 totals`、`:231 roll_daily_upnl`、`:270 reconcile`、`refresh_accounts`；`accounts.py:149 snapshot` 增加按完整持仓身份的浮盈亏。`daily_upnl` 持久化当日各仓和汇总基准；昨日缓存不能建立今日基准，对账中跨日只暂时停开、等待新快照，不写永久账户故障锁。 | `test_r5_unchanged_overnight_loss_does_not_lock_new_day`；`test_r5_baseline_survives_restart_and_only_new_loss_latches`；`test_r5_previous_day_cache_cannot_set_new_daily_baseline`；`test_r5_realizing_overnight_loss_does_not_count_it_twice`；`test_r5_midnight_during_reconcile_waits_without_permanent_stop` |
| 6. 对账时序和自动解除 | `background.py:8 SnapshotCache`；`execution.py:126 confirm`；`engine.py:243 reconcile_positions`。快照携带读取起点，须严格晚于本地最新成交观察/成交时间；部分成交在 UNKNOWN 前也持久化观察时间。连续三次不同的新快照一致才清除 `position_mismatch`，写 `POSITION_MATCH_RECOVERED`；重复缓存不计数，新的差异重置，其他原因保留。 | `test_r6_pre_fill_or_equal_snapshot_never_flags_mismatch`；`test_r6_three_distinct_matching_snapshots_clear_and_log`；`test_r6_mismatch_resets_recovery_streak_and_preserves_other_stop`；`test_r6_partial_fill_observation_survives_unknown_confirmation` |
| 7. HALTING 事件和告警去重 | `limits.py:41 check_equity`；`engine.py:47 alert`、`unhedged`。`EQUITY_HALT`/`UNHEDGED` 及 `ALERT`/告警 sink 共用一次原因和数值比较；同一 HALTING 周期内多个相同原因轮流出现也不重复，数值变化重新输出。 | `test_r7_equity_events_and_alerts_share_value_sensitive_dedup`；`test_r7_halting_multiple_unchanged_reasons_do_not_repeat`；`test_r7_unhedged_event_and_alert_change_together` |
| 8. 线程异常和轮后退避 | `background.py:27 PeriodicWorker`、`:36 run`，`engine.py::background_error`、`:704 flatten_all`。最外层异常同时写标准日志和固定字段事件，同类型每 60 秒最多一次；不输出异常正文/traceback。HALTING 使用 finally 在本轮结束后设置下一次 2/4/8/16/32/60 秒退避，慢请求及异常返回均覆盖。 | `test_r8_outer_worker_exception_logged_throttled_and_sanitized`；`test_r8_distinct_worker_errors_reach_journal_once_each`；`test_r8_halting_backoff_begins_after_slow_round_finishes`；`test_r8_snapshot_error_also_sets_backoff_after_completion` |

关键数量证据：审查场景 `1 / 0.999`、步长 `.1 / .001` 的原裸露量为 `.001`；第一腿拟减 `.1` 本身会把裸露量变成 `.099`，因此现在直接拒绝此计划，没有第二笔依赖假设成交的单。原测试注入第一腿零成交时，旧实现最终裸露量为 `.100`，新实现仍为 `.001`。另以 `.107 / .1` 覆盖合法粗步长：第一笔零成交即停止；第一笔 `.01` 只成交 `.008` 时，第二笔按实仓只减 `.001`，不能沿用原先假设的 `.003`。

共享放行仍不是跨交易所原子成交：任一腿在放行后的网络、交易所接单或撮合阶段仍可能失败，原 UNKNOWN 恢复和单腿风险处理继续生效。补平停止标记只禁止继续补平，不阻止正常退出/风险全平。

### 官方字段核实

本轮只打开下列两家官方文档，未运行示例 curl、请求交易 API 或读取账户凭据：

| 官方来源 | 路径与已核实字段 |
|---|---|
| [Gate CrossEx API v1.0.8](https://www.gate.com/docs/developers/crossex/en/) | 订单详情 `GET /crossex/orders/{order_id}` 的 `CrossexOrder` schema：`executed_avg_price`，类型 string，成交均价；`executed_qty` 为已成交基础币数量。旧 `avg_price` 夹具不符合该 schema。 |
| [Bitget UTA Get Order Details](https://www.bitget.com/legacy-docs/uta/trade/Get-Order-Details) | `GET /api/v3/trade/order-info`：`avgPrice` 为 Average executed price，类型 string；`cumExecQty` 为基础币累计成交量。该页明确是 UTA v3，不能与 Classic v2 字段混用。 |

两者均仅用于订单详情价格兜底风控；真实 `vwap`、手续费及收益继续由逐笔成交校验，缺失则保持记账未知。

### 复现和最终验证

首批测试在修改任何实现前执行，全部 8 项均有失败证据：

```text
Ran 21 tests in 0.405s

FAILED (failures=18, errors=1)
```

随后追加边界测试先得到 `Ran 25 tests in 0.505s / FAILED (failures=5)`，再修复持久化停止补平、初始远端持仓、放行后取消屏障、跨日旧缓存、裸露事件去重。最后单独复现对账执行中跨午夜的永久停开，结果为 `Ran 1 test in 0.016s / FAILED (failures=1)`，再增加等待新日基准的专门异常路径。最终新增 29 项。

完整回归时同步更新旧夹具：假 HTTP 服务器按实际已成交量更新持仓，兼容单双向字段；费率夹具显式发布不可变版本；日亏测试显式建立期初 0 基准。旧“零成交仍试三次”断言按本轮要求改为“第一笔后立即停止”，没有放宽风控阈值或删除原有安全覆盖。修正假服务器单向持仓字段遗漏后，旧 Engine 33 项通过。

最终两条 unittest 命令和原始汇总行：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 162 tests in 55.823s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.462s

OK
```

执行器 162 项包含全部 24 项只读 API 测试。取得最终结果之后没有再修改实现或测试。全套 23 个 Python 文件通过 `ast.parse(feature_version=(3,10))`；CLI `--help` 与 `git diff --check` 通过。运行环境为 Windows / Python 3.12.10 / SQLite 3.49.1。执行器完整原始输出保存在本机 `%TEMP%/crossex-round4-final-unittest.log`，不入库。

### 文件、线上状态和下一步

本轮修改实现 `accounts.py/adapters.py/background.py/engine.py/execution.py/limits.py`；新增 `tests/test_round4.py`，修改 `tests/test_engine.py/tests/test_exec.py/tests/test_round3.py`；文档仅本交接和 README。相对基线，整个 `tools/fill_sim`、只读 API 三文件、backend、compose、部署脚本和模板均无修改。没有新增环境变量、真实 `.env` 或秘密入库。

日界线采用 UTC 换日后的首个有效账户观测，保存各仓浮盈亏及总额；后台按 5 秒周期调度，真实请求可能更慢，不能把缓存称为精确午夜的交易所报价。昨日缓存或正在跨日的对账只暂时停开，等到新快照再判定；当天已存在的亏损锁和其他风控不会因重启丢失。持仓一致恢复需要三个不同采样起点，受既有 60 秒对账调度影响。HALTING 告警去重为进程内同一停机周期，重启可再次输出首条诊断。

最终为本地交付，无待保留的新未跟踪工作区文件；Python 缓存由既有 ignore 规则排除。未 push、SSH、WSL、amend、鉴权接口调用、真实服务启动、生产备份或部署，线上版本与实盘开关未改变。尚未验证 Ubuntu 22.04 / Python 3.10 / SQLite 3.37 实机、systemd、80M 内存、真实账户时序、撮合和长期运行。

本模块结束。下一模块请新建任务，从第四轮清单、本节及 feat 提交开始独立复核；C1/C3、Linux/生产动作及开启实盘须另有明确授权。本地回归通过不代表实盘验收通过。

## 第五轮本地修复与待决验收（2026-11-11）

### 状态、范围与提交

**当前未完成整体验收：5 项修复和 20 项专项回归通过，但严格第 1 条不变量失败。** 没有修改断言来伪造通过，没有停止自动平仓来规避测试。用户已收到异步澄清，尚未答复：补平严格不增裸露、正常双腿平仓允许暂时裸露并持续恢复，或所有减仓都严格不增裸露（相等双腿因此不能自动平仓）。选择会影响既有退出/止损行为，不能擅自代选。

先读 `docs/crossex-exec-round5-2026-11-11.md`，再读本交接。任务仅为 crossex-exec 第五轮，worktree 为 `C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支 `codex/crossex-exec`；基线 `c5134803291ce8f98bd86e554779458e1a0a19b7`，开始时工作区干净。

完全离线；未读取真实凭据、调用真实鉴权接口、运行服务 `run`、push、SSH、WSL、amend、生产备份或部署。HTTP 测试仅使用原有 loopback 假服务和虚构签名，保留 socket/DNS 外网守护。普通 Git 写索引因父仓库在可写根外被文件沙箱拒绝，随后按用户已授权的本地提交范围通过自动审批；所有提交禁用 hooks 和 GPG 签名。

| 类型 | 完整 hash | 内容 |
|---|---|---|
| test | `b21e284e42ec433da21f5d8d8e0ea95136cd0d17` | 先提交覆盖全部 5 项的 14 个失败复现 |
| test | `3b1e9fa05e92117b3369169bd5ac01a7ad153e7d` | 固定种子模拟器、旧测试按新契约适配、HALTED/迁移/历史持仓/退出边界 |
| feat | `c1a03c2c1fd4150f49f938a294c45e92c3755928` | 独立锁、流水/实仓分离、订单内存待写及恢复、午夜基线 |
| test | `f3cb7614195a53c4719c0515ab1c212f3061efa3` | 两腿发送完成前不启动确认的先失败测试；失败时完整输出步骤序列 |
| feat | `2048f2fc0ce4d0b4992e1ca11dc6fd4d71431e6c` | 共享放行后先完成两腿发送，再并发确认 |
| docs | 随本节与 README 单独提交 | 当前真实证据及待决口径；不硬编码自身 hash |

docs hash：`git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md`。未改写任何既有提交。

### 5 项修复位置与全部专项测试

代码路径相对 `tools/crossex_exec/`；以下 20 项均在 `tests/test_round5.py::Round5Tests`。行号对应上述最终 feat。

| 项目 | 修复位置及规则 | 测试名 |
|---|---|---|
| 1. 独立停开锁 | `stops.py:6 StopLocks`；`engine.py:50 alert`、`:164 can_open`、`:518 clear_repair_locks`、`:618 balance`；`limits.py::check_equity`。按原因和 pair 保存集合，展示原因不覆盖其他锁。补平锁只在实仓相等、流水一致、无非终态/待记账订单、补平结束时解除；停止补平仍允许正常退出。HALTED 不被后台改回 HALTING。 | `test_r1_matching_ledger_cannot_clear_failed_repair_lock`；`test_r1_balanced_remote_clears_only_this_pairs_repair_locks`；`test_r1_lock_set_survives_restart_and_halted_is_never_auto_cleared`；`test_r1_background_equity_check_cannot_revive_halted`；`test_r1_legacy_singleton_repair_lock_migrates_to_pair_owner`；`test_r1_stopped_repair_still_allows_scheduled_exit` |
| 2. 流水/实仓分离 | `engine.py:453 refresh_pair`、`:511 save_remote`、`:282 reconcile_positions`、`:542 resolve_exposure`。`remaining` 仅累计成交；`remote_remaining/remote_captured_at` 独立保存实仓。旧 anchor 不再作为流水基准；记账未完成时不累计匹配解锁；历史 CLOSED 不获得新 pair 的远端仓位。 | `test_r2_remote_snapshot_never_overwrites_fill_ledger`；`test_r2_pending_fill_accounting_never_advances_unlock_streak`；`test_r2_legacy_anchor_is_not_a_local_ledger_baseline`；`test_r2_closed_history_cannot_claim_new_pairs_remote_position` |
| 3. 写失败与非终态恢复 | `execution.py:30 orders`、`:36 save`、`:47 flush`、`:55 execute_pair`、`recover`；`engine.py:815 tick`、`recover_pair/resolve_exposure`。内存待写状态优先于旧数据库状态，重试只补写，不重发；SENT 落库前失败不发送。每 tick 检查 SENT/ACK/UNKNOWN，不依赖 pair 的缓存状态/调度时间。发送与确认分阶段，避免先完成的一腿在另一腿尚未完成发送时启动 UNKNOWN 恢复。 | `test_r3_unknown_write_failure_retains_recovery_and_retries_write`；`test_r3_every_tick_recovers_sent_even_when_pair_state_and_schedule_are_stale`；`test_r3_failed_sent_commit_never_calls_the_exchange`；`test_r3_pair_dispatch_finishes_before_confirmation_can_latch_peer` |
| 4. 午夜前最后浮亏基线 | `engine.py:242 roll_daily_upnl`、`:262 save_tracking_upnl`、`totals/track/close_pair/resolve_exposure`；`accounts.py:149 position_snapshot`。持续保存 `last_upnl` 各仓和汇总值；跨日先冻结旧值再存新观测，不受新日首次快照前平仓影响。持仓接口有浮盈亏时保存，缺字段不阻止已知数量的恢复。 | `test_r4_close_before_first_new_day_snapshot_uses_last_saved_upnl`；`test_r4_tracking_saves_each_position_upnl_before_midnight_and_restart`；`test_r4_position_recovery_saves_available_upnl_without_blocking_on_missing_upnl` |
| 5. HALTING 启动恢复 | `engine.py:176 startup`、`flatten_all`；`execution.py::recover`；`accounts.py:149 position_snapshot`。INTENT 先转 UNSENT；恢复先于账户验证，持仓读取不调用余额或 settings。远端或订单仍未知则保留 HALTING、按轮后退避重试。 | `test_r5_halting_startup_account_failure_still_retires_intent`；`test_r5_account_outage_does_not_block_position_only_recovery`；`test_r5_position_reader_does_not_require_account_or_settings` |

旧测试调整均有契约依据：恢复顺序改为先查后撤；持仓失败注入独立持仓读取器；旧“新日首次快照为基线”改为上日最后保存值；账户启动失败前仍须恢复订单。没有删除原测试、提高风险阈值或放宽模拟器断言。

### 模拟器、复现和待决口径

入口：`tests/test_invariants.py::InvariantSimulationTests.test_fault_invariants_seed_20261111_2400_rounds`。调用真实 Engine/Executor，使用可注入一次提交失败的假数据库和独立假交易所；重启丢弃进程对象，仅从已提交副本恢复。固定种子 **20261111**，**2400 回合**，为 100 个各 24 步的状态序列，其中 600 步影子、1800 步 live 假交易所。每步均执行 7 条断言；失败即输出种子和步骤序列，第 1 条失败会收集并继续跑满回合，最终仍以失败退出，不 skip、不 xfail。

覆盖部分/零成交、发送后超时并迟到 ACK 或保持 UNKNOWN、查单失败、独立持仓接口失败、一次数据库提交失败、从已提交副本重启、跨 UTC 午夜、盘口过期、价差消失、大幅反向价格引发止损/熔断，并额外覆盖账户不可读。部分/零成交还与退出价格组合，防止只测试开仓部分成交而漏掉不对称平仓。

**N=1**：若本轮跟踪前有非终态订单或已知净裸露，下一次协调器 tick 就进入查询/撤单/读取实仓/补平或平仓的恢复路径。HALTING 进入后可等待已记录的退避期限；每次到期继续尝试，未证明实仓和非终态订单都清零不得 HALTED。N 是协调器步数，不是秒数，也不保证交易所故障在 N 步内消失。

最终实际覆盖包括：296 笔开仓、359 笔减仓、112 次部分成交、73 次零成交、72 次 timeout 后 ACK、86 次 timeout 后 UNKNOWN、78 次写失败、142 次重启、111 次恢复义务检查、140 次持仓读取失败、151 次账户失败。查单重试次数随实际线程调度和确认时间预算变化，不用固定循环次数冒充固定延迟；随机注入计划和数量使用固定种子。

**结果：第 2–7 条通过，补平逐笔不扩大净裸露通过；第 1 条对正常双腿平仓的严格要求失败 14 次。** 第一个反例在零基步骤 **76**（第 77 回合）：原实仓 `0.980 / 0.980`，止损平仓时两腿成交比例为 `0.25 / 1`；三轮减仓后实仓 `0.414 / 0`，净裸露从 `0` 变成 `0.414`。这已经是整轮结束后的检查，不只是第一腿成交的短暂中间状态。

两家交易所的 IOC 不是原子撮合；只要允许一腿零成交或独立失败，不能同时保证“相等的两腿自动平仓”和“净裸露每轮绝不增大”。本轮保留正常退出机制和严格失败证据。未采用“相等两腿不平仓”或“只验证补平”的替代验收，等待用户明确口径；当前不能声称 7 条不变量全部通过或可以实盘。

模拟器还暴露了共享放行后的一条确认时序边界：先完成的一腿可能在另一腿仍发送时写 UNKNOWN 锁。先补测试观察 `early=[True]`，再将 `execute_pair` 拆为发送汇合、并发确认两阶段；最终模拟器第 2 条通过。

### 测试原始汇总与运行边界

首批测试在修改实现前：

```text
Ran 14 tests in 0.296s

FAILED (failures=12, errors=2)
```

新增迁移、HALTED、历史仓位和停止补平后的正常退出边界也先观察到 4 项失败；持仓浮盈亏字段及两腿发送/确认时序分别先观察到缺字段错误和断言失败，再修复。最终专项：

```text
Ran 20 tests in 0.861s

OK
```

最终两条完整 unittest 命令及原始汇总（**第一条未通过**）：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 183 tests in 71.211s

FAILED (failures=1)

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.279s

OK
```

183 项中 182 项通过，唯一失败为上述严格第 1 条模拟器断言；包含全部原有只读 API 测试。模拟器原始计数：

```text
INVARIANTS seed=20261111 rounds=2400 N=1 checks={1: 2400, 2: 2400, 3: 2400, 4: 2400, 5: 2400, 6: 2400, 7: 2400} I1_failures=14
```

Windows / Python 3.12.10；26 个 Python 文件通过 `ast.parse(feature_version=(3,10))`，CLI `--help`、`git diff --check` 通过。完整原始输出在本机 `%TEMP%/crossex-round5-final-unittest.log` 和 `%TEMP%/crossex-round5-fill-sim-unittest.log`，种子/步骤也在前者，日志不入库。未重新运行未修改的 fill-sim 测试来替换已经取得的原始结果。

### 文件、线上状态与下一步

实现仅修改 `engine.py/execution.py/accounts.py/limits.py` 并新增 `stops.py`；测试新增 `test_round5.py/test_invariants.py`，适配 `test_engine.py/test_round3.py/test_round4.py`；文档为本交接及 README。未修改只读 API 实现、整个 fill-sim、backend、compose、部署脚本/模板、真实 env 或实盘开关。Python 缓存仍被忽略，没有需保留的新未跟踪工作区产物。

没有 push、生产备份、部署和线上验证，线上版本未变。未验证 Linux/systemd/80M 内存、真实账户时序或撮合。`remaining` 为流水字段，成交回报落后于实仓减仓时可以暂时为负并标记待记账；不得拿它冒充远端实仓。人工复核 HALTED 时不得只删除 `stop_reason` 或整个数据库绕过独立锁。

下一步仍在本模块：收到用户对第 1 条的明确口径后，先补相应测试，再按 test/feat/docs 新提交收尾，重新跑 crossex-exec 完整套件并更新本节。当前不要转入新模块或把第五轮标为完成；完成后再为下一个模块新建任务。
