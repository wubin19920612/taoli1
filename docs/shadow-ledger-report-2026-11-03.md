# 阶段 1：影子账本实现与离线验证报告

日期：2026-11-03。分支：`codex/shadow-ledger`；基线：`81585e43d60b03d3ca9df14b3f9379c5f108c183`。

**交付状态：A–F 已实现；本次 PM 性能返工、后端全量、前端全量、tsc 及两个离线基准全部通过，已分步提交本地。** 返工基线为 `dc0fec6`，验收预算以规格末尾 PM decisions 为准；本机通过不等于生产可启用。 `SHADOW_ENABLED` 默认仍为 0。未推送、未部署、未连接服务器、未运行 WSL、未启动真实服务、未访问外网行情或交易接口。所有验证均为离线测试；本次未安装或更新依赖。

## 实现内容与口径

- A：只删除 `opportunity_matches_rule` 对缺失成交额的一票否决。已知腿的成交额仍比较阈值；标签生成器补充 `volume_unknown`，可通过 `excluded_risk_labels` 排除。没有用假机会评分拦告警。
- B：`execution_routes.py` 保存 CrossEx 的 `(exchange_type, business_type, base, tag)` 集合。只接受 live FUTURE，按原始合约去报价币精确匹配，HL 的 DEX/tag 必须一致；bitget 只有默认 native 通道。默认每 1800 秒刷新，失败保留旧集合并 900 秒后重试。Astro 原有按合约查询的目录文件完全未改。
- C：每轮快照消费者跟踪当前市场，机会从列表消失后仍可平仓。每个开仓同时产生 `conv-0.1` 和 `conv-half` 两行；默认 1500、允许配置至 3000，上限按行计，满额丢弃新的开仓并计数。相同机会有任一变体未平时不重复开仓，最后一个变体平仓后冷却 600 秒。
- D：市场维护中间价及最后变动时间；价格波动与价差样本使用定长数值环形缓冲。按交易所去重投票，已知不同资产身份不能参与同一中位价。市场状态最多 6000 个、价差序列最多 3000 个，另有 TTL 和丢弃计数。BMT 用真实公式计算合成盘口的价差，可同时命中 frozen_leg、spread_plateau。
- E：三条 `/api/shadow/*` 路由使用现有密码依赖。统计只用已平仓事件，默认排除假机会。北京时间平仓日期分组；Wilson 95%、样本标准差、t 单侧下界、2000 次按整天重抽样、两下界的较小值、n_min、CVaR5、净值曲线和最大回撤。前端提供中文维度筛选、时间范围、包含假机会开关、桶表格、最近影子单与原始市场信息。
- F：默认关闭影子记录；独立连接批量写入，异常计数且每 300 秒最多一条消费错误日志。恢复失败不从空账本继续开仓。写入失败时先重试原批次，再处理下一代，避免无限积累待写数据。

### 开平仓公式与费用

先读取了 `spread_engine.midpoint_spread_pct`，保持同一方向：

```text
open_pct  = 200 * (sell.bid - buy.ask) / (buy.ask + sell.bid)
close_pct = 200 * (sell.ask - buy.bid) / (buy.bid + sell.ask)
spread_bps = (open_pct - close_pct) * 100
net_bps = spread_bps - slippage_bps - fees_bps + funding_bps
```

平仓负价差会提高价差收益，未取绝对值或调换买卖腿。过期/缺失满 600 秒以各腿最后有效价关闭，优先级高于止损、收敛和超时；10 秒内的真实 book 才能用于开仓及正常平仓。`conv-half` 的 24 小时条件与收敛条件重合，所以按任务书的优先级会记 `converged`；`timeout` 可以出现在 `conv-0.1`。

费用直接使用机会已有 `buy_fee_pct`、`sell_fee_pct`，开平共 `2 * (buy_fee_pct + sell_fee_pct) * 100` bp。源码现状是 `FeeSettings` 按现货/合约给费率，并没有另一个逐交易所费率表，未杜撰费率。未再次扣现有 `safety_slippage_pct`，以免和影子滑点重复。

流动性分档是**临时成交额代理口径，不是实际深度**：两腿较小的 24h 成交额 `>5000万 / 500万–5000万 / 50万–500万 / <50万 / 未知`；默认每次单腿滑点 `2 / 5 / 15 / 30 / 30` bp，共四次。

