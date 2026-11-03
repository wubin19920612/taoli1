# 阶段 1：影子账本实现与离线验证报告

日期：2026-11-03。分支：`codex/shadow-ledger`；基线：`81585e43d60b03d3ca9df14b3f9379c5f108c183`。

**交付状态：A–F 已实现并提交本地；性能验收尚未通过，不能据此开启生产影子记录。** `SHADOW_ENABLED` 默认仍为 0。未推送、未部署、未连接服务器、未运行 WSL、未启动真实服务、未访问外网行情或交易接口。所有测试使用合成数据；依赖安装使用 npm 离线缓存。

## 实现内容与口径

- A：只删除 `opportunity_matches_rule` 对缺失成交额的一票否决。已知腿的成交额仍比较阈值；标签生成器补充 `volume_unknown`，可通过 `excluded_risk_labels` 排除。没有用假机会评分拦告警。
- B：`execution_routes.py` 保存 CrossEx 的 `(exchange_type, business_type, base, tag)` 集合。只接受 live FUTURE，按原始合约去报价币精确匹配，HL 的 DEX/tag 必须一致；bitget 只有默认 native 通道。默认每 1800 秒刷新，失败保留旧集合并 900 秒后重试。Astro 原有按合约查询的目录文件完全未改。
- C：每轮快照消费者跟踪当前市场，机会从列表消失后仍可平仓。每个开仓同时产生 `conv-0.1` 和 `conv-half` 两行；3000 上限按行计，满额丢弃新的开仓并计数。相同机会有任一变体未平时不重复开仓，最后一个变体平仓后冷却 600 秒。
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

生产文件数据库使用独立连接；批量写用 savepoint，整批成功才释放提交，失败回滚到本批次，不能回滚先前的其他写入。影子表初始化放在 `ShadowRuntime.initialize` 的 try/except 内；不会因影子表单独失败阻断主服务启动。历史记录与现有消费者继续使用原有流程。

## 配置

```dotenv
SHADOW_ENABLED=0
SHADOW_MIN_OPEN_PCT=0.2
SHADOW_MAX_OPEN=3000
SHADOW_SLIPPAGE_BPS={"gt50k":2,"5k-50k":5,"500-5k":15,"lt500":30,"unknown":30}
NATIVE_ROUTE_EXCHANGES=hyperliquid,lighter,bitget
CROSSEX_CATALOG_REFRESH_SECONDS=1800
```

`SHADOW_MAX_OPEN` 限于 2–3000，按两个变体原子开仓。SHADOW_ENABLED 控制账本跟踪与写入；通道/假机会展示仍更新。后台模式且账本开启时挂现有最新快照邮箱；关闭时及旧 inline 模式通过独立观察器读取已发布代数，不改变历史消费者、采集器调度或 inline 行为。共享快照模型不修改；展示使用同代的带字段副本。

## 验证环境与命令原始汇总

Windows 本机 Python 3.12.10，pytest 8.4.2、pydantic 2.12.5、FastAPI 0.135.1、aiosqlite 0.22.1。**不是 PM 的锁定依赖 venv**；锁定环境最终验收由 PM 执行。

后端命令在 `backend` 目录运行；PowerShell 先设 `$env:PYTHONPATH='.'`：

```powershell
python -m pytest tests -q -p no:cacheprovider --basetemp ../.pytest-tmp/full-final
```

最终原始汇总（退出码 0）：

```text
2476 passed, 7 deselected, 12 warnings in 251.17s (0:04:11)
```

7 项 deselected 包括显式联网/性能测试，本次性能测试另行运行。12 个 warning 来自原有 datetime.utcnow 弃用警告。

前端命令在 `frontend` 目录运行：

```powershell
npm.cmd ci --offline --ignore-scripts --no-audit --no-fund
npm.cmd test -- --reporter=dot
.\node_modules\.bin\tsc.cmd --noEmit
```

最终原始汇总（退出码 0）：

```text
Test Files  31 passed (31)
     Tests  413 passed (413)
  Duration  158.79s (transform 4.24s, setup 489.34s, collect 237.96s, tests 377.79s, environment 194.06s, prepare 11.07s)
```

类型检查原始退出结果：`TSC_EXIT_CODE=0`（tsc 无诊断输出）。依赖安装输出：`added 245 packages in 52s`，命令明确使用 `--offline`，没有联网安装。

