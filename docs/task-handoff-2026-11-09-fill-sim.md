# fill-sim 本地实现交接（2026-11-09）

## 目标、范围与交付状态

本模块为独立 `tools/fill_sim/` 服务：轮询 taoli1 新影子事件，读取六家公开规格与 50 档深度，模拟 100/300/1000/3000 USD、实测/+1s/腿间延迟三变体，持仓跟踪、SQLite 留存及日报/通过线报告。

完整阅读了任务书 `docs/fill-sim-spec-2026-11-09.md` 和业务设计稿 `D:/吴彬工作/claude/套利/research/small-lot-quant/paper-fill-design.md`。遵守任务书 §2 的 API 触发覆盖设计稿读 radar.db 的约定，以及 §6 的局部修改、不 SSH、不 WSL、不 push、不使用任何交易所密钥、不下单的边界。本次没有读取或使用真实 taoli1 API token；联网仅为公开文档与行情。

**工程验收：76 项离线测试通过；六家合约和额外六家现货的真实公开接口冒烟成功；本地分开提交。完整净收益观察仍受资金费数据源缺口限制（详见风险第 1 项），不能声称设计稿 §8 盈利通过或可直接开始完整生产验收。**

## 分支和提交

- Worktree：`C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/fill-sim`
- 分支：`codex/fill-sim`
- 开始基线：`c00d02fd072a1dcce860bf4ca014fbf05b6a14a0`
- 实现提交：`9030247c43218499fc1028d4574b262f0f1b64fd` — `feat(fill-sim): add public order book paper execution service`
- 测试/夹具/原始证据提交：`20ab46a6e387fe82c832d403536e80bbdbb6663d` — `test(fill-sim): cover execution lifecycle with offline public fixtures`
- 本文与包说明单独以 `docs(fill-sim)` 提交；其提交号可由 `git log -1 --format=%H -- docs/task-handoff-2026-11-09-fill-sim.md` 获取，避免文档自引用提交哈希。
- 未 push、未备份线上库、未部署、未改变生产版本；这些是本任务显式边界，不是部署成功声明。

## 文件清单和代码入口

全部变更为新增；未修改 backend、frontend、compose、既有部署脚本和 `.env`。

| 文件 | 职责 |
| --- | --- |
| `tools/fill_sim/__init__.py`、`__main__.py` | 独立包；run/report/smoke；env/CLI；守护；状态和日报 |
| `tools/fill_sim/core.py` | Market/Spec/Book、Decimal VWAP、共同数量、费用、平仓、资金费计算 |
| `tools/fill_sim/exchanges.py` | 六家 future/spot 公共 URL、规格与深度解析、面值转换、倍率交叉校验 |
| `tools/fill_sim/network.py` | 标准库 GET、3s socket timeout、API 退避、每所/全局令牌桶和滚动秒窗、暂停 |
| `tools/fill_sim/engine.py` | 过滤、ID/机会去重、10 机会组上限、冷规格等待、三变体、持仓优先和恢复 |
| `tools/fill_sim/storage.py` | WAL/事务、三张业务表及 seen/meta/runtime 辅助表、进程锁、日归档/30 天保留 |
| `tools/fill_sim/reporting.py` | 日指标、全部延迟变体、top3/其余、按天 bootstrap、逐条通过线 |
| `tools/fill_sim/smoke.py` | 显式联网录制、规格/深度输出、四档 BTC 计算示例 |
| `tools/fill_sim/deploy/fill-sim.service` | 未安装的 systemd 模板，资源限制和守护停止码 |
| `tools/fill_sim/deploy/fill-sim.env.example` | 无真实凭据的配置样例，合约费是假设，现货费为 null |
| `tools/fill_sim/README.md`、`.gitignore` | 运行说明、业务口径、已知缺口；忽略运行库/缓存/实际 env |
| `tools/fill_sim/tests/__init__.py`、`test_fill_sim.py` | 76 项纯 unittest 离线回归，socket connect 被测试基类禁止 |
| `tools/fill_sim/tests/fixtures/{gate,bitget,binance,bybit,okx,aster}_{future,spot}.json` | 12 份真实公开规格/深度，带 URL/接收时刻；exchangeInfo 只保留目标原始行 |
| `tools/fill_sim/tests/fixtures/README.md` | 录制与裁剪说明 |
| `tools/fill_sim/tests/test-output.txt`、`smoke-output.txt` | 最终测试和联网冒烟的完整原始输出（UTF-8） |
| 本交接文件 | 范围、结果、业务解释和后续事项 |