资金费分别按买腿负、卖腿正，在各自结算点累计；周期不同也独立处理。使用结算前最近观察到的费率，结算后的下一周期费率不回填上一期。持有期间跨越多个结算点但未观察到其间变化，只能延用最近已知费率，所有结果标记 `funding_estimated=true`；已知结算点费率缺失另计 `funding_unknown_settlements`。这不是交易所真实资金费流水。

### 存储、恢复与异常隔离

`shadow_trades` 提供主键、机会 id、变体、币、开平时间和 fake_flag 列及索引，完整交易字段/MarketKey JSON/恢复状态放在 payload 中。开仓和平仓立即批量落库；跟踪状态每 60 秒检查点保存，退出时再保存。异常退出可能丢失最多约 60 秒的“最后有效价/未结算费率观察”，已经提交的开平仓和资金费结算不会重复记账。仍未平仓的数据不受 30 天清理影响。

文件数据库使用独立连接，影子连接的 SQLite 页缓存限定为 512 KiB；批量写按最多 200 行划分，每个分块使用 savepoint 独立提交。失败只回滚当前分块，先前成功的分块保留；剩余待写项必须重试成功后才接收下一代计算，不能回滚先前的其他写入。影子表初始化放在 `ShadowRuntime.initialize` 的 try/except 内；不会因影子表单独失败阻断主服务启动。历史记录与现有消费者继续使用原有流程。

## 配置

```dotenv
SHADOW_ENABLED=0
SHADOW_MIN_OPEN_PCT=0.2
SHADOW_MAX_OPEN=1500
SHADOW_SLIPPAGE_BPS={"gt50k":2,"5k-50k":5,"500-5k":15,"lt500":30,"unknown":30}
NATIVE_ROUTE_EXCHANGES=hyperliquid,lighter,bitget
CROSSEX_CATALOG_REFRESH_SECONDS=1800
```

`SHADOW_MAX_OPEN` 限于 2–3000，按两个变体原子开仓。SHADOW_ENABLED 控制账本跟踪与写入；通道/假机会展示仍更新。后台模式且账本开启时挂现有最新快照邮箱；关闭时及旧 inline 模式通过独立观察器读取已发布代数，不改变历史消费者、采集器调度或 inline 行为。共享快照模型不修改；展示使用同代的带字段副本。

## PM 性能返工说明

1. **增量跟踪**：`MarketChanges` 对精确 MarketKey 下的报价时间、盘口、资金费计划、资产身份及有效性比较内容修订，不依赖 Python 对象地址。`ShadowLedger.by_market` 把两腿映射到持仓；报价变化、消失或过期只触达相关持仓。资金费结算、600 秒 stale、24/48 小时持有期限通过有界定时堆单独唤醒，因此没有新报价也不会漏结算或超时。检查点仍保存所有未平行。压力场景常规轮重算 1500 个双变体持仓，真实规模中位重算 167 个，未平行数分别为 3000/1000。
2. **评分增量**：仅更新报价修订影响的市场及资产组；分钟切换执行历史/冻结时间边界维护。价差环形缓冲只保留开仓价差 ≥1% 的机会，降至阈值以下即删除。冻结、离群和 BMT 旧回归保持通过。
3. **不可变线程输入**：`freeze_snapshot` 在事件循环无 await 地复制本轮标量和列表元组；市场数据扁平化为不可变 tuple，工作线程才构造记录，避免在事件循环分配成千上万个包装对象。机会输入缓存通过字段内容比较识别原地改值和列表变化，且每轮只保留当前机会。输入不含原始 MarketSnapshot/Opportunity 或可变目录集合；CrossEx 目录使用本轮 frozenset。验证元数据只在事件循环用于展示，工作线程不访问。
4. **线程所有权与互斥**：`submit` 接入原有邮箱和 inline 观察器；已有工作未完成时立即跳过并累加 `skipped_rounds`，从 `/api/shadow/status` 返回。`consume` 为可等待的测试/调用入口。计算、恢复、检查点、JSON 编码、批量写和关闭 flush 均在 `asyncio.to_thread` 工作流程中；现有 aiosqlite 的 SQL 操作继续由其专用 SQLite 线程执行。账本、评分器和计算缓存只由互斥工作轮修改；目录刷新任务与工作线程拥有各自的通道缓存。取消等待者不会释放执行门闩，关闭会先等活动轮结束。
5. **一致状态发布**：工作线程生成状态字典，事件循环整份替换最近完成轮次的状态。接口不遍历正在变化的 positions/pending/scorer。旧 generation 的展示结果不会覆盖新快照；展示副本在工作线程构造/复用，原始共享模型不修改。输入捕获错误、计算及写入异常均隔离。
6. **写入与内存**：两个变体共享不变开仓元数据；`PendingRows` 只保留账本拥有的行和持仓引用，到写入时最多展开 200 行。成功分块立即从 pending 移除，失败分块及后续分块留待重试。展示模型的字段集合按 dict 大小预分配，避免每个模型多分配约 4 KiB。没有改 schema.py、repositories.py 或采集调度。
7. **配置**：Settings 和环境变量缺省均为 `SHADOW_MAX_OPEN=1500`；压力基准明确覆盖为 3000。`SHADOW_ENABLED` 仍默认关闭。

