# crossex-exec 小额实盘执行服务任务书 2026-11-11

先读设计稿（业务规则以它为准）：`docs/crossex-live-design-2026-11-11.md`。用户已拍板：D1–D5、D7 按建议，D6 Bitget 走原生 API，D8 两个账户都已开通。CrossEx 接口的背景资料见 `docs/crossex-research-2026-11-03.md`。

**这是真钱交易程序，安全优先于功能。**

## 1. 位置与依赖
- 新包 `tools/crossex_exec/`，只用 Python 3.10 标准库，目标环境是 Ubuntu 22.04、SQLite 3.37。
- 复用 `tools/fill_sim` 里的代码：盘口抓取与解析、规格、`walk`、`opening`、`closing`、资金费、限频调度、存储模式。能直接 import 就 import；要改 fill_sim 的话，只能做向后兼容的小改动，并且 fill_sim 的已有测试必须全部通过。
- 部署模板放 `tools/crossex_exec/deploy/`：
  - `crossex-exec.service`：MemoryMax=80M，`Restart=on-failure`；
  - `crossex-exec.env.example`：只写变量名和默认值，**不写任何密钥值**。

## 2. 接口（实现前必须先查官方文档核实，并在交接文档里列出处 URL）
允许联网，但**只能读官方文档和免鉴权的公开接口**。不得调用任何需要鉴权的接口，也没有 key 可用。

- **Gate CrossEx**（APIv4 签名）
  - 下单：`POST /crossex/orders`，用 IOC、`reduce_only`，`text` 防重；
  - 查单：`GET /crossex/orders/{order_id}`；
  - 撤单；
  - 成交：`GET /crossex/history_trades`；
  - 账户与持仓：`/crossex/accounts`、`/crossex/positions`；
  - 费率：`/crossex/fee`；
  - 规则：`/crossex/rule/symbols`。
- **Bitget**（v2 mix，USDT-FUTURES，签名带 passphrase）
  - 下单：`place-order`，限价单、`force=ioc`、`clientOid` 防重，按开平仓方向设 `tradeSide`/`reduceOnly`；
  - 查单 `detail`、撤单、成交 `fills`；
  - 持仓、账户（保证金率）、费率。
  - 持仓模式（单向/双向）启动时只读检测，和配置不符就拒绝启动。
- 签名、时间戳、错误码按文档实现，用文档里的示例向量写签名单测。文档没有示例向量时，在交接里写明。

## 3. 行为
- **触发**：与 fill-sim 相同，轮询 taoli1 `/api/shadow/trades`。
- **过滤**：
  - 只接受永续对永续，6 家：CrossEx 下的 gate/binance/bybit/okx/aster，加 bitget 原生；
  - 只接受精确映射的币，映射表来自 CrossEx `rule/symbols` 和各所原始代码，启动时生成并持久化；
  - 成交额 ≥500 万美元；
  - 资金费黑名单：开仓前 24 小时内资金费率绝对值 >0.5%。
- **复核**：并行抓两腿公开 50 档盘口（不用 CrossEx 行情），按 100 美元计算。扣掉真实费率（启动时读 `/crossex/fee` 和 Bitget 费率接口，读不到就拒绝开仓）和一个周期的资金费差之后，净价差 ≥15bp 才开；任一腿离资金费结算 <30 分钟不开。
- **下单**：
  - 两腿并发发送 IOC 限价单，限价 = 吃到目标数量的最差一档 ±3bp；
  - 数量取两腿步长的公倍数向下取整，不足最小名义额就不开；
  - 同一个币同时只允许一对仓位，最多 3 对；
  - **先过限频，再标记“已发送”**；
  - 订单号先落库再发送；结果不明（超时、5xx）时，先按订单号查单，确认没有这笔单才允许重发。
- **确认**：ACK 后保存 order_id，每 200ms 查单直到终态（最多 10 秒）。成交价和手续费从成交接口补拉。
- **单腿处理**：两腿成交量不一致时，对多出的部分用 `reduce_only` IOC 平掉，每次放宽 10bp，最多 3 次；仍失败就停止开新仓并告警。
- **平仓**（设计稿 §6）：
  - 价差收敛到开仓时的一半；
  - 价差反向扩大 ≥50bp，或浮亏 ≥1% 名义额；
  - 持仓满 2 小时；
  - 任一腿离资金费结算 <5 分钟；
  - 盘口 60 秒拉不到。
- **熔断**（设计稿 §7）：
  - 当日亏损 ≥20 美元：当天停止开新仓；
  - 累计亏损 ≥100 美元：全部平仓并退出，状态写 `HALTED`，需要人工删除状态才能再启动；
  - CrossEx、Bitget 保证金率分别 <300% 停开、<200% 全平；
  - 连续 3 次单腿失败，或 1 小时内拒单率 >30%：停开；
  - 每 60 秒和交易所持仓对账，不一致就停开并告警。
- **开关**：
  - `CROSSEX_LIVE_ENABLED` 默认 0。
  - 为 0 时是**影子模式**：完整执行触发、复核、生成下单意图（含价格和数量）并落库，**绝不调用下单和撤单接口**。只读接口在有 key 时可以调用，用于对账和费率。
  - 文件开关 `var/STOP` 存在时立即停止开新仓。
  - 测试里必须有守护：影子模式下，下单、撤单函数被调用即判失败。
- **密钥**：只从 env 文件读取（文件权限必须是 600，否则拒绝启动）。日志、数据库、异常信息里都不得出现 key、secret、passphrase 和签名。写测试保证这一点。
- **日志**：
  - 每笔记录：触发、复核、下单意图、ACK、终态、成交均价、手续费、两腿发送时间差和成交时间差（毫秒）、对应的 fill-sim 预估；
  - 每 300 秒一行状态 JSON；
  - `report` 命令输出实盘与模拟的对比：每笔滑点 bp、两腿时间差、单腿失败率、净收益。

## 4. 测试
- 用本地假 CrossEx 和假 Bitget HTTP 服务（127.0.0.1）覆盖：签名、ACK 后转终态、超时后查单再决定、重复订单号被拒、部分成交、单腿成交后补平、平仓各条件、各项熔断、对账不一致、`HALTED` 不可自启、影子模式绝不下单、密钥不进日志。
- 单元测试禁止访问外网。
- fill_sim 已有测试全部通过。
- 命令：
  - `python -m unittest discover -s tools/crossex_exec/tests -v`
  - `python -m unittest discover -s tools/fill_sim/tests -v`
  - 贴原始汇总行。

## 5. 约束
- 不改 backend、compose、部署脚本和 `.env`；不 push、不 SSH、不跑 WSL；不调用任何鉴权接口。
- 按 test/feat/docs 分开 commit，不 amend。

## 6. 交付
交接文档 `docs/task-handoff-2026-11-11-crossex-exec.md`，写清：
- 文件清单和提交 hash；
- 接口出处 URL；
- 文档未写清、需要开户后用只读接口确认的点；
- env 变量清单；
- 剩余风险。
