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