新增 8 项安全回归覆盖：取消后仍互斥/关闭等待/状态快照、输入与目录隔离/旧代不覆盖、缓存识别原地修改、200 行分块失败重试及 JSON 编码线程、无新报价的结算/stale/检查点、精确 DEX 索引及定时堆容量、评分增量与 ≥1% 缓冲、配置默认值。原有 41 项账本/评分/API/通道用例也通过。基准只按 PM 指定的新预算重新定义断言，没有删除失败保护或放宽其他测试。

## 验证环境与命令原始汇总

Windows 本机 Python 3.12.10，pytest 8.4.2、pydantic 2.12.5、FastAPI 0.135.1、aiosqlite 0.22.1。仍非 PM 锁定依赖 venv；生产同构环境与真实行情验收不在本次授权范围内。

后端在 `backend` 目录运行，先设 `$env:PYTHONPATH='.'`：

```powershell
python -m pytest tests -q -p no:cacheprovider --basetemp ../.pytest-tmp/perf-full-final
```

原始汇总（退出码 0）：

```text
2484 passed, 8 deselected, 12 warnings in 201.89s (0:03:21)
```

8 项 deselected 包括已有显式联网/性能测试，以及本次两个显式 benchmark；两个影子基准已另行独立运行。12 个 warning 为原有 datetime.utcnow 弃用警告。离线网络保护沿用 `tests/conftest.py`，未改选择规则。

前端在 `frontend` 目录运行（使用已有 node_modules，未安装依赖）：

```powershell
npm.cmd test -- --reporter=dot
.\node_modules\.bin\tsc.cmd --noEmit
```

最终原始汇总（两条命令退出码均为 0）：

```text
 Test Files  31 passed (31)
      Tests  413 passed (413)
   Duration  120.76s (transform 3.23s, setup 397.14s, collect 200.05s, tests 310.30s, environment 137.52s, prepare 8.32s)
TSC_EXIT_CODE=0
```

tsc 无诊断输出。前端已有 React act 与 Ant Design 弃用警告保留。首次前后端并行运行时，旧 AppShell 的 `opens the settings history tab for a legacy history bookmark` 等待标签页失败，原始汇总为：

```text
Test Files  1 failed | 30 passed (31)
     Tests  1 failed | 412 passed (413)
  Duration  131.26s (transform 15.25s, setup 396.68s, collect 206.24s, tests 320.19s, environment 105.55s, prepare 17.62s)
```

后端结束后，保持同一前端源码、断言及超时，单独重跑前端全量得到上述 413 passed；可确认隔离重跑通过，不能单凭一次重跑断言根因已完全定位。未修改前端或其测试。

完整日志保存在忽略目录 `.pytest-tmp/perf-backend-full.log`、`perf-frontend-full.log`（首次失败）、`perf-frontend-final.log`、`perf-tsc.log`、`perf-benchmark-final.log`；测试库/临时目录也保留在仓库内 `.pytest-tmp/`。

## 两个场景的性能基准

独立运行，未与后端/前端全量或 tsc 负载并行：

```powershell
python -m pytest tests/test_shadow_benchmark.py -q -s -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/perf-bench-final
```

两个场景都经过真实 ShadowRuntime、目录标注、评分、账本及文件 SQLite 分块写入：压力为 1000 机会/3000 未平行且全部报价变化；真实规模为 400 机会/1000 未平行，每轮轮换约 1/3 市场对报价（对应可见机会约 1/3）。初次开仓分别输入 1500/500 个机会以建满两变体持仓；随后 51 个常规轮，另有第 61 秒检查点。沿用原基准的可变合成模型，每轮等待消费结束后原地更新，不将上游 fixture 模型反复重建的内存当作影子增量。