过程记录：第一步通道/原 Astro 目录/告警测试 `55 passed in 0.56s`；首轮前端 `Test Files 30 passed (30)`、`Tests 410 passed (410)`；新增影子页及导航定向测试 `Test Files 2 passed (2)`、`Tests 32 passed (32)`。首轮后端 `3 failed, 2469 passed, 6 deselected, 12 warnings in 202.56s (0:03:22)`，暴露消费者接入与事务保护问题，已修正实现，未修改这两组旧保护测试。唯一改动的旧告警断言是任务书明确要求变更的成交额缺失行为；前端旧导航期望只新增了影子账本条目。

离线网络保护由现有 `tests/conftest.py` 自动夹具执行，真实网络测试默认 deselect。详细运行日志保留于仓库内忽略目录 `.pytest-tmp/`，未入 Git。

## 性能基准

独立运行，无后端/前端全量测试并行：

```powershell
python -m pytest tests/test_shadow_benchmark.py -q -s -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/bench-final
```

合成 1500 对市场、3000 个未平变体行、每轮 1000 个机会，盘口持续变动，覆盖真实 Runtime（评分、通道、展示副本、账本跟踪、文件 SQLite 批量写）。RSS 为 Windows WorkingSetSize 差值，行情 fixture 建立后取基线，再建立账本；不是 tracemalloc 推测。单独报告首次开仓批量写和 60 秒检查点写入，不混入常规轮的中位数。基准包含硬断言，中位数大于 5 ms 或 RSS 增量大于 30 MiB 会失败。

最终原始基准与汇总（退出码 **1**，耗时硬断言失败）：

```text
SHADOW_BENCHMARK {"checkpoint_batch_ms": 183.80839994642884, "median_ms": 29.853499960154295, "open_batch_ms": 209.13370000198483, "open_rows": 3000, "opportunities": 1000, "p95_ms": 69.20919998083264, "rounds": 51, "rss_delta_mib": 24.81640625, "within_memory_budget": true, "within_time_budget": false}
FAILED tests/test_shadow_benchmark.py::test_shadow_3000_rows_1000_opportunities
1 failed in 4.37s
```

| 指标 | 结果 | 任务书门槛 | 判定 |
| --- | ---: | ---: | --- |
| 完整消费者常规轮中位数 | 29.85 ms | ≤5 ms | 未通过 |
| 常规轮 P95 | 69.21 ms | 未指定 | 记录 |
| 常驻 WorkingSet 增量 | 24.82 MiB | ≤30 MiB | 本机合成场景通过 |
| 首次 3000 行批量开仓 | 209.13 ms | 未单列 | 记录 |
| 60 秒检查点批量写 | 183.81 ms | 未单列 | 记录 |

最终基准比早期静态盘口基准增加了持续变化的盘口与合成 CrossEx 条目；不同轮次还存在本机调度抖动，不能把早期更小数字当最终验收结果。后续优先分析评分扫描/同代展示副本、检查点序列化的成本。不得删除或放宽基准断言以使验收变绿。

早期完整消费者基准 28.93 ms；去除重复身份/盘口解析、增加定长缓存和常规跟踪快速路径后，优化中测得 17.44 ms。**截至该次仍未满足 ≤5 ms 预算**。最终结果以本节最终原始输出为准，不把功能测试通过当作性能验收通过。尚未在 1.9 GiB 生产同构环境测量，未进行真实 10 分钟行情验收。

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
7. 性能预算未通过前，不建议开启生产；首次开仓和检查点 JSON 编码/写库还有明显延迟。30 天大量已平仓统计的冷查询峰值尚未做生产规模压力测试。
8. 用户最新指令禁止联网与启动服务，故未做任务书中真实 10 分钟行情验收；锁定 venv、生产同构资源验收、独立只读审查仍由 PM 安排。没有部署、备份或生产验证可报告。

## Git 与范围检查

已分步提交：`42b692f`（A/B/F 基础）、`23615a3`（账本/评分/统计接口）、`b6a8747`（中文页面）、`acfb50b`（恢复/结算/隔离复核）、`4d05c02`（资源预算硬断言）；文档提交见本分支 `git log`。

`main.py`、`config.py` 相对 81585e4 仅追加；未触碰并行 SQLite 任务的已有钩子。禁止文件差异检查为空：collector、astro_*、arb_mvp_*、fs_astro、history 写入、原 gate_crossex_hyperliquid。所有提交均只在当前本地分支，未 push。

用户提供的 `docs/shadow-ledger-spec-2026-11-03.md` 原先未跟踪，原样保留，不擅自纳入提交。交接见 `docs/task-handoff-2026-11-03-shadow-ledger.md`。
