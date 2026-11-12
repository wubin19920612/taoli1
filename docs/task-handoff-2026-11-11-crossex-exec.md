# crossex-exec 本地实现交接 — 2026-11-11

> `codex/crossex-exec-r8` 已合入本分支的终审修复，最新状态与双版本原始验收见 [合并终审修复](task-handoff-2026-11-12-crossex-exec-r8.md#合并终审修复)。下文保留来源分支及第八轮的历史证据。

> **来源分支历史状态：第七轮本地验收通过，已采用批准的非零 5bp 基准；其终审修复见文末“终审修复”。** 第七轮续验收执行器 270 项、fill-sim 158 项及三个原种子各 2448 回合通过。历史失败和当时的待决提案保留作证据，不代表当前待决事项。本任务仅本地交付，没有推送、部署、真实行情或鉴权接口调用。

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

## 第五轮本地修复与待决验收（2026-11-11，历史记录，后续已验收）

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

## 第五轮 PM 决定补充验收（2026-11-11）

**本次本地验收完成：1a/1b/1c/1d 与 2–7 全部严格通过，三个种子各 2448 回合，两条完整 unittest 均通过。** 本节接续前面的历史记录，取代其中“口径待决、整体验收未通过”的当前状态；保留原失败证据，不修改旧结论发生时的记录。

### 范围、基线与提交

- 先读取 `docs/crossex-exec-round5-2026-11-11.md` 末尾 PM 决定和本交接第五轮部分。本次只处理 crossex-exec 第五轮不变量及其暴露的恢复问题。
- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支：`codex/crossex-exec`；基线：`9036b1856105d0d51254626c54df98bf03a46da1`；开始时工作区干净。
- 不变量 1 的拆分是 **PM 明确决定，不是放宽断言**。新定义先独立 test 提交；实现修复随后独立 feat；本节与 README 独立 docs。不改写旧提交，没有 amend。
- 全程本地操作，未联网、push、SSH、WSL、读取真实凭据、运行服务 `run`、备份生产或部署。只使用内存交易所、假数据库及已有 loopback HTTP 测试；外网 socket/DNS 守护仍在。

| 类型 | 完整提交 hash | 内容 |
|---|---|---|
| test | `2bba9e5a323272854a417cd5a55f5636c0a56305` | PM 四条独立断言、三个种子及首批 7 项定向复现 |
| test | `9324caa869fb4dd12928a233e2da5241ea68ee73` | 保留原随机回合并追加故障可达性覆盖；补齐平仓后读取失败、慢请求及单腿最小额边界，共 10 项定向测试 |
| test | `f45479ad741e59a2ba016eec3c5178733a09d6f3` | 从实际双腿发单建立恢复义务，避免 `exit_reason` 漏写或错误 CLOSED 状态隐藏失败 |
| feat | `636534abaa1934d8d8aa2c83fab5c6fc086ac2e9` | 单腿安全检查、裸露门禁、持续恢复、持久化退避、发送后持仓未知标记 |
| docs | 随本节及 README 单独提交 | 原始结果、计数、规则、边界及下一步；不硬编码自身 hash |

本节 docs hash：`git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md`。本地 Git 索引位于父仓库，通过已授权范围的自动审批写入；仅暂存本任务文件，提交禁用 hooks 和 GPG 签名。

### 不变量的严格口径

| 不变量 | 实际断言 |
|---|---|
| 1a | 对每笔实际单腿 reduce-only 发单，检查 `abs(after[0]-after[1]) <= abs(before[0]-before[1])`。包括退出过程中的单腿单，不以 `exit_reason` 豁免。 |
| 1b | 仅把同一实际批次包含两条不同腿且均为 reduce-only 的发单视为双腿平仓。逐笔中间成交与整批数量均受原批次实仓约束：净裸露绝对值不超过平仓前较大单腿数量 `max(before)`，每条腿均满足 `0 <= after <= before`；等仓时上界就是原单腿数量。 |
| 1c | 双腿平仓残余裸露后，N=1 内实际进入查询/撤单/两腿持仓读取/补平恢复。读取可能失败，但不能只写一条 RECOVERY_ATTEMPT 事件冒充执行。残余量大于一个步长时，检查该 pair 的 `unhedged_position` 锁及 ALERT；未清零的恢复义务有持久化且不超过 60 秒的重试期限，到期必须实际尝试读取和恢复。 |
| 1d | 每个新开仓批次的共享放行时实仓必须等量；发送行必须关联该放行记录。每个回合结束只要还有任何差额，`can_open()` 必须为 false。未靠“已有锁”间接推断，而有专项测试验证缺锁、旧平衡快照下仍拒绝新开仓。 |
| 2–7 | 保留停开锁、禁止穿零、150/600 硬上限、影子零发单/撤单、N=1 恢复、HALTING 清零或继续有限退避的原有断言。 |

1d 沿用第四、五轮已有的双腿共享放行：同批的两个开仓腿在任何一腿发送前共同获准，随后完成该批对冲发送；同批第二腿不是再次申请的新仓。检查新批次放行前的实仓，并检查每回合结束的门禁，不在第一腿成交后取消已经共同放行的对冲腿。

**N=1 是协调器步数，不是秒数或保证成交的期限。** 首次发现临时裸露即进入恢复；失败后在每次 tick 处理恢复义务，新的减仓遵守持久化的 2/4/8/16/32/60 秒退避，60 秒封顶后持续重试。退避从本轮完成时刻开始，慢请求、异常和重启均不能把期限重置为立即发送。非终态订单的查询/撤销/确认不因减仓退避停止。

模拟器的两个合约步长都是 `.001`；通用阈值取两腿步长的较小值。1a/1b 没有浮点容差或步长豁免。锁的解除仍要求实仓等量、流水一致、无待写/非终态/待记账状态且补平结束；小于等于一步也不会自动假报完全平仓。

### 修复入口与定向测试

实现仅改 `tools/crossex_exec/engine.py`。新定向测试位于 `tests/test_pm_invariants.py::PMInvariantTests`，以下行号对应 feat 提交。

| 位置与修复 | 测试名 |
|---|---|
| `can_open:164`：直接检查本地流水、远端数量差和 `remote_pending`，缺少停开锁也拒绝新开仓批次。 | `test_i1d_remote_exposure_blocks_admission_without_a_stop_lock`；`test_i1d_local_exposure_blocks_admission_with_old_balanced_remote` |
| `_close_pair:714`：第一批双腿平仓后重读实仓，残余差额优先逐单补平；双腿计划只剩一笔合法订单时仍检查该笔不得增裸露。 | `test_i1a_one_untradable_close_leg_cannot_turn_pair_into_unsafe_single`；`test_i1a_single_close_cannot_hide_behind_exit_reason`；`test_i1b_checks_intermediate_fill_and_original_batch_bound` |
| `needs_recovery:532`、`recover_pair:538`、`balance:626`：退出未完成、实仓未知或补平停止均继续恢复；`repair_stopped` 停止当前补平轮次，之后按退避退出剩余仓位。 | `test_i1c_stopped_entry_repair_eventually_retries_remaining_positions` |
| `close_pair:686`、`unhedged:597`：残余裸露加 pair 独立锁并告警，保存 `next_close/close_attempts`，持续退避；重启保留期限。 | `test_i1c_incomplete_close_locks_alerts_and_retries_to_flat`；`test_i1c_close_retry_survives_restart_and_backs_off_from_completion`；`test_i1c_retry_deadline_starts_after_slow_snapshot_failure` |
| `refresh_pair:456`、`save_remote:514`、`clear_repair_locks:521`、`_close_pair`：发单前保存 `remote_pending`，新实仓快照成功才清除；失败时不得以旧平衡数量清锁或报 CLOSED，告警数量/剩余量/超额腿为 null。 | `test_i1c_post_close_snapshot_failure_latches_uncertain_exposure` |
| `close_pair` 的退避入口：继续确认迟到成交，退避仅约束新的减仓。 | 原有 `test_round3.Round3Tests.test_r1_late_entry_fill_after_remote_flat_never_reopens_or_double_closes`，断言未修改 |

定向测试先观察到 `Ran 7 tests in 0.143s / FAILED (failures=5)`。三个种子在旧实现上的首个失败分别为 20261111 第 77 回合、20261112 第 28 回合缺少 unhedged 锁，以及 20261113 第 46 回合缺少持久化重试期限。后补的发送后持仓失败测试也先失败，再修复。没有 skip、xfail、捕获断言后当作通过或删除既有回归。

### 模拟器回合、计数与原始输出

入口为 `tests/test_invariants.py::InvariantSimulationTests.test_fault_invariants_seed_<seed>_2448_rounds`。三个种子固定为 **20261111、20261112、20261113**，每个 **2448 回合**，合计 **7344 回合**。每个种子保留原来的 2400 回合随机生成方式和顺序，再追加两个 24 步 live 假交易所序列；每种子共 600 步 shadow、1848 步 live 假交易所。

追加 48 回合的原因：初次长跑中，新种子 20261112 的查单故障只调度到了无待查单场景，20261113 没有实际发生 HALTING 失败重试，非空覆盖断言正确失败。保留这些断言及全部随机回合，追加“UNKNOWN 后查单失败”和“HALTING 单腿零成交后持续恢复”序列，使故障真正到达相关路径；没有更换失败种子，也没有用定向回合替换原 2400 回合。

仍覆盖部分/零成交、超时后 ACK/UNKNOWN、查单与持仓读取失败、一次数据库写失败、从已提交副本恢复、UTC 午夜、过期盘口/消失价差、逆向价格/熔断，并额外覆盖账户失败。随机注入计划和数量可复现；查询重试次数受真实线程调度和确认时间预算影响。失败输出种子和完整步骤序列。

`checks` 是每回合对完整不变量组的检查次数，含该回合没有对应发单/故障时的条件检查：

| 种子 | 1a | 1b | 1c | 1d | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20261111 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 |
| 20261112 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 |
| 20261113 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 | 2448 |

`exercised` 另外统计条件实际成立时的检查次数，并逐种子断言每项都大于零。一次回合可包含多次发单/恢复检查，这不是独立交易数：

| 种子 | 1a | 1b | 1c | 1d | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20261111 | 148 | 242 | 58 | 360 | 328 | 390 | 328 | 600 | 96 | 408 |
| 20261112 | 145 | 242 | 37 | 350 | 316 | 387 | 316 | 600 | 87 | 410 |
| 20261113 | 114 | 216 | 42 | 328 | 304 | 330 | 304 | 600 | 86 | 447 |

以下复制最终模拟器输出（仅去掉 unittest 同行测试名前缀）：

```text
INVARIANTS seed=20261111 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 148, '1b': 242, '1c': 58, '1d': 360, '2': 328, '3': 390, '4': 328, '5': 600, '6': 96, '7': 408} coverage={'account_failures': 151, 'db_failures': 89, 'full_fill': 526, 'halted': 406, 'halting_retry': 2, 'intent_write_abort': 88, 'open': 328, 'partial_fill': 119, 'position_failures': 139, 'position_read': 3668, 'query': 17400, 'query_failures': 4052, 'recovery_obligation': 96, 'reduce': 390, 'restarts': 142, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 458, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'timeout_ack': 82, 'timeout_unknown': 99, 'zero_fill': 73}
INVARIANTS seed=20261112 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 145, '1b': 242, '1c': 37, '1d': 350, '2': 316, '3': 387, '4': 316, '5': 600, '6': 87, '7': 410} coverage={'account_failures': 116, 'db_failures': 84, 'full_fill': 499, 'halted': 406, 'halting_retry': 4, 'intent_write_abort': 81, 'open': 316, 'partial_fill': 144, 'position_failures': 141, 'position_read': 3706, 'query': 11978, 'query_failures': 655, 'recovery_obligation': 87, 'reduce': 387, 'restarts': 142, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 458, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'timeout_ack': 76, 'timeout_unknown': 83, 'zero_fill': 60}
INVARIANTS seed=20261113 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 114, '1b': 216, '1c': 42, '1d': 328, '2': 304, '3': 330, '4': 304, '5': 600, '6': 86, '7': 447} coverage={'account_failures': 134, 'db_failures': 94, 'full_fill': 480, 'halted': 446, 'halting_retry': 1, 'intent_write_abort': 91, 'open': 304, 'partial_fill': 94, 'position_failures': 141, 'position_read': 3559, 'query': 16709, 'query_failures': 1576, 'recovery_obligation': 86, 'reduce': 330, 'restarts': 114, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 458, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'timeout_ack': 78, 'timeout_unknown': 113, 'zero_fill': 60}
```

### 两条 unittest 的原始汇总

最终执行器命令是在实现和测试停止修改后执行；之后仅提交已测试的实现以及更新文档。fill-sim 未发生代码或测试修改，保留下列本轮实际运行结果，不为调整耗时而重复执行。

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 195 tests in 95.251s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.670s

OK
```

195 项包括三个种子的完整模拟器、10 项新 PM 定向测试及全部原有 API/执行器回归。Windows / Python 3.12.10 / SQLite 3.49.1；27 个执行器 Python 文件通过 `ast.parse(feature_version=(3,10))`，CLI `--help`、`git diff --check` 通过。未引入依赖安装或网络查询。

完整原始日志保留在本地被 Git 忽略的 output 目录：

| 文件 | SHA-256 |
|---|---|
| `output/crossex-pm-final-unittest.log` | `03bb87bdec7aa29a7f28b71f539adaf00a87483aa608ac90644dea01d3d60353` |
| `output/crossex-pm-fill-sim.log` | `2a4f86816b915aa378d429fe5873364cd85c05413bc6ac7babc01fa94f747609` |

诊断日志 `output/crossex-pm-red.log`、`output/crossex-invariants-red.log`、`output/crossex-pm-edge-red.log`、`output/crossex-pm-full-check.log` 等同样保留，不入库。

### 线上状态、文件与下一步

本次只修改 `engine.py`、`tests/test_invariants.py`、新增 `tests/test_pm_invariants.py`，并更新 README 和本交接。只读 API、整个 fill-sim、backend、frontend、compose、部署配置与 PM 决定原文均未改。没有其他任务的修改或未跟踪文件被暂存；测试日志和 Python 缓存保留在忽略范围内。

未 push、生产备份、部署或线上验证，线上版本未变。实盘撮合、Linux/systemd/80M 内存、真实账户时序仍未验收。远端持续不可用、流动性不足或最小下单量导致无法消除尾差时，仍可能有持仓：保留锁、告警和持续重试，不承诺 N=1 内成交。

第五轮 PM 补充的本地验收已收尾。下一个模块请新建任务，从本节与 PM 决定开始；联网、账户验证、Linux 验收或上线仍需后续任务明确授权。

最终 Git 状态检查时新出现未跟踪文件 `docs/crossex-exec-round6-2026-11-11.md`，本轮未创建、未读取、未修改、未暂存，保留原样供下一任务处理；本轮提交后工作区因此不标记为完全干净。第五轮实现及测试没有再次变动。

## 第六轮交付（2026-11-11）

### 范围、基线和提交

按要求先读第六轮任务书，再读本交接。模块限于常态基准、超额开平仓、小时资金费成本、冷却及只读观测。worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支：`codex/crossex-exec`；基线：`ff4af35208117761a56d8589e0474fa0cdb8ea08`。开始时工作区干净，第六轮任务书已在基线中。

先测试、后实现，只核实官方公开接口文档。未执行文档示例、调用真实行情或鉴权接口、读取真实env/密钥、运行服务run、push、SSH、WSL、amend、备份或部署生产。测试使用内存交易所及127.0.0.1假HTTP服务，保留socket/DNS外网守护。

| 类型 | 完整提交 hash | 内容 |
|---|---|---|
| test | `6ff69e8b1cf0243d54201391a1015d3a675d0ea7` | 首批24项契约、旧测试的合成基准历史、模拟器基准/冷却检查 |
| test | `1e14895bf2f77f0b57a4c9cbb8966f6aef2214d7` | 扩为30项专项；实际限频计数、亏损退出、无基准拒绝和发送时效；退出场景隔离 |
| feat | `d836978cc307f1356f1718fd369897e30b618b6f` | 六个实现文件：基准、策略、原子冷却和观测 |
| docs | `f66873e296f2cfca475106b0eccd9b8a8191fd28` | 本轮规则、官方出处、契约、README和原始证据 |
| test | `720d4a9c55d897ddccc2a23a67e1e53af3c5fc00` | 仅删除两个新增测试文件末尾各一行空白；测试逻辑未变 |
| docs | 随本次交接补记独立提交 | 记录空白清理提交和验证边界 |

docs hash：`git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md`。所有提交禁用hooks/GPG。worktree索引在父仓库，普通写入被文件沙箱拒绝后，按已授权的本地提交范围通过自动审批写入；没有自动审批拒绝待处理。

### 文件、入口与规则

| 文件（相对 tools/crossex_exec） | 内容 |
|---|---|
| `baseline.py`（新增） | `BATCH_URLS/parse_tickers`、`Baseline.record/status/require`、无方向组合键及冷却条件 |
| `market.py` | `baseline_quotes`每所一个全市场GET，复用queue/client/limiter，priority=2 |
| `storage.py` | 新增baseline_samples及时间索引、30s去重/24h清理；关闭记录与冷却原子提交 |
| `policy.py` | 小时资金费×2h、review/send_review净超额门槛、冻结超额止盈 |
| `engine.py` | 独立30s基准任务；ingest和原共享准入关口检查；保存快照；track传入新止盈参数 |
| `api_data.py` | positions/trades三项bp、summary三项计数，保留原字段 |
| `tests/test_round6.py`（新增） | 30项离线专项测试 |
| `tests/baseline_fixtures.py`（新增） | 真实统计所用合成30s历史，不绕过生产准入门 |

旧测试改动：`test_api.py/test_engine.py/test_exec.py/test_round3.py/test_invariants.py`。原HTTP退出测试的五个共享子场景改为独立临时数据库测试，原退出断言保留，另加实际亏损退出后拒绝下一触发的集成测试。

`execution.py`、适配器、私有传输、账户、硬上限、停开锁，以及Engine恢复/补平/平仓执行/熔断函数均未改。整个fill-sim、backend、frontend、compose、部署脚本/模板和真实.env未改。共享准入关口在读完基准后重新取时间，读取耗时计入盘口2s限制。

1. **采样**：独立于taoli1/CrossEx行情。只接受白名单精确USDT永续代码，不归并倍率或交割代码。重复、空、非正、交叉、NaN/Inf报价拒绝。有时间戳时拒绝超过90s旧值或领先5s；Gate批量ticker无报价时间戳，以有总时限的本轮接收时间为准。一所失败不复用上轮报价；整轮超过20s丢弃。
2. **存储**：键为`base|字典序交易所a|交易所b`，价差`(mid_b/mid_a-1)*10000`。每30s桶首个有效点落库，采样时删除24h外样本；统计仅取最近6h。中位数/MAD使用Decimal。反向先把每个样本变为`(1/(1+bps/10000)-1)*10000`再求中位数，不能简单取负。
3. **预热/拉黑**：有效点≥300、首末跨度≥10800s、最新点≤90s才ready。缺历史/断流时`baseline_warming`，无0基准回退。规范方向`abs(baseline)≥80`或`MAD≥40`时`structural_spread`；首次预热后评估，再每600s评估。拉黑和评估时刻落库，重启保留；缺数据不解除旧拉黑，数据恢复后可自动恢复。
4. **开仓**：原50档深度、100USDT、步长/限价/最小额/150/600上限不变。以最终合法数量VWAP求S，`excess=S-baseline`；`excess−2*(taker_long+taker_short)*10000−funding_cost_bps≥15bp`才通过。真实账户费率缺失仍拒绝。例：常态40bp、当前60bp只算20bp超额，四笔费用共5bp且资金费0时恰好通过。
5. **退出**：开仓冻结`baseline_at_open/excess_at_open`；可平仓价差≤`baseline_at_open+excess_at_open/2`止盈，持有中不跟随新基准。反向50bp、浮亏1%、7200s、结算前300s、陈旧60s退出均保留。升级前缺快照旧持仓保留原半价差止盈，API为null，不伪造历史。
6. **资金费**：`max(0,rate_long*3600/interval_long_seconds−rate_short*3600/interval_short_seconds)*2*10000`。每腿先按公开读取的实际周期折算小时，再合计截断收款；不是直接相减一期费率，也不对每条收款腿单独截断。缺任一费率或周期、非正周期、非有限值均拒绝。原24h历史完整性、绝对费率0.5%黑名单和结算前30min不开仍有效。成本为预估，保留interval/next/estimated，实际跨期资金费未知仍为null。
7. **冷却**：CLOSED、资金费/费用完整已知、net_usd<0，且退出原因映射为stop_loss/max_hold/pre_funding/stale/one_leg时，写`pair_cooldowns[key]=max(旧截止时间,closed_at+86400)`。覆盖内部loss_limit/adverse_spread/max_age/funding_exit/funding_unknown/stale_exit/repair_failed/unknown_order/unhedged_position别名。双向共用，盈利/零收益、converged/halt和未知收益不触发。延迟费用确认后再判断，期限从原关闭时刻算。事务注入失败时关闭记录与冷却一起回滚；重启/重复刷新不延长期限。未知记账仍由原停开锁保护。
8. **观测**：新增bp字段均为开仓复核估算快照，不替换实际成交价差/收益。summary按无方向组合计数，ready可与拉黑/冷却重叠。API仍独立只读；契约只增加字段和说明。fill-sim估值保持原策略，不把它称为第六轮同策略回测。

### 官方公开批量接口出处（本轮核实）

仅打开官方文档，未访问下面的实际行情URL。每轮六个请求均进入共享限频预算（每所2/s、全局8/s及429退避），每30s每所一次。Binance全市场权重5（新增10权重/分钟），Aster全市场权重2（新增4权重/分钟）；现有limiter按请求次数记账，不声称它独立实现了交易所原生权重模型。

| 交易所 | 实际公开GET（不传symbol/contract） | 字段 | 官方出处 |
|---|---|---|---|
| Binance | `https://fapi.binance.com/fapi/v1/ticker/bookTicker` | symbol、bidPrice、askPrice、time | [USD-M Symbol Order Book Ticker](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#symbol-order-book-ticker) |
| Bybit | `https://api.bybit.com/v5/market/tickers?category=linear` | result.category/list、symbol、bid1Price、ask1Price、time | [Get Tickers](https://bybit-exchange.github.io/docs/v5/market/tickers) |
| OKX | `https://www.okx.com/api/v5/market/tickers?instType=SWAP` | instType、instId、bidPx、askPx、ts | [GET Tickers](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers) |
| Gate | `https://api.gateio.ws/api/v4/futures/usdt/tickers` | contract、highest_bid、lowest_ask | [Get all futures trading statistics](https://www.gate.com/docs/developers/apiv4/en/futures/#get-all-futures-trading-statistics) |
| Bitget | `https://api.bitget.com/api/v3/market/tickers?category=USDT-FUTURES` | category、symbol、bid1Price、ask1Price、requestTime | [UTA Get Tickers](https://www.bitget.com/legacy-docs/uta/public/Tickers) |
| Aster | `https://fapi.asterdex.com/fapi/v3/ticker/bookTicker` | symbol、bidPrice、askPrice、time | [官方Futures V3](https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md#symbol-order-book-ticker) |

**Aster与任务书示例的差异**：任务书列v1；当前[官方仓库](https://github.com/asterdex/api-docs)明确推荐新集成用V3，Futures V3文档明确提供全市场bookTicker，因此采用v3，与现有执行器Aster公开目录/深度一致。未对旧v1文档作推断，没有请求实际API验证可用性。

资金费周期来源沿用公开解析：Binance/Aster的premiumIndex+fundingInfo.fundingIntervalHours；Bybit ticker.fundingIntervalHour；Gate contract.funding_interval（秒）；OKX的nextFundingTime−fundingTime（毫秒差转秒）；Bitget公开v2 current-fund-rate.fundingRateInterval（小时）。出处为上述Binance/Aster/Bybit/Gate文档及[OKX funding-rate](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate)、[Bitget Classic Market](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-current-funding-rate)。Binance fundingInfo若无目标市场记录，拒绝开仓而不推断8h。

### 测试过程和最终证据

修改实现前首批：`Ran 24 tests in 0.065s / FAILED (failures=1, errors=23)`；模块/接口缺失及旧发送公式按预期失败。后来慢基准读取测试先复现：`Ran 29 tests in 0.445s / FAILED (failures=1)`，缺省基准测试加入后也先失败，再修正。期间纠正一次测试资源清理顺序：MarketData先于SQLite关闭。

第一次完整219项回归仅旧共享退出子场景失败4次，原因是前一笔亏损正确触发新冷却；拆为五项独立测试后，原退出断言全通过。最终229项包括原195项覆盖、拆分新增4项及第六轮30项。没有skip/xfail、删除安全断言或提高阈值。下面原始输出来自最后一次实现与测试逻辑修改之后；随后仅更新文档，并删除两个新增测试文件末尾各一行空白。该清理的`git diff --ignore-blank-lines`为空，未因纯空白修改重跑测试。

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 229 tests in 101.548s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.588s

OK

```

不变量入口仍为 `tests/test_invariants.py::InvariantSimulationTests`，种子 **20261111、20261112、20261113**，每个 **2448回合**，合计 **7344回合**。保留原2400随机回合顺序和48个故障可达性回合；每种子600影子回合、1848 live假交易所回合。1a/1b/1c/1d及2–7全部通过，每条每种子checks=2448。N=1仍指下一协调器步骤进入真实恢复尝试，不保证成交期限。

模拟器加入持久化的合成40bp历史，随后追加独立公共中间价观测；跨午夜缺口不回填。重启复制已提交样本、结构状态和冷却。不清冷却、不mock准入门。每次开仓检查冻结基准和冷却截止时间；新增coverage必须大于0。基准/冷却减少了部分开仓次数，但所有原故障与非空覆盖断言保留。

以下为实际触发条件的exercised次数，不是独立交易数：

| 种子 | 回合 | 1a | 1b | 1c | 1d | 2 | 3 | 4 | 5 | 6 | 7 | 基准准入 | 冷却生效回合 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20261111 | 2448 | 111 | 164 | 52 | 254 | 230 | 275 | 230 | 600 | 58 | 395 | 230 | 424 |
| 20261112 | 2448 | 101 | 174 | 29 | 246 | 222 | 275 | 222 | 600 | 65 | 395 | 222 | 207 |
| 20261113 | 2448 | 80 | 154 | 36 | 225 | 206 | 234 | 206 | 600 | 61 | 407 | 206 | 276 |

原始模拟器行（只去掉unittest同行测试名前缀；查询重试数随线程调度变化）：

```text
INVARIANTS seed=20261111 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 111, '1b': 164, '1c': 52, '1d': 254, '2': 230, '3': 275, '4': 230, '5': 600, '6': 58, '7': 395} coverage={'account_failures': 151, 'baseline_admission': 230, 'cooldown_active': 424, 'db_failures': 52, 'full_fill': 366, 'halted': 394, 'halting_retry': 1, 'intent_write_abort': 51, 'open': 230, 'partial_fill': 78, 'position_failures': 138, 'position_read': 3506, 'query': 9775, 'query_failures': 2528, 'recovery_obligation': 58, 'reduce': 275, 'restarts': 142, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 458, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'timeout_ack': 68, 'timeout_unknown': 53, 'zero_fill': 61}
INVARIANTS seed=20261112 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 101, '1b': 174, '1c': 29, '1d': 246, '2': 222, '3': 275, '4': 222, '5': 600, '6': 65, '7': 395} coverage={'account_failures': 116, 'baseline_admission': 222, 'cooldown_active': 207, 'db_failures': 59, 'full_fill': 352, 'halted': 392, 'halting_retry': 3, 'intent_write_abort': 57, 'open': 222, 'partial_fill': 99, 'position_failures': 139, 'position_read': 3549, 'query': 9156, 'query_failures': 649, 'recovery_obligation': 65, 'reduce': 275, 'restarts': 142, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 458, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'timeout_ack': 60, 'timeout_unknown': 63, 'zero_fill': 46}
INVARIANTS seed=20261113 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 80, '1b': 154, '1c': 36, '1d': 225, '2': 206, '3': 234, '4': 206, '5': 600, '6': 61, '7': 407} coverage={'account_failures': 134, 'baseline_admission': 206, 'cooldown_active': 276, 'db_failures': 62, 'full_fill': 332, 'halted': 406, 'halting_retry': 1, 'intent_write_abort': 60, 'open': 206, 'partial_fill': 66, 'position_failures': 141, 'position_read': 3423, 'query': 11969, 'query_failures': 1554, 'recovery_obligation': 61, 'reduce': 234, 'restarts': 114, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 458, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'timeout_ack': 59, 'timeout_unknown': 77, 'zero_fill': 42}
```

完整原始日志位于被Git忽略的output目录：

| 文件 | SHA-256 |
|---|---|
| `output/crossex-round6-final-unittest.log` | `4dd2075905bb7f526e039de12d7c1380b93b8400367a21460848503f9ce163ab` |
| `output/crossex-round6-fill-sim.log` | `b82a452fc683ffda96e5e121f03b3b38e3c73177607eb03f1f06307d754eae69` |

红测及首次完整回归诊断保留于 `output/crossex-round6-red.log`、`output/crossex-round6-admission-red.log`、`output/crossex-round6-first-suite.log`；不入库。Windows / Python 3.12.10 / SQLite 3.49.1，30个执行器Python文件通过Python 3.10 AST语法检查，CLI `--help`及 `git diff --check` 通过。

### 线上状态、保留文件与下一步

没有推送、生产备份、部署或线上验证；线上版本未变，实盘开关未改。官方文档核实不证明当前真实API可用、数据质量、账户模式/费率/成交时序或实盘撮合。Ubuntu/Python 3.10/SQLite 3.37实机、systemd、80M内存及白名单规模下的长期采样/写库/调度延迟仍待独立验收。新增样本表为追加式schema升级，不删除旧订单、仓位或元数据。

未知费用/跨期资金费不冒充净收益，仍停开等核对；历史缺基准快照不会被回填。正常预热至少3小时；长时间断流可能重新预热。真实交易所故障、流动性或最小量仍可能留敞口，原恢复/锁/退避机制保留，本地模拟通过不构成开启实盘授权。

执行中出现非本任务创建的未跟踪文件 `docs/crossex-exec-round7-pending-2026-11-11.md`，只记录路径，未读取、修改或暂存，保留原样。因而交付工作区不标为完全干净；本轮代码/测试/文档均分组提交，其余产物是既有忽略范围内的日志和Python缓存。

第六轮本地交付完成。下一模块请新建任务，从本节、当前契约和对应待办文档继续；真实接口、账户、Linux/C3或上线验收需后续任务明确授权。

## 第七轮交付（2026-11-12，续验收已通过）

**最新结论：用户已批准以非零 5bp 适配旧夹具；续验收执行器 270 项、fill-sim 158 项全部通过。** 完整授权、覆盖证据、独立提交和原始输出见本节末尾“第七轮续验收结论”。下面保留批准前的失败及待决记录。

### 续验收前的状态与待决冲突（历史记录）

**A/B 实现、41 项新增专项和契约字段已完成，本轮整体验收尚未通过。** 最新执行器全套为 270 项，262 项通过、8 项失败；fill-sim 158 项通过。不宣称不变量模拟器通过，不将该结果作为实盘授权。

唯一需要用户决定的旧断言冲突：`tests/test_invariants.py::run_seed` 对每笔实际新开仓断言 `baseline_at_open == 40`，对应 `World` 的合成历史也是 40bp；第七轮新规则要求 `|baseline_bps| <= 10`。用户同时要求“不得删改或放宽已有测试断言”，两者无法同时满足。已发出异步确认：是否允许**仅将这项基准夹具和对应期望值由 40 改为 0，并新增 <=10 校验**，1a–1d、2–7 的所有风控断言及非空覆盖要求保持原样。此时尚未收到答复，不能将等待时间当作授权。

当前仍保留该 40bp 断言和夹具，因此所有模拟开仓都被 `baseline_not_flat` 拒绝，三个种子虽各运行 2448 回合，却在非空覆盖断言失败；依赖同一 `World` 开仓的 5 项 PM 恢复定向测试也无法建立仓位。没有跳过这些测试、捕获后算通过、删除覆盖检查或给模拟器绕过 v1 门禁。

### 范围、分支与提交

- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支 `codex/crossex-exec`；基线 `7646e883a42d30339b00521d92323686d4d884e7`（第七轮任务书），开始时工作区干净。
- 先读 AGENTS、第五/六轮交接、接口契约，再读第七轮任务书。修改仅限 `tools/crossex_exec` 及本任务 docs；**本轮没有修改 `tools/fill_sim`**。
- 仅联网读取官方盘口时间戳文档，没有请求实际公开行情或鉴权 API，没有读取真实 env/凭据、启动服务 run、push、SSH、WSL、amend、生产备份或部署。所有交易调用均为原有 loopback 假服务/内存交易所，外网 socket/DNS 守护保留。
- Git 索引在父仓库可写根之外，初次写索引被文件沙箱拒绝后，按用户已授权的本地提交范围通过自动审批写入；没有自动审批拒绝待处理。提交均禁用 hooks/GPG，只暂存本任务文件。

| 类型 | 完整 hash | 内容 |
|---|---|---|
| test | `43039b7fb0a59ca8c78e19e45d0b047242e8c668` | 首批 32 项，先运行得到预期失败 |
| test | `044fd982570f56ad640c22dd910f00cf6267d7b5` | 扩展为 40 项，慢定价红测、共享门禁、原子计数及旧夹具适配 |
| test | `fcffc2ee79d2941ec7166f5c638a302bbb570eeb` | 缺时钟只限制开仓的先失败测试；陈旧盘口夹具补齐 Book 导入 |
| feat | `8d92d812bdb40c4927a567331d7ef4b87b5f2f5f` | 同时性、v1 准入、最新报价轮次、观测和持久化计数 |
| docs | 随本节、契约和 README 独立提交 | 真实验收状态、原始失败证据及待决事项 |

docs hash 仍用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md` 取得，不硬编码自身 hash。

### 实现入口与业务规则

| 文件（相对 tools/crossex_exec） | 入口与变更 |
|---|---|
| `admission.py`（新增） | `OpeningLimits.books/funding/flat/outlier`、阈值常量、`AdmissionError.reason`、只观测不抛错的 `book_skew_ms` |
| `engine.py` | `opening_review` 初始全复核；`check_pair` 共享发送前全复核；`fetch_opening_books` 成对重拉；减仓记录时间差 |
| `market.py` | `opening_books/parse_execution_book` 要求官方有时钟的深度字段；普通 `books` 保持原平仓解析路径 |
| `baseline.py` | `record/latest` 持久化完整最新报价轮次，成功、部分失败和空轮都整体替换 |
| `policy.py` | 原 `send_review` 校验通过后返回重算的 VWAP、超额及资金费估算；原公式和门槛不放松 |
| `storage.py` | `skip` 将触发 ID 去重标记、六类计数、SKIP 事件放在同一事务中 |
| `api_data.py` | positions/trades 两个时间差、summary `opening_skip_counts`；原字段全部保留 |

1. **时间门禁**：默认两腿交易所时间戳绝对差 <=300ms；每腿交易所时间年龄及收到时间年龄均在 [0,1] 秒。不存在交易所时钟的 Book 结构才使用收到时间；实际六所开仓深度响应缺应有时钟字段时解析失败，不伪装为回退。未来时间、NaN/Inf、缺腿均拒绝。陈旧与不同步同时存在时优先 `book_stale`。
2. **重拉预算**：初始复核与共享发送前复核各最多一次；每次整体提交两腿到原公共队列，保留每所 2/s、全局 8/s、429 退避和请求总时限，不增加平行请求通道。重拉失败不复用其中一条旧腿。定价/基准读取/限频/共享复核写库耗时均纳入年龄检查；最后仍满足原限价、净超额、资金费/费率和账户缓存硬风控才能同时放行。发送后不独立取消一腿。
3. **资金费**：分别以实际周期秒数折算每小时。两腿差的绝对值 <=0.5bp/h；每腿小时率绝对值乘 8 <=0.0005（0.05%）。任一腿率/周期未知、非有限或周期非正均 `funding_diff`。原历史完整性、绝对费率黑名单、近结算禁开以及 2h 成本公式继续保留。
4. **平坦基准**：初始及共享关口均要求方向基准绝对值 <=10bp、MAD <=10bp；缺值也拒绝，原因 `baseline_not_flat`。原预热、80/40 结构性锁存、冷却继续适用；不会将尚未 ready 的历史当作 0。
5. **一家异常**：最新一轮至少 3 家白名单所报价，包含交易两腿和至少一家外部参考，默认轮次年龄 <=90s。M 为该轮有效中间价的中位数；用实际当前两腿深度中间价对照 M，异常腿正确方向偏离 >=当次扣费前超额的 70%，另一腿偏离 <=10bp；外部参考也须全部落在 M 的 10bp 内。买低/卖高，不能反向；两腿都异常或参考意见不一致时 `not_single_venue_outlier`；缺所、非白名单所不能凑数、参考轮次过期或缺失时 `too_few_venues`。完整失败轮次会清空参考集合，不跨轮合并。
6. **阈值**：`admission.py` 常量固定上/下界；10 项可选配置详见 README，只能调严。所有 MAX 配置须有限、非负且 <=常量；MIN_OUTLIER_SHARE 只接受 [0.7,1]，MIN_VENUES 只接受 3–6 整数；调松/无效值在 Engine 构造时直接拒绝。重拉次数常量为 1，不提供放宽开关。
7. **观测**：开仓时间差取最终获准盘口；共享关口重拉后重算超额及预估成本并持久化，协调器重读以免旧局部对象覆盖。平仓不运行上述时间或 v1 门禁；最近减仓定价使用两腿盘口的时间差会保存，可能包含自身缓存盘口，未知为 null。时间差不替代订单发送/成交延时。所有六类放弃次数按 trigger ID 去重，跨日/重启保留，重拉成功不计数，计数写失败整笔事务回滚。

契约仅增加 7 行，未删除或改写既有字段。其历史 20bp 基准示例保留，并注明只用于兼容历史仓位，不是第七轮新开仓的合法例子。JSON meta/pairs/triggers 为追加字段，无破坏性 schema 迁移。

### 官方盘口时间戳核实

仅阅读下列官方文档，未执行示例和实际行情请求。**当前六所使用的永续深度接口均提供交易所时间戳，不提供时间戳的交易所名单为空。** 不把 Gate 批量 ticker 的接收时间口径误套到 Gate 深度。

| 交易所 | 当前深度路径及时间字段 | 官方出处 |
|---|---|---|
| Binance | `/fapi/v1/depth`；`T` 优先，`E` 后备；毫秒 | [USD-M Order Book](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#order-book) |
| Bybit | `/v5/market/orderbook`；`result.cts` 撮合引擎时间优先，`result.ts` 后备；毫秒；开仓不能用外层响应 time 代替缺失的盘口时钟 | [Get Orderbook](https://bybit-exchange.github.io/docs/v5/market/orderbook) |
| OKX | `/api/v5/market/books`；`data[0].ts` 盘口生成时间；毫秒 | [Order book](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-order-book) |
| Gate | `/api/v4/futures/usdt/order_book`；`update` 优先，`current` 后备；官方示例为带小数秒 | [Futures order book](https://www.gate.com/docs/developers/apiv4/en/futures/#futuresorderbook) |
| Bitget | `/api/v2/mix/market/merge-depth`；`data.ts` 撮合引擎时间；毫秒（公开深度沿用 v2，不改变私有 UTA v3） | [Get Merge Market Depth](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-merge-market-depth) |
| Aster | `/fapi/v3/depth`；`T` 交易时间优先，`E` 输出时间后备；毫秒 | [Futures V3 官方文档](https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md#order-book) |

时间字段、优先级、秒/毫秒换算均有离线测试。文档核实不证明真实接口当前可用、时钟同步或市场流动性。

### TDD 与旧测试保护

- 首批实现前红测：`Ran 32 tests in 0.503s / FAILED (failures=18, errors=59)`，其中参数化子测试分别报告错误；随后 32 项通过。
- 慢定价新边界先失败：`Ran 40 tests in 0.958s / FAILED (failures=1)`，修复后 `Ran 40 tests in 0.916s / OK`。
- 缺时间戳不能额外阻止平仓先复现：`Ran 3 tests in 0.043s / FAILED (failures=1, errors=1)`；随后拆分开仓/普通读取路径。最终新增 41 项在下面完整回归中全部通过。
- 旧拒绝陈旧盘口场景只调整市场夹具：一次重拉仍返回原陈旧数据；原 `UNSENT/零发单` 断言全部保留。新增专项独立覆盖重拉变好后放行。
- 原第六轮“冻结 40bp 基准止盈”场景改为升级前已开仓夹具，所有 40/160/收敛平仓断言保留；新开仓非零合法 5bp/195bp 冻结由新增专项验证。纯定价公式测试、历史统计测试和 API 历史值断言继续保留 40bp。
- API 字段集合只增项；内存数据库补齐 skip 的持久化模型。未改 fill-sim 或私有传输/执行器发送恢复状态机。
- 对六个变动的原测试文件逐函数比较基线与当前 `assert* / fail / skipTest` 调用的 AST，结果：`Existing test assertion AST audit: 6 files; changes=[]`。没有 skip/xfail 或降低旧覆盖要求。静态语法检查 32 个执行器 Python 文件通过 Python 3.10 AST；CLI `--help`、`git diff --check` 通过。

### 两条完整 unittest 与模拟器原始结果（历史失败，续验收已通过）

最后一次实现与测试修改之后，实际执行（Windows / Python 3.12.10 / SQLite 3.49.1）：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 270 tests in 84.865s

FAILED (failures=8)

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.962s

OK
```

模拟器实际失败原文（不是 INVARIANTS 成功行；成功输出在非空覆盖断言之后，当前未到达）：

```text
AssertionError: seed=20261111 checked_rounds=2448 error=0 not greater than 0 : vacuous invariant: 1a
AssertionError: seed=20261112 checked_rounds=2448 error=0 not greater than 0 : vacuous invariant: 1a
AssertionError: seed=20261113 checked_rounds=2448 error=0 not greater than 0 : vacuous invariant: 1a
```

其他 5 项失败均在 `test_pm_invariants.PMInvariantTests.world` 的原断言 `len(world.store.pairs(active=True)) == 1`：

```text
test_i1a_one_untradable_close_leg_cannot_turn_pair_into_unsafe_single
test_i1c_close_retry_survives_restart_and_backs_off_from_completion
test_i1c_incomplete_close_locks_alerts_and_retries_to_flat
test_i1c_post_close_snapshot_failure_latches_uncertain_exposure
test_i1c_retry_deadline_starts_after_slow_snapshot_failure
```

所有原始日志在本机 `%TEMP%`，不入库；完整步骤序列保留在执行器日志内：

| 文件 | SHA-256 |
|---|---|
| `crossex-round7-pending-unittest.log` | `27bdcaab7bdaa97c2c8d0fda4de530adc67cce04b9fc0a8b6417b3bbbeea1a5d` |
| `crossex-round7-fill-sim.log` | `2c6bc0ee870fa83bb8bb5c17636a10b4f0e00b65ffb7cd21c3d8e55c37d667d7` |

初始诊断另外保留为 `crossex-round7-red.log`、`crossex-round7-first.log`、`crossex-round7-first-suite.log`、`crossex-round7-pricing-red.log`、`crossex-round7-specialist.log`。首次完整 261 项有 13 项失败，后续修正陈旧盘口和基准状态夹具后，减少到上述 8 项；不隐藏历史失败。

### 线上状态、剩余风险与继续步骤（历史待决，续验收已解决）

没有 push、生产备份、部署或线上验证；线上版本、实盘开关与真实配置未变。没有额外未跟踪仓库文件需要交接；Python 缓存仍在既有忽略范围，日志只在临时目录。Ubuntu/Python 3.10/SQLite 3.37 实机、systemd/80M、真实账户时序与撮合仍未验收；本轮公共重拉增加的等待时间受原队列限频约束，可能导致更多放弃，这是保守准入的预期结果。

当前留在本模块，不转入下个模块。收到用户对上述唯一断言适配的明确许可后：先以独立 test 提交把模拟器历史/对应期望 40 改为 0，最新轮报价补齐第三家独立参考，并增加新门槛的非空覆盖检查；保留原随机种子/2400 随机回合及 48 故障回合、1a–1d/2–7 全部原风控断言。随后重跑完整执行器套件，贴真实 INVARIANTS 三行和汇总，补充本节验收结论；若发现实现问题，继续先红测后 feat。fill-sim 本轮完全未改，以上 158 项结果仍有效。未经该许可不能以测试专用绕过、改计量单位或放宽生产条件来消除冲突。

### 第七轮续验收结论（2026-11-12，已按批准完成）

**第七轮本地验收通过。** 本次用户转达 PM（Claude）代用户批准的具体适配值为 **5bp，不是上一段提案中的 0bp**；该授权已解决上文待决事项。两个完整套件均通过，三个种子各完成 2400 随机回合及原有 48 故障回合，1a–1d、2–7 的检查与非空断言全部通过。未发现需要修复的生产实现问题，因此只有 test/docs 分开提交，没有 feat 提交。

#### 范围、基线与提交

- worktree 仍为 `C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`，分支 `codex/crossex-exec`；续验收基线 `1647b12f66426db7a8dbc02fc5c854e74a90826f`。开始时工作区干净。
- test 提交：`8f6d385a0a5b5333bea93ff158c73e6ac97fd6aa`，仅修改 `tools/crossex_exec/tests/test_invariants.py` 和 `tools/crossex_exec/tests/test_pm_invariants.py`。
- docs 提交：本节及第七轮状态说明独立提交；用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md` 取得其完整 hash。
- 生产代码、阈值、鉴权/传输和 `tools/fill_sim` 均无变更。没有绕过任何 v1 门禁，没有新增生产配置或特殊放行。Git 仅写入已授权的本地分支，禁用 hooks/GPG；父仓库索引写入通过自动审批，没有审批拒绝或待确认事项。

#### 夹具、覆盖与旧断言保护

1. `World` 的正常历史为 361 个间隔 30 秒的 **5bp** 样本，MAD=0；最新轮使用模拟两腿的当前价格，并补齐独立 Bybit 参考价 100。正常初始盘口 100/102、参考中位数 100，能够真实通过一家异常门禁。历史统计、初始及发送前复核仍调用真实 Baseline/Engine/OpeningLimits。
2. `run_seed` 原 `baseline_at_open == 40` 改为 **==5**。新增实际发送对应的基准绝对值≤10、0≤MAD≤10、以买卖 VWAP 重算的 `excess_at_open == gross - 5` 断言，确保非零扣减仍被覆盖。
3. 原 RNG、FAULTS、种子 20261111/20261112/20261113、2400+48 回合和 N=1 保持不变。另用 `random.Random(f'{seed}:opening')` 选择每个随机 episode 的准入坏数据：六个正常选项加六类故障选项，约一半 episode 保留正常输入；末尾 48 回合不叠加这些故障。只改变输入数据，不替换准入返回值。
4. 六类拒绝均覆盖：15bp 合成历史触发 `baseline_not_flat`；独立参考价 101 触发 `not_single_venue_outlier`；缺第三所触发 `too_few_venues`；短腿时钟落后 500ms 触发 `book_skew`；100 秒旧盘口触发 `book_stale`；长腿 0.00048/8h、短腿 0/8h 的 0.6bp/h 差触发 `funding_diff`（每腿仍低于 0.05%/8h）。负面基准 episode 的 MAD 同样为 0；它不能新开仓。
5. `skip_*` 只累计 Engine 实际请求 SKIP 后、按 trigger 去重的内存持久化模型计数增量，重启/重复读取不重复计数；不以“安排了故障”代替实际触发。每个种子的六类计数**逐类 >0**。新增 `opened_pairs >0`，按实际非减仓发送且至少一腿成交量>0的 pair ID 去重，排除纯 SHADOW、零成交和只写意图的情况。
6. PM 的五项恢复定向测试直接复用适配后的 `World`，原 `len(world.store.pairs(active=True)) == 1` 及两腿数量相等断言保留；另验证正持仓量、5bp/195bp、Bitget 异常腿及 Bybit 独立参考。该模块全部 10 项通过。

对两份修改文件与基线作 `assert* / fail / skipTest` 调用 AST 比对，仅归一化已批准的 40→5 后：旧断言缺失或改变均为 `[]`，分别新增 6/5 个断言调用；原种子、回合数、恢复上限、INVARIANTS 和 FAULTS 定义 AST 完全相同。逐项 diff 复核没有 skip/xfail、降低原非空要求或生产修改。`git diff --check` 通过，32 个执行器 Python 文件通过 Python 3.10 AST 语法检查。

实际非空覆盖（计数来自下面完整原始输出）：

| 种子 | 实际成交新仓 | baseline_not_flat | not_single_venue_outlier | too_few_venues | book_skew | book_stale | funding_diff |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20261111 | 37 | 75 | 109 | 23 | 28 | 112 | 56 |
| 20261112 | 34 | 65 | 139 | 42 | 20 | 86 | 91 |
| 20261113 | 47 | 43 | 120 | 17 | 28 | 73 | 68 |

上述“实际成交”均指内存交易所执行的模拟成交，不是真实账户交易。

#### 两条完整回归与 INVARIANTS 原文

修改前先复现 PM 夹具失败：`Ran 10 tests in 0.234s / FAILED (failures=5)`，五项仍在原 active==1 断言失败。适配后的首次完整执行器套件即全部通过，未修改生产实现。

最后一次测试代码修改后实际执行，Windows / Python 3.12.10 / SQLite 3.49.1：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 270 tests in 97.004s

OK

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 31.353s

OK
```

三条 INVARIANTS 原文，仅去掉 unittest 同行测试名称前缀：

```text
INVARIANTS seed=20261111 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 35, '1b': 60, '1c': 25, '1d': 84, '2': 74, '3': 95, '4': 74, '5': 600, '6': 23, '7': 345} coverage={'account_failures': 151, 'baseline_admission': 74, 'cooldown_active': 167, 'db_failures': 22, 'full_fill': 122, 'halted': 344, 'halting_retry': 1, 'intent_write_abort': 21, 'open': 74, 'opened_pairs': 37, 'partial_fill': 27, 'position_failures': 137, 'position_read': 3257, 'query': 4036, 'query_failures': 1356, 'recovery_obligation': 23, 'reduce': 95, 'restarts': 142, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 458, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'skip_baseline_not_flat': 75, 'skip_book_skew': 28, 'skip_book_stale': 112, 'skip_funding_diff': 56, 'skip_not_single_venue_outlier': 109, 'skip_too_few_venues': 23, 'timeout_ack': 28, 'timeout_unknown': 21, 'zero_fill': 20}
INVARIANTS seed=20261112 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 27, '1b': 56, '1c': 13, '1d': 77, '2': 68, '3': 83, '4': 68, '5': 600, '6': 30, '7': 355} coverage={'account_failures': 116, 'baseline_admission': 68, 'cooldown_active': 24, 'db_failures': 16, 'full_fill': 112, 'halted': 354, 'halting_retry': 1, 'intent_write_abort': 15, 'open': 68, 'opened_pairs': 34, 'partial_fill': 24, 'position_failures': 139, 'position_read': 3275, 'query': 5053, 'query_failures': 652, 'recovery_obligation': 30, 'reduce': 83, 'restarts': 142, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 458, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'skip_baseline_not_flat': 65, 'skip_book_skew': 20, 'skip_book_stale': 86, 'skip_funding_diff': 91, 'skip_not_single_venue_outlier': 139, 'skip_too_few_venues': 42, 'timeout_ack': 22, 'timeout_unknown': 34, 'zero_fill': 15}
INVARIANTS seed=20261113 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 37, '1b': 78, '1c': 27, '1d': 106, '2': 94, '3': 115, '4': 94, '5': 600, '6': 31, '7': 385} coverage={'account_failures': 134, 'baseline_admission': 94, 'cooldown_active': 158, 'db_failures': 20, 'full_fill': 162, 'halted': 384, 'halting_retry': 1, 'intent_write_abort': 20, 'open': 94, 'opened_pairs': 47, 'partial_fill': 27, 'position_failures': 141, 'position_read': 3256, 'query': 5524, 'query_failures': 634, 'recovery_obligation': 31, 'reduce': 115, 'restarts': 114, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 458, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'skip_baseline_not_flat': 43, 'skip_book_skew': 28, 'skip_book_stale': 73, 'skip_funding_diff': 68, 'skip_not_single_venue_outlier': 120, 'skip_too_few_venues': 17, 'timeout_ack': 39, 'timeout_unknown': 37, 'zero_fill': 20}
```

日志保留在本机 `%TEMP%`，不入库；保留了上文第七轮原失败记录，没有覆盖原日志：

| 文件 | SHA-256 |
|---|---|
| `crossex-round7-resume-pm-before.log` | `2bd7a7d9ffde9f81b4a402ec6a47c6d35e7748cd8e3ad8b16f551b4f21fdf98d` |
| `crossex-round7-resume-unittest.log` | `1ec7da48db053700b2042e019a5fc6cb5856f7e79e72f2a5ba2df2e1806c59e2` |
| `crossex-round7-resume-fill-sim.log` | `be1287178cd2e66d7fb1e2cdc2f39c8519710236f14bc9c91bc40dfabe4d0409` |

#### 交付边界与下一步

本轮遵循本地交付约束：未 push、SSH、WSL、amend、访问外部公开行情或鉴权接口、读取真实凭据、生产备份、部署或线上验证。测试仍使用内存交易所和 loopback 假服务，并保留外网 socket/DNS 守护。线上版本与配置未触碰；Ubuntu/Python 3.10/SQLite 3.37 实机、systemd/80M、真实账户时序与撮合仍未验收，Python 3.10 AST 检查不等同于该运行环境验收。

第七轮本地验收已收尾，不再有本轮待决事项。工作区只提交上述测试文件和本交接文档，没有需要移交的额外未跟踪文件；临时日志及忽略的 Python 缓存保留。下个模块请新建任务并从本节接续；联网、实机验收或上线按后续任务的明确范围另行开展。

## 第八轮交付（2026-11-12，契约红测与冲突交接，尚未实现）

**第八轮未完成，按用户要求停在旧断言冲突处，等待 PM 决定。** 当前只有新增红测与文档，没有第八轮生产实现。完整回归为执行器原有 270 项通过、新增 2 项预期失败；fill-sim 158 项通过。原不变量三个种子各 2448 回合通过，但尚未加入第八轮“运行中改参数、映射币种开仓”覆盖，不能作为第八轮验收通过的证据。

### 范围、基线与提交

- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec-r8`；分支 `codex/crossex-exec-r8`；开始时工作区干净。
- 基线：`698985a6b88407b8a211f7f95000203a3bbc4961`，父提交为第七轮验收 `cde65ed`。已读取 AGENTS、本交接第五至七轮、接口契约及第八轮任务书。
- test 提交：`d8b21a68b27d81e043498fca9b567d4a7a0456da`，只新增 `tools/crossex_exec/tests/test_round8_contract.py`。
- docs：本节及契约末尾待实现说明独立提交；hash 用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md` 获取。没有 feat 提交。
- 新测试使用真实临时 SQLite `Store/JournalView`，保留原 socket/DNS 外网守护。未修改原测试、夹具或生产代码。对基线已跟踪的两个测试目录逐文件比较（仅归一化 CRLF/LF）：`Existing tracked test files checked: 48 changes= []`。

### 必须由 PM 决定的冲突

第八轮要求 summary 增加 `size_too_small / oi_missing / mapping_ratio_mismatch` 放弃次数。现有计数契约位于 `opening_skip_counts`；第七轮测试 `tests/test_round7.py::ReferenceAndAPITests.test_all_six_counters_are_zero_then_count_once_per_trigger_across_restart` 在第 462、470 行对**整个字典**作严格相等断言，本文件 `REASONS` 固定为六项。因此新增三项，即使原六项值、去重及持久化全正确，也会让旧断言失败。

复现过程没有修改旧断言：

1. 新增两个第八轮测试：空历史三项必须为 0；三类原因按 trigger 去重，重启后仍各为 1，事件数为 3。当前字段缺失，两项均失败。
2. 临时诊断先运行上述原测试：`Ran 1 test in 0.041s / OK`；再仅以 `unittest.mock.patch.object(api_data, 'SKIP_REASONS', 原六项 + 新三项)` 扩展内存输出字段：`Ran 1 test in 0.021s / FAILED (failures=1)`，停在原第 462 行。这个 mock 只证明 schema 冲突，不是实现或验收通过；没有写回源码。

```text
self.assertEqual(view.get('summary',{})['opening_skip_counts'],dict.fromkeys(REASONS,0))
AssertionError: {'boo[117 chars]s': 0, 'size_too_small': 0, 'oi_missing': 0, '[22 chars]': 0} != {'boo[117 chars]s': 0}
```

**提请批准的最小适配，尚未执行**：仅扩展这两处完整字典期望，保留六项 `REASONS` 及原循环。首处期望原六项和新三项全为 0；第二处期望原六项为 1、新三项仍为 0。原事件数等于 6 的断言不动；新增三类的持久化、去重由新测试验证。没有把“等于”改成子集比较，也不降低安全断言、非空覆盖或回合数。

已发出此项 PM 决策请求，截至本次交接未收到批准。用户明确要求“如与新设计冲突，停下并在交接中说明，交由 PM 决定”，因此暂停生产实现；不得以等待时间当作批准，或按测试/调用方输出不同 schema 来隐藏冲突。后续若发现其他断言冲突仍须逐项说明，本提案不是任意改测试的授权。

### 官方 OI 与目录字段核查

2026-11-12 仅读取官方文档，未执行示例或请求真实行情/鉴权接口。以下区分已核实和待补证，**不是已实现的适配器清单**。

| 所 | OI 字段与单位 | 目录分类与边界 |
|---|---|---|
| Binance | [Open Interest](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#open-interest)：`/fapi/v1/openInterest` 返回 `symbol/openInterest/time`；本次该页未取得币/张/USDT的明确单位说明，**单位待补证**。统计接口的 `sumOpenInterestValue` 也不能仅凭字段名就视为单位已核实。 | [Exchange Information](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#exchange-information) 示例有 `underlyingType=COIN/underlyingSubType`；完整枚举及 PREMARKET 定义待补证。不能用 COIN 单字段代替任务书要求的 RWA/黄金/股票/ETF/商品排除。 |
| Bybit | [Open Interest](https://bybit-exchange.github.io/docs/v5/market/open-interest)：linear 的 `openInterest` 单位为基础币，是双边合计；`singleOpenInterest` 为单边，`timestamp` 毫秒，最短周期 `5min`。需明确选择口径，不能静默混用。 | [Instruments Info](https://bybit-exchange.github.io/docs/v5/market/instrument)：`isPreListing/symbolType`；TradFi 另有 `fullName/marketRegion/underlyingTicker`，韩国代码为 `KR`，应用要求 `KRX`，需显式转换并由用户确认原始标的。合约上市时间不等于公司 IPO 事实。 |
| OKX | [Open Interest](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-open-interest)：`/api/v5/public/open-interest` 的 `oi` 为张、`oiCcy` 为币、`oiUsd` 为 USD，`ts` 毫秒。 | [Public Instruments](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)：`instCategory` 1=crypto、3=stocks、4=commodities、5=forex、6=bonds、空串=未知。`instType=SWAP` 不证明 crypto。 |
| Gate | [Futures statistics](https://www.gate.com/docs/developers/apiv4/en/futures/)：`/futures/{settle}/contract_stats` 的 `open_interest` 为张，`open_interest_usd` 为报价币名义额，`time` 为统计时间；USDT 结算需保留原报价币。 | 同一官方页：`quanto_multiplier` 是每张合约对应的标的数量；`contract_type` 例举 stocks/metals/indices/forex/commodities。当前英文页未找到 `is_pre_market`，不能把旧印象称为本次官方已核实。 |
| Bitget | [Classic Open Interest](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-open-interest)：`/api/v2/mix/market/open-interest` 的 `data.openInterestList[].size` 为基础币，`data.ts` 毫秒，须匹配返回 symbol。 | [Classic Market](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market)：合约目录 `isRwa=YES/NO`；`symbolType=perpetual`、`symbolStatus=normal` 均不能证明 crypto 或标的公司已上市。 |
| Aster | [官方 Futures V3](https://raw.githubusercontent.com/asterdex/api-docs/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md)：本次全文未找到 `openInterest`，**公开 OI 接口及单位未核实**。不能根据 Binance 兼容外观推定 `/fapi/v3/openInterest`，未采用第三方社区补写作为官方证据。 | 同一官方文档目录示例含 `underlyingType/underlyingSubType/baseAsset`，示例不证明完整分类枚举；未知仍需人工判断。 |

继续实现前需补齐未证实项；合法 OI 仍不可得时按任务书 `oi_missing` 停开，不能默认无限 OI、放宽五分钟时效或把张数直接当 USD。金额换算需保留原市场、价格、数量倍率和快照时间。本轮未改变既有交易范围。

### 完整回归与 INVARIANTS 原文

Windows / Python 3.12.10 / SQLite 3.49.1；最后一次测试文件修改后执行：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
----------------------------------------------------------------------
Ran 272 tests in 93.233s

FAILED (failures=2)

python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 158 tests in 29.458s

OK
```

仅失败新增 `Round8SummaryContractTests` 的两项，原 270 项全过。首次先红：`Ran 2 tests in 0.049s / FAILED (failures=2)`。33 个执行器 Python 文件通过 Python 3.10 AST 语法检查，这不是 Ubuntu/Python 3.10 实机验收。

下面三行来自本次完整日志，只去除可能同行的 unittest 测试名称前缀。它们是**未修改的第七轮模拟器**结果，没有第八轮运行中参数变更或映射开仓覆盖：

```text
INVARIANTS seed=20261111 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 35, '1b': 60, '1c': 25, '1d': 84, '2': 74, '3': 95, '4': 74, '5': 600, '6': 23, '7': 345} coverage={'account_failures': 151, 'baseline_admission': 74, 'cooldown_active': 167, 'db_failures': 22, 'full_fill': 122, 'halted': 344, 'halting_retry': 1, 'intent_write_abort': 21, 'open': 74, 'opened_pairs': 37, 'partial_fill': 27, 'position_failures': 137, 'position_read': 3257, 'query': 4217, 'query_failures': 1397, 'recovery_obligation': 23, 'reduce': 95, 'restarts': 142, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 458, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'skip_baseline_not_flat': 75, 'skip_book_skew': 28, 'skip_book_stale': 112, 'skip_funding_diff': 56, 'skip_not_single_venue_outlier': 109, 'skip_too_few_venues': 23, 'timeout_ack': 28, 'timeout_unknown': 21, 'zero_fill': 20}
INVARIANTS seed=20261112 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 27, '1b': 56, '1c': 13, '1d': 77, '2': 68, '3': 83, '4': 68, '5': 600, '6': 30, '7': 355} coverage={'account_failures': 116, 'baseline_admission': 68, 'cooldown_active': 24, 'db_failures': 16, 'full_fill': 112, 'halted': 354, 'halting_retry': 1, 'intent_write_abort': 15, 'open': 68, 'opened_pairs': 34, 'partial_fill': 24, 'position_failures': 139, 'position_read': 3275, 'query': 4946, 'query_failures': 630, 'recovery_obligation': 30, 'reduce': 83, 'restarts': 142, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 458, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'skip_baseline_not_flat': 65, 'skip_book_skew': 20, 'skip_book_stale': 86, 'skip_funding_diff': 91, 'skip_not_single_venue_outlier': 139, 'skip_too_few_venues': 42, 'timeout_ack': 22, 'timeout_unknown': 34, 'zero_fill': 15}
INVARIANTS seed=20261113 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 37, '1b': 78, '1c': 27, '1d': 106, '2': 94, '3': 115, '4': 94, '5': 600, '6': 31, '7': 385} coverage={'account_failures': 134, 'baseline_admission': 94, 'cooldown_active': 158, 'db_failures': 20, 'full_fill': 162, 'halted': 384, 'halting_retry': 1, 'intent_write_abort': 20, 'open': 94, 'opened_pairs': 47, 'partial_fill': 27, 'position_failures': 141, 'position_read': 3256, 'query': 5603, 'query_failures': 635, 'recovery_obligation': 31, 'reduce': 115, 'restarts': 114, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 458, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'skip_baseline_not_flat': 43, 'skip_book_skew': 28, 'skip_book_stale': 73, 'skip_funding_diff': 68, 'skip_not_single_venue_outlier': 120, 'skip_too_few_venues': 17, 'timeout_ack': 39, 'timeout_unknown': 37, 'zero_fill': 20}
```

日志在本机 `%TEMP%`，不入库，不覆盖旧轮次日志：

| 文件 | SHA-256 |
|---|---|
| `crossex-round8-contract-red.log` | `b341d3f729b486839dfb5dc9a432e9a5ff0319a5700aa90a87f1bc6d954ea522` |
| `crossex-round8-old-assertion-conflict.log` | `970997e916a1a3248f73b89a260b5e151dba004783f7cd434e0487eb6fdabb9b` |
| `crossex-round8-pending-unittest.log` | `85e5f60ac1e1cf37e7615532647390d3114c11d1843127bb03dbf1cf71ba2f3a` |
| `crossex-round8-fill-sim.log` | `8dbc09f2ed6a675eeb8e1e61fd8555cffa802548b38ac946e665a4850e6ec4ed` |

### 继续入口与线上状态

继续时先读本节和 PM 决定。获准后按任务书先红后绿实现；当前不是全部需求测试。参数表/审计/范围及版本复核、深度与 OI 定量、白名单候选确认、映射确认及倍数换算、股票/ETF 标的市场确认、管理令牌写接口、sizing API 字段、新模拟器覆盖全部未实现。

现有入口：`engine.py::ingest/opening_review/check_pair`、`policy.py::review/send_review`、`admission.py::OpeningLimits`、`limits.py::HardLimits`、`storage.py::Store`、`market.py::MarketData`、`api.py::APIService/_Handler.dispatch`、`api_data.py::JournalView`。映射必须贯穿开仓、成交记账、实仓恢复及减仓数量，不只改开仓公式，保持第一至七轮裸露、穿零和共享放行约束。

未变更生产代码；未 push、SSH、WSL、amend、调用真实鉴权接口、读取真实 env/凭据、启动服务、生产备份、部署或线上验证，线上版本未触碰。父仓库索引的首次普通写入被文件沙箱阻止，随后在用户已授权的本地提交范围内通过自动审批，没有审批拒绝；提交禁用 hooks/GPG。仅提交本任务文件，临时日志及被忽略的 Python 缓存保留，没有额外未跟踪仓库文件需要移交。仍停在 crossex-exec 第八轮，不进入前端模块。

## 第八轮交付（2026-11-12 续跑，定量行为冲突待 PM，未完成）

**本次未完成第八轮。** 已读取 `ab33e05` 修订任务书、本交接上一节、当前实现和旧测试。用户本轮允许扩展新增键引起的精确字典期望，但明确要求遇到风控行为、阈值、上限、方向或恢复语义断言冲突时停下交给 PM。本次发现并复现了最小下单量的取整行为冲突，未修改旧断言或生产实现，已发出裁决问题，尚未收到答复。

### 范围、分支与提交

- 模块仍为 crossex-exec 第八轮；taoli1 前端由 `live-exec-page` 任务负责。
- 指定 worktree 和分支未变；续跑基线为 `ab33e05`，起始工作区干净。`67897da` 的三项新增计数期望保留。
- test：`b98d4125171dac5569a341ed2fc036054d67cc16`，只新增 `tools/crossex_exec/tests/test_round8_sizing_contract.py`，两个先红测试。
- docs：本节和 `docs/live-exec-contract-2026-11-11.md` 的修订状态说明独立提交；用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md` 获取 hash。没有 feat 提交。
- 本次未扩展任何原测试期望；允许的新增字段适配清单为空。未改动 `execution.py`、`storage.py` 或 service 文件，便于后续合并主分支终审修复。

### PM 待决：向上补最小量与向下取整后放弃

`policy.py::review` 先经 `fill_sim.core.opening(..., amount=100)` 得到公倍数步长向下取整的数量，再在交易所最小下单要求不足时补一个步长。第三轮明确锁定这个行为：

| 原测试（`tools/crossex_exec/tests/test_round3.py`） | 旧行为与断言 | 第八轮要求 |
|---|---|---|
| `Round3PolicyTests.test_r6_final_sell_limit_minimum_bumps_one_common_step`，487–497 行 | 卖腿最小名义额 `99.94`；数量 `0.980` 在最终卖限价 `101.96` 下只有 `99.92080`，补到 `0.981`，断言数量恰好 `0.981`；随后 `max_qty=0.980` 必须拒绝 | 向下取整后低于任一腿最小名义额则 `size_too_small`，不可补一步长 |
| `Round3PolicyTests.test_r6_min_quantity_bumps_once_and_still_invalid_minimum_rejects`，499–507 行 | 最小数量 `0.981` 时由 `0.980` 补到 `0.981` 并成功；最小数量 `0.982` 时拒绝 | 向下取整后不足最小量即放弃 |

新红测复用这两项的原始盘口、费率、资金费率、成交额和规格输入，要求 `ValueError('size_too_small')`。它们只隔离最终定量行为，不假装覆盖 OI、版本化参数、默认 50 USD 或新的完整公式。当前代码仍返回成功，因此两项均为 `AssertionError: ValueError not raised`。

建议 PM 采用第八轮规则，授权替换上述两项“补一步长成功”的旧行为断言，同时保留交易所最大量拒绝及其余安全断言，并补充分别由深度/OI/单笔/余额约束主导的边界用例。采用建议之前不做该行为变更；没有通过调用方特判、保留只为旧测试服务的路径、mock 生产结果或弱化断言来伪造全绿。

### 本次联网核实的公开字段

以下为 2026-11-12 官方文档读取结果；没有执行文档示例，没有请求真实鉴权接口。字段说明不是已交付的适配器。USD、USDT、基础币和张数须分别保留原单位，不能直接互换；任何未经证实的 OI 单位仍须拒绝开仓并记 `oi_missing`。

| 所 | OI | 批量成交额与目录分类 |
|---|---|---|
| Binance | [USD-M Market Data / Open Interest](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#open-interest) 确认 `/fapi/v1/openInterest`、`openInterest/symbol/time`；该页仍未明确基础币/张单位，待补证 | 同页 [24hr ticker](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#24hr-ticker-price-change-statistics) 的 `quoteVolume` 为报价币量；[exchange information](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#exchange-information) 有 `underlyingType=COIN` 示例、`underlyingSubType` 数组，完整 RWA/PREMARKET 枚举未证实 |
| Bybit | [Open Interest](https://bybit-exchange.github.io/docs/v5/market/open-interest) 明确 linear 的 `openInterest` 是基础币且为双边合计，`singleOpenInterest` 是单边，`timestamp` 毫秒 | [tickers](https://bybit-exchange.github.io/docs/v5/market/tickers) 有 `turnover24h`、`volume24h`；[目录](https://bybit-exchange.github.io/docs/v5/market/instrument) 有 `isPreListing`；[symbolType 枚举](https://bybit-exchange.github.io/docs/v5/enum#symboltype) 有 `commodity/stock/ETF/forex`。未知类别不可仅凭 linear 自动当作 crypto，股票仍须用户确认原始标的市场 |
| OKX | [Open Interest](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-open-interest) 的 `oi/oiCcy/oiUsd` 分别为张/币/USD，`ts` 毫秒 | [tickers](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers) 的衍生品 `volCcy24h` 是基础币量，`vol24h` 是张数；当前旧代码乘 `low24h` 得到的是保守金额下界，并非精确 24h 报价成交额。[目录](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments) 的 `instCategory`：1 crypto、3 stocks、4 commodities、5 forex、6 bonds、空值未知 |
| Gate | [Futures statistics](https://www.gate.com/docs/developers/apiv4/en/futures/) 的 `open_interest` 为张数，`open_interest_usd` 为报价币金额，`time` 为统计时间 | 同页 `/futures/{settle}/tickers` 的 `volume_24h_quote` 为报价币量；旧 `volume_24h_usd` 已标记弃用。目录 `contract_type` 例举 stocks/metals/indices/forex/commodities；未取得完整 crypto/pre-market 枚举，未知分类须人工判断 |
| Bitget | [Classic Contract Market / Open Interest](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-open-interest) 的 `/api/v2/mix/market/open-interest` 返回 `data.openInterestList[].size`，单位为基础币，`data.ts` 毫秒；须校验返回 symbol | 同页批量 tickers 的 `usdtVolume` 明确为 USDT 量、`quoteVolume` 为报价币量、`holdingAmount` 为基础币持仓量；目录 `isRwa=YES/NO`。另查 [UTA Market Data](https://www.bitget.com/docs/catalog/market/market-data) 确认 V3 `turnover24h/openInterest`，但本次未取得 V3 OI 更明确的单位说明，不能把 V2 与 V3 字段静默混用 |
| Aster | [官方 V3 文档](https://raw.githubusercontent.com/asterdex/api-docs/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md) 全文未找到 `openInterest`，公开 OI 接口及单位仍未证实 | 同文档 `/fapi/v3/ticker/24hr` 支持省略 symbol 返回全市场数组，示例含 `quoteVolume`；目录示例含 `underlyingType/underlyingSubType`。不可仅因 API 外观兼容 Binance 就推定 OI 路由或分类枚举 |

新增预筛应使用全市场 24h ticker 数据。当前 Binance/Aster 的 `baseline.BATCH_URLS` 仍为 `bookTicker`，不含 24h 成交额；这部分尚未实现。后续应保留盘口批次时效与精确 raw symbol 身份，不能用旧 bookTicker 当作成交额证据。

### 本地验证与验收缺口

在新增测试后执行用户要求的两条命令：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
Ran 274 tests in 90.948s
FAILED (failures=5)

python -m unittest discover -s tools/fill_sim/tests -v
Ran 158 tests in 29.884s
OK
```

执行器 269 项通过、5 项失败，失败清单：

1. 第七轮 `test_all_six_counters_are_zero_then_count_once_per_trigger_across_restart`：`67897da` 已扩展三键期望，但生产实现尚无新键。
2. `test_round8_contract.py` 两项 summary 新键红测：同样尚无生产实现。
3. `test_round8_sizing_contract.py` 两项本次新增的定量行为红测。

没有其他失败。单独执行 `python -m unittest discover -s tools/crossex_exec/tests -p test_round3.py -k Round3PolicyTests -v`，原五项全部通过；新定量文件单独执行，两项均先红。已有第一至七轮测试文件及夹具均未修改。

完整回归包含原第七轮不变量模拟器：种子 `20261111/20261112/20261113` 各 `2448` 回合（随机 `2400`），全部通过，各原不变量 exercised 非零；本次开仓数分别为 `37/34/47`。**没有新增“运行中改参数、映射币种开仓、成交额不足放弃”覆盖，不能作为第八轮验收证据。** 约 500 合约的新版采样尚未实现，也未做新版内存实测，不给未经实测的 MemoryMax 结论。

日志保留在 `%TEMP%`，未覆盖上一轮日志：

| 文件 | SHA-256 |
|---|---|
| `crossex-round8-resume-sizing-unittest-20261112.log` | `1f6ecda37d7994926de72a0c696932d6e99adb79e195844eed0f753740f69cb6` |
| `crossex-round8-resume-fill-sim-20261112.log` | `0712d3df2b084cccaf0c1981c51560f3c4b116ef973c9fbc347b5d36748774aa` |

### 继续入口、线上状态与未提交产物

先取得 PM 对上述两项行为断言的裁决，再在本 worktree 按修订后的第八轮任务书继续 TDD。现有入口仍是 `engine.py::ingest/opening_review/check_pair`、`policy.py::review/send_review`、`market.py::MarketData`、`baseline.py::Baseline`、`admission.py::OpeningLimits`、`limits.py::HardLimits`、`api.py` 和 `api_data.py`。参数与审计、动态 universe、成交额和 OI、按预筛的 SQLite 采样优化、映射数量贯穿恢复/减仓、管理 API、字段投影以及新模拟器覆盖均未交付。

`codex/crossex-exec` 的终审修复由另一任务进行，本次未合并或改动其工作区。本轮遵守不 push、不 SSH、不 WSL、不 amend、不调真实鉴权接口；未备份、部署或验证线上，线上版本未触碰。Git 索引位于父仓库，普通暂存受沙箱阻止后按用户既有本地提交授权经自动审批执行，没有审批拒绝。仅暂存本任务文件，提交禁用 hooks/GPG；未读取真实凭据。没有额外未跟踪仓库文件，忽略的 Python 缓存和上述临时日志保留。

## 第八轮交付（2026-11-12 再续跑，独立修复已提交，成交额断言待 PM）

**第八轮仍未完成。** 本次从 `5bb0e65` 接续，按 PM 授权替换第三轮两个补步长成功断言，保留原最大量等拒绝断言；`c1ccc0f` 已提交不足量直接 `size_too_small` 和 Python 3.10 空查询串修复。Binance 三个原生合约的公开 OI/标记价比值已实测，原始响应与倍率解释已记录。

继续实现时发现旧 `test_engine.py::test_actual_fees_missing_or_low_volume_never_sends` 用 `4999999/9000000` 成交额要求零发单，与任务书默认 100 万冲突。已提交具体 PM 方案：该旧测试显式配置 500 万以保留原输入与安全断言，另测默认 100 万；尚未收到批准，旧断言未改。按用户要求暂停依赖该裁决的验收。

本次**有必须保留的未提交实现及六个新增未跟踪模块**，与上一个续跑不同。详细范围、test/feat 提交、两项断言替换前后、OI 证据、Python 3.10 实测、WIP 备份、回归结果和未完成事项全部转入 [2026-11-12 第八轮专用交接](task-handoff-2026-11-12-crossex-exec-r8.md)。下一任务先读新交接，不要只根据 HEAD 还原工作；不 push/SSH/WSL/amend/鉴权、不触碰线上和前端的边界继续有效。

## 终审修复（2026-11-12）

**终审报告列出的 3 项 P1、2 项 P2 已完成本地修复与验收。** 新增 19 项专项；执行器完整 **289 项**、fill-sim **158 项**通过，三个原种子各 **2448 回合（2400 随机 + 48 故障覆盖）**全部严格不变量及非空覆盖通过。第七轮已通过的 5bp 适配继续有效；上文历史失败和待决提案不代表当前状态。

### 范围、基线与本地提交

- worktree：`C:\Users\wubin\Desktop\code\codex\taoli1\.worktrees\crossex-exec`；分支 `codex/crossex-exec`；用户指定基线 `cde65ed`。
- 开始时仅有未跟踪的 `docs/crossex-exec-final-review-2026-11-12.md`，首先将其原文单独提交，没有改写原只读报告。原报告针对 `cde65ed` 的否决结论属于修复前证据。
- 本轮修改仅限 `tools/crossex_exec` 和该模块交接文档；`tools/fill_sim` 源码和测试未修改。没有新增实际联网、鉴权、交易或部署授权。

| 类型 | 提交 | 内容 |
|---|---|---|
| docs | `f36d3df` | 原始终审报告 |
| test | `18e475c` | 首批失败回归与离线内存探针 |
| test | `f580331` | 最终 19 项专项、模拟存储原子/筛选接口适配及内存测量 |
| feat | `dab6d98116df7f9868a7ef21cfda1784a14a34fa` | 执行协调、恢复、SQL/流式读取及 service 布局/内存上限 |
| docs | 随本节独立提交 | README 当前状态、部署步骤、内存建议和原始验收汇总 |

文档提交用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-crossex-exec.md` 查询；不硬编码本次文档自身提交号。所有提交均只在本地，无 push、amend、reset 或 force push。

### 修复入口与保持的业务规则

1. **P1-1：`Executor._execute_pair` → `Store.save_orders(SENT)` → `check_order` → `Engine.check_release` → barrier 放行。** 两个线程先完成限频/建连，共享准入仍保留原有一次整对重拉。两条 SENT 及对应事件原子提交完成后再次检查硬上限、账户缓存、权益、停开条件，最后检查 1 秒盘口年龄及时间差；最终检查后没有写库、刷新或单腿独立否决。`can_open(ignore_cids=...)` 只排除本批次已预写 SENT 对自身的非终态阻挡/拒绝率样本，**硬敞口计算仍包含这两条订单**，其他 UNKNOWN、待写集合和停开锁不被忽略。1.01s 慢提交整对零发送，1.00s 等号放行；另一项测试直接在 SQLite `COMMIT` 回调注入 1.01s，仍整对拒发。
2. **P1-2：两腿 INTENT 和事件同事务准备，任何验证/第二腿写入失败全部回滚。** 所有执行中的 client ID 有进程内 ownership 计数，退出执行路径自动释放；运行期恢复只清理没有执行线程持有且仍为 INTENT 的孤立意图。已有 SENT/ACK/UNKNOWN 即使远端暂时查不到，仍按原编号确认、撤单、再确认，不自动重发或按未发送清理。当前 barrier 从未释放时有明确零发送证据，才允许本批次失败处理写 UNSENT；崩溃重启不保留该证据，已落盘 SENT 始终保守恢复。第二腿 SQLite trigger 失败测试证明订单和 INTENT 事件全部回滚，同一 Engine 无需重启即可 tick 到 HALTED、零活动仓位和零远端数量；另验证运行期孤立 INTENT 及正在等待关口的合法 INTENT。
3. **P1-3：`deploy/crossex-exec.service` 的 WorkingDirectory 改为 `/home/ubuntu/wubin/crossex-exec`。** `ExecStart` 模块入口、env、SQLite/STOP 默认位置和 `ReadWritePaths` 已逐项核对，均落在独立目录布局。README 增加固定 SHA 的 `git archive`、保留 `tools/` 层级、同时打包 `tools/crossex_exec` 与 `tools/fill_sim`、var/配置权限和以后安装的步骤。不要求启动 fill-sim 服务。
4. **P2-4：`storage.py` 在 SQL 过滤，历史按 rowid 分页；风险和恢复路径不再调用全量 `all_orders()` 或 `pairs()`。** 仓位页 64 条、订单页 128 条，页内仅逐条解码；固定读取上界且不使用 OFFSET，更新已读订单的状态不会跳过后续记录。支持按 pair ID、状态、最近发送时点和待记账标志过滤，并添加相应索引。空/未知状态不会因 SQL NULL 的 NOT IN 语义被藏掉。`Executor.iter_orders` 先合并未落盘观察再应用等价筛选；历史损益用有界 entry 缓存和 Decimal 累计，超过第一页的亏损/未知敞口有专项覆盖。API 保留只读 authorizer、2 秒/8MiB 预算和原响应字段，逐仓读取相关订单并释放历史对象；summary 不解码 SHADOW/SKIPPED，已关闭记录只保留必要汇总投影。超过预算仍返回不可用，绝不截断后声称统计完整。
5. **P2-5：README 与本交接顶部统一为第七轮通过。** 旧 40bp/8 项失败、0bp 未采用提案及第五轮待决记录明确标为历史；当前采用已批准的非零 5bp。本轮未改变任何风控阈值或既有断言。

### TDD、原断言与归档检查

先运行新回归再修实现，日志均保留在本机 `%TEMP%`：

```text
首批终审回归：Ran 13 tests in 0.463s / FAILED (failures=8, errors=4)
运行路径全量历史读取：Ran 17 tests in 1.666s / FAILED (failures=2)
API 历史订单同时驻留：Ran 18 tests in 1.761s / FAILED (failures=1)
SQL NULL 状态遗漏：Ran 19 tests in 1.839s / FAILED (failures=1)
最终定向回归：Ran 19 tests in 1.915s / OK
```

首批错误包含尚不存在的分页接口；行为失败分别覆盖慢 SENT 仍发送、第二腿 SENT 失败仍单腿发送、第二腿 INTENT 遗留、提交后风控未复查、运行期孤立意图和活动查询全量解码。API 增补红测观察到 129 个历史订单同时驻留，修复后同一场景最多 4 个；不以降低输入量消除失败。

既有 13 个测试文件的 **866 个** `assert* / fail / skipTest` 调用按 AST 计数与 `cde65ed` 比较，无缺失或改写；没有使用允许的“新增字段精确相等冲突”例外，没有修改旧断言。旧测试改动只有测试执行器补接新的最终关口、内存存储模型增加真实接口所需的原子/筛选行为；原随机序列、种子、回合数、N=1、FAULTS、INVARIANTS 和六类开仓拒绝均不变。

```text
Existing assertion AST audit: files=13 assertions=866 missing_or_changed=[]
Invariant seeds, rounds, bounds and definitions: unchanged
Python 3.10 AST compatibility: 34 files PASS
```

从 feat 完整 SHA 做真实 `git archive`，在新的临时目录解包，移除 `PYTHONPATH` 后执行独立包的 CLI `--help` 成功；没有运行 `run`。两个模块和全部 service/env 默认路径静态核验通过。`git diff --check` 通过。

```text
Archive SHA=dab6d98116df7f9868a7ef21cfda1784a14a34fa
Archive path=C:\Users\wubin\AppData\Local\Temp\crossex-final-archive-ftyzs0gh\crossex-exec.tar
Archive SHA256=86e570622bda294ca486559218c9592ed14b214a57041bf92b44c988344cd8c9
Archive modules: tools/crossex_exec + tools/fill_sim PASS
Isolated archive CLI --help: PASS
Service/env layout static check: PASS
Linux/systemd runtime: NOT RUN
```

### 两条 unittest 与不变量原始汇总

最后一次生产实现修改后实际执行，Windows 11 / Python 3.12.10 / SQLite 3.49.1。三条 INVARIANTS 仅去掉 unittest 同行测试名称前缀，其他字段保持原文：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
Ran 289 tests in 109.533s
OK

python -m unittest discover -s tools/fill_sim/tests -v
Ran 158 tests in 29.534s
OK

INVARIANTS seed=20261111 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 35, '1b': 60, '1c': 25, '1d': 84, '2': 74, '3': 95, '4': 74, '5': 600, '6': 23, '7': 345} coverage={'account_failures': 151, 'baseline_admission': 74, 'cooldown_active': 167, 'db_failures': 22, 'full_fill': 122, 'halted': 344, 'halting_retry': 1, 'intent_write_abort': 21, 'open': 74, 'opened_pairs': 37, 'partial_fill': 27, 'position_failures': 137, 'position_read': 3257, 'query': 4137, 'query_failures': 1399, 'recovery_obligation': 23, 'reduce': 95, 'restarts': 142, 'scheduled_account': 149, 'scheduled_adverse': 133, 'scheduled_database': 140, 'scheduled_detail': 148, 'scheduled_equity': 142, 'scheduled_midnight': 121, 'scheduled_none': 458, 'scheduled_partial': 164, 'scheduled_positions': 136, 'scheduled_restart': 142, 'scheduled_spread': 129, 'scheduled_stale': 150, 'scheduled_timeout_ack': 146, 'scheduled_timeout_unknown': 146, 'scheduled_zero': 144, 'skip_baseline_not_flat': 75, 'skip_book_skew': 28, 'skip_book_stale': 112, 'skip_funding_diff': 56, 'skip_not_single_venue_outlier': 109, 'skip_too_few_venues': 23, 'timeout_ack': 28, 'timeout_unknown': 21, 'zero_fill': 20}
INVARIANTS seed=20261112 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 27, '1b': 56, '1c': 13, '1d': 77, '2': 68, '3': 83, '4': 68, '5': 600, '6': 30, '7': 355} coverage={'account_failures': 116, 'baseline_admission': 68, 'cooldown_active': 24, 'db_failures': 16, 'full_fill': 112, 'halted': 354, 'halting_retry': 1, 'intent_write_abort': 15, 'open': 68, 'opened_pairs': 34, 'partial_fill': 24, 'position_failures': 139, 'position_read': 3275, 'query': 4866, 'query_failures': 625, 'recovery_obligation': 30, 'reduce': 83, 'restarts': 142, 'scheduled_account': 116, 'scheduled_adverse': 135, 'scheduled_database': 149, 'scheduled_detail': 128, 'scheduled_equity': 138, 'scheduled_midnight': 134, 'scheduled_none': 458, 'scheduled_partial': 159, 'scheduled_positions': 138, 'scheduled_restart': 142, 'scheduled_spread': 184, 'scheduled_stale': 145, 'scheduled_timeout_ack': 139, 'scheduled_timeout_unknown': 149, 'scheduled_zero': 134, 'skip_baseline_not_flat': 65, 'skip_book_skew': 20, 'skip_book_stale': 86, 'skip_funding_diff': 91, 'skip_not_single_venue_outlier': 139, 'skip_too_few_venues': 42, 'timeout_ack': 22, 'timeout_unknown': 34, 'zero_fill': 15}
INVARIANTS seed=20261113 rounds=2448 random_rounds=2400 N=1 checks={'1a': 2448, '1b': 2448, '1c': 2448, '1d': 2448, '2': 2448, '3': 2448, '4': 2448, '5': 2448, '6': 2448, '7': 2448} exercised={'1a': 37, '1b': 78, '1c': 27, '1d': 106, '2': 94, '3': 115, '4': 94, '5': 600, '6': 31, '7': 385} coverage={'account_failures': 134, 'baseline_admission': 94, 'cooldown_active': 158, 'db_failures': 20, 'full_fill': 162, 'halted': 384, 'halting_retry': 1, 'intent_write_abort': 20, 'open': 94, 'opened_pairs': 47, 'partial_fill': 27, 'position_failures': 141, 'position_read': 3256, 'query': 5479, 'query_failures': 642, 'recovery_obligation': 31, 'reduce': 115, 'restarts': 114, 'scheduled_account': 134, 'scheduled_adverse': 151, 'scheduled_database': 156, 'scheduled_detail': 134, 'scheduled_equity': 144, 'scheduled_midnight': 142, 'scheduled_none': 458, 'scheduled_partial': 152, 'scheduled_positions': 141, 'scheduled_restart': 114, 'scheduled_spread': 153, 'scheduled_stale': 145, 'scheduled_timeout_ack': 157, 'scheduled_timeout_unknown': 134, 'scheduled_zero': 133, 'skip_baseline_not_flat': 43, 'skip_book_skew': 28, 'skip_book_stale': 73, 'skip_funding_diff': 68, 'skip_not_single_venue_outlier': 120, 'skip_too_few_venues': 17, 'timeout_ack': 39, 'timeout_unknown': 37, 'zero_fill': 20}
```

### 2000 条历史内存实测与 service 建议

命令：`python -m tools.crossex_exec.tests.memory_probe`。固定 2000 条 SHADOW 仓位、4000 订单、每腿每侧 50 档；再添加三条活动仓位测查询/观测接口。下表为同一探针修复前后 **tracemalloc 临时 Python 分配峰值**，不包含全部 native/线程/cgroup 开销。原终审报告的 76.05MiB 使用不同合成字段；本轮 fixture 包含真实 ingest 生成的业务字段，92.0559MiB 是本轮实际修复前结果，不替换或改写旧报告。

| 路径 | 修复前 MiB | 修复后 MiB |
|---|---:|---:|
| `pairs(active=True)`，零活动 | 92.0559 | 0.0022 |
| `can_open` | 105.4587 | 0.0045 |
| 执行器恢复 | 13.5529 | 0.0062 |
| 硬敞口读取 | 13.4070 | 0.0042 |
| `Engine.totals` | 13.5530 | 0.0049 |
| `Engine.tick` | 92.1064 | 0.0064 |
| API summary | 74.0691，最终预算拒绝 | 0.0182，正常返回 |
| 2000 历史 + 3 活动的活动查询 | 92.1939 | 0.1566 |
| 完整遍历 2000 仓位 / 4000 订单 | 原接口物化全部 | 0.7174 / 0.2015 |

最终探针原文：

```text
{"platform": "Windows-11-10.0.26200-SP0", "python": "3.12.10", "sqlite": "3.49.1", "pairs": 2000, "orders": 4000, "levels_per_side_per_leg": 50, "metric": "tracemalloc Python allocations, not Linux RSS"}
{"peak_bytes": 2280, "peak_mib": 0.0022, "probe": "active_pairs_0", "result": 0, "retained_bytes": 1274}
{"peak_bytes": 6463, "peak_mib": 0.0062, "probe": "executor_recover", "result": null, "retained_bytes": 4381}
{"peak_bytes": 4677, "peak_mib": 0.0045, "probe": "can_open", "result": true, "retained_bytes": 3049}
{"peak_bytes": 4403, "peak_mib": 0.0042, "probe": "hard_exposure", "result": "98.02940", "retained_bytes": 2353}
{"peak_bytes": 5101, "peak_mib": 0.0049, "probe": "engine_totals", "result": ["0", "0"], "retained_bytes": 2530}
{"peak_bytes": 6716, "peak_mib": 0.0064, "probe": "engine_tick", "result": null, "retained_bytes": 4712}
{"peak_bytes": 752239, "peak_mib": 0.7174, "probe": "paged_pair_history", "result": 2000, "retained_bytes": 21198}
{"peak_bytes": 211251, "peak_mib": 0.2015, "probe": "paged_order_history", "result": 4000, "retained_bytes": 17628}
{"peak_bytes": 19085, "peak_mib": 0.0182, "probe": "api_summary", "result": 1155, "retained_bytes": 6803}
{"peak_bytes": 5991, "peak_mib": 0.0057, "probe": "api_positions", "result": 2, "retained_bytes": 1952}
{"peak_bytes": 6163, "peak_mib": 0.0059, "probe": "api_trades", "result": 2, "retained_bytes": 2072}
{"peak_bytes": 8088, "peak_mib": 0.0077, "probe": "api_stats", "result": 837, "retained_bytes": 5412}
{"peak_bytes": 164258, "peak_mib": 0.1566, "probe": "active_pairs_3", "result": 3, "retained_bytes": 11864}
{"peak_bytes": 112898, "peak_mib": 0.1077, "probe": "api_positions_3", "result": 3, "retained_bytes": 11562}
{"probe": "process_peak_working_set", "peak_bytes": 42696704, "peak_mib": 40.7188, "metric": "Windows process working set including fixture and tracemalloc, not Linux RSS"}
```

探针进程峰值 **Windows working set 为 40.7188MiB**，包含 fixture 构造和 tracemalloc；这不是 Linux RSS 或服务 24h 峰值。结合用户提供的宿主机 1.9G、约 500MB 可用余量，service 暂建议 **MemoryMax=192M**：相对本地约 41MiB 工作集保留约 4.7 倍余量，同时留下约 300MB 给宿主机与其他服务。该值仅是后续影子测试起点，原 80M 缺乏完整负载依据。并未声称实际白名单、真实账户缓存、后台线程与 API 并发已完成实机资源验收。

### 原始日志校验、线上状态与交接

以下日志保存在本机 `%TEMP%`，未提交到仓库；原先各轮日志没有覆盖。第一批 PowerShell 重定向日志保留了其原始格式，后续通过 subprocess 直接合并原始 stdout/stderr 写入 UTF-8。

| 文件 | SHA-256 |
|---|---|
| `crossex-final-review-red.log` | `f0e3f0669f643245db2e2492a8507dc34a39f8ade6116e8b17c226ca5bbd2c4d` |
| `crossex-final-review-p2-red.log` | `5a37118c0ecf92671fc23e7907c1418de6f8eafce654a54aaa0ddd80f319cf1a` |
| `crossex-final-review-api-red.log` | `9f00fe07921d4c97136c174e40edd53f880efbea0d27cb1d7f2e043a652ea383` |
| `crossex-final-review-null-red.log` | `c02b54ac23e9f98de5c9b974ece922beb3077f9f25ce03acb5de9e5ce3a7bca2` |
| `crossex-final-review-targeted-final.log` | `79470f5843abe99837d8d57e84a6b3739a018f9a03c5de2f4414190b5c74a247` |
| `crossex-final-review-unittest-first.log` | `af63596189cb203d3823711a2b90f6d3ce81de85aeeaa18e707e58093be96a1b` |
| `crossex-final-review-unittest.log` | `64b028c4b807de2f4081ae3e795ba4b4db6b70e30c89d0546d39084a854d5e36` |
| `crossex-final-review-fill-sim-final.log` | `b590bc02269800549617862746aeb46230fae92a4a1f716eca807a6c71757831` |
| `crossex-final-review-memory-before.log` | `96526f390c859da9fa9da889336009fd18af5d1772f799570d1e16410eb731ae` |
| `crossex-final-review-memory-accepted.log` | `efc1eddaa7b2a4bb4fab9b53312c036c409d1f69a69ce4a3478570250ca9afc0` |
| `crossex-final-review-static.log` | `9f3611f3b96e358db37e8acf58fef4a8b437d2565fdc3d3ed7fb238434f379f1` |
| `crossex-final-review-archive.log` | `feecec50c24761fb671324c06b6076fc274cfd6a6107cbbba32c55a0c12a8c0a` |

所有业务测试为内存交易所/loopback 假服务，保留外网 socket/DNS 守护；内存探针只读写自己的临时 SQLite，API 测量直接调用本地 JournalView。没有读取真实 env/凭据、请求真实行情/鉴权接口、push、SSH、WSL、amend、生产备份、部署或线上验证。线上版本和配置未触碰，无法报告新的线上部署版本或线上验证成功。

交付时只提交上述模块文件和 docs，没有额外未跟踪仓库文件；临时日志、归档包和忽略的 Python 缓存保留。后续新任务从本节开始：使用本次 docs 完成后的完整 SHA 重新归档，在明确授权的 Linux 环境完成 Python 3.10/SQLite 3.37、systemd 权限、实际白名单与 API 查询并发、24h MemoryCurrent/MemoryPeak/OOM/跟踪延迟验收，重新核对主机可用内存；账户验证和启动 `run` 必须属于后续明确范围。自动重启不能替代仓位管理连续性验收，本地通过不构成实盘授权。