事件循环同步主体计时包含不可变捕获、任务创建、同代展示/状态发布及输入释放，不把 await 工作线程的等待算作事件循环执行。中位/P95/max 都含首次开仓和检查点。工作线程使用 `thread_time` 测 CPU、`perf_counter` 测完整墙钟（含 aiosqlite 等待）；常规 CPU 中位预算分别为 20/8 ms。Windows CPU 时钟约 15.625 ms 粒度，真实场景的 0 ms 中位不代表零成本，因此额外保留更保守的工作线程墙钟中位 ≤20/8 ms 断言。RSS 为 Windows WorkingSetSize，fixture 和主测试 DB 建立后取基线，随后初始化真实独立影子 DB 连接；最终包含账本、缓存及线程等增量，非 tracemalloc 估算。

最终原始输出（退出码 0）：

```text
SHADOW_BENCHMARK {"capture_median_ms": 2.7231998974457383, "changed_fraction": 1.0, "checkpoint_event_loop_ms": 2.7988001238554716, "checkpoint_worker_wall_ms": 156.6392999375239, "event_loop_max_ms": 9.065199992619455, "event_loop_median_ms": 2.8439000016078353, "event_loop_p95_ms": 3.50580015219748, "open_event_loop_ms": 9.065199992619455, "open_rows": 3000, "open_worker_wall_ms": 192.44190002791584, "opportunities": 1000, "publish_median_ms": 0.09510002564638853, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 26.3984375, "scenario": "stress", "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 23.590686274509803, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 19.80879995971918}
.SHADOW_BENCHMARK {"capture_median_ms": 0.9296999778598547, "changed_fraction": 0.3333333333333333, "checkpoint_event_loop_ms": 0.8501000702381134, "checkpoint_worker_wall_ms": 33.39190001133829, "event_loop_max_ms": 2.5803999742493033, "event_loop_median_ms": 0.9787000017240644, "event_loop_p95_ms": 1.2692000018432736, "open_event_loop_ms": 2.5803999742493033, "open_rows": 1000, "open_worker_wall_ms": 53.4727000631392, "opportunities": 400, "publish_median_ms": 0.0315000070258975, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 6.14453125, "scenario": "realistic", "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 3.982843137254902, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 5.079800030216575}
2 passed in 3.33s
```

| 指标 | 压力场景 | 真实规模 | PM 预算 |
| --- | ---: | ---: | --- |
| 事件循环中位 | 2.844 ms | 0.979 ms | ≤3 ms |
| 事件循环 P95 | 3.506 ms | 1.269 ms | ≤10 ms |
| 事件循环最大值（含开仓/检查点） | 9.065 ms | 2.580 ms | ≤20 ms |
| 工作线程 CPU 中位（量化时钟） | 15.625 ms | 0.000 ms | ≤20 / ≤8 ms |
| 工作线程墙钟中位（额外硬保护） | 19.809 ms | 5.080 ms | ≤20 / ≤8 ms |
| WorkingSet 增量 | 26.398 MiB | 6.145 MiB | ≤30 MiB |
| 首次开仓工作线程墙钟 | 192.442 ms | 53.473 ms | 单独记录 |
| 检查点工作线程墙钟 | 156.639 ms | 33.392 ms | 单独记录 |

**边界**：压力场景事件循环中位与工作线程墙钟的余量较小；CPU 均值为 23.591 ms（预算针对中位数），仍有长尾。上述同步主体计时不能替代真实服务事件循环调度延迟、GIL/GC 争用及生产负载观测。首次开仓和检查点仍需较长工作线程墙钟，但不再在事件循环编码/写库，期间新轮会被计数跳过。本次没有 1.9 GiB 无 swap 同构环境、长时间真实行情或大量已平仓统计冷查询验收。

返工前历史结果保留于 `ff22a89` 报告：常规中位 29.8535 ms、P95 69.2092 ms、首次开仓 209.1337 ms、检查点 183.8084 ms，旧事件循环 ≤5 ms 断言失败。本次只使用 PM 明确批准的事件循环/工作线程分离预算；开发中未通过的场景继续优化实现，未调高阈值。本节最终原始输出是当前验收依据。

## 币安成交额为空：源码调查与离线复现

来源为 `backend/app/exchanges/binance.py`，没有修改采集器或适配器。