## 重要业务与工程口径

1. ID 持久去重，不按 closed_at 过滤，limit 永远 50；成功间隔至少 1s，API 失败 2/4/8/16/30s；超过 120s 事件记录 `skipped_stale_trigger`。并发上限/重复机会/限频等跳过事件均记录原因。
2. 10 笔按“去重后的机会组”计；每组最多 12 个独立研究子仓位，不能把三个变体和四档金额相加视为真实组合。每组跟踪盘口共享，四档计算不重复请求。
3. 两次两腿并行抓取，第二次在第一次较晚收到的盘口后至少等待 1s；腿间延迟混合第一 A、第二 B。保留 t0/t1/tA/tB 和交易所时间，真实延迟可能大于名义 1s。
4. 两腿共同数量采用 Decimal 步长最小公倍数向下取整，而非简单取 max(step)，避免 0.02/0.03 等非整倍步长错误。两侧先分别检验目标金额深度，再检查取整后的最小数量/名义额和市场最大数量。
5. Gate 与 OKX 合约盘口量是张，乘公开规格面值；price_multiplier 非 1 且无原生规格依据时拒绝，contract multiplier 与公开规格不一致记 `spec_mismatch` 和对应 buy/sell 腿。只接受 USDT 原生单位、线性永续或现货，不猜别名/倍数。
6. 平仓回补开仓的实际币数；若重新按目标美元额算币数会残留敞口，因此不这样做。净 bp 用实际买腿开仓金额为分母；四笔手续费按实际成交金额。conv-half 用开平 VWAP 毛价差对比，开仓门槛另外扣预估四笔费用。
7. 24h 且 half 收敛与即时收敛规则重叠：24h 后符合条件标 `timeout_24h_converged`；48h 无条件退出。连续失败 60s 使用最后盘口退出并排除主收益；无可完整平仓深度时退回上次完整退出盘口并标 stale，从未有完整退出盘口则 `unpriced_exit`，不虚构收益。重启断档 >60s 也 stale。
8. 启动加载并刷新已知市场，预热六家 BTC；其他 exact symbol 首次事件最多等待 6s 获取规格，等待占并发机会额度；每 6h 更新，过期规格不准新开仓；持仓跟踪优先。未知或冷缓存超时有独立跳过原因。
9. 单写进程锁，WAL/FULL；归档完整已结束事件族，活动族不移动。日归档迁移短暂切 DELETE journal 进行多库原子复制/删除，再恢复 WAL，保留 30 天。重启不重放未完成延迟阶段，记录 `interrupted_restart`。
10. 日报按 UTC；触发数/还在率按发现日，USD 收益按平仓日。单侧 90% 下界：按完整平仓日重抽样 2000 次，计算交易数加权平均的第 10 百分位，少于 2 天不给数值。观察时长及资源证据按所请求时间窗口查询，缺证据不能 PASS。

## 公开接口与限频依据

所有请求均为 GET、无交易所认证，规格和盘口共用预算。**本服务每所 ≤2/s、全局 ≤8/s 是设计稿 §4.2 的独立保守预算，不把它误写为交易所官方额度；它也不能证明共享出口的 taoli1 一定不会限频。** 429/418/限频码暂停至少 60s，连续触发指数退避至 3600s，遵守更长数字 Retry-After；Bybit 403 保守等待至少 600s。新开仓预算不足不排无限队列。

