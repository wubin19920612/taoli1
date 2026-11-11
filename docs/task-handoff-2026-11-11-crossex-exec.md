# crossex-exec 本地实现交接 — 2026-11-11

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