1. `_parse_book_tickers` 只从 bookTicker 建立盘口，不赋成交额，模型默认 `None`。现货路径直接返回这些盘口，故该路径成交额缺失是代码确定的。
2. 合约 `fetch_future_tickers` 另外请求 `/fapi/v1/ticker/24hr`，只取 `quoteVolume`，以响应中的原始 `symbol` 精确关联 `snapshot.raw_symbol`。
3. `_fetch_future_24h_volumes` 请求异常时返回空字典，整批已拿到的盘口仍返回，但成交额全部为 None。这个辅助请求失败不会令整次行情采集失败。
4. 响应不是 list 时等效空字典；单个 symbol 缺失、原始 symbol 不一致、`quoteVolume` 缺失/无法解析时，该合约取值为 None。不以 base volume 或 mark price 猜算，不缓存上轮有效成交额。
5. 本次五种合成用例分别复现正常、请求失败、缺少 symbol、非法 quoteVolume、错误响应形状。失败四类均保留有效盘口而成交额为空；现货默认空也已验证。

**结论边界**：以上可以解释空值如何产生；没有线上请求/响应与日志，不能断言生产某个具体币当时是超时、限流、新合约覆盖缺口还是异常字段。此处未修复，待另一个币安元数据任务拿到获授权的线上证据再处理。

## 与任务书的差异及未决事项

1. 新增两个辅助模块 `shadow_runtime.py` 和前端 `api/shadow.ts`；职责分别为接入隔离、前端类型。风险标签额外改动 `risk_labels.py` 两行，因为采集器会重建标签，单在 spread_engine 填标签会被覆盖。这是 A 的必要补充；没有改机会生成/方向算法。
2. 原 Astro 目录仅复用 URL 与 tag 语义，新增独立全量目录；未泛化或改写原类，保留其缓存、失败关闭及全部原测试行为。
3. 完整字段以 payload JSON 存储，索引字段单列；读取统计时 SQL 只投影必要字段。没有对 opportunity_history 的写入/位图编码做任何改动。
4. 任务书称“每所费率设置”，源码实际上是现货/合约默认费率，按机会的既有腿费率复用。流动性、滑点及资金费均是估计口径。
5. 价差序列每分钟一个样本，60 个连续分钟样本才启用 plateau；一分钟内不重复计样本。容量满时不继续增大，计数公开；容量之外的市场/序列无法得到完整历史规则评分，但仍计算可用的即时软标签。
6. t 下界使用任务书指定的 df<30 表，df≥30 取 1.645；分位数采用线性插值；CVaR5 取最差 ceil(5% * n) 个事件均值；E≤0 时 n_min=null，最低事件门槛仍为 30。种子固定为 20261103。这些细节供 arb-mvp 对齐复核。
7. 本机两个新预算场景已通过，但压力场景余量较小；首次开仓和检查点工作线程仍有长尾。不能据此直接开启生产。30 天大量已平仓统计的冷查询峰值尚未做生产规模压力测试。
8. 用户最新指令禁止联网与启动服务，故未做任务书中真实 10 分钟行情验收；锁定 venv、生产同构资源验收、独立只读审查仍由 PM 安排。没有部署、备份或生产验证可报告。

## Git 与范围检查

历史实现提交：`42b692f`、`23615a3`、`b6a8747`、`acfb50b`、`4d05c02`；旧报告为 `ff22a89`。本次从已提交 PM decisions 的 `dc0fec6` 继续，初始工作区干净。

本次分步本地提交：

- `52c5e62` — `perf: move incremental shadow ledger processing off event loop`
- `b0766fd` — `test: enforce shadow worker budgets and snapshot isolation`
- `docs:` 报告与交接提交见本分支 `git log`（文档不自引提交号）。

返工仅涉及 shadow runtime/ledger/scorer、config 默认值、main 的影子消费者入口、两个影子测试文件以及本报告和交接文档。`schema.py`、`repositories.py`、collector、Astro/arb-mvp、history 写入及前端均无本轮差异；`git diff --check` 通过。用户规格已由基线 `dc0fec6` 跟踪，本轮未修改。

没有 push、服务器连接、备份、部署或线上验证。忽略目录 `.pytest-tmp/` 的日志/诊断脚本/测试库与既有 node_modules 均保留。交接见 [task-handoff-2026-11-03-shadow-ledger.md](task-handoff-2026-11-03-shadow-ledger.md)；下一步是本模块的 PM 锁定环境与独立验收，真实行情及生产操作需要另行授权。