| 交易所 | 本次使用的深度 / 规格（域名见录制 URL） | 官方来源及校验情况 |
| --- | --- | --- |
| Gate | 合约 `/api/v4/futures/usdt/order_book?contract=…&limit=50` / `/api/v4/futures/usdt/contracts/{raw}`；现货 `/api/v4/spot/order_book?currency_pair=…&limit=50` / `/api/v4/spot/currency_pairs/{raw}` | [合约文档](https://www.gate.com/docs/developers/apiv4/en/futures/)、[总文档及 Frequency limit rule](https://www.gate.com/docs/developers/apiv4/en/)：公共端点按 IP、每端点 200 请求/10s。面值 `quanto_multiplier`、最小/最大张数来自规格。 |
| Bitget | 合约 `/api/v2/mix/market/merge-depth?productType=USDT-FUTURES&precision=scale0&symbol=…&limit=50` / `/api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=…`；现货 `/api/v2/spot/market/orderbook?type=step0&symbol=…&limit=50` / `/api/v2/spot/public/symbols?symbol=…` | [官方合约目录](https://www.bitget.com/docs/catalog/classic-contract-market)、[官方现货目录](https://www.bitget.com/docs/catalog/classic-spot-market)。旧文档已迁移，抓取目录未给出可核验的端点数字额度；接口本身实测成功。采用项目 2/s，而不凭旧资料声称已核实新的官方上限。 |
| Binance | 合约 `fapi.binance.com/fapi/v1/depth?symbol=…&limit=50` / `/fapi/v1/exchangeInfo`；现货 `data-api.binance.vision/api/v3/depth?symbol=…&limit=50` / `/api/v3/exchangeInfo?symbol=…` | [官方合约 Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)、[官方 Spot REST 文档](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)。官方按请求权重区分深度档位；本服务额外限制请求数，规格响应 `rateLimits` 保存在夹具。现货使用单 symbol 规格避免整表 >8MiB。 |
| Bybit | `/v5/market/orderbook?category=linear或spot&symbol=…&limit=50` / `/v5/market/instruments-info?category=linear或spot&symbol=…` | [盘口](https://bybit-exchange.github.io/docs/v5/market/orderbook)、[规格](https://bybit-exchange.github.io/docs/v5/market/instrument)、[限频](https://bybit-exchange.github.io/docs/v5/rate-limit)：默认 HTTP IP 600/5s；10006 为限频，403 过频要求至少等 10 分钟。记录 result.cts/ts。 |
| OKX | `/api/v5/market/books?instId=…&sz=50` / `/api/v5/public/instruments?instType=SWAP或SPOT&instId=…` | [官方 API 文档](https://www.okx.com/docs-v5/en/) 的 GET Order book：按 IP 40/2s，衍生品量为张。解析 ctVal × ctMult，确认 ctType=linear、ctValCcy 与原生 base 一致。 |
| Aster | 合约 `fapi.asterdex.com/fapi/v1/depth?symbol=…&limit=50` / `/fapi/v1/exchangeInfo`；现货 `sapi.asterdex.com/api/v1/depth?symbol=…&limit=50` / `/api/v1/exchangeInfo?symbol=…` | [官方文档仓库](https://github.com/asterdex/api-docs)、[推荐 V3 文档](https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md)。本次只读 V1 端点实测仍成功，返回 REQUEST_WEIGHT 2400/min；官方现在推荐 V3，需要留意 V1 公共接口退役公告。未触碰其认证接口。 |

上述数字仅用于记录本次文档/响应证据，不承诺将来额度不变。手续费未用这些接口的展示费率自动替代配置。

## §7 验收证据

环境：Windows 本机 Python **3.12.10**、SQLite **3.49.1**；没有安装 Python 3.10，未使用 WSL。所有 Python 文件用 `ast.parse(feature_version=(3,10))` 检查；生产代码导入均为标准库或本包，无 tomllib/ExceptionGroup/TaskGroup/datetime.UTC；SQL 使用 SQLite 3.37 已支持语法。Ubuntu 3.10.12/SQLite 3.37 实机运行未验证，不能把语法检查等同目标运行时测试。

执行命令：

```text
python -m unittest discover -s tools/fill_sim/tests -v
python -m compileall -q tools/fill_sim
git diff --cached --check
```

最终单元测试原始汇总行（完整输出在 `tools/fill_sim/tests/test-output.txt`）：

```text
----------------------------------------------------------------------
Ran 76 tests in 0.738s

OK
```

覆盖：六所 × future/spot 的真实夹具；VWAP 多档/最深价/滑点；共同数量及步长；below_min/too_thin/gone/thin；三延迟变体/不重复抓取；收敛/24h/48h/stale_exit；ID/反向所对去重/10 机会容量；持仓共享请求和优先预算；429/418/返回码/指数暂停；API 超时/503/固定 limit/退避；冷规格等待/TTL；进程锁；真正关闭并重开 SQLite；持仓/部分阶段恢复；长时间重启断流；WAL/事务回滚/归档/30 天清理；日报、按平仓日计收益、bootstrap 及通过线；资源未知/旧时间窗口不假通过；无守护时启动在网络请求之前停止；Python 3.10 AST 和纯标准库约束。

联网冒烟命令：

```text
python -m tools.fill_sim.smoke --include-spot --fixtures tools/fill_sim/tests/fixtures
```

最终录制开始于 **2026-11-09 06:50:44 UTC**，约 25 秒完成；每所每市场 1 次规格、1 次深度，共 24 次公开 GET。六家 BTC 合约全部成功，额外六家现货也成功，盘口均为 50 bids + 50 asks。原始摘要：

```text
SMOKE exchanges=6 markets=12 failures=0
```

| 合约 | 面值（币/张；币数量接口为 1） | 共同数量计算所用本腿步长 | 最小名义额（USDT） |
| --- | ---: | ---: | ---: |
| Gate BTC_USDT | 0.0001 | 0.0001 | 规格未单列，按最小张数 |
| Bitget BTCUSDT | 1 | 0.0001 | 5 |
| Binance BTCUSDT | 1 | 0.001 | 50 |
| Bybit BTCUSDT | 1 | 0.001 | 5 |
| OKX BTC-USDT-SWAP | 0.01 | 0.0001 | 规格未单列，按 minSz |
| Aster BTCUSDT | 1 | 0.001 | 5 |

假触发 Gate→Aster 的 100/300/1000/3000 档计算均为 `gone`，示例净价差 **-13.644937358689882 bp**。这是顺序录制的盘口和设计稿费率假设的工程演示，不是同时可成交报价或交易授权。单元测试用确定性盘口验证“应开仓”和独立平仓路径。

最初的额外现货试跑曾触发 Binance 整表规格超过 8MiB；已改为按 symbol 请求，并以上述最终全通过结果及夹具验收。没有因此放宽响应体上限或提高请求速率。

## 上线前待核对项与剩余风险

1. **资金费数据源缺口，影响完整盈利验收**：当前普通 shadow payload 不保证提供开仓两腿的 rate/next/interval，设计又限制只读深度/规格。代码支持明确的 `fill_sim_funding` 开仓快照和跨结算计算，但上游未提供此扩展字段；缺失时主收益排除该仓位，单独给 `provisional_price_only`，不会把未知当零。本期没有擅自增加资金费/ticker 请求或修改 backend。未来需确认允许的单品种公开资金费来源或扩展上游契约后补齐；在默认合约事件下，主收益可能全部因未知资金费而不能通过。
2. **费用仍为待核假设**：设计稿 future taker 比率分别为 Gate 0.0005、Bitget 0.0006、Binance 0.0005、Bybit 0.00055、OKX 0.0005、Aster 0.00035；逐所核对 VIP0、地区、活动/折扣、具体合约类型。spot 默认 null，需要核对并填写；未填时拒绝涉及该腿的开仓。不访问账户或 fee 认证接口。
3. **守护集成未部署**：需要现有生产监控定期原子写入 `guard.json`，含 timestamp、taoli_stalls_last_hour，以及基线比较布尔值 taoli_stalls_not_worse/no_429_increase。Linux MemAvailable <200MiB、RSS ≥120MiB、卡顿 >4/h 或守护数据缺失/过期都会停止。模板有 RestartPreventExitStatus=78；未修改或验证现有生产守护脚本。
4. **目标环境和负载未验收**：未在 Ubuntu Python 3.10.12/SQLite 3.37 跑测试、未安装 systemd、未做七天观察。生产与其他采集器共享 IP，2/s/8/s 仅限制本进程，不能保证整个 IP 总量。进程锁只保护同一 DB 路径，不应额外部署不同 DB 的第二个实例。
5. **模型局限**：仅 USDT 线性永续/现货原生单位；其他倍率/资产别名保守拒绝。盘口快照不能模拟队列、隐藏深度、网络拥塞下真实撮合、库存/可借币、保证金和账户限制。现货 sell 假设已有库存，不能证明可直接做空。主收益也没有借贷利息模型。
6. **覆盖率与延迟**：固定最近 50 行 API 无游标，突发 >50 个新事件或退避期间可能漏触发，遵守任务书不能自行扩大 limit。规格冷启动可能耗时/超时；+1s 是最短等待，记录实测时间。所有跳过原因进入覆盖率分母，不能只保留成功样本。
7. **接口演进和时钟**：Aster 官方推荐 V3，当前代码 V1 公共接口实测可用但需监控退役；Bitget 官网迁移后的数字额度待再次核实。录制盘口交易所时间与本机存在约数秒差，时间戳都有保留，生产须确认时钟同步。
8. **审查流程**：本轮完成实现、自查及测试，没有独立审查者的签字/结论；仍按设计流程交给 Claude 验收。不得把本地自动化测试结果当作独立审查或实盘批准。

## 工作区与下一步

开始时工作区干净，交付只含本模块新文件。没有需要保留的其他任务 WIP；运行产生的 `__pycache__` 已被包内 `.gitignore` 忽略，不纳入提交；测试临时库自动清理，没有实际 env 或密钥产物。

下一步在独立审查/验收任务中使用本交接，先审查资金费缺口及生产守护输入契约、核对费用，再在目标 Python/SQLite 上跑同一套离线测试。按用户授权另开部署/七天观察任务，不能在本次任务中推送、SSH 或启动生产服务。

---

## PM decisions 第一轮补充实现记录（2026-11-09）

前文保留为上一轮历史记录。**本节更新前文风险 1、2、3、5 中关于资金费来源、手续费、守护字段和现货卖腿的状态**；生产部署、目标运行时及七天观察仍未验收。

### 范围、分支与本地提交

- 同一 worktree、分支 `codex/fill-sim`；本轮基线 `448a2901c7eb8d7d1976b5be712a4901e7cb740a`，开始时工作区干净。
- 已将用户 F1–F4 原文追加在 `docs/fill-sim-spec-2026-11-09.md` 末尾“PM decisions 第一轮补充”，再实现。
- feat：`1f207b4d430992dc6c4933f2d73d4af6d9942a2b` — `feat(fill-sim): apply PM funding fees and spot short decisions`。
- test：`06e14b8b40814667491d7d259981a6e19b37837e` — `test(fill-sim): verify public funding cache and PM guard boundaries`。
- docs：本文、规格补充、README 与夹具说明单独提交；用 `git log -1 --format=%H -- docs/task-handoff-2026-11-09-fill-sim.md` 定位最终 docs 提交，避免自引用哈希。
- 遵守 §6：仅修改 fill-sim 包和本模块文档；未改 backend/frontend/compose/既有部署脚本/真实 .env。未读交易所密钥、未读真实 taoli1 token、未下单、未 push、未 SSH、未用 WSL。Git worktree 索引位于主仓库 .git，暂存/提交使用授权的本地写权限，未操作其他分支。
- 无生产备份、无部署、无线上版本变更；这些操作不在本轮授权范围。

### 实现与关键入口

| 文件 | 本轮变更 |
| --- | --- |
| `tools/fill_sim/funding.py`（新增） | 六家公开资金费 URL、rate/next/interval 单位解析、exact symbol 校验、时间有效性；Binance/Aster 另取 fundingInfo，缺周期不默认 8 小时 |
| `engine.py` | 移除对上游 fill_sim_funding 的依赖；资金费异步阶段、市场 300 秒缓存、失败降级、仓位快照；现货 sell 记 unsupported_spot_short |
| `network.py`、`exchanges.py` | 同一 PublicClient/令牌桶/暂停计数；允许 Binance/Aster 的公开配置数组响应 |
| `storage.py` | awaiting_funding 加入重启中断恢复与活动事件禁止归档条件；无数据库表结构迁移 |
| `reporting.py`、`__main__.py` | 状态/日报 funding_unknown 比例及分母、当前所有持仓未知比例、unconfigured_fees；默认配置加载；guard 字段严格类型检查 |
| `default-fees.json`（新增）、`deploy/fill-sim.env.example` | 六家现货默认费、复核合约费，Aster 改为 0.0004，Binance 合约未核实设 null；CLI/env 可整体覆盖 |
| `smoke.py` | 合约 smoke 同时获取并打印六家资金费，录制独立 funding 夹具；仍只用公开 GET |
| `tests/test_pm_round1.py`（新增）、`tests/test_fill_sim.py` | 增加 25 项离线回归，合计 101 项；原引擎测试改为种入明确的公共资金费缓存，避免继续依赖 payload |
| `tests/fixtures/funding/*.json`（新增） | 六家 BTC 资金费原始响应、URL、接收时间；配置列表仅裁剪精确 BTC 行 |
| `tests/fixtures/fees/okx-spot.json`（新增） | OKX 官方费用页 HTML 内嵌 Regular user 现货行的原样提取证据 |
| `tests/fixtures/*_{future,spot}.json` | 本轮联网重录 12 份规格/盘口；原始旧日志保留为历史记录 |
| `tests/test-output-pm-round1.txt`、`tests/smoke-output-pm-round1.txt`（新增） | 本轮最终完整原始输出 |
| `README.md`、夹具 README、规格和本文 | 更新运行口径、单独 guard.json 契约、来源及本轮交接 |

### F1 资金费行为与官方接口

所有资金费请求使用原 PublicClient；和规格/深度共享 **每所 ≤2/s、全局 ≤8/s** 令牌桶及滚动秒窗，沿用限频暂停/退避。这个预算仅约束本进程，不是共享出口的总预算。

| 交易所 | 公开请求与字段映射 | 官方文档（查询 2026-11-09） |
| --- | --- | --- |
| Gate | `/api/v4/futures/usdt/contracts/{raw}`；funding_rate、funding_next_apply（秒）、funding_interval（秒） | [Gate Futures API](https://www.gate.com/docs/developers/apiv4/en/futures/) |
| Bitget | `/api/v2/mix/market/current-fund-rate?symbol=…&productType=USDT-FUTURES`；fundingRate、nextUpdate（毫秒→秒）、fundingRateInterval（小时→秒） | [Classic Contract Market / Get Current Funding Rate](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market)：20 次/秒/IP；已找到迁移后的具体文档 |
| Binance | `/fapi/v1/premiumIndex?symbol=…`；lastFundingRate、nextFundingTime（毫秒→秒）；另 `/fapi/v1/fundingInfo` 取该 raw symbol 的 fundingIntervalHours（小时→秒） | [Binance Market Data / Mark Price / Get Funding Rate Info](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)：fundingInfo 与 fundingRate 共用 500/5min/IP；它是资金费配置列表，不是行情 ticker 列表 |
| Bybit | `/v5/market/tickers?category=linear&symbol=…`；fundingRate、nextFundingTime（毫秒→秒）、fundingIntervalHour（小时→秒） | [Get Tickers](https://bybit-exchange.github.io/docs/v5/market/tickers)，严格传单个 symbol，不拉 ticker 列表 |
| OKX | `/api/v5/public/funding-rate?instId=…`；fundingRate；**fundingTime 才是最近待结算时间**；间隔为 nextFundingTime − fundingTime，均毫秒→秒 | [OKX Get Funding Rate](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate)：nextFundingTime 是再下一期，不能直接当最近结算点 |
| Aster | 推荐 V3：`/fapi/v3/premiumIndex?symbol=…` 和 `/fapi/v3/fundingInfo?symbol=…`；lastFundingRate、nextFundingTime、fundingIntervalHours | [官方 V3 文档](https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md)，两个 public GET 各 weight 1；本轮未迁移已有 V1 规格/深度 |

每个新机会在首轮盘口之前获取两腿资金费，缓存键 `exchange|market_type|raw_symbol`，成功和失败结果缓存 300 秒（最多 2048 个市场）；已跨结算点的成功快照立即失效。Binance 配置接口官方说明只涵盖调整过的市场，若目标缺失则未知，不擅自补 8 小时。现货零资金费，无资金费 HTTP。

资金费阶段最多等 7 秒，盘口预算最多等到该阶段开始后第 8 秒；不会阻塞主循环，不会无限排队，持仓跟踪先调度。失败/预算不足则记录资金费未知，盘口可用时仍开 provisional 仓位；盘口也不能获取时按原逻辑跳过。首轮、+1s、腿间延迟及四金额档共用快照，不为每档多发请求；若延迟变体开仓已跨结算或快照超过 300 秒，则该变体资金费未知。完整快照随 positions 保存，重启不改写旧仓位费率，内存缓存重建。

`funding_known=false` 不计主收益。估算仍是“开仓费率 × 每次跨越的结算点”，多头支付、空头收取，保留 funding_estimated，不是最终资金费历史回放。状态与日报的 funding_unknown_ratio 按仓位（3 变体 × 4 档）计数，分子是任一腿未知的仓位数，分母 funding_observations；分母 0 时 null。日报按当日未平开仓及当日平仓窗口，status 同用今日窗口；open_funding_unknown_ratio 另覆盖所有当前持仓。

### F2 默认手续费复核

下表均为 taker，无代币折扣、返佣或活动；配置值为小数比率。普通加密资产/USDT 市场公开基准，不是特定账户费率证明。**六家现货均已填入；未确认项仅 `binance:future`，默认 null，状态/日报/report 的 unconfigured_fees 会列出。**

| 交易所 | 现货 taker（配置值） | USDT 合约 taker（配置值） | 官方出处与证据 | 查询日期 |
| --- | --- | --- | --- | --- |
| Gate | 0.100%（0.001） | 0.0500%（0.0005） | [2026-04-09 现货及合约费率调整公告](https://www.gate.com/announcements/article/50390)：VIP0 现货 0.100%，合约 A/B/C 组 VIP0 均 0.0500%。不采用 Learn 文章的旧 0.20% 或 contracts 响应中的未绑定 VIP 手续费字段 | 2026-11-09 |
| Bitget | 0.1%（0.001） | 0.06%（0.0006） | [Trading Fees FAQ](https://www.bitget.com/support/articles/12560603892734/) 明示基础 spot/futures taker，未计 BGB/VIP 优惠 | 2026-11-09 |
| Binance | 0.100%（0.001） | **未核实（null）** | [现货 Regular User 费率表](https://www.binance.com/en/fee/trading) 可读；[USDS-M 费率表](https://www.binance.com/en/fee/futureFee) 本次 No records found，本机公开抓取为空；[官方 FAQ](https://www.binance.com/en/support/faq/detail/360033544231) 的 0.05% 明示 hypothetical fee rates，故不当成已确认当前费率填入 | 2026-11-09 |
| Bybit | 0.1000%（0.001） | 0.0550%（0.00055） | [Trading Fee Structure](https://www.bybit.com/en/help-center/article/Trading-Fee-Structure)：VIP0 Crypto-Crypto spot 与普通 perpetual/futures；特殊板块、地区有不同表 | 2026-11-09 |
| OKX | 0.100%（0.001） | 0.05%（0.0005） | [公开费用页](https://www.okx.com/fees) HTML 的 tableData.normal 原始 Regular user 行：level=0、tradeType=1、takerFee=0.001（证据已存 fees/okx-spot.json）；[合约费率说明](https://www.okx.com/help/how-to-calculate-the-contract-transaction-fee) 明示普通 Lv1 taker 0.05%。不套用 UAE/EEA/US 地区另一套现货表 | 2026-11-09 |
| Aster | 0.04%（0.0004） | 0.04%（0.0004） | [Spot Fee Structure](https://docs.asterdex.com/trading/spot/spot-fee-structure)、[USDT Perpetual Fees](https://docs.asterdex.com/trading/perpetuals/fees-and-specs/fees)；合约已从旧设计 0.035% 更新。 [VIP Program](https://docs.asterdex.com/program-and-rewards/vip-program) 区分 General、Group B 与 RWA，不能视为所有市场统一费率 | 2026-11-09 |

默认 null 会使 Binance 合约相关事件 fee_unconfigured，覆盖率分母仍保留；六家资金费冒烟与解析测试独立于交易手续费配置，故 Binance 资金费解析通过不等于已允许其默认配置开仓。下轮可在获得真实当前官方费率表依据后补齐该项。显式 CLI/env JSON 为整体覆盖，不填的项继续拒绝；unconfigured_fees 对应最近一次 run 配置，并非逐笔历史配置快照。

### F3 / F4

- sell.market_type=spot：`unsupported_spot_short`，filter_passed 保持 true；不请求该事件的规格/资金费/盘口，不产生持仓，计入 considered_events 和还在率分母。buy spot + sell future 允许模拟，spot 资金费为零。
- PM 负责生产 guard.json。`tools/fill_sim/README.md` 新增独立“guard.json 字段契约（由 PM 生成）”：timestamp 必须有限数值 UTC 秒，最多旧 90 秒、最多超前 5 秒；卡顿次数必须非负整数，>4 停止；两个基线比较字段为布尔，缺失允许运行但资源证据 UNKNOWN，false 不单独立即停服。缺必填项/坏格式/类型错误停服 78；验证了 90 秒、5 秒边界和 NaN/Infinity/bool/string 等异常。未写生产监控。

### 本轮验收原始结果

环境仍为 Windows Python **3.12.10**、SQLite **3.49.1**。实际执行全部 unittest；全部 13 个 Python 文件通过 `ast.parse(feature_version=(3,10))`，`python -m compileall -q tools/fill_sim` 成功，提交前 `git diff --cached --check` 成功。目标 Ubuntu Python 3.10.12/SQLite 3.37 实机仍未运行。

```text
python -m unittest discover -s tools/fill_sim/tests -v
----------------------------------------------------------------------
Ran 101 tests in 1.075s

OK
```

完整原始输出在 `tools/fill_sim/tests/test-output-pm-round1.txt`。新增覆盖：六家录制资金费解析、小时/毫秒/秒转换、负率及实际周期、OKX 最近结算点、缺周期不猜、市场错配/重复/非有限/过期响应、两次请求共享限频、429 暂停、300 秒 TTL/负缓存/跨结算失效、多变体共用/跨事件复用、异常 provisional 主收益排除、快照持久化/等待中重启、超时不阻塞持仓、现货方向与覆盖分母、状态/日报未知比例、默认费用一致性和 null 列表、guard 字段契约。

联网命令及原始摘要：

```text
python -m tools.fill_sim.smoke --include-spot --fixtures tools/fill_sim/tests/fixtures
SMOKE exchanges=6 markets=12 failures=0
FUNDING exchanges=6 parsed=6 failures=0
```

最终录制 **2026-11-09 07:17:06–07:17:29 UTC** 左右完成，共 32 次公开 GET：12 市场 ×（规格+深度）=24，资金费=8（Binance/Aster 各 2，其余各 1）。完整输出在 `tools/fill_sim/tests/smoke-output-pm-round1.txt`。六家资金费解析摘要（rate 是小数比率，不是百分数）：

| 市场 | rate | next（UTC 秒） | interval（秒） | captured_at（UTC） |
| --- | ---: | ---: | ---: | --- |
| gate future BTC_USDT | 0.0001 | 1794211200 | 28800 | 07:17:08.290068 |
| bitget future BTCUSDT | 0.0001 | 1794211200 | 28800 | 07:17:09.762993 |
| binance future BTCUSDT | 0.00010000 | 1794211200 | 28800 | 07:17:11.567260 |
| bybit future BTCUSDT | 0.00009296 | 1794211200 | 28800 | 07:17:14.698087 |
| okx future BTC-USDT-SWAP | 0.0000995657001196 | 1794211200 | 28800 | 07:17:16.172263 |
| aster future BTCUSDT | 0.00010000 | 1794211200 | 28800 | 07:17:23.194662 |

六个 next 均为当日 08:00:00 UTC；本次 BTC 都是 8 小时，代码和测试仍接受真实 1/4 小时等周期，不由 BTC 推断所有市场。四档 Gate→Aster 示例均 gone，净价差约 -17.12093 bp；采用新费用，仍是非同步快照工程示例，不是可交易结论。

### 剩余风险与下一任务

1. Binance 合约默认 null 是本轮唯一未取得当前公开费率表的项；其事件默认跳过，不能以 FAQ 假设偷偷补齐。
2. 资金费来源缺口已补，但使用开仓快照估算，不覆盖持仓中费率/结算间隔变化或最终扣款。5 分钟负缓存可能延后恢复；到期立即失效、冷请求和共享预算会增大实测延迟，须用真实观察判断影响。
3. 费用配置仍按 exchange × market_type，不能自动识别所有特殊费率组/活动/地区。特别是 Aster Group B/RWA、Bybit 特殊板块，生产观察前要明确普通市场范围或另开任务扩展精确市场费率，不能用当前默认宣称这些组净收益已核实。
4. 本轮未做独立子代理审查；已自查差异、运行回归及公开冒烟。代码供下一独立审查/Claude 验收任务使用，未获实盘或生产资源验收结论。
5. PM 生成 guard、Ubuntu 目标运行时验证、七天观察与共享出口影响仍待下一部署/验收任务。Aster 新资金费用 V3，原规格/深度 V1 的接口演进风险仍存在。
6. 开始时无其他 WIP；只提交本轮模块文件，未跟踪测试缓存由既有 .gitignore 忽略，无真实 env、密钥或运行库产物。下一模块请另建任务并从本节继续；本轮到本地提交和交接为止。

## PM 补充（2026-11-09）：币安合约费率已核实

PM 在官方页面 https://www.binance.com/en/fee/futureFee 核实（浏览器渲染后读取表格）：USDⓈ-M 合约 Regular User（30 天成交 < 500 万美元）USDT 保证金 maker 0.0200% / taker 0.0500%；BNB 抵扣 9 折后 0.0180% / 0.0450%。fill-sim 采用 taker 0.0005，不假设 BNB 抵扣。该页面是前端渲染的，直接抓 HTML 拿不到表格，这就是之前查不到的原因。
