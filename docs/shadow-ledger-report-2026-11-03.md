# 阶段 1：影子账本实现与离线验证报告

## PM 第二轮修复（2026-11-04，当前交付）

本轮按任务书「PM decisions 第二轮」修复四条审查问题，并检查关闭顺序。工作树为 `taoli1/.worktrees/shadow-ledger`，分支 `codex/shadow-ledger`，起点 `4dfd696`（包含性能基线 `db05329`）；进入任务时工作区干净，无 `HANDOFF.md`。以下本轮记录优先于后面的历史记录，尤其是开关关闭后的行为、API 连接和资金费恢复说明。

### 修复与失败回归

| 审查项 | 修复前证据 | 最终行为与验证 |
| --- | --- | --- |
| 变体资金费恢复 | 202 行分两批（200/2）更新，第一批提交后打断；两种恢复顺序分别出现 2/0、2/4 bp；`2 failed, 27 deselected in 1.54s` | 每行持有独立的两腿资金费状态，序列化到该行既有 `tracking.funding`，包括 `last`、`next`、费率与周期；恢复不再拿第一行的进度给所有变体使用。两种顺序均为 2/2 bp，再次恢复也不重复结算。保留原 payload 格式，不迁移表结构。 |
| 关闭仍运行和 DELETE | SQLite trace 捕获到 `shadow_trades` 的保留期 DELETE；已存记录仍经 API 返回 | `initialize/submit/_process/inline observer/aclose` 在关闭时直接返回；main 不启动影子消费者及影子 CrossEx 目录任务。trace 断言无 INSERT/UPDATE/DELETE，to_thread 派发次数为零、没有活动影子工作轮或专用 DB；inline/background 两种模式都不注册影子任务。status 返回 `enabled=false`，正常 trades/buckets 请求均为空，原认证及参数校验保留。 |
| API 读到未提交事务 | 已提交 2 行、写连接外层 BEGIN 内再保存 2 行，两个接口都读到 4 行；`1 failed, 4 deselected in 24.23s` | 路由在 `asyncio.to_thread` 内创建独立 `file:...?mode=ro` 连接，复用既有仓库查询方法，读完关闭。提交前/回滚后都是 2 行，提交后为 4 行；trades 与 buckets 均覆盖。另验证连接创建不在事件循环线程，URI 正确编码空格和 #，同 URI 写入被 SQLite 拒绝。 |
| 所有异常路径统一限频 | capture 连续三次打印三条、catalog 没有统一分类计数；publish 异常逃逸并阻断关闭；`3 failed, 6 deselected in 17.58s` | `ShadowErrorLog` 用锁维护按类别的计数和上次日志时刻，每类 300 秒最多一条，其余仍计数。`errors` 是总计，`error_counts` 返回计数副本；目录计数无需等下一轮就能在 status 看到。验证 299/300 秒边界和不同类别互不压制。 |

API 只读查询封装在 `routes_shadow.py`，没有改 `repositories.py`、`schema.py`，避免触及其他 SQLite 任务的共享实现。启用态 API 验收改用临时**文件** SQLite；独立只读连接不回退借用 `:memory:` 的写连接。文件不可读或统计失败返回 503，并进入统一分类计数。关闭态仍用内存库验证旧数据不可见；原数据断言、认证断言和参数校验断言全部保留。

异常分类覆盖：`initialize`、`capture`、`consumer`（处理/持久化）、`worker`、`publish`、`catalog`、`read_list_trades`、`read_closed_events`、`statistics`、`shutdown_flush`、`shutdown_close`、`shutdown_wait`。前三类、发布、目录和 API 的故障注入测试验证了计数及日志次数；正常关闭、取消关闭和重复关闭另有顺序回归。没有改变 CrossEx 匹配规则或失败重试间隔。

关闭顺序：main 原有的“先停 supervisor、再 await shadow_runtime.aclose、最后关闭共享连接”保留。`aclose` 现在复用同一个受 shield 保护的关闭任务；等待活动轮结束，再 flush，再关闭自有连接。调用方取消关闭也必须等这个流程完成，重复关闭不再写已关闭连接。发布异常由 `_finish` 隔离，不能使关闭提前跳过。原取消消费者/互斥测试继续通过。

关于曾出现的 `PytestUnhandledThreadExceptionWarning`：本轮没有复现原警告，不能断言其历史根因。新增发布失败用例确实暴露了旧关闭路径提前退出、遗留 SQLite 线程的问题；失败日志已写出 pytest 汇总后，该次测试进程仍未退出，随后仅中断了该测试会话。修复后的影子相关测试将该警告提升为错误，全部通过；全量日志也检查该警告。

回归开发中另外保留了夹具失败记录：关闭态启动测试最初错误组合 `start_collector=True/start_background_workers=False`；只读测试最初有路径循环变量覆盖和 mock 参数名冲突（`2 failed, 7 passed in 19.74s`）。这些只修夹具，未删除或放宽断言，后续同组为 `9 passed in 19.30s`。红灯原始输出位于 `.pytest-tmp/round2-funding-red.log`、`round2-disabled-red.log`、`round2-reader-red.log`、`round2-logging-red.log`。

### 最终本地环境与原始汇总

Windows 11，Python **3.12.10**（`C:/Users/wubin/AppData/Local/Programs/Python/Python312/python.exe`），pytest **8.4.2**，FastAPI **0.135.1**，Starlette **0.52.1**，Pydantic **2.12.5**，aiosqlite **0.22.1**。当前 `backend/requirements.lock` 为 FastAPI **0.162.0**、Starlette **1.10.0**、Pydantic **2.14.1**、aiosqlite **0.22.1**；本轮未安装、升级或改动依赖，**锁定依赖验收由 PM 负责**。

以下命令从 `backend` 执行，测试库和日志全部置于忽略的 `.pytest-tmp/` scratchpad，保留离线网络保护：

```powershell
python -m pytest tests/test_shadow_ledger.py tests/test_shadow_api_runtime.py tests/test_shadow_fake.py tests/test_shadow_routes.py tests/test_shadow_stats.py tests/test_shadow_binance_volume.py tests/test_shadow_performance_safety.py tests/test_shadow_review_regressions.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning -p no:cacheprovider --basetemp ../.pytest-tmp/round2-related-final
python -m pytest tests -q -p no:cacheprovider --basetemp ../.pytest-tmp/round2-full-final
python -m pytest tests/test_shadow_benchmark.py -q -s -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/round2-bench-records
```

最终原始汇总（各命令退出码均为 0）：

```text
75 passed in 23.18s
2502 passed, 8 deselected, 12 warnings in 201.85s (0:03:21)
2 passed in 3.30s
```

日志：`.pytest-tmp/round2-related-final.log`、`round2-full-final.log`、`round2-bench-records.log`。本轮早期全量也通过：`2502 passed, 8 deselected, 12 warnings in 181.77s (0:03:01)`；此后只调整快照构造，再执行上面的最终全量。最终 12 条 warning 均为既有 `datetime.utcnow()` 弃用警告，没有 `PytestUnhandledThreadExceptionWarning`。8 项 deselected 来自原有 network/benchmark 筛选规则；影子两个 benchmark 独立运行。没有修改筛选规则、断言、阈值或超时。前端无改动，本轮未重复运行前端测试/tsc；后文前端结果为上一轮历史证据。

### 性能预算：保留 db05329 的全部断言

基准文件 `test_shadow_benchmark.py` 与性能安全测试文件均未修改。所有最终基准都独立运行，不与本轮其他测试并行。逐行资金费恢复增加开销后，初次压力场景墙钟中位 **23.4105 ms**，未通过原 20 ms 门槛；中间几次也失败，分别保留在 `round2-funding-bench*.log`、`round2-benchmark-current.log`、`round2-bench-tuples.log`。未将这些失败记成通过。

修复方式：无结算计划时跳过逐行资金费处理，持仓只缓存各行最早结算时刻用于调度；捕获快照保留已经生成的不可变市场元组，避免“展平、再切片”复制；工作线程对来自固定 itemgetter 的确定宽度元组直接构造记录，避免 `_make` 的逐行 Python 检查。限频包装不额外持有冻结输入，原事件循环计时仍包含输入释放。不可变输入、代数隔离、列表原地修改、精确 DEX 索引、200 行分批重试及内存上限的原回归全部保留。

| 最终指标 | 压力：1000 机会 / 3000 未平行 | 真实：400 机会 / 1000 未平行，1/3 变化 | 原预算 |
| --- | ---: | ---: | --- |
| 事件循环中位 | 2.7911 ms | 0.8181 ms | ≤3 ms |
| 事件循环 P95 | 4.5384 ms | 1.3441 ms | ≤10 ms |
| 事件循环最大（含开仓/检查点） | 7.2537 ms | 2.3064 ms | ≤20 ms |
| 工作线程 CPU 中位 | 15.625 ms | 0 ms | ≤20 / ≤8 ms |
| 工作线程墙钟中位 | 17.8257 ms | 4.8503 ms | ≤20 / ≤8 ms（已有附加断言） |
| WorkingSet 增量 | 25.3086 MiB | 3.3281 MiB | ≤30 MiB |
| 初次开仓工作线程墙钟 | 200.4322 ms | 54.8385 ms | 单独记录 |
| 检查点工作线程墙钟 | 159.1867 ms | 43.0045 ms | 单独记录 |

额外以 scratchpad 脚本从 `git show db05329` 只读加载旧影子模块，同机重跑原基准，未替换工作树文件：`2 passed in 2.81s`，压力/真实规模事件循环中位 2.9809/0.8756 ms、工作线程墙钟 19.6798/4.3834 ms、RSS 26.6563/6.0352 MiB；日志 `round2-bench-baseline.log`。这仅辅助比较，并不能消除主机负载波动。Windows CPU 时钟粒度仍约 15.625 ms，0 中位不是零成本；压力场景余量仍有限，最终全部预算通过不等于生产同构验收。

### 分步提交、范围与后续交接

- `cb2572e` — `fix(shadow): restore funding progress per variant`
- `90b7ea2` — `fix(shadow): fully disable runtime and API data when switched off`
- `d4d6fad` — `fix(shadow): read API data through isolated read-only connections`
- `6eacf39` — `fix(shadow): rate limit failures by category and drain workers on close`（同时修正上述只读测试夹具）
- `e0b7ca8` — `perf(shadow): avoid redundant snapshot copies and record checks`
- 本报告文档提交见分支 `git log`，不自引提交号。

本轮仅改 `shadow_ledger.py`、`shadow_runtime.py`、`execution_routes.py`、`routes_shadow.py`、main 的影子任务注册块、三个影子测试文件和本报告。`config.py`、数据库共享实现、采集调度、历史写入、Astro/arb-mvp、前端和任务书均未改。原任务书在 `db05329..4dfd696` 的 PM 文档变化不属于本轮修改。`git diff --check` 纳入交付检查。

**没有 push、服务器连接、WSL、备份、部署或线上验证，也没有启动真实行情服务。** 线上版本未核验、未更改。保留既有未跟踪/忽略产物，新增日志、测试库和只读对比/性能诊断脚本只放在 `.pytest-tmp/`；交付源文件只暂存本任务文件。

下一步由 PM 进行锁定依赖、独立只读复审及生产同构验收；真实行情、部署仍须另行授权。当前不新增业务规则，也不回填已经发生的历史错账。后续仍以精确 MarketKey/DEX、逐行资金费进度及默认关闭约束继续本模块；无依赖的新模块另开任务。本报告包含本轮交接所需的基线、入口、规则、验证与残余风险，旧交接文件的运行行为以本节为准。

---

以下保留 **2026-11-03 / db05329 的历史报告**，不代表本轮最终行为或重新执行的验收。

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

## 合入已上线部署分支与登录鉴权回归（2026-11-05）

本轮仅在 `codex/shadow-ledger` 工作树本地合并。合并前 HEAD 为 `6d4389db6279b2bc9b66541ee0f9e9886257b96b`，工作区干净；已 fetch 的 `origin/codex/frontend-localization-polish` 核对为 `9a362a824276ec3c6cf6e560fc39e11bd88a1790`。执行 `git merge --no-ff --no-edit origin/codex/frontend-localization-polish`；`--no-edit` 仅使用默认合并说明，保留两父提交合并。

冲突均同时保留两边语义：

- `backend/app/core/config.py`：保留影子配置字段、默认值及 `validate_shadow_settings(self)`，同时保留验证码登录字段、Settings repr 脱敏和会话天数 1–30 的校验。
- `backend/app/main.py`：同时初始化影子运行时与独立认证存储；同时注册影子路由和登录路由，统一使用 `Depends(require_dashboard_password)`。该历史名称的守卫现已委托新 `require_authentication`，关闭时同时释放两类资源。影子消费者及关闭开关逻辑保留。
- 自动合并的 `frontend/src/api/client.ts` 保留三项影子 API 调用，同时改走新 `sessionFetch` 的同源 Cookie 请求。

逐条确认：

1. 当前全部 `/api/shadow*` 路由为 `GET /api/shadow/status`、`GET /api/shadow/trades`、`GET /api/shadow/buckets`，全部经过新会话 Cookie 或 `X-Api-Token` 鉴权。`backend/tests/test_route_permissions.py` 新增三对 method/path 的保留清单断言，并动态遍历所有该前缀下的路由和方法，在生产配置、影子开启/关闭两种状态下分别检查有效 Cookie、有效 Token 返回 200，以及无凭据、旧密码头、无效 Cookie、无效 Token 返回 401，错误体精确为 `{"detail": "Authentication required"}`。仅模拟业务读取，不替换认证守卫或真实 AuthStore。原 118 对保留端点基线、公开端点集合、逐方法 401 及生产缺配置拒绝等断言全部保留。
2. `SHADOW_ENABLED` 默认仍为 0：`Settings.shadow_enabled=False`、`get_settings()` 使用 `bool_env("SHADOW_ENABLED", False)`。扩展原配置测试，明确检查环境变量缺省、显式 `0` 均关闭，显式 `1` 才开启；原配置非法值断言保留。
3. `docker-compose.yml` 固定子网仍为 `172.18.0.0/24`、网关 `172.18.0.1`；后端可信代理默认 `/24` 和前端 nginx 网关 `/32` 保留。现有 `test_login_2fa_review.py::test_proxy_defaults_match_fixed_compose_network` 等配置契约测试通过。认证核心、Compose、前端 nginx 及两份验证码登录测试与目标分支完全一致。

影子 API 原测试仅迁移鉴权准备：配置合成 TOTP、使用真实会话检查，保留所有关闭隐藏、校验错误、事务提交/回滚隔离、只读连接、工作线程及错误计数断言。只读连接观测前预先打开认证存储，仍严格断言一次影子读取仅建立一个 `mode=ro` 连接；没有增加连接计数白名单或放宽断言。

环境：Windows PowerShell、本机 Python 3.12.10；FastAPI 0.135.1、Starlette 0.52.1、Pydantic 2.12.5、aiosqlite 0.22.1、httpx 0.28.1、pytest 8.4.2、pytest-asyncio 0.25.3；Node 24.14.0、npm 11.9.0。使用已有 Python 包和 `frontend/node_modules`，未安装依赖。本轮不替代锁定依赖或线上验收。后端所有命令均在 `backend` 目录执行，沿用项目离线保护和默认 `not network and not benchmark` 选择规则，另单独启用影子 benchmark；未运行 WSL。

首次回归诊断命令：

```powershell
python -m pytest tests/test_route_permissions.py tests/test_shadow_api_runtime.py tests/test_shadow_review_regressions.py -q -p no:cacheprovider --basetemp ../.pytest-tmp/merge-2fa-initial
```

原始汇总（退出码 1；当时影子业务夹具仍使用旧密码头或未配置登录，新权限矩阵已通过）：

```text
7 failed, 47 passed in 20.24s
```

首次完整定向回归迁移后，连接观测把按请求临时创建的认证库连接也计入影子读取，触发原 `len(connections) == 1` 断言；修正夹具初始化顺序，未修改该断言。该次原始汇总（退出码 1）：

```text
1 failed, 182 passed, 2 deselected in 23.80s
```

最终定向回归命令（收集全部 `test_shadow*.py`、权限矩阵及全部 `test_login_2fa*.py`）：

```powershell
$testFiles = @(Get-ChildItem -Path tests/test_shadow*.py | ForEach-Object { $_.FullName })
$testFiles += 'tests/test_route_permissions.py'
$testFiles += @(Get-ChildItem -Path tests/test_login_2fa*.py | ForEach-Object { $_.FullName })
python -m pytest @testFiles -q -p no:cacheprovider --basetemp ../.pytest-tmp/merge-2fa-targeted-final
```

原始汇总（退出码 0；两项 deselected 为后续单独运行的影子性能基准）：

```text
183 passed, 2 deselected in 22.64s
```

额外独立运行影子性能基准，未与其他测试并行：

```powershell
python -m pytest tests/test_shadow_benchmark.py -q -s -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/merge-2fa-benchmark
```

首次原始汇总（退出码 1）：

```text
1 failed, 1 passed in 4.05s
```

失败为压力场景事件循环中位 `3.245200146920979 ms > 3 ms`；该场景 P95 为 4.834 ms、最大 7.511 ms、工作线程墙钟中位 19.248 ms、RSS 增量 26.422 MiB，其他预算通过，真实规模场景通过。`shadow_runtime.py`、`shadow_ledger.py` 和 `test_shadow_benchmark.py` 均与合并前完全一致，没有修改实现、性能断言或阈值。

后端全量命令：

```powershell
python -m pytest -q -p no:cacheprovider --basetemp ../.pytest-tmp/merge-2fa-full
```

原始汇总（退出码 0）：

```text
2586 passed, 8 deselected, 12 warnings in 155.34s (0:02:35)
```

8 项按项目默认 network/benchmark 标记排除，其中 2 项影子 benchmark 已单独运行。12 条 warning 为已有 `datetime.utcnow()` 弃用提示。未扩大排除清单、添加 skip 或降低断言。

全量结束后，再次独立运行同一影子基准源码（与前端测试也不并行）：

```powershell
python -m pytest tests/test_shadow_benchmark.py -q -s -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/merge-2fa-benchmark-final
```

复核原始汇总（退出码 1）：

```text
1 failed, 1 passed in 4.19s
```

压力场景仍在事件循环中位断言失败：`3.1916999723762274 ms > 3 ms`；该次最大耗时为 43.992 ms，也超出既有 20 ms 预算（测试先在中位断言停止），P95 为 4.236 ms、工作线程墙钟中位 19.534 ms、RSS 增量 26.930 MiB。真实规模场景中位 1.083 ms、最大 2.426 ms、工作线程墙钟中位 5.448 ms、RSS 增量 4.434 MiB，全部断言通过。**影子压力性能基准本轮未通过**，不得据此开启生产；两次结果及原阈值保留，原因尚未定位，不能仅凭源码未变断言是机器波动。性能优化不扩展到本次合并任务。

因合并包含前端改动，在 `frontend` 目录运行本地 TypeScript 编译器：

```powershell
.\node_modules\.bin\tsc.cmd --noEmit
```

编译器无诊断输出，执行器原始退出码行：

```text
TSC_EXIT_CODE=0
```

前端全量 Vitest 在后端与性能基准全部结束后单独运行，固定两个 worker 降低本机资源争用：

```powershell
npm.cmd test -- --reporter=dot --maxWorkers=2 --minWorkers=2
```

原始汇总（退出码 0）：

```text
 Test Files  33 passed (33)
      Tests  427 passed (427)
   Duration  234.28s (transform 1.41s, setup 155.02s, collect 72.96s, tests 183.34s, environment 44.57s, prepare 3.93s)
```

包含影子页面、登录、API 客户端、旧 origin 清理及 Hyperliquid DEX 相关前端用例；有 React act 等既有 warning，未修改超时、排除用例或降低断言，合并后前端没有额外补丁。

本轮完整原始日志均留在被忽略的 `.pytest-tmp/`，不纳入提交：`merge-2fa-initial.log`、`merge-2fa-targeted.log`、`merge-2fa-targeted-final.log`、`merge-2fa-benchmark.log`、`merge-2fa-full.log`、`merge-2fa-benchmark-final.log`、`merge-2fa-tsc.log`（空日志）和 `merge-2fa-vitest.log`；测试数据库/临时文件位于同名前缀目录。既有产物没有清理。

Git 范围检查：原报告前缀与合并前完全一致，本轮仅末尾追加；冲突全部解决，保留两父提交；除目标分支变更外，仅包含本轮必要的影子测试迁移、矩阵补充及报告/交接，未包含测试输出、依赖目录、数据库或其他任务文件。`git diff --check` 和暂存差异检查通过。提交号以本分支最终 `git log -1` 为准，交接见 [task-handoff-2026-11-05-shadow-ledger.md](task-handoff-2026-11-05-shadow-ledger.md)。

按本次明确范围，没有 push、连接服务器、备份、部署、线上验证或运行 WSL。认证与功能回归通过，压力性能基准未通过；后续本模块复核与新模块工作请按交接范围分别继续。


## PM 第三轮：测试先行，触发第 11 条停止（2026-11-05）

本轮基线 `b6c999f`，分支 `codex/shadow-ledger`，工作树 `.worktrees/shadow-ledger`。用户已有未提交改动仅为任务书「PM decisions 第三轮」和「第三轮补充」，原文完整随首个提交 `824f771` 一起提交。首个提交还包含等价性反例与 production 基准；没有实现提交，生产代码与基线逐文件相同。

**结论：未完成第三轮功能验收，已按第 11 条停止实现。** 发现除第 10 条 frozen_leg 采样差异以外的容量准入依赖。停止后只完成用户指定的基线验证、证据归档和本地提交，没有自行决定扩容、淘汰规则或降低等价性标准。

### 新阻塞：全局 PriceState 容量影响相关资产评分

入口：`backend/app/services/fake_opportunity.py:104` 的默认 `max_markets=6000`，`:149` 达到上限时丢弃新市场。该上限由所有资产共同占用，不只由相关机会占用；12,000 市场也无法全部各自保有 PriceState。`score` 的新市场准入依赖不相关市场在分钟内的出现顺序。

`test_shadow_prefilter_equivalence.py` 以固定随机种子 `20261105` 构造三轮，测试内全量对照与按第 1/8/9 条选择的候选路径调用同一个现有 scorer/ledger：

1. 分钟边界：两条路径都收到 5997 个市场。
2. +1 秒：新增三个不相关市场，全量路径分配剩余三个名额；分钟采样路径不处理它们。
3. +2 秒：新相关资产在 Binance/Bitget/OKX 的价格为 100/104/100，形成可开仓机会。全量路径因容量满而丢弃三个目标市场；候选路径纳入同资产全部三个市场并命中 median_outlier。

两边开仓数量和时间一致，但全量 `fake_score=0, fake_flag=false, fake_reasons=[]`，候选 `fake_score=2, fake_flag=true, fake_reasons=[median_outlier]`，开仓行保存的 fake 元数据不一致。这会改变默认排除假机会的统计集合。缩小到容量 6 的最小反例也失败；未到容量的对照通过。

这是 **median_outlier 的精确等价性失败**，不是 frozen_leg 的时间误差，不能套用第 10 条豁免。候选选择器仅在测试内建模；没有声称完整预筛选生产路径已实现，也没有将失败测试改为 xfail/skip。UUID 为独立生成，行比较只排除 `id`，其余分项和 tracking 都保留。

PM 待决策：12,000 市场下 PriceState 的容量、准入/淘汰规则，以及容量满时全量与预筛选如何保持精确评分等价。单纯提高容量只能推迟饱和，不能证明更大或动态市场集合下等价；本轮没有擅自提高上限，也没有实现全市场逐轮准入扫描。

停止点发生在等价性测试的容量分支。完整的跨门槛、消失、过期、冷却、max_open、结算、检查点、重启组合序列，以及 BMT 首次越阈 frozen_leg、60 秒采样界限、spread_plateau 清除、混合列表前后端回归，均留待 PM 决策后继续。第 10 条允许的 frozen_leg 差异仍严格限于从未相关资产的采样：changed 最多晚 60 秒，以及分钟内先变后回/高低点可能漏采；本轮没有修改相关生产注释，因为没有实施采样路径。

首次等价性红灯原始汇总（退出码 1）：

```text
SHADOW_EQUIVALENCE {"capacity": 6000, "full": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "full_dropped": 0, "full_price_states": 9, "initial_markets": 3, "opening_metadata_equal": true, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6}
.SHADOW_EQUIVALENCE {"capacity": 6, "full": {"fake_flag": false, "fake_reasons": [], "fake_score": 0}, "full_dropped": 3, "full_price_states": 6, "initial_markets": 3, "opening_metadata_equal": false, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6}
FSHADOW_EQUIVALENCE {"capacity": 6000, "full": {"fake_flag": false, "fake_reasons": [], "fake_score": 0}, "full_dropped": 3, "full_price_states": 6000, "initial_markets": 5997, "opening_metadata_equal": false, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6000}
        print("SHADOW_EQUIVALENCE " + json.dumps({
        print("SHADOW_EQUIVALENCE " + json.dumps({
2 failed, 1 passed in 1.16s
```

### production 基准和最终连续三次当前代码基线

新增场景为 5500 机会、12000 市场、650 个达到 0.2% 门槛、750 对持仓（1500 行）。测试先通过影子表保存 750 对，再由 runtime 正常恢复；100 对持仓当前低于开仓门槛。低开仓价差来自实际 bid/ask，较宽盘口使这些已持仓机会的平仓价差仍高于收敛目标，不是单纯覆盖机会字段。每轮三分之一报价变化。NOW 初始轮、51 个一秒常规轮及 +61 秒分钟全量/检查点轮均经过 runtime；后两类尖峰计入事件循环 p95/max。production 基准在当前尚未预筛选代码上执行，是失败基线，不是优化后验收结果。

运行前负载通过 Windows GetSystemTimes 采样 0.2 秒，记录全系统所有核的繁忙比例。基准进程顺序运行，未与本任务其他测试并行；没有停止其他用户进程或宣称系统独占。RSS 为 Windows 工作集增量。CPU thread_time 量化仍保留既有限制和墙钟保护。

只调整第 5 条明确授权的 stress 事件循环中位 3→5 ms、worker 墙钟 20→30 ms，CPU 中位仍 20 ms。realistic 所有原预算保持，stress p95/max 和所有 RSS 预算保持。production 使用第 4 条预算；没有提高任何失败阈值。

最终命令在 backend 目录逐次运行（N=1,2,3，每次独立 Python 进程）：

```powershell
python -m pytest tests/test_shadow_benchmark.py -q -s --tb=short -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/round3-final-baseline-N
```

| 次数 | 场景 | CPU 前负载 % | 事件循环中位 / p95 / 最大 ms | worker CPU / 墙钟中位 ms | RSS 增量 MiB |
| --- | --- | --- | --- | --- | --- |
| 1 | stress | 21.31 | 4.085 / 5.540 / 8.003 | 15.625 / 24.822 | 26.520 |
| 1 | realistic | 45.56 | 1.481 / 2.232 / 3.025 | 0.000 / 6.988 | 3.816 |
| 1 | production | 56.91 | 28.149 / 149.625 / 162.866 | 203.125 / 198.400 | 70.539 |
| 2 | stress | 26.26 | 4.967 / 6.337 / 10.911 | 31.250 / 28.807 | 26.875 |
| 2 | realistic | 37.36 | 1.195 / 1.829 / 3.679 | 0.000 / 5.645 | 3.637 |
| 2 | production | 32.42 | 124.785 / 158.780 / 172.807 | 125.000 / 121.657 | 70.820 |
| 3 | stress | 13.11 | 4.136 / 5.529 / 12.175 | 31.250 / 25.938 | 26.805 |
| 3 | realistic | 64.48 | 2.318 / 3.317 / 3.960 | 15.625 / 10.464 | 4.465 |
| 3 | production | 53.30 | 34.048 / 167.282 / 171.254 | 218.750 / 232.198 | 70.273 |

production 三次均失败。stress 第一次通过，第二/三次 CPU 中位 31.25 ms 超过 20 ms；realistic 前两次通过，第三次 CPU 15.625 ms 和墙钟 10.464 ms 均超过 8 ms。不从多次结果中挑选通过者。

事件循环主要耗时位于捕获/dispatch（包含 `shadow_runtime.py:35 freeze_snapshot`）：production 三次 capture 中位 27.026 / 123.717 / 32.314 ms，publish 中位 1.252 / 1.233 / 1.380 ms。+61 秒分钟全量轮 capture 为 127.512 / 22.213 / 149.193 ms，publish 为 1.252 / 0.071 / 1.138 ms，合计 128.764 / 22.284 / 150.331 ms；第一、三次超过 40 ms。该轮 worker 墙钟为 325.302 / 172.084 / 354.763 ms。这里测的是原代码全量捕获，不能推断优化后全量轮一定无法达标；本轮停止实现的独立充分依据是容量等价性反例。

worker 包含 `_calculate` 的全量输入重建/显示模型处理、`_process` 的评分/账本/写库；上述字段是 worker 总时间，不能据此宣称其中某个子函数占比。没有把未实测的细分热点当作结论。

最终连续三次原始汇总行（均退出码 1；保留 pytest 点号前缀）：

第 1 次：

```text
SHADOW_BENCHMARK {"capture_median_ms": 3.9775000186637044, "changed_fraction": 1.0, "checkpoint_capture_ms": 4.6681000385433435, "checkpoint_event_loop_ms": 4.948299960233271, "checkpoint_publish_ms": 0.2801999216899276, "checkpoint_worker_wall_ms": 237.83890006598085, "event_loop_max_ms": 8.003200055100024, "event_loop_median_ms": 4.085200023837388, "event_loop_p95_ms": 5.539900157600641, "markets": 3000, "open_event_loop_ms": 8.003200055100024, "open_rows": 3000, "open_worker_wall_ms": 195.9015999455005, "opportunities": 1000, "publish_median_ms": 0.2405000850558281, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 26.51953125, "scenario": "stress", "system_cpu_before_pct": 21.311475409836067, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 26.96078431372549, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 24.822000064887106}
.SHADOW_BENCHMARK {"capture_median_ms": 1.409800024703145, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.6680000117048621, "checkpoint_event_loop_ms": 1.7312000272795558, "checkpoint_publish_ms": 0.06320001557469368, "checkpoint_worker_wall_ms": 45.162800000980496, "event_loop_max_ms": 3.025099984370172, "event_loop_median_ms": 1.481400104239583, "event_loop_p95_ms": 2.2319001145660877, "markets": 1000, "open_event_loop_ms": 2.437699935398996, "open_rows": 1000, "open_worker_wall_ms": 73.10240005608648, "opportunities": 400, "publish_median_ms": 0.06410002242773771, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 3.81640625, "scenario": "realistic", "system_cpu_before_pct": 45.55555555555556, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 7.965686274509804, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 6.987800006754696}
.SHADOW_BENCHMARK {"capture_median_ms": 27.025500079616904, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 127.51200003549457, "checkpoint_event_loop_ms": 128.76360001973808, "checkpoint_publish_ms": 1.2515999842435122, "checkpoint_worker_wall_ms": 325.3019000403583, "event_loop_max_ms": 162.86570008378476, "event_loop_median_ms": 28.148800134658813, "event_loop_p95_ms": 149.62489996105433, "markets": 12000, "open_event_loop_ms": 47.206100076437, "open_rows": 1500, "open_worker_wall_ms": 437.48379999306053, "opportunities": 5500, "publish_median_ms": 1.2515999842435122, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 70.5390625, "scenario": "production", "system_cpu_before_pct": 56.9060773480663, "within_memory_budget": false, "within_time_budget": false, "worker_cpu_mean_ms": 158.70098039215685, "worker_cpu_median_ms": 203.125, "worker_wall_median_ms": 198.39989999309182}
1 failed, 2 passed in 20.36s
```

第 2 次：

```text
SHADOW_BENCHMARK {"capture_median_ms": 4.681600024923682, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.222899977117777, "checkpoint_event_loop_ms": 3.2387999817728996, "checkpoint_publish_ms": 0.015900004655122757, "checkpoint_worker_wall_ms": 250.79710001591593, "event_loop_max_ms": 10.911299963481724, "event_loop_median_ms": 4.967199987731874, "event_loop_p95_ms": 6.336500053294003, "markets": 3000, "open_event_loop_ms": 10.911299963481724, "open_rows": 3000, "open_worker_wall_ms": 291.67269996833056, "opportunities": 1000, "publish_median_ms": 0.22229994647204876, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 26.875, "scenario": "stress", "system_cpu_before_pct": 26.256983240223462, "within_memory_budget": true, "within_time_budget": false, "worker_cpu_mean_ms": 32.169117647058826, "worker_cpu_median_ms": 31.25, "worker_wall_median_ms": 28.807300026528537}
FSHADOW_BENCHMARK {"capture_median_ms": 1.1273999698460102, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.1949000181630254, "checkpoint_event_loop_ms": 1.3043999206274748, "checkpoint_publish_ms": 0.1094999024644494, "checkpoint_worker_wall_ms": 45.324299950152636, "event_loop_max_ms": 3.6790999583899975, "event_loop_median_ms": 1.1948999017477036, "event_loop_p95_ms": 1.8289999570697546, "markets": 1000, "open_event_loop_ms": 3.6790999583899975, "open_rows": 1000, "open_worker_wall_ms": 89.47909995913506, "opportunities": 400, "publish_median_ms": 0.04779989831149578, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 3.63671875, "scenario": "realistic", "system_cpu_before_pct": 37.362637362637365, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 4.901960784313726, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 5.644800025038421}
.SHADOW_BENCHMARK {"capture_median_ms": 123.71749989688396, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 22.213199990801513, "checkpoint_event_loop_ms": 22.284299950115383, "checkpoint_publish_ms": 0.07109995931386948, "checkpoint_worker_wall_ms": 172.08430008031428, "event_loop_max_ms": 172.80709987971932, "event_loop_median_ms": 124.78529987856746, "event_loop_p95_ms": 158.78009994048625, "markets": 12000, "open_event_loop_ms": 45.06260005291551, "open_rows": 1500, "open_worker_wall_ms": 383.2054999656975, "opportunities": 5500, "publish_median_ms": 1.2329999590292573, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 70.8203125, "scenario": "production", "system_cpu_before_pct": 32.417582417582416, "within_memory_budget": false, "within_time_budget": false, "worker_cpu_mean_ms": 162.07107843137254, "worker_cpu_median_ms": 125.0, "worker_wall_median_ms": 121.65740004274994}
2 failed, 1 passed in 21.30s
```

第 3 次：

```text
SHADOW_BENCHMARK {"capture_median_ms": 3.782800049521029, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.0610000248998404, "checkpoint_event_loop_ms": 3.343000076711178, "checkpoint_publish_ms": 0.28200005181133747, "checkpoint_worker_wall_ms": 253.82250000257045, "event_loop_max_ms": 12.175100040622056, "event_loop_median_ms": 4.136100062169135, "event_loop_p95_ms": 5.529399961233139, "markets": 3000, "open_event_loop_ms": 12.175100040622056, "open_rows": 3000, "open_worker_wall_ms": 263.49290006328374, "opportunities": 1000, "publish_median_ms": 0.2172999083995819, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 26.8046875, "scenario": "stress", "system_cpu_before_pct": 13.11475409836066, "within_memory_budget": true, "within_time_budget": false, "worker_cpu_mean_ms": 32.169117647058826, "worker_cpu_median_ms": 31.25, "worker_wall_median_ms": 25.937999947927892}
FSHADOW_BENCHMARK {"capture_median_ms": 2.1915999241173267, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.9307000329717994, "checkpoint_event_loop_ms": 2.0580000709742308, "checkpoint_publish_ms": 0.1273000380024314, "checkpoint_worker_wall_ms": 68.02989996504039, "event_loop_max_ms": 3.9604000048711896, "event_loop_median_ms": 2.317999955266714, "event_loop_p95_ms": 3.3174999989569187, "markets": 1000, "open_event_loop_ms": 3.845999948680401, "open_rows": 1000, "open_worker_wall_ms": 108.13919990323484, "opportunities": 400, "publish_median_ms": 0.11250004172325134, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.46484375, "scenario": "realistic", "system_cpu_before_pct": 64.48087431693989, "within_memory_budget": true, "within_time_budget": false, "worker_cpu_mean_ms": 10.416666666666666, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 10.463700047694147}
FSHADOW_BENCHMARK {"capture_median_ms": 32.31389995198697, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 149.19310004916042, "checkpoint_event_loop_ms": 150.33069998025894, "checkpoint_publish_ms": 1.1375999310985208, "checkpoint_worker_wall_ms": 354.7627000370994, "event_loop_max_ms": 171.25439981464297, "event_loop_median_ms": 34.04839988797903, "event_loop_p95_ms": 167.2823999542743, "markets": 12000, "open_event_loop_ms": 50.852099899202585, "open_rows": 1500, "open_worker_wall_ms": 487.64399997889996, "opportunities": 5500, "publish_median_ms": 1.3797000283375382, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 70.2734375, "scenario": "production", "system_cpu_before_pct": 53.2967032967033, "within_memory_budget": false, "within_time_budget": false, "worker_cpu_mean_ms": 185.66176470588235, "worker_cpu_median_ms": 218.75, "worker_wall_median_ms": 232.19759995117784}
3 failed in 24.02s
```

### 回归结果与环境边界

当前本机 Windows / Python 3.12.10，`venv=False`；pytest 8.4.2、pytest-asyncio 0.25.3、pydantic 2.12.5、aiosqlite 0.22.1。沿用现有环境，没有安装/更新依赖；不能将此称为锁定依赖 venv 验收。

影子相关测试与 `test_route_permissions`（项目默认排除 benchmark/network）：

```powershell
$shadowTests = @(Get-ChildItem tests/test_shadow*.py | ForEach-Object { $_.FullName })
python -m pytest @shadowTests tests/test_route_permissions.py -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3-targeted
```

```text
SHADOW_EQUIVALENCE {"capacity": 6, "full": {"fake_flag": false, "fake_reasons": [], "fake_score": 0}, "full_dropped": 3, "full_price_states": 6, "initial_markets": 3, "opening_metadata_equal": false, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6}
SHADOW_EQUIVALENCE {"capacity": 6000, "full": {"fake_flag": false, "fake_reasons": [], "fake_score": 0}, "full_dropped": 3, "full_price_states": 6000, "initial_markets": 5997, "opening_metadata_equal": false, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6000}
2 failed, 111 passed, 3 deselected in 29.59s
```

后端全量（沿用项目默认 marker，未增加跳过项；benchmark 已单独运行）：

```powershell
python -m pytest -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3-full
```

```text
SHADOW_EQUIVALENCE {"capacity": 6, "full": {"fake_flag": false, "fake_reasons": [], "fake_score": 0}, "full_dropped": 3, "full_price_states": 6, "initial_markets": 3, "opening_metadata_equal": false, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6}
SHADOW_EQUIVALENCE {"capacity": 6000, "full": {"fake_flag": false, "fake_reasons": [], "fake_score": 0}, "full_dropped": 3, "full_price_states": 6000, "initial_markets": 5997, "opening_metadata_equal": false, "selected": {"fake_flag": true, "fake_reasons": ["median_outlier"], "fake_score": 2}, "selected_dropped": 0, "selected_price_states": 6000}
2 failed, 2587 passed, 9 deselected, 12 warnings in 182.35s (0:03:02)
```

以上退出码均为 1。仅新增的两个容量反例失败；既有影子、权限及其他后端用例未放宽。没有在失败的状态下宣称全量通过。未实现展示变化，所以没有运行新混合列表的前端验收。

### 夹具开发期间的非最终测量（保留历史，不计作最终三次）

第一次尝试在 seed 写库前未初始化 shadow schema，production 报 `sqlite3.OperationalError: no such table: shadow_trades`，尚未得到 production 测量。修正测试夹具初始化与异常时关闭连接后继续；没有修改数据库生产代码。该次 pytest 输出后未正常退出，终止了本任务的测试会话，未终止其他进程。

```text
SHADOW_BENCHMARK {"capture_median_ms": 2.983099897392094, "changed_fraction": 1.0, "checkpoint_capture_ms": 2.7350999880582094, "checkpoint_event_loop_ms": 2.8310000197961926, "checkpoint_publish_ms": 0.09590003173798323, "checkpoint_worker_wall_ms": 166.08260001521558, "event_loop_max_ms": 39.44369999226183, "event_loop_median_ms": 3.1049998942762613, "event_loop_p95_ms": 4.606300033628941, "markets": 3000, "open_event_loop_ms": 6.984100094996393, "open_rows": 3000, "open_worker_wall_ms": 196.02279993705451, "opportunities": 1000, "publish_median_ms": 0.14739995822310448, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 26.8046875, "scenario": "stress", "system_cpu_before_pct": 28.57142857142857, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 21.752450980392158, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 19.368099980056286}
FSHADOW_BENCHMARK {"capture_median_ms": 1.641599927097559, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.9271000055596232, "checkpoint_event_loop_ms": 1.936799963004887, "checkpoint_publish_ms": 0.009699957445263863, "checkpoint_worker_wall_ms": 54.04289998114109, "event_loop_max_ms": 3.87450004927814, "event_loop_median_ms": 1.7032999312505126, "event_loop_p95_ms": 2.5334000820294023, "markets": 1000, "open_event_loop_ms": 2.5334999663755298, "open_rows": 1000, "open_worker_wall_ms": 83.439200068824, "opportunities": 400, "publish_median_ms": 0.06929994560778141, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.22265625, "scenario": "realistic", "system_cpu_before_pct": 51.39664804469274, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 7.659313725490196, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 7.496600039303303}
            print("SHADOW_BENCHMARK " + json.dumps(result, sort_keys=True))
2 failed, 1 passed in 5.50s
```

随后三次初版夹具只覆盖机会的 open_spread_pct 字段，当前盘口仍对应 0.5%。为避免不自洽的输入，最终夹具改用实际盘口构造低于门槛机会，再完整连续重跑三次；没有修改预算。初版结果不能代替上面的最终证据，原始行保留如下：

初版第 1 次：

```text
SHADOW_BENCHMARK {"capture_median_ms": 2.8753000078722835, "changed_fraction": 1.0, "checkpoint_capture_ms": 2.3577999090775847, "checkpoint_event_loop_ms": 2.4712998420000076, "checkpoint_publish_ms": 0.11349993292242289, "checkpoint_worker_wall_ms": 191.18389999493957, "event_loop_max_ms": 7.270799833349884, "event_loop_median_ms": 3.175400081090629, "event_loop_p95_ms": 4.462799872271717, "markets": 3000, "open_event_loop_ms": 7.270799833349884, "open_rows": 3000, "open_worker_wall_ms": 188.74470004811883, "opportunities": 1000, "publish_median_ms": 0.13689999468624592, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.1484375, "scenario": "stress", "system_cpu_before_pct": 20.441988950276247, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 23.897058823529413, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 18.867700011469424}
.SHADOW_BENCHMARK {"capture_median_ms": 0.9867000626400113, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 0.8078999817371368, "checkpoint_event_loop_ms": 0.8577998960390687, "checkpoint_publish_ms": 0.04989991430193186, "checkpoint_worker_wall_ms": 30.914500006474555, "event_loop_max_ms": 2.5740000419318676, "event_loop_median_ms": 1.0187000734731555, "event_loop_p95_ms": 1.6497999895364046, "markets": 1000, "open_event_loop_ms": 2.5740000419318676, "open_rows": 1000, "open_worker_wall_ms": 57.97770002391189, "opportunities": 400, "publish_median_ms": 0.04989991430193186, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.0703125, "scenario": "realistic", "system_cpu_before_pct": 10.857142857142854, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 4.595588235294118, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 4.696300020441413}
.SHADOW_BENCHMARK {"capture_median_ms": 16.74660004209727, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 114.67289994470775, "checkpoint_event_loop_ms": 115.45269994530827, "checkpoint_publish_ms": 0.7798000006005168, "checkpoint_worker_wall_ms": 254.07929997891188, "event_loop_max_ms": 123.75789997167885, "event_loop_median_ms": 17.616599914617836, "event_loop_p95_ms": 121.08300009276718, "markets": 12000, "open_event_loop_ms": 44.51489995699376, "open_rows": 1500, "open_worker_wall_ms": 408.4302999544889, "opportunities": 5500, "publish_median_ms": 0.9050999069586396, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 66.8671875, "scenario": "production", "system_cpu_before_pct": 13.812154696132595, "within_memory_budget": false, "within_time_budget": false, "worker_cpu_mean_ms": 134.19117647058823, "worker_cpu_median_ms": 171.875, "worker_wall_median_ms": 173.4611999709159}
1 failed, 2 passed in 16.02s
```

初版第 2 次：

```text
SHADOW_BENCHMARK {"capture_median_ms": 2.5124000385403633, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.0263999942690134, "checkpoint_event_loop_ms": 3.2001000363379717, "checkpoint_publish_ms": 0.17370004206895828, "checkpoint_worker_wall_ms": 159.00159999728203, "event_loop_max_ms": 8.482399978674948, "event_loop_median_ms": 2.654799958691001, "event_loop_p95_ms": 3.5252999514341354, "markets": 3000, "open_event_loop_ms": 8.482399978674948, "open_rows": 3000, "open_worker_wall_ms": 216.14060003776103, "opportunities": 1000, "publish_median_ms": 0.11929997708648443, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.33984375, "scenario": "stress", "system_cpu_before_pct": 19.444444444444443, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 18.38235294117647, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 16.630499972961843}
.SHADOW_BENCHMARK {"capture_median_ms": 0.7553999312222004, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 0.6925000343471766, "checkpoint_event_loop_ms": 0.7284000748768449, "checkpoint_publish_ms": 0.03590004052966833, "checkpoint_worker_wall_ms": 29.89760006312281, "event_loop_max_ms": 2.5748000480234623, "event_loop_median_ms": 0.8134000236168504, "event_loop_p95_ms": 1.2271999148651958, "markets": 1000, "open_event_loop_ms": 2.5748000480234623, "open_rows": 1000, "open_worker_wall_ms": 50.93160003889352, "opportunities": 400, "publish_median_ms": 0.06469991058111191, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.06640625, "scenario": "realistic", "system_cpu_before_pct": 22.22222222222222, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 4.28921568627451, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 4.090600064955652}
.SHADOW_BENCHMARK {"capture_median_ms": 112.4412000644952, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 109.40209997352213, "checkpoint_event_loop_ms": 110.12900003697723, "checkpoint_publish_ms": 0.7269000634551048, "checkpoint_worker_wall_ms": 248.8431999227032, "event_loop_max_ms": 137.24090007599443, "event_loop_median_ms": 113.454700098373, "event_loop_p95_ms": 121.573599986732, "markets": 12000, "open_event_loop_ms": 31.691799988038838, "open_rows": 1500, "open_worker_wall_ms": 404.9447999568656, "opportunities": 5500, "publish_median_ms": 0.9261999512091279, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 67.07421875, "scenario": "production", "system_cpu_before_pct": 25.730994152046783, "within_memory_budget": false, "within_time_budget": false, "worker_cpu_mean_ms": 111.21323529411765, "worker_cpu_median_ms": 78.125, "worker_wall_median_ms": 70.86829992476851}
1 failed, 2 passed in 15.18s
```

初版第 3 次：

```text
SHADOW_BENCHMARK {"capture_median_ms": 2.5668999878689647, "changed_fraction": 1.0, "checkpoint_capture_ms": 2.5025999639183283, "checkpoint_event_loop_ms": 2.641999861225486, "checkpoint_publish_ms": 0.13939989730715752, "checkpoint_worker_wall_ms": 159.3948999652639, "event_loop_max_ms": 38.060100050643086, "event_loop_median_ms": 2.6779999025166035, "event_loop_p95_ms": 3.9048000471666455, "markets": 3000, "open_event_loop_ms": 7.7809999929741025, "open_rows": 3000, "open_worker_wall_ms": 187.10940005257726, "opportunities": 1000, "publish_median_ms": 0.1303000608459115, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 26.9453125, "scenario": "stress", "system_cpu_before_pct": 10.982658959537572, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 19.301470588235293, "worker_cpu_median_ms": 15.625, "worker_wall_median_ms": 16.70629996806383}
FSHADOW_BENCHMARK {"capture_median_ms": 1.0282000293955207, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 0.8529000915586948, "checkpoint_event_loop_ms": 0.8604001486673951, "checkpoint_publish_ms": 0.007500057108700275, "checkpoint_worker_wall_ms": 28.56529993005097, "event_loop_max_ms": 3.323900164104998, "event_loop_median_ms": 1.0907999239861965, "event_loop_p95_ms": 1.3589001027867198, "markets": 1000, "open_event_loop_ms": 3.323900164104998, "open_rows": 1000, "open_worker_wall_ms": 53.10999997891486, "opportunities": 400, "publish_median_ms": 0.04179996903985739, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.234375, "scenario": "realistic", "system_cpu_before_pct": 65.38461538461539, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 3.982843137254902, "worker_cpu_median_ms": 0.0, "worker_wall_median_ms": 4.601299995556474}
.SHADOW_BENCHMARK {"capture_median_ms": 16.35529997292906, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 16.340099973604083, "checkpoint_event_loop_ms": 17.366799875162542, "checkpoint_publish_ms": 1.0266999015584588, "checkpoint_worker_wall_ms": 148.58139993157238, "event_loop_max_ms": 148.48420000635087, "event_loop_median_ms": 17.18199998140335, "event_loop_p95_ms": 135.69500006269664, "markets": 12000, "open_event_loop_ms": 31.728699919767678, "open_rows": 1500, "open_worker_wall_ms": 313.91530006658286, "opportunities": 5500, "publish_median_ms": 0.8248001104220748, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 66.28515625, "scenario": "production", "system_cpu_before_pct": 16.763005780346816, "within_memory_budget": false, "within_time_budget": false, "worker_cpu_mean_ms": 128.9828431372549, "worker_cpu_median_ms": 156.25, "worker_wall_median_ms": 170.2182999579236}
2 failed, 1 passed in 15.17s
```

### 交付边界

仅提交测试、原任务书两段决策和报告/交接；`backend/app`、数据库 schema/repositories、采集器、Astro/arb-mvp、前端及默认 SHADOW_ENABLED 均无修改。完整日志/数据库留在忽略目录 `.pytest-tmp/round3-*`，不提交测试输出文件、不清理既有产物。`git diff --check` 通过；本地测试提交为 `824f771`，后续文档提交记录见 `git log`。

没有 push、服务器连接、线上备份、部署、线上验证或 WSL 操作。线上状态未检查；SHADOW_ENABLED 默认仍为 0。后续先由 PM 解决容量准入与精确等价性的冲突，再继续本模块；其他无依赖模块另开任务。交接见 `docs/task-handoff-2026-11-05-shadow-ledger.md` 的第三轮追加章节。


## 第三轮补充二实施与本地验收（2026-11-05，续 929a3f1）

本节覆盖 PM 第三轮第 1–17 条，取代前节“尚未实现/等待容量决策”的继续入口。基线 `929a3f1`，指定 shadow-ledger 工作树，分支 `codex/shadow-ledger`。用户未提交的补充二原样随首个实现提交 `1c76a16` 提交。

**结论：生产实现及功能回归已完成，但三次 production 基准均未通过，按第 17 条停止性能改动，等待 PM 决定后续方案。** 未放宽预算、未自行加节流、未引入独立进程、未挑选重跑结果。第 16 条未触发，production RSS 三次均 ≤30 MiB。

### 生产实现与所有权

- `ShadowRuntime.submit` 仅冻结达到开仓门槛或已持仓 id 的机会；worker 将持仓 id/精确市场 key 以 frozenset 发布，事件循环不遍历可变持仓。即使机会消失，全部持仓腿仍参与当前轮。
- `_select_markets` 在 worker 建立当前快照精确 key 查找表，保留 exchange/type/raw symbol/DEX/倍率；相关两腿和全部持仓腿直接从该表取得。资产→市场索引在分钟轮 worker 构建，普通轮事件循环不扫描全市场。查找 CPU/墙钟计入 worker 总预算。
- 同资产扩展市场每轮送评分器。新出现的同资产市场最多晚 60 秒进入 median 投票，这是第 15 条新增豁免；精确机会腿和持仓腿不受此延迟影响。
- 每个新分钟按最多 1000 个来源市场一片捕获非相关历史，片间 `asyncio.sleep(0)`。single-flight 覆盖查找、分片、计算和发布，期间新轮直接计入 skipped_rounds。相关盘口分片前单独捕获；后续分片只能更新 frozen_leg 历史，不覆盖当轮账本盘口或 median 现价。
- 行情所有权依据：`collector._store_adapter_result` 用 `pack` 保存；`_combined_exchange_markets` 每轮 `unpack_async` 重建模型及可变列表；身份 `annotate` 返回 `model_copy`，API 刷新替换 store 列表；PublishedSnapshot 是 frozen dataclass + tuple。生产发布后无原地行情修改路径。模型类型自身并非 frozen，故保留相关字段/列表的冻结，只在 worker 查找期间读取发布引用。新增压缩缓存解包隔离测试，原有 worker 运行中修改源字段/列表的测试未放宽且通过。
- 混合发布按原顺序逐 id 替换相关对象，非相关对象、数量、默认空标注保持不变；输入/展示缓存只含相关机会。API JSON、告警判断和前端列表增加回归。history/index 等 mailbox 消费者仍接收原 PublishedSnapshot；store 的 API、stream、health、按 id 查找仍接收 Opportunity 模型，没有引入新行类型。

### 容量、评分与等价性

- PriceState 默认上限 20000；`scorer_price_states` 和既有 `scorer_dropped` 由 worker 发布到 status。饱和时保护全部相关资产，按 seen 淘汰最旧非相关市场；没有可淘汰市场才 dropped。每轮最多构造一个有界候选堆，准入时不逐次全表扫描；同轮 seen 更新通过堆顶校验维持顺序。
- 精确身份 LRU 容量从 10000 改为 20000，避免 12000 市场逐轮查找时缓存抖动；没有修改资源预算。824f771 的两个容量反例改为优先准入及完整投票语义。原 `test_shadow_fake.py` 未修改，其 2 市场/1 序列、dropped=2 的断言继续通过。
- 非相关资产分钟采样的 changed 最多比全量路径晚 60 秒；分钟内先变后回及高低点可能遗漏。分片之间允许相差一个捕获周期；仅影响 frozen_leg 历史。代码注释及 59 秒偏差回归落实第 10/14 条。
- 固定种子 `20261105` 的组合序列用实际生产 runtime 对照测试内全量评分/账本，逐轮比较全部持久化交易字段（除随机 UUID）、各变体持仓及 funding 状态、冷却和丢弃计数。覆盖门槛上下穿、部分变体平仓、机会消失、缺失/过期盘口、max_open、资金费结算、检查点、真实 SQLite 持久化后重启、冷却拒绝及再次开仓。
- 单独回归：BMT 冻结 16 分钟、此前可成交价差低于门槛，首次越阈开仓行立即带 frozen_leg；新同资产第三交易所最迟下一分钟参与 median；plateau 满 60 样本后，机会消失保留历史、跌破门槛立即清除，重新出现不误报。
- 全量对照仅存在于测试，生产无切换开关。未修改 schema/repositories、采集调度、opportunity_history、Astro/arb-mvp。

### 功能验收与环境

- 影子相关测试 + test_route_permissions：`121 passed, 3 deselected in 18.04s`；3 个 benchmark 按原项目默认 marker 排除，随后单独连续执行。
- 后端全量：`2597 passed, 9 deselected, 12 warnings in 178.90s (0:02:58)`；默认排除 network/benchmark，未新增跳过。12 条均为既有 datetime.utcnow 弃用警告，无 PytestUnhandledThreadExceptionWarning。
- 前端 OpportunityTable + ShadowLedgerPage：17 passed；前端全量：33 文件、428 测试通过，110.77 秒；有 React/Ant Design act 警告。
- `npm.cmd run build` 成功，含 `tsc -b` 与 Vite production build；Vite 阶段 8.28 秒。
- Windows，Python 3.12.10，pytest 8.4.2，pydantic 2.12.5，aiosqlite 0.22.1；venv=False，沿用现有依赖，未安装/升级。这不是锁定依赖 venv 的独立验收。
- 本地提交：`1c76a16`（实现、补充二、组合等价性等），`c443c8b`（分片/准入/混合发布回归和基准计量）；本报告与交接另有文档提交，见 git log。独立只读审查仍需后续安排，本轮仅做本地自查。

测试命令（后端从 backend、前端从 frontend 执行，所有 pytest 临时路径在忽略的 scratchpad）：

```powershell
$shadowTests = @(Get-ChildItem tests/test_shadow*.py | ForEach-Object { $_.FullName })
python -m pytest @shadowTests tests/test_route_permissions.py -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3b-targeted
python -m pytest -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3b-full
npm.cmd test
npm.cmd run build
```

### 连续三次最终基准：未通过

回归和构建全部结束后，在 backend 目录依次运行 N=1,2,3；每次独立 Python 进程，每次记录运行前系统 CPU 负载，没有额外重跑：

```powershell
python -m pytest tests/test_shadow_benchmark.py -q -s --tb=short -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/round3b-benchmark-N
```

production 最大值按第 14 条计连续占用片段；中位/p95 仍按整轮各片累计占用 + 混合发布统计，累计单轮最大值保留为 event_loop_total_max_ms。stress/realistic 最大值仍用原整轮口径。所有数值预算保持本轮基线不变；第 5 条 stress 中位 5 ms/墙钟 30 ms 已在 824f771 中落地。原字段 within_time_budget 只表示 worker 判定，不代表整个测试通过，事件循环有独立严格断言。

| 次数 | 场景 | 系统 CPU 前负载 % | loop 中位 / p95 / 最大 ms | worker CPU / 墙钟中位 ms | RSS MiB |
| --- | --- | --- | --- | --- | --- |
| 1 | stress | 14.365 | 4.050 / 5.321 / 36.349 | 15.625 / 20.625 | 27.059 |
| 1 | realistic | 16.571 | 1.283 / 1.659 / 2.604 | 0.000 / 6.017 | 4.535 |
| 1 | production | 25.000 | 6.204 / 9.632 / 70.711 | 15.625 / 18.552 | 28.105 |
| 2 | stress | 25.414 | 4.028 / 6.153 / 38.333 | 15.625 / 21.330 | 27.078 |
| 2 | realistic | 17.318 | 1.171 / 1.729 / 2.745 | 0.000 / 5.696 | 4.676 |
| 2 | production | 17.222 | 6.232 / 8.405 / 8.805 | 15.625 / 18.764 | 27.969 |
| 3 | stress | 6.952 | 3.872 / 5.107 / 38.322 | 15.625 / 21.008 | 27.340 |
| 3 | realistic | 7.182 | 1.225 / 2.025 / 2.865 | 0.000 / 5.404 | 4.340 |
| 3 | production | 9.884 | 6.141 / 8.787 / 8.715 | 15.625 / 18.376 | 27.168 |

耗时分布与残余问题：

- production loop 中位 6.204 / 6.232 / 6.141 ms >5，三次均失败；p95 9.632 / 8.405 / 8.787 ms 均 ≤15。首次最大连续占用 70.711 ms >40，位于 +61 秒分钟历史捕获片；后两次最大 8.805 / 8.715 ms。片大小限制了输入数量，仍不能保证实际连续占用预算。
- production capture 中位 4.885 / 5.039 / 4.971 ms，publish 中位 1.174 / 1.174 / 1.136 ms；普通轮成本集中于预筛选/冻结及混合列表发布。分项中位不应直接相加作为整轮中位。
- production worker 总墙钟中位 18.552 / 18.764 / 18.376 ms，均 ≤30；全量 key 查找/相关集合选择中位 8.596 / 8.870 / 8.475 ms，计算中位 10.094 / 10.462 / 10.023 ms。计算子阶段 prepare 约 1.82–1.96 ms、score 约 1.92–2.03 ms、routes 约 0.64–0.69 ms、ledger/flush 约 0.95–0.99 ms；剩余包含模型构造、展示缓存及循环包装，未对未单测子函数做精确归因。
- production 分钟轮 worker 墙钟 105.577 / 190.135 / 162.855 ms；评分子阶段 25.671 / 27.456 / 23.404 ms，ledger/flush 54.478 / 53.218 / 51.598 ms。首次 70.711 ms 捕获尖峰未做 GC/调度归因，不将猜测当作证据。
- production RSS 28.105 / 27.969 / 27.168 MiB，均通过但余量有限。实测保留 12000 PriceState、6000 资产索引条目、750 展示缓存条目、1500 未平行；20000 是容量上限，不是该场景实际占用。
- stress 三次 worker CPU 中位 15.625 ms、墙钟 20.625 / 21.330 / 21.008 ms 均通过，但 loop 最大 36.349 / 38.333 / 38.322 ms >20，三次失败。realistic 三次所有断言通过。

九条原始汇总行如下，保留 pytest 点号/失败前缀；三次 pytest 均退出码 1：

第 1 次，pytest 退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 17.754499916918576, "capture_median_ms": 3.5342000192031264, "changed_fraction": 1.0, "checkpoint_capture_ms": 2.748599974438548, "checkpoint_capture_segments_ms": [2.57210002746433, 0.05859998054802418, 0.05619996227324009, 0.061700004152953625], "checkpoint_event_loop_ms": 3.423699992708862, "checkpoint_publish_ms": 0.6751000182703137, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 114.52549998648465, "prepare_ms": 4.778799950145185, "routes_ms": 36.01289994549006, "score_ms": 6.623600027523935}, "checkpoint_worker_wall_ms": 169.36499997973442, "display_cache_entries": 1000, "event_loop_max_ms": 36.349200177937746, "event_loop_median_ms": 4.049699986353517, "event_loop_p95_ms": 5.321099888533354, "event_loop_total_max_ms": 36.349200177937746, "markets": 3000, "open_event_loop_ms": 8.847099961712956, "open_rows": 3000, "open_worker_wall_ms": 201.9923001062125, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.4814999410882592, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.05859375, "scenario": "stress", "select_wall_median_ms": 2.9352999990805984, "system_cpu_before_pct": 14.364640883977897, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 22.36519607843137, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 3.268800093792379, "prepare_ms": 3.7391999503597617, "routes_ms": 0.772200059145689, "score_ms": 5.539899924769998}, "worker_wall_median_ms": 20.62520000617951}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 5.0629000179469585, "capture_median_ms": 1.0474000591784716, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 0.9945998899638653, "checkpoint_capture_segments_ms": [0.9419999551028013, 0.05259993486106396], "checkpoint_event_loop_ms": 1.1947999009862542, "checkpoint_publish_ms": 0.20020001102238894, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 24.655900080688298, "prepare_ms": 1.6349999932572246, "routes_ms": 0.5049000028520823, "score_ms": 2.059000078588724}, "checkpoint_worker_wall_ms": 32.65029995236546, "display_cache_entries": 400, "event_loop_max_ms": 2.6036000344902277, "event_loop_median_ms": 1.2829001061618328, "event_loop_p95_ms": 1.6585999401286244, "event_loop_total_max_ms": 2.6036000344902277, "markets": 1000, "open_event_loop_ms": 2.6036000344902277, "open_rows": 1000, "open_worker_wall_ms": 55.631899973377585, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.22030004765838385, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.53515625, "scenario": "realistic", "select_wall_median_ms": 0.9265999542549253, "system_cpu_before_pct": 16.57142857142857, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 4.595588235294118, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.39840000681579113, "prepare_ms": 1.01200002245605, "routes_ms": 0.3152000717818737, "score_ms": 0.8443000260740519}, "worker_wall_median_ms": 6.01669994648546}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.09420002810657, "capture_median_ms": 4.8853999469429255, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 84.12849972955883, "checkpoint_capture_segments_ms": [4.5897999079898, 0.07259997073560953, 0.46520004980266094, 1.6021999763324857, 1.089200028218329, 70.71129989344627, 0.7689000340178609, 0.7495000027120113, 0.7872999412938952, 0.7302999729290605, 0.7392000406980515, 0.7475999882444739, 1.0753999231383204], "checkpoint_event_loop_ms": 86.44719969015568, "checkpoint_publish_ms": 2.3186999605968595, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 54.477799916639924, "prepare_ms": 3.113799961283803, "routes_ms": 0.6967000663280487, "score_ms": 25.670900009572506}, "checkpoint_worker_wall_ms": 105.57709995191544, "display_cache_entries": 750, "event_loop_max_ms": 70.71129989344627, "event_loop_median_ms": 6.204100092872977, "event_loop_p95_ms": 9.632100001908839, "event_loop_total_max_ms": 86.44719969015568, "markets": 12000, "open_event_loop_ms": 20.94440010841936, "open_rows": 1500, "open_worker_wall_ms": 251.9046999514103, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1741999769583344, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 28.10546875, "scenario": "production", "select_wall_median_ms": 8.595899911597371, "system_cpu_before_pct": 25.0, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 21.139705882352942, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9504999034106731, "prepare_ms": 1.8224999075755477, "routes_ms": 0.6418999982997775, "score_ms": 1.9169999286532402}, "worker_wall_median_ms": 18.552300054579973}
2 failed, 1 passed in 7.41s
```

第 2 次，pytest 退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 18.341299961321056, "capture_median_ms": 3.479299950413406, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.664399730041623, "checkpoint_capture_segments_ms": [3.3480998827144504, 0.10249996557831764, 0.10649999603629112, 0.10729988571256399], "checkpoint_event_loop_ms": 4.255599691532552, "checkpoint_publish_ms": 0.5911999614909291, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 107.82769997604191, "prepare_ms": 5.715099978260696, "routes_ms": 42.67719993367791, "score_ms": 7.414399995468557}, "checkpoint_worker_wall_ms": 172.19119996298105, "display_cache_entries": 1000, "event_loop_max_ms": 38.33250002935529, "event_loop_median_ms": 4.02809982188046, "event_loop_p95_ms": 6.15289993584156, "event_loop_total_max_ms": 38.33250002935529, "markets": 3000, "open_event_loop_ms": 8.795899921096861, "open_rows": 3000, "open_worker_wall_ms": 200.17720002215356, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.49999996554106474, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.078125, "scenario": "stress", "select_wall_median_ms": 3.0607000226154923, "system_cpu_before_pct": 25.41436464088398, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 23.284313725490197, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 3.720299922861159, "prepare_ms": 3.892799955792725, "routes_ms": 0.8036000654101372, "score_ms": 5.5327999871224165}, "worker_wall_median_ms": 21.330100018531084}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 4.80919994879514, "capture_median_ms": 0.9590999688953161, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 0.9171999990940094, "checkpoint_capture_segments_ms": [0.866799964569509, 0.05040003452450037], "checkpoint_event_loop_ms": 1.1445999843999743, "checkpoint_publish_ms": 0.22739998530596495, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 25.615100050345063, "prepare_ms": 0.978299998678267, "routes_ms": 0.31459995079785585, "score_ms": 1.7336000455543399}, "checkpoint_worker_wall_ms": 31.56089992262423, "display_cache_entries": 400, "event_loop_max_ms": 2.744999947026372, "event_loop_median_ms": 1.1710000690072775, "event_loop_p95_ms": 1.729399897158146, "event_loop_total_max_ms": 2.744999947026372, "markets": 1000, "open_event_loop_ms": 2.744999947026372, "open_rows": 1000, "open_worker_wall_ms": 55.361300124786794, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.20889996085315943, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.67578125, "scenario": "realistic", "select_wall_median_ms": 0.8672000840306282, "system_cpu_before_pct": 17.31843575418994, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 5.208333333333333, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.4000000189989805, "prepare_ms": 0.9880000725388527, "routes_ms": 0.32130000181496143, "score_ms": 0.8304000366479158}, "worker_wall_median_ms": 5.696399952284992}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.462000034749508, "capture_median_ms": 5.038700066506863, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 15.210800105705857, "checkpoint_capture_segments_ms": [4.881200031377375, 0.0610999995842576, 0.47610001638531685, 1.4513999922201037, 1.225799904204905, 1.6293999506160617, 0.7405000505968928, 0.7613999769091606, 0.7381000323221087, 0.7412999402731657, 0.7611000910401344, 0.7255000527948141, 1.0179000673815608], "checkpoint_event_loop_ms": 17.403400037437677, "checkpoint_publish_ms": 2.19259993173182, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 53.218499990180135, "prepare_ms": 2.2264999570325017, "routes_ms": 0.7621999830007553, "score_ms": 27.45579998008907}, "checkpoint_worker_wall_ms": 190.13479992281646, "display_cache_entries": 750, "event_loop_max_ms": 8.804500102996826, "event_loop_median_ms": 6.2324999598786235, "event_loop_p95_ms": 8.405300206504762, "event_loop_total_max_ms": 20.45209996867925, "markets": 12000, "open_event_loop_ms": 20.45209996867925, "open_rows": 1500, "open_worker_wall_ms": 155.05890001077205, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1735999723896384, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.96875, "scenario": "production", "select_wall_median_ms": 8.870399906300008, "system_cpu_before_pct": 17.22222222222223, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 19.91421568627451, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9902000892907381, "prepare_ms": 1.9616000354290009, "routes_ms": 0.6863001035526395, "score_ms": 2.034100005403161}, "worker_wall_median_ms": 18.764100037515163}
2 failed, 1 passed in 7.37s
```

第 3 次，pytest 退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 17.720800009556115, "capture_median_ms": 3.395100007764995, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.6863001296296716, "checkpoint_capture_segments_ms": [3.0608000233769417, 0.21650001872330904, 0.18690002616494894, 0.2221000613644719], "checkpoint_event_loop_ms": 4.17700014077127, "checkpoint_publish_ms": 0.4907000111415982, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 107.00930003076792, "prepare_ms": 3.346299985423684, "routes_ms": 35.8275999315083, "score_ms": 5.76349999755621}, "checkpoint_worker_wall_ms": 159.59679998923093, "display_cache_entries": 1000, "event_loop_max_ms": 38.322399952448905, "event_loop_median_ms": 3.8717000279575586, "event_loop_p95_ms": 5.106800002977252, "event_loop_total_max_ms": 38.322399952448905, "markets": 3000, "open_event_loop_ms": 9.033299866132438, "open_rows": 3000, "open_worker_wall_ms": 198.28509993385524, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.4848999669775367, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.33984375, "scenario": "stress", "select_wall_median_ms": 2.7666999958455563, "system_cpu_before_pct": 6.951871657754005, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 23.284313725490197, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 3.256899886764586, "prepare_ms": 3.588199964724481, "routes_ms": 0.798500026576221, "score_ms": 5.577299976721406}, "worker_wall_median_ms": 21.008400013670325}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 4.526100005023181, "capture_median_ms": 1.0011999402195215, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.0399001184850931, "checkpoint_capture_segments_ms": [0.9874000679701567, 0.05250005051493645], "checkpoint_event_loop_ms": 1.2624001828953624, "checkpoint_publish_ms": 0.22250006441026926, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 25.480000069364905, "prepare_ms": 0.8787000551819801, "routes_ms": 0.3109999233856797, "score_ms": 1.8217000178992748}, "checkpoint_worker_wall_ms": 31.413299962878227, "display_cache_entries": 400, "event_loop_max_ms": 2.865399932488799, "event_loop_median_ms": 1.224899897351861, "event_loop_p95_ms": 2.0249999361112714, "event_loop_total_max_ms": 2.865399932488799, "markets": 1000, "open_event_loop_ms": 2.865399932488799, "open_rows": 1000, "open_worker_wall_ms": 55.05639989860356, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.2128999913111329, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.33984375, "scenario": "realistic", "select_wall_median_ms": 0.8362999651581049, "system_cpu_before_pct": 7.182320441988955, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 2.7573529411764706, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.3790999762713909, "prepare_ms": 0.9295000927522779, "routes_ms": 0.3007999621331692, "score_ms": 0.8234999841079116}, "worker_wall_median_ms": 5.404299939982593}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.022800066508353, "capture_median_ms": 4.971499904058874, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 13.131599873304367, "checkpoint_capture_segments_ms": [4.007900017313659, 0.05589995998889208, 0.448299921117723, 0.8429000154137611, 0.7911999709904194, 1.1011000024154782, 1.0131000308319926, 0.8368999697268009, 0.7912999717518687, 0.7618999807164073, 0.736100017093122, 0.7376000285148621, 1.0073999874293804], "checkpoint_event_loop_ms": 14.287199941463768, "checkpoint_publish_ms": 1.1556000681594014, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 51.59839999396354, "prepare_ms": 2.0168001065030694, "routes_ms": 0.8471999317407608, "score_ms": 23.403899976983666}, "checkpoint_worker_wall_ms": 162.85530000459403, "display_cache_entries": 750, "event_loop_max_ms": 8.715100004337728, "event_loop_median_ms": 6.140699959360063, "event_loop_p95_ms": 8.787200087681413, "event_loop_total_max_ms": 21.51169953867793, "markets": 12000, "open_event_loop_ms": 21.51169953867793, "open_rows": 1500, "open_worker_wall_ms": 193.08619992807508, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1356000322848558, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.16796875, "scenario": "production", "select_wall_median_ms": 8.475000038743019, "system_cpu_before_pct": 9.883720930232553, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 17.769607843137255, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9846000466495752, "prepare_ms": 1.868200022727251, "routes_ms": 0.6754000205546618, "score_ms": 1.931800041347742}, "worker_wall_median_ms": 18.375700106844306}
2 failed, 1 passed in 7.29s
```


### 停止与交付边界

按第 17 条停止，先由 PM 决定后续性能方案，包括是否允许固定周期处理；本任务未自行节流、放宽断言或更改预算。后续生产代码若改动，需重跑相称回归与完整连续三次基准。

没有 push、服务器连接、WSL、线上备份、部署、实时公网行情运行或修改其他进程。线上状态未检查，SHADOW_ENABLED 默认仍为 0。工作区外仅因本地提交写入已授权的 Git worktree 元数据。原始日志、SQLite 和临时目录留在忽略目录 `.pytest-tmp/round3b-*`，不提交测试输出文件；本报告只嵌入要求的原始汇总行。既有产物不清理，独立审查及锁定环境验收仍未完成，不能称为生产验收通过。

## 第三轮补充三：发布隔离与机会版本（2026-11-05，第 18–20 条）

本轮从 `6051fe0` 开始，仍在指定 `shadow-ledger` worktree / `codex/shadow-ledger` 分支。本轮授权覆盖第 18–20 条：两项修复分别先写失败回归再修复；基准仅运行一次 production、只记录，不因超预算停止。没有变更性能预算、配置或既有测试断言。初始唯一未提交文件为任务书新增的补充三，原样随第一个提交纳入。

### 实现与分步提交

- `5f1d4a3 fix(shadow): preserve held snapshots when publication fails`：`_finish_round` 收到 worker 结果后先更新不可变持仓 id / 市场 key 快照；展示发布异常单独隔离并继续更新 status / 耗时。仍调用原有限频 `error("publish")`。第 18 条回归使用真实 SQLite：开仓两行后注入发布异常，下一轮机会消失但盘口收敛，两行必须按当前时间 `converged`，持仓快照与 status 必须清空。
- `4d24716 fix(shadow): reject publication after opportunity version changes`：`SnapshotStore.opportunities_version` 从 0 开始，每次 `set_opportunities` 都递增，含空列表、重复列表及影子自身发布。原 setter 仍返回 None，getter 仍返回新列表并保留元素引用，其他消费者接口不变。新增 `set_opportunities_if_version`，在同一 RLock 中校验版本并调用原 setter，避免校验/写回之间出现覆盖。
- 影子在 `submit` 捕获输入时、首次 dispatch/yield 前保存机会版本，传经分片捕获和 worker；发布前版本不匹配则跳过展示并累加 `/api/shadow/status.skipped_publications`，原 generation 校验保留。跳过展示不取消计算、持仓快照或数据库提交。锁内校验失败也计入同一计数。
- 第 19 条回归使用真实 `routes_market_identities._refresh_store` 和 `MarketValidationService`，分别在市场选择阶段、账本 worker 阶段暂停 dispatch，再刷新并清空机会；恢复任务后列表必须仍为空、generation 不变、版本不被旧任务推进。随后机会消失仍能收敛平仓，连续两次影子自己的发布正确推进版本且不误计跳过。
- 新增测试只在 `backend/tests/test_shadow_publication_regressions.py`。生产代码仅改 `shadow_runtime.py`、`snapshot_store.py`；没有修改 schema/repositories、采集调度、Astro/arb-mvp、opportunity_history、前端或基准源码。`git diff --check` 通过。

### 失败回归与定向测试原始汇总

全部在本机 Windows / Python 3.12.10 运行，解释器 `Python312/python.exe`，`venv=False`；未安装或更新依赖，不能替代锁定依赖 venv 验收。命令 cwd 为 `backend`，临时目录均指定到被忽略的 `.pytest-tmp/round3c-*` scratchpad。完整日志保留在同名前缀 `.log`，不提交输出文件。

```powershell
python -m pytest tests/test_shadow_publication_regressions.py -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-h1-red
python -m pytest tests/test_shadow_publication_regressions.py -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-h1-green
python -m pytest tests/test_shadow_publication_regressions.py -k 'identity_refresh or opportunity_versions' -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-h2-red
python -m pytest tests/test_shadow_publication_regressions.py -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-h2-green
$shadowTests = @(Get-ChildItem tests/test_shadow*.py | ForEach-Object { $_.FullName })
python -m pytest @shadowTests tests/test_route_permissions.py tests/test_snapshot_consumers.py tests/test_market_identity_routes.py -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-targeted
```

按上述顺序，原始汇总行如下；退出码依次为 1 / 0 / 1 / 0 / 0：

```text
1 failed in 1.10s
1 passed in 0.54s
3 failed, 1 deselected in 1.63s
4 passed in 1.11s
154 passed, 3 deselected in 21.49s
```

H1 红灯发生在“下一轮两行应 `converged`”断言，直接复现持仓 key 快照丢失。H2 两个异步用例红灯均发生在刷新清空后旧机会被恢复；另一个用例因缺少版本 API 失败。所有红灯都在对应生产修复之前执行，未加 skip/xfail。定向测试包含全部影子测试、`test_route_permissions`，并补跑快照消费者和身份路由；3 deselected 是原配置默认排除的影子 benchmark 参数，production 单独运行见下。

### backend 全量与一次 production 基准

定向测试后运行全量，完成后再启动独立 production 基准进程，没有同时跑两组测试，也没有重跑挑选结果。第 20 条是本轮基准运行规则；不再套用上一轮“三次全部通过才能继续交付”的要求。

```powershell
python -m pytest -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-full
python -m pytest 'tests/test_shadow_benchmark.py::test_shadow_resource_budgets[production]' -q -s --tb=short -m benchmark -p no:cacheprovider --basetemp ../.pytest-tmp/round3c-production-once
```

后端全量退出码 0，原始汇总：

```text
2601 passed, 9 deselected, 12 warnings in 150.60s (0:02:30)
```

9 deselected 来自项目原有 network/benchmark marker 排除，没有新增排除。12 条均为 `datetime.utcnow()` 弃用警告，出现在 `test_api` / `test_index_components`，没有线程未处理异常警告。本轮没有前端改动，未重复前端测试或构建。

production 基准退出码 1：事件循环中位 `5.761399865150452 ms > 5 ms`，该断言失败；预算保持原样。运行前系统 CPU 负载 40%。事件循环 p95 8.223 ms / 连续占用最大 7.135 ms，按现有定义前者统计整轮各片累计、后者统计最大单片，二者口径不同；累计单轮最大 19.175 ms。worker 墙钟中位 17.508 ms / RSS 增量 28.574 MiB 均在原预算内。`within_time_budget=true` 仅表示 worker 预算，不表示整个基准通过。

耗时分布：capture 中位 4.631 ms，publish 中位 1.108 ms；worker 选择阶段中位 8.144 ms、计算阶段中位 9.552 ms。分钟轮捕获累计 13.980 ms、发布 2.103 ms、worker 墙钟 160.961 ms。仅报告实测，不进行性能优化、预算调整或固定周期节流。保留 12000 PriceState / 6000 资产索引 / 750 展示缓存 / 1500 未平行。

完整原始汇总行如下：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 9.552399977110326, "capture_median_ms": 4.630900104530156, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 13.980000279843807, "checkpoint_capture_segments_ms": [5.1512999925762415, 0.1357999863103032, 0.46870007645338774, 0.9024000028148293, 0.7933999877423048, 1.1820000363513827, 0.7300000870600343, 0.7284000748768449, 0.7693000370636582, 0.7408000528812408, 0.7588999578729272, 0.7445999654009938, 0.8744000224396586], "checkpoint_event_loop_ms": 16.082600341178477, "checkpoint_publish_ms": 2.1026000613346696, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 51.215399987995625, "prepare_ms": 3.6198999732732773, "routes_ms": 0.5613999674096704, "score_ms": 23.71290000155568}, "checkpoint_worker_wall_ms": 160.96120001748204, "display_cache_entries": 750, "event_loop_max_ms": 7.134899846278131, "event_loop_median_ms": 5.761399865150452, "event_loop_p95_ms": 8.223199867643416, "event_loop_total_max_ms": 19.174600020051003, "markets": 12000, "open_event_loop_ms": 19.174600020051003, "open_rows": 1500, "open_worker_wall_ms": 151.60569990985096, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.108399941585958, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 28.57421875, "scenario": "production", "select_wall_median_ms": 8.14439996611327, "system_cpu_before_pct": 40.0, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 16.850490196078432, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9466999908909202, "prepare_ms": 1.7516999505460262, "routes_ms": 0.5883000558242202, "score_ms": 1.8714000470936298}, "worker_wall_median_ms": 17.507500131614506}
1 failed in 4.01s
```

### 本轮交付边界

第 18–20 条的本地修复与要求的验证完成，production 性能仍未通过，不能据此开启生产；默认 `SHADOW_ENABLED=0`。下一步性能方案仍需 PM 决策，本轮未作任务书之外的业务/预算决策。没有 push、服务器连接、WSL、备份、部署或线上验证；线上状态未检查。仅本地提交需要写入主仓库中的该 worktree Git 元数据，已按授权执行。

本轮日志和临时 SQLite 全部保存在已忽略的 `.pytest-tmp/round3c-*`，既有产物保留，不提交测试输出。交接追加到 `docs/task-handoff-2026-11-05-shadow-ledger.md`。本轮是本机功能回归与一次性能记录，锁定环境及独立只读审查仍为后续验收项。


## 第三轮补充四：本机预算调整与 status 核对（2026-11-05，第 21–22 条）

本节为本轮交付及后续继续入口，覆盖前文关于旧预算和等待预算决策的状态；历史实测与失败记录保留。**本机基准只防退化，开启判断看生产灰度**。本轮仅完成用户指定的本地修改、三次基准、全部 shadow 测试和提交，不执行生产灰度。

### 范围与预算

- 分支 `codex/shadow-ledger`，起始基线 `d52afd535e95e312912793ea5c36e3e99b10c6d8`，工作树 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/shadow-ledger`。开始时唯一未提交改动是任务书第三轮补充四（第 21–22 条），保持原文并与本轮修改一起提交。
- 仅修改 `backend/tests/test_shadow_benchmark.py` 的三个预算值及对应注释，更新本报告；status 已具备所需字段，本轮没有修改生产代码、配置或新增测试。
- `production` 事件循环中位数由 5 改为 7 ms，最大连续占用由 40 改为 80 ms；`stress` 最大耗时由 20 改为 40 ms。`realistic` 与其余预算、统计口径、场景夹具及断言全部保持。

| 场景 | 事件循环中位 / p95 / 最大（ms） | worker CPU 中位（ms） | worker 墙钟中位（ms） | RSS 增量（MiB） |
| --- | --- | --- | --- | --- |
| stress | 5 / 10 / 40 | ≤20 | ≤30 | ≤30 |
| realistic | 3 / 10 / 20 | ≤8 | ≤8 | ≤30 |
| production | 7 / 15 / 80 | 不设 CPU 断言 | ≤30 | ≤30 |

production 的最大值沿用最大单次连续占用口径；事件循环中位数 / p95 沿用分片累计整轮口径，`event_loop_total_max_ms` 继续报告累计单轮最大值。其他场景的最大值仍按累计整轮耗时计算。本轮没有改变计量方法。

### 第 22 条核对

现有 `ShadowRuntime.__init__` 初始化累计计数 `skipped_publications=0`；`_finish_round` 在机会版本不匹配或锁内 `set_opportunities_if_version` 校验失败时递增；`status()` 返回该字段，`GET /api/shadow/status` 直接返回 runtime status，能够看到因版本变化放弃发布的累计次数。补充三已经实现，不重复修改。

本轮全部 shadow 测试包含 `test_shadow_publication_regressions.py` 的真实 `_refresh_store` 回归：分别在市场选择和 worker 阶段刷新身份，旧机会不得重新发布，`skipped_publications` 为 1，持仓和数据库进度正常；后续正常影子发布继续推进版本且计数不增加。该组测试本轮通过。

### 命令与测试结果

本机 Windows 11 / Python 3.12.10 / pytest 8.4.2，解释器为 `Python312/python.exe`，`venv=False`；没有安装或更新依赖，本轮结果不替代锁定依赖 venv 验收。所有测试使用 Windows Python，无 WSL。

以下命令 cwd 为 `backend`。三个基准进程串行运行，每次覆盖 stress、realistic、production 三个场景，未与其他测试并行；恰好运行三次，没有追加重跑或挑选结果。`Tee-Object` 原始日志及临时 SQLite 位于已忽略的 `.pytest-tmp/round3d-20261105-112125-*`，不提交；既有产物保留。

```powershell
foreach ($runNumber in 1..3) {
    $runName = "round3d-20261105-112125-benchmark-$runNumber"
    python -m pytest tests/test_shadow_benchmark.py -q -s --tb=short -m benchmark -p no:cacheprovider --basetemp "../.pytest-tmp/$runName" 2>&1 | Tee-Object -FilePath "../.pytest-tmp/$runName.log"
    $benchmarkExit = $LASTEXITCODE
    Write-Output "END $runName EXIT_CODE=$benchmarkExit"
}
$shadowTests = @(Get-ChildItem tests/test_shadow*.py -File | Sort-Object Name | ForEach-Object { $_.FullName })
python -m pytest @shadowTests -q --tb=short -p no:cacheprovider --basetemp ../.pytest-tmp/round3d-20261105-112125-shadow 2>&1 | Tee-Object -FilePath ../.pytest-tmp/round3d-20261105-112125-shadow.log
```

全部 shadow 功能测试退出码 0，原始汇总行：

```text
90 passed, 3 deselected in 15.05s
```

3 deselected 为项目默认 marker 排除的三个 benchmark 参数，已由上述三次基准独立运行。没有新增 skip/xfail、更改 marker 或放宽其他断言。本轮按限定范围未运行后端全量、前端测试或构建。

三次完整基准各自退出码均为 1，均为 stress 失败、realistic 和 production 通过；因此**未达到三次全部场景均通过的本机基准验收条件**。

- 第 1 次：stress worker CPU 中位 31.25 ms > 20 ms，在 `within_time_budget` 断言失败；事件循环最大值 38.99270005058497 ms 在新 40 ms 预算内。
- 第 2 次：stress 事件循环最大值 40.58819997590035 ms > 40 ms；worker CPU 中位 15.625 ms 在 20 ms 预算内。
- 第 3 次：stress 事件循环最大值 41.61229997407645 ms > 40 ms；worker CPU 中位 15.625 ms 在 20 ms 预算内。

Windows 线程 CPU 时间沿用现有量化采样，不能用墙钟或零 CPU 中位数替换 CPU 断言；上述失败如实保留。没有因失败继续放宽预算、修改运行时或自行增加节流。`within_time_budget` 只判断 worker，不能代表整个基准通过，第 2/3 次 stress 即为 true 但最大耗时断言失败。

下表从原始输出提取，数值四舍五入到三位小数；精确值及全部字段见后续九条原始行。

| 次数 | 场景 | 前置系统 CPU（%） | loop 中位 / p95 / 最大（ms） | worker CPU / 墙钟中位（ms） | RSS（MiB） | 场景结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | stress | 26.257 | 4.260 / 5.039 / 38.993 | 31.250 / 22.287 | 27.289 | 失败 |
| 1 | realistic | 34.921 | 1.192 / 1.450 / 2.983 | 0.000 / 6.146 | 4.301 | 通过 |
| 1 | production | 29.348 | 6.333 / 8.964 / 9.059 | 15.625 / 18.540 | 27.680 | 通过 |
| 2 | stress | 38.251 | 4.674 / 5.437 / 40.588 | 15.625 / 23.978 | 27.246 | 失败 |
| 2 | realistic | 30.726 | 1.378 / 2.863 / 3.156 | 0.000 / 6.786 | 4.578 | 通过 |
| 2 | production | 74.586 | 6.406 / 7.901 / 8.374 | 15.625 / 18.676 | 27.164 | 通过 |
| 3 | stress | 28.902 | 4.549 / 5.186 / 41.612 | 15.625 / 23.253 | 28.004 | 失败 |
| 3 | realistic | 48.000 | 1.377 / 1.705 / 3.251 | 0.000 / 6.788 | 4.582 | 通过 |
| 3 | production | 41.209 | 6.630 / 8.401 / 8.764 | 15.625 / 19.570 | 27.629 | 通过 |

production 三次事件循环中位数 6.333 / 6.406 / 6.630 ms、worker 墙钟中位数 18.540 / 18.676 / 19.570 ms、RSS 增量 27.680 / 27.164 / 27.629 MiB 均在新预算内。捕获中位数 5.058 / 5.104 / 5.401 ms，发布中位数 1.176 / 1.203 / 1.251 ms；worker 选择阶段中位数 8.741 / 8.709 / 8.980 ms，计算阶段中位数 9.900 / 10.227 / 10.311 ms。分钟检查点 worker 墙钟为 167.448 / 169.536 / 175.825 ms，该单轮值继续报告，不是本轮调整的预算项。

### 三次基准全部原始汇总行

以下行直接复制各次日志，保留 pytest 输出在部分行首的 `F` / `.` 进度字符；没有删减字段或重写数值。

第 1 次，退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 19.024800043553114, "capture_median_ms": 3.742600092664361, "changed_fraction": 1.0, "checkpoint_capture_ms": 4.453799920156598, "checkpoint_capture_segments_ms": [3.8887999253347516, 0.19449996761977673, 0.17570005729794502, 0.19479996990412474], "checkpoint_event_loop_ms": 4.9906999338418245, "checkpoint_publish_ms": 0.5369000136852264, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 111.70969996601343, "prepare_ms": 3.6909999325871468, "routes_ms": 44.188100029714406, "score_ms": 6.9512000773102045}, "checkpoint_worker_wall_ms": 175.7346000522375, "display_cache_entries": 1000, "event_loop_max_ms": 38.99270005058497, "event_loop_median_ms": 4.25980007275939, "event_loop_p95_ms": 5.039000068791211, "event_loop_total_max_ms": 38.99270005058497, "markets": 3000, "open_event_loop_ms": 9.055000031366944, "open_rows": 3000, "open_worker_wall_ms": 203.43269989825785, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.528500066138804, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.2890625, "scenario": "stress", "select_wall_median_ms": 3.323799930512905, "system_cpu_before_pct": 26.256983240223462, "within_memory_budget": true, "within_time_budget": false, "worker_cpu_mean_ms": 26.348039215686274, "worker_cpu_median_ms": 31.25, "worker_phase_medians_ms": {"ledger_flush_ms": 3.7832000525668263, "prepare_ms": 3.8831999991089106, "routes_ms": 0.7755000842735171, "score_ms": 5.724100046791136}, "worker_wall_median_ms": 22.287299972958863}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 5.175599944777787, "capture_median_ms": 0.974200083874166, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.121500157751143, "checkpoint_capture_segments_ms": [1.0675000958144665, 0.0540000619366765], "checkpoint_event_loop_ms": 1.3645002618432045, "checkpoint_publish_ms": 0.24300010409206152, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 25.969100068323314, "prepare_ms": 1.1869999580085278, "routes_ms": 0.32919994555413723, "score_ms": 1.8638999899849296}, "checkpoint_worker_wall_ms": 33.03130005951971, "display_cache_entries": 400, "event_loop_max_ms": 2.9830000130459666, "event_loop_median_ms": 1.1924999998882413, "event_loop_p95_ms": 1.449999981559813, "event_loop_total_max_ms": 2.9830000130459666, "markets": 1000, "open_event_loop_ms": 2.9830000130459666, "open_rows": 1000, "open_worker_wall_ms": 57.27310001384467, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.215300009585917, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.30078125, "scenario": "realistic", "select_wall_median_ms": 0.8826999692246318, "system_cpu_before_pct": 34.92063492063492, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 3.982843137254902, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.4464000230655074, "prepare_ms": 1.0705000022426248, "routes_ms": 0.3301999531686306, "score_ms": 0.9050000226125121}, "worker_wall_median_ms": 6.146300002001226}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 9.899800061248243, "capture_median_ms": 5.05819998215884, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 14.748599962331355, "checkpoint_capture_segments_ms": [4.9681998789310455, 0.11639995500445366, 0.5002999678254128, 0.8868000004440546, 0.8458000374957919, 1.2292000465095043, 1.0244000004604459, 0.9712000610306859, 0.8053999627009034, 0.8312000427395105, 0.7881000638008118, 0.7628999883309007, 1.0186999570578337], "checkpoint_event_loop_ms": 17.08599994890392, "checkpoint_publish_ms": 2.3373999865725636, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 52.643399918451905, "prepare_ms": 2.038200036622584, "routes_ms": 1.002000062726438, "score_ms": 24.406999931670725}, "checkpoint_worker_wall_ms": 167.4476999323815, "display_cache_entries": 750, "event_loop_max_ms": 9.059000061824918, "event_loop_median_ms": 6.3328000251203775, "event_loop_p95_ms": 8.963800151832402, "event_loop_total_max_ms": 22.066600038670003, "markets": 12000, "open_event_loop_ms": 22.066600038670003, "open_rows": 1500, "open_worker_wall_ms": 178.52849990595132, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1764999944716692, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.6796875, "scenario": "production", "select_wall_median_ms": 8.741199970245361, "system_cpu_before_pct": 29.34782608695652, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 22.058823529411764, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 1.0133999167010188, "prepare_ms": 1.928800018504262, "routes_ms": 0.6655999459326267, "score_ms": 1.9756000256165862}, "worker_wall_median_ms": 18.540300079621375}
1 failed, 2 passed in 7.70s
```

第 2 次，退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 20.09909995831549, "capture_median_ms": 4.140700097195804, "changed_fraction": 1.0, "checkpoint_capture_ms": 4.297799780033529, "checkpoint_capture_segments_ms": [3.8712999084964395, 0.14129991177469492, 0.13339996803551912, 0.1517999917268753], "checkpoint_event_loop_ms": 4.836799809709191, "checkpoint_publish_ms": 0.5390000296756625, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 118.66260005626827, "prepare_ms": 4.4664000160992146, "routes_ms": 44.16420008055866, "score_ms": 8.328899974003434}, "checkpoint_worker_wall_ms": 185.82649994641542, "display_cache_entries": 1000, "event_loop_max_ms": 40.58819997590035, "event_loop_median_ms": 4.673900082707405, "event_loop_p95_ms": 5.437099956907332, "event_loop_total_max_ms": 40.58819997590035, "markets": 3000, "open_event_loop_ms": 9.615200106054544, "open_rows": 3000, "open_worker_wall_ms": 205.7820000918582, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.5382000235840678, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.24609375, "scenario": "stress", "select_wall_median_ms": 3.9347000420093536, "system_cpu_before_pct": 38.25136612021858, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 25.122549019607842, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 4.360100021585822, "prepare_ms": 4.39760007429868, "routes_ms": 0.8151000365614891, "score_ms": 5.642300006002188}, "worker_wall_median_ms": 23.97830004338175}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 5.836900090798736, "capture_median_ms": 1.133600017055869, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.1306001106277108, "checkpoint_capture_segments_ms": [1.076500047929585, 0.05410006269812584], "checkpoint_event_loop_ms": 1.3783000176772475, "checkpoint_publish_ms": 0.2476999070495367, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 26.840500067919493, "prepare_ms": 1.0379999876022339, "routes_ms": 0.3423999296501279, "score_ms": 1.9327000482007861}, "checkpoint_worker_wall_ms": 33.542899996973574, "display_cache_entries": 400, "event_loop_max_ms": 3.156200167723, "event_loop_median_ms": 1.3783000176772475, "event_loop_p95_ms": 2.8631999157369137, "event_loop_total_max_ms": 3.156200167723, "markets": 1000, "open_event_loop_ms": 2.7206999948248267, "open_rows": 1000, "open_worker_wall_ms": 55.21919997408986, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.2429999876767397, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.578125, "scenario": "realistic", "select_wall_median_ms": 1.0587999131530523, "system_cpu_before_pct": 30.72625698324022, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 6.740196078431373, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.4786000354215503, "prepare_ms": 1.227300032041967, "routes_ms": 0.37319993134588003, "score_ms": 0.9920999873429537}, "worker_wall_median_ms": 6.786400103010237}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.22749999538064, "capture_median_ms": 5.104199983179569, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 15.004099812358618, "checkpoint_capture_segments_ms": [4.939200007356703, 0.11750007979571819, 0.5196999991312623, 0.9679999202489853, 0.9071999229490757, 1.2841999996453524, 0.7422000635415316, 0.7477999897673726, 0.7430999539792538, 0.9229999268427491, 1.0791000677272677, 0.9726999560371041, 1.0603999253362417], "checkpoint_event_loop_ms": 16.10299979802221, "checkpoint_publish_ms": 1.0988999856635928, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 53.33749996498227, "prepare_ms": 2.0349000114947557, "routes_ms": 1.2135000433772802, "score_ms": 24.50259996112436}, "checkpoint_worker_wall_ms": 169.5361000020057, "display_cache_entries": 750, "event_loop_max_ms": 8.373599965125322, "event_loop_median_ms": 6.405999884009361, "event_loop_p95_ms": 7.900599972344935, "event_loop_total_max_ms": 20.991900004446507, "markets": 12000, "open_event_loop_ms": 20.991900004446507, "open_rows": 1500, "open_worker_wall_ms": 163.10079989489168, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.2027999619022012, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.1640625, "scenario": "production", "select_wall_median_ms": 8.709099958650768, "system_cpu_before_pct": 74.58563535911603, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 21.752450980392158, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.983899924904108, "prepare_ms": 1.9739000126719475, "routes_ms": 0.6663000676780939, "score_ms": 1.9428000086918473}, "worker_wall_median_ms": 18.67559994570911}
1 failed, 2 passed in 8.01s
```

第 3 次，退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 19.64740001130849, "capture_median_ms": 3.936000051908195, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.9643998024985194, "checkpoint_capture_segments_ms": [3.6906999303027987, 0.08679996244609356, 0.08769996929913759, 0.09919994045048952], "checkpoint_event_loop_ms": 4.550699843093753, "checkpoint_publish_ms": 0.5863000405952334, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 117.666200036183, "prepare_ms": 3.7908999947831035, "routes_ms": 39.25120003987104, "score_ms": 7.424799958243966}, "checkpoint_worker_wall_ms": 178.88300004415214, "display_cache_entries": 1000, "event_loop_max_ms": 41.61229997407645, "event_loop_median_ms": 4.548800061456859, "event_loop_p95_ms": 5.185800022445619, "event_loop_total_max_ms": 41.61229997407645, "markets": 3000, "open_event_loop_ms": 11.253399890847504, "open_rows": 3000, "open_worker_wall_ms": 206.14259992726147, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.5486999871209264, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 28.00390625, "scenario": "stress", "select_wall_median_ms": 3.435299964621663, "system_cpu_before_pct": 28.90173410404624, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 23.897058823529413, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 4.238600027747452, "prepare_ms": 3.9440999971702695, "routes_ms": 0.7970000151544809, "score_ms": 5.7622999884188175}, "worker_wall_median_ms": 23.252599989064038}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 5.631500040180981, "capture_median_ms": 1.126500079408288, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.0605000425130129, "checkpoint_capture_segments_ms": [1.0101000079885125, 0.05040003452450037], "checkpoint_event_loop_ms": 1.3358000433072448, "checkpoint_publish_ms": 0.2753000007942319, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 27.205599937587976, "prepare_ms": 1.089200028218329, "routes_ms": 0.3475999692454934, "score_ms": 1.916700042784214}, "checkpoint_worker_wall_ms": 34.181099967099726, "display_cache_entries": 400, "event_loop_max_ms": 3.2507998403161764, "event_loop_median_ms": 1.3773000100627542, "event_loop_p95_ms": 1.7052999464794993, "event_loop_total_max_ms": 3.2507998403161764, "markets": 1000, "open_event_loop_ms": 3.2507998403161764, "open_rows": 1000, "open_worker_wall_ms": 59.23129990696907, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.22799998987466097, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.58203125, "scenario": "realistic", "select_wall_median_ms": 0.9829000337049365, "system_cpu_before_pct": 48.0, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 6.4338235294117645, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.4782000323757529, "prepare_ms": 1.1461999965831637, "routes_ms": 0.3394999075680971, "score_ms": 0.9419999551028013}, "worker_wall_median_ms": 6.787699996493757}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.311400052160025, "capture_median_ms": 5.401000031270087, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 14.809599961154163, "checkpoint_capture_segments_ms": [5.268000066280365, 0.07049995474517345, 0.484099960885942, 0.8699999889358878, 1.0082999942824244, 1.7114999936893582, 0.7269999478012323, 0.7317999843508005, 0.7420999463647604, 0.7334999972954392, 0.7427000673487782, 0.744700082577765, 0.9753999765962362], "checkpoint_event_loop_ms": 17.57599995471537, "checkpoint_publish_ms": 2.7663999935612082, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 58.878300013020635, "prepare_ms": 2.12760001886636, "routes_ms": 0.7187000010162592, "score_ms": 24.485199945047498}, "checkpoint_worker_wall_ms": 175.82450015470386, "display_cache_entries": 750, "event_loop_max_ms": 8.764100028201938, "event_loop_median_ms": 6.630299962125719, "event_loop_p95_ms": 8.401299943216145, "event_loop_total_max_ms": 19.58390010986477, "markets": 12000, "open_event_loop_ms": 19.58390010986477, "open_rows": 1500, "open_worker_wall_ms": 165.04669992718846, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.2510999804362655, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.62890625, "scenario": "production", "select_wall_median_ms": 8.979500038549304, "system_cpu_before_pct": 41.208791208791204, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 20.220588235294116, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 1.0812999680638313, "prepare_ms": 1.9756999099627137, "routes_ms": 0.7260000566020608, "score_ms": 2.0097000524401665}, "worker_wall_median_ms": 19.569700118154287}
1 failed, 2 passed in 8.02s
```

### 本轮交付与后续边界

第 21 条指定的三个预算值及注释已调整，第 22 条所需 status 字段已核对存在；三次基准与全部 shadow 测试已执行并保留原始结果。shadow 功能测试通过，stress 仍有上述失败，本轮不声明全部性能验收通过。后续生产灰度需在独立运维任务中执行并以实测决定是否长期开启；本机基准只防退化，开启判断看生产灰度。

本轮只作本地 conventional commit，将用户已有任务书补充四与测试/报告一起纳入；没有 push、连接服务器、WSL、备份、部署、生产灰度或线上验证，线上状态未检查。配置和默认开关保持原样。本报告提供本轮交接入口；按用户限定不另改交接文件。其他无直接依赖模块另开任务。


## 第三轮补充五：快照版本绑定与 stress 预算（2026-11-05，第 23、25 条）

第 23 条已先失败回归再修复，第 25 条仅按指定调整 stress；第 24 条不新增补发代码。backend 全量通过；三次完整基准前两次通过、第三次 stress 与 production 失败，**未满足三次全部达标**，不宣称性能验收通过。没有重跑挑选结果、额外放宽预算或扩展性能优化。

### 范围、提交与实现

- 工作树 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/shadow-ledger`；分支 `codex/shadow-ledger`，起始基线 `778817288167e092b6f28b2cab2e35acce33d771`。初始唯一未提交文件为任务书，补充五第 23–25 条原文已一起提交。
- `9a64cbc test(shadow): reproduce stale queued snapshot publication`：先提交失败回归及任务书。真实 `MarketCollector._publish_to_slots` 入队 → 真实 `_refresh_store` 清空 → 从 `LatestSnapshotSlot` 消费旧快照；修复前在 `store.get_opportunities() == []` 失败，实际恢复了一条旧机会。
- `0019c3c fix(shadow): bind opportunity versions to published snapshots`：`PublishedSnapshot.opportunities_version` 在发布入口随内容捕获，collector 和 inline observer 构造均无中途 await；`submit` 只读快照携带版本，经原 capture/finish 链传给条件发布。失配计入 `skipped_publications`，账本写入和持仓快照继续处理。
- 四参数快照构造仍兼容其他消费者；默认版本 `None` 在影子中只处理账本，不回写展示，不从当前 store 推断版本。未改 `LatestSnapshotSlot`、`SnapshotConsumer`、历史记录、指数处理及采集调度逻辑。生产文件仅改 collector 发布元数据一行、快照字段与 shadow runtime 两个入口。
- 新回归检查相同快照仍送达历史消费者，市场/机会对象及参数保持；补充版本不可变、无版本不回写、inline 版本绑定测试。现有 shadow 夹具显式传入捕获时版本，既有账本/混合展示/异常隔离断言全部保留。基准每轮增加 `skipped_publications == 0`，保证测量包含正常展示发布开销。
- `8e38a59 test(shadow): apply approved stress timing budgets`：stress CPU 中位继续输出但不作通过判据，墙钟中位保持 ≤30 ms，事件循环最大从 40 改为 50 ms。Windows `thread_time` 15.625 ms 量化理由已写入注释。stress 中位 ≤5 / p95 ≤10、production 中位 ≤7 / p95 ≤15 / 最大连续片段 ≤80、realistic 全部预算和 RSS ≤30 MiB 不变。
- 第 24 条仅记录现有接受边界：持续弃发、不补发，生产灰度盯 `skipped_publications`。本机基准只防退化，开启判断看生产灰度。

### 环境、命令和功能验证

Windows 原生 Python 3.12.10，`C:/Users/wubin/AppData/Local/Programs/Python/Python312/python.exe`，`venv=False`；沿用现有环境，未安装/升级依赖，不属于锁定依赖 venv 独立验收。命令在 `backend` 执行；全量保留项目默认 `not network and not benchmark` 排除，基准单独显式运行。stdout/stderr 重定向到对应 `.pytest-tmp/round3e-*.log`，临时数据库使用同名目录，全部已忽略、保留不提交。

```powershell
python -m pytest tests/test_shadow_publication_regressions.py -q -k queued_snapshot --basetemp=../.pytest-tmp/round3e-red
python -m pytest tests/test_shadow_publication_regressions.py tests/test_snapshot_consumers.py tests/test_shadow_api_runtime.py tests/test_shadow_performance_safety.py tests/test_shadow_prefilter_equivalence.py tests/test_shadow_review_regressions.py tests/test_shadow_selection_runtime.py -q --basetemp=../.pytest-tmp/round3e-green
python -m pytest -q --basetemp=../.pytest-tmp/round3e-full
foreach ($benchmarkRun in 1..3) {
    python -m pytest tests/test_shadow_benchmark.py -m benchmark -q -s --basetemp="../.pytest-tmp/round3e-bench-$benchmarkRun"
}
```

红灯退出码 1，定向及全量退出码 0。原始汇总：

```text
1 failed, 4 deselected in 1.99s
72 passed in 21.50s
2604 passed, 9 deselected, 12 warnings in 202.73s (0:03:22)
```

全量 12 条警告均为既有 `datetime.utcnow()` 弃用警告，无线程未处理异常。未改前端，本轮未跑前端测试/构建。已做本地差异复核，未另行运行独立审查代理。

### 三次基准与失败位置

全量 pytest 结束后串行运行三次完整基准，没有并行施压或追加重跑。表内耗时单位 ms、RSS 单位 MiB；四舍五入仅用于表格，原始精确值见后文。CPU 前负载逐场景采样，不能据此认定超限原因。

| 次数 | 场景 | CPU 前负载 % | loop 中位 | loop p95 | loop 最大 | worker CPU 中位 | worker 墙钟中位 | RSS | 结果 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | stress | 29.121 | 4.949 | 6.453 | 41.410 | 31.250 | 24.703 | 27.918 | 通过 |
| 1 | realistic | 25.882 | 1.184 | 1.867 | 2.774 | 0.000 | 5.609 | 4.309 | 通过 |
| 1 | production | 29.834 | 6.705 | 8.802 | 8.993 | 15.625 | 19.696 | 27.715 | 通过 |
| 2 | stress | 26.966 | 4.367 | 5.860 | 41.386 | 15.625 | 23.517 | 27.605 | 通过 |
| 2 | realistic | 23.034 | 1.486 | 3.832 | 4.731 | 0.000 | 7.606 | 3.676 | 通过 |
| 2 | production | 48.045 | 6.652 | 7.648 | 71.940 | 15.625 | 19.014 | 28.160 | 通过 |
| 3 | stress | 58.011 | 5.863 | 11.514 | 47.682 | 31.250 | 32.856 | 27.273 | 失败 |
| 3 | realistic | 25.967 | 1.618 | 2.766 | 3.334 | 0.000 | 7.529 | 4.594 | 通过 |
| 3 | production | 34.807 | 7.474 | 12.667 | 12.162 | 15.625 | 22.980 | 28.363 | 失败 |

第三次 stress 首个失败断言为 worker 墙钟中位 32.856 >30；原始指标还显示 loop 中位 5.863 >5、p95 11.514 >10；最大 47.682 <50、RSS 27.273 <30 达标。第三次 production 失败断言为 loop 中位 7.474 >7，其余记录的预算指标达标。realistic 三次通过。

主要耗时位置（第三次，分项中位不能直接相加）：stress 捕获 4.953、发布 0.824，worker 选择 4.990、计算 27.513；计算分项 prepare 5.795、score 7.765、routes 1.221、ledger/flush 6.105。production 捕获 5.479、发布 2.111，worker 选择 10.196、计算 12.347。第二次 production 分钟采样有 71.940 ms 连续片段、整轮累计 87.942 ms，按既有第 14/21 条连续片段 ≤80 ms 的口径通过；整轮累计不是该最大预算的判据。完整保留尖峰，未改计量方式。

### 原始 SHADOW_BENCHMARK 与 pytest 汇总

逐行从完整日志提取，不删字段、不改数值；`.` / `F` 为 pytest 原始进度前缀。

第 1 次，退出码 0：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 21.098500001244247, "capture_median_ms": 4.245699965395033, "changed_fraction": 1.0, "checkpoint_capture_ms": 4.025400034151971, "checkpoint_capture_segments_ms": [3.647199831902981, 0.12800004333257675, 0.1167000737041235, 0.1335000852122903], "checkpoint_event_loop_ms": 4.601599997840822, "checkpoint_publish_ms": 0.5761999636888504, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 113.13860001973808, "prepare_ms": 3.3707000548020005, "routes_ms": 41.95449990220368, "score_ms": 6.680799997411668}, "checkpoint_worker_wall_ms": 174.8328999383375, "display_cache_entries": 1000, "event_loop_max_ms": 41.40959994401783, "event_loop_median_ms": 4.9486999632790685, "event_loop_p95_ms": 6.452999892644584, "event_loop_total_max_ms": 41.40959994401783, "markets": 3000, "open_event_loop_ms": 9.606000036001205, "open_rows": 3000, "open_worker_wall_ms": 218.66089990362525, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.5657000001519918, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.91796875, "scenario": "stress", "select_wall_median_ms": 3.675100044347346, "system_cpu_before_pct": 29.120879120879117, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 28.49264705882353, "worker_cpu_median_ms": 31.25, "worker_phase_medians_ms": {"ledger_flush_ms": 4.634499899111688, "prepare_ms": 4.613399971276522, "routes_ms": 0.8630999363958836, "score_ms": 6.022999994456768}, "worker_wall_median_ms": 24.70279997214675}
.SHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 4.746799939312041, "capture_median_ms": 0.9076999267563224, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.1087998282164335, "checkpoint_capture_segments_ms": [1.051899860613048, 0.05689996760338545], "checkpoint_event_loop_ms": 1.4777998439967632, "checkpoint_publish_ms": 0.3690000157803297, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 26.408600038848817, "prepare_ms": 1.389099983498454, "routes_ms": 0.37070002872496843, "score_ms": 1.9070999696850777}, "checkpoint_worker_wall_ms": 35.19580001011491, "display_cache_entries": 400, "event_loop_max_ms": 2.7744999388232827, "event_loop_median_ms": 1.1844000546261668, "event_loop_p95_ms": 1.8666000105440617, "event_loop_total_max_ms": 2.7744999388232827, "markets": 1000, "open_event_loop_ms": 2.7744999388232827, "open_rows": 1000, "open_worker_wall_ms": 59.70390001311898, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.20659994333982468, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.30859375, "scenario": "realistic", "select_wall_median_ms": 0.8547999896109104, "system_cpu_before_pct": 25.882352941176467, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 4.901960784313726, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.4178000381216407, "prepare_ms": 1.0288000339642167, "routes_ms": 0.33029995393007994, "score_ms": 0.8585000177845359}, "worker_wall_median_ms": 5.608899868093431}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.560600087046623, "capture_median_ms": 5.290200002491474, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 15.21099975798279, "checkpoint_capture_segments_ms": [4.773199907504022, 0.4141000099480152, 1.0653999634087086, 1.1916999937966466, 0.812400016002357, 1.412699930369854, 0.7452000863850117, 0.7124999538064003, 0.7272999500855803, 0.741099938750267, 0.76299998909235, 0.750600011087954, 1.1018000077456236], "checkpoint_event_loop_ms": 18.944199779070914, "checkpoint_publish_ms": 3.7332000210881233, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 53.19849995430559, "prepare_ms": 2.0808998961001635, "routes_ms": 0.7093000458553433, "score_ms": 26.010600035078824}, "checkpoint_worker_wall_ms": 173.0938000837341, "display_cache_entries": 750, "event_loop_max_ms": 8.992699906229973, "event_loop_median_ms": 6.704599945805967, "event_loop_p95_ms": 8.802100084722042, "event_loop_total_max_ms": 21.59800019580871, "markets": 12000, "open_event_loop_ms": 21.59800019580871, "open_rows": 1500, "open_worker_wall_ms": 173.05199999827892, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.228500041179359, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.71484375, "scenario": "production", "select_wall_median_ms": 9.28070000372827, "system_cpu_before_pct": 29.83425414364641, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 21.44607843137255, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9651000145822763, "prepare_ms": 1.933799940161407, "routes_ms": 0.6555999862030149, "score_ms": 1.9468000391498208}, "worker_wall_median_ms": 19.696100032888353}
3 passed in 7.51s
```

第 2 次，退出码 0：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 19.969700020737946, "capture_median_ms": 3.8745999336242676, "changed_fraction": 1.0, "checkpoint_capture_ms": 3.926300094462931, "checkpoint_capture_segments_ms": [3.557200077921152, 0.12749992311000824, 0.11100003030151129, 0.1306000631302595], "checkpoint_event_loop_ms": 4.435700131580234, "checkpoint_publish_ms": 0.5094000371173024, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 114.512600004673, "prepare_ms": 3.65209998562932, "routes_ms": 41.070499923080206, "score_ms": 8.504800032824278}, "checkpoint_worker_wall_ms": 178.79090004134923, "display_cache_entries": 1000, "event_loop_max_ms": 41.38579987920821, "event_loop_median_ms": 4.36650007031858, "event_loop_p95_ms": 5.860000033862889, "event_loop_total_max_ms": 41.38579987920821, "markets": 3000, "open_event_loop_ms": 10.100700194016099, "open_rows": 3000, "open_worker_wall_ms": 206.94709988310933, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.4841999616473913, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.60546875, "scenario": "stress", "select_wall_median_ms": 3.409500000998378, "system_cpu_before_pct": 26.96629213483146, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 24.50980392156863, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 3.549200017005205, "prepare_ms": 4.033199977129698, "routes_ms": 0.7592999609187245, "score_ms": 6.218099966645241}, "worker_wall_median_ms": 23.516700020991266}
.SHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 6.6009999718517065, "capture_median_ms": 1.234399969689548, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 0.8821000810712576, "checkpoint_capture_segments_ms": [0.8327000541612506, 0.049400026910007], "checkpoint_event_loop_ms": 1.1081001721322536, "checkpoint_publish_ms": 0.22600009106099606, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 27.936500031501055, "prepare_ms": 0.9304999839514494, "routes_ms": 0.3272000467404723, "score_ms": 1.7786999233067036}, "checkpoint_worker_wall_ms": 34.29289988707751, "display_cache_entries": 400, "event_loop_max_ms": 4.73059993237257, "event_loop_median_ms": 1.4863001415506005, "event_loop_p95_ms": 3.8315000711008906, "event_loop_total_max_ms": 4.73059993237257, "markets": 1000, "open_event_loop_ms": 3.1125000678002834, "open_rows": 1000, "open_worker_wall_ms": 55.813100072555244, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.22860011085867882, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 3.67578125, "scenario": "realistic", "select_wall_median_ms": 1.0945999529212713, "system_cpu_before_pct": 23.03370786516854, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 6.740196078431373, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.4400999750941992, "prepare_ms": 1.1514999205246568, "routes_ms": 0.3626999678090215, "score_ms": 0.9458999847993255}, "worker_wall_median_ms": 7.6058999402448535}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.611900012008846, "capture_median_ms": 5.415499792434275, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 85.38070030044764, "checkpoint_capture_segments_ms": [4.716099938377738, 0.07020006887614727, 0.4596998915076256, 1.0183000704273582, 1.4702000189572573, 71.93960004951805, 0.7398000452667475, 0.776599976234138, 0.7735000690445304, 0.7272000657394528, 0.7630999898537993, 0.7529000286012888, 1.173500088043511], "checkpoint_event_loop_ms": 87.94200036209077, "checkpoint_publish_ms": 2.5613000616431236, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 54.18799992185086, "prepare_ms": 3.981199930422008, "routes_ms": 0.6796000525355339, "score_ms": 25.28499998152256}, "checkpoint_worker_wall_ms": 105.32180010341108, "display_cache_entries": 750, "event_loop_max_ms": 71.93960004951805, "event_loop_median_ms": 6.652499781921506, "event_loop_p95_ms": 7.647500140592456, "event_loop_total_max_ms": 87.94200036209077, "markets": 12000, "open_event_loop_ms": 20.50339977722615, "open_rows": 1500, "open_worker_wall_ms": 171.94419994484633, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1666000355035067, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 28.16015625, "scenario": "production", "select_wall_median_ms": 8.894100086763501, "system_cpu_before_pct": 48.044692737430175, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 23.897058823529413, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9704000549390912, "prepare_ms": 1.8592000706121325, "routes_ms": 0.6712999893352389, "score_ms": 1.965800067409873}, "worker_wall_median_ms": 19.013799959793687}
3 passed in 7.40s
```

第 3 次，退出码 1：

```text
SHADOW_BENCHMARK {"asset_index_assets": 1500, "calculate_wall_median_ms": 27.51349995378405, "capture_median_ms": 4.953400115482509, "changed_fraction": 1.0, "checkpoint_capture_ms": 5.287099978886545, "checkpoint_capture_segments_ms": [4.780300077982247, 0.16299996059387922, 0.1674999948590994, 0.17629994545131922], "checkpoint_event_loop_ms": 6.1354999197646976, "checkpoint_publish_ms": 0.8483999408781528, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 134.47799999266863, "prepare_ms": 5.688000004738569, "routes_ms": 42.10580000653863, "score_ms": 7.746699964627624}, "checkpoint_worker_wall_ms": 201.54090004507452, "display_cache_entries": 1000, "event_loop_max_ms": 47.681699972599745, "event_loop_median_ms": 5.862899939529598, "event_loop_p95_ms": 11.514300131238997, "event_loop_total_max_ms": 47.681699972599745, "markets": 3000, "open_event_loop_ms": 17.125999787822366, "open_rows": 3000, "open_worker_wall_ms": 380.58550003916025, "opportunities": 1000, "price_states": 3000, "publish_median_ms": 0.8237999863922596, "recomputed_median": 1500, "rounds": 51, "rss_delta_mib": 27.2734375, "scenario": "stress", "select_wall_median_ms": 4.990199930034578, "system_cpu_before_pct": 58.011049723756905, "within_memory_budget": true, "within_time_budget": false, "worker_cpu_mean_ms": 34.620098039215684, "worker_cpu_median_ms": 31.25, "worker_phase_medians_ms": {"ledger_flush_ms": 6.105299922637641, "prepare_ms": 5.795300006866455, "routes_ms": 1.2211999855935574, "score_ms": 7.765399990603328}, "worker_wall_median_ms": 32.85630000755191}
FSHADOW_BENCHMARK {"asset_index_assets": 500, "calculate_wall_median_ms": 6.348300026729703, "capture_median_ms": 1.3238999526947737, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 1.1705999495461583, "checkpoint_capture_segments_ms": [1.116700004786253, 0.05389994475990534], "checkpoint_event_loop_ms": 1.5451000072062016, "checkpoint_publish_ms": 0.37450005766004324, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 36.326700006611645, "prepare_ms": 2.2589999716728926, "routes_ms": 0.6480000447481871, "score_ms": 4.214299959130585}, "checkpoint_worker_wall_ms": 49.199999892152846, "display_cache_entries": 400, "event_loop_max_ms": 3.333500004373491, "event_loop_median_ms": 1.6180002130568027, "event_loop_p95_ms": 2.765999990515411, "event_loop_total_max_ms": 3.333500004373491, "markets": 1000, "open_event_loop_ms": 3.333500004373491, "open_rows": 1000, "open_worker_wall_ms": 60.515500023029745, "opportunities": 400, "price_states": 1000, "publish_median_ms": 0.22990000434219837, "recomputed_median": 167, "rounds": 51, "rss_delta_mib": 4.59375, "scenario": "realistic", "select_wall_median_ms": 1.048400066792965, "system_cpu_before_pct": 25.96685082872928, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 5.514705882352941, "worker_cpu_median_ms": 0.0, "worker_phase_medians_ms": {"ledger_flush_ms": 0.46120001934468746, "prepare_ms": 1.104300026781857, "routes_ms": 0.3507001092657447, "score_ms": 0.9568000677973032}, "worker_wall_median_ms": 7.529099937528372}
.SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 12.347300071269274, "capture_median_ms": 5.4787000408396125, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 15.666600200347602, "checkpoint_capture_segments_ms": [6.838600034825504, 0.06220000796020031, 0.4627000307664275, 0.8705999935045838, 0.8071999764069915, 1.1276999721303582, 0.7024001097306609, 0.743800075724721, 0.7298999698832631, 0.7243000436574221, 0.799400033429265, 0.7776999846100807, 1.0200999677181244], "checkpoint_event_loop_ms": 17.898800200782716, "checkpoint_publish_ms": 2.232200000435114, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 59.45979990065098, "prepare_ms": 2.1747000282630324, "routes_ms": 0.7258000550791621, "score_ms": 24.42130004055798}, "checkpoint_worker_wall_ms": 182.04989994410425, "display_cache_entries": 750, "event_loop_max_ms": 12.161900056526065, "event_loop_median_ms": 7.473799982108176, "event_loop_p95_ms": 12.667099945247173, "event_loop_total_max_ms": 33.832799876108766, "markets": 12000, "open_event_loop_ms": 33.832799876108766, "open_rows": 1500, "open_worker_wall_ms": 193.89299990143627, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 2.11130001116544, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 28.36328125, "scenario": "production", "select_wall_median_ms": 10.195999988354743, "system_cpu_before_pct": 34.806629834254146, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 24.20343137254902, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 1.147200004197657, "prepare_ms": 2.0985000301152468, "routes_ms": 0.9197000181302428, "score_ms": 2.6240000734105706}, "worker_wall_median_ms": 22.98020012676716}
2 failed, 1 passed in 10.05s
```

### 交付与后续边界

代码及用户任务书已分步 conventional commits；本报告和 `docs/task-handoff-2026-11-05-shadow-ledger.md` 另作 docs 提交。未 push、未连接服务器、未运行 WSL，未备份/部署/灰度或验证线上；`SHADOW_ENABLED` 默认 0 不变。未改 schema/repositories、Astro/arb-mvp、机会历史写入、配置或身份刷新业务逻辑。既有产物保留，日志与临时库已忽略且不提交。

第 23、25 条实现及要求的验证/记录完成，但三次性能门槛未通过，按既有停止规则不扩展优化。下一步由 PM 根据原始耗时决定性能方案，再在单独授权的运维任务中做灰度；本轮不启动。独立审查、锁定环境验收仍待安排。其他无直接依赖模块另开任务。

## PM 第四轮停止报告（2026-11-05，第 26–31 条）

本轮未完成实现。按第 31 条“若发现账本行为依赖第 26–29 条未说明的取舍，停下写入报告，不要自行决定”停止生产代码修改。只提交任务书原有未提交内容、两项明确要求的失败测试以及报告/交接；没有改变任何既有测试、预算或场景准备。

### 已复现的取舍：v1 退役是否触发 v2 冷却

当前 `ShadowTradeRepository.restore`（`backend/app/db/repositories.py:83`）以 `MAX(closed_at)` 恢复最近 10 分钟的 cooldown，查询没有策略版本或 `exit_reason` 过滤。`ShadowLedger.restore` 直接接收该映射；`process`（`shadow_ledger.py:208/300`）在 600 秒内跳过相同 `opportunity_id`。

第 29 条要求恢复时将 v1 未平行设置为 `retired`、`closed_at=恢复时刻`。因此，如果按现有查询恢复冷却，退役也会触发新的 10 分钟禁开期；即使首轮基线后已持续低于门槛 60 秒并重新上穿，v2 仍不能开仓。若从 cooldown 查询排除退役，则相同序列可以开仓。第 26 条要求保留原 cooldown，第 29 条说明退役不进入账本/名额/收益统计，但没有明确退役是否属于触发 cooldown 的平仓。两种选择都会影响开仓时机，不能仅当作统计实现细节。

复现使用锁定 venv、临时内存 SQLite 和现有 repository：保存一条 `strategy_version=shadow-v1`、`exit_reason=retired`、`closed_at=2026-11-05T00:00:00+00:00`、`net_bps=None` 的行，61 秒后调用 `repo.restore`，原始输出为：

```text
{"cooldowns": {"same-op": 1793836800.0}, "open_rows": 0, "retired_in_cooldown": true, "seconds_remaining": 539}
```

这是对退役行进入既有恢复查询的合成复现，不代表已实现自动退役，也不是线上数据。需要 PM 明确：退役是否触发冷却；如果做版本隔离，排除范围是仅 `retired`，还是包括旧版本正常平仓记录。当前未选择任一规则。

### 失败测试与本地提交

- 起始基线 `ac602b9`，分支 `codex/shadow-ledger`，指定 worktree 未变。
- 初始唯一 WIP 为任务书第四轮 19 行，已原样纳入 `14d6d1f test(shadow): capture round four baseline and retirement requirements`。
- 新增 `backend/tests/test_shadow_round4.py`：启动首轮不应开仓；repository 恢复时 v1 应退役且保留历史、空净收益。这两项没有为未决 cooldown 规则写入预设答案。
- 先运行失败测试，再执行取舍复现；没有开始生产实现。红灯原始汇总：

```text
2 failed in 1.27s
```

首项实际 `open_count=2`，预期 0；第二项实际仍恢复一条 v1 未平行，预期为空。没有 xfail、skip、放宽断言或修改旧测试。其他第四轮测试及生产实现尚未完成，包括上穿/重试/名额/status/版本统计，以及全量与预筛选多轮等价性扩展。

### 验证环境和命令

Windows Python 3.12.10，`sys.prefix != sys.base_prefix` 为 True，使用用户指定的 `scratchpad/venv-lock/Scripts/python.exe`，没有安装或升级依赖。所有 pytest 在 `backend` 目录运行，`PYTHONPATH=.`，禁用 cacheprovider；basetemp 均是 venv 同级 scratchpad 下独立的新目录。

```powershell
$env:PYTHONPATH='.'
$round4Scratch='C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad'
& "$round4Scratch/venv-lock/Scripts/python.exe" -m pytest tests/test_shadow_round4.py -q -p no:cacheprovider --basetemp "$round4Scratch/shadow-r4-red-20261105"
& "$round4Scratch/venv-lock/Scripts/python.exe" -m pytest tests -q -p no:cacheprovider --basetemp "$round4Scratch/shadow-r4-full-20261105"
# 以下命令依次执行三次，$round4Run 分别为 1、2、3；没有重跑挑选。
& "$round4Scratch/venv-lock/Scripts/python.exe" -m pytest tests/test_shadow_benchmark.py -q -s -m benchmark -k production -p no:cacheprovider --basetemp "$round4Scratch/shadow-r4-bench-$round4Run-20261105"
```

全量保留原 `not network and not benchmark` 默认排除；production 基准单独运行。以下基准仅度量未修改的 v1 实现，不能作为第四轮完成验收；原预算和夹具完全未改。日志在忽略目录 `.pytest-tmp/round4-*.log`，临时数据库在上述 scratchpad 内。没有 push、服务器连接、WSL、部署、灰度或线上验证；`SHADOW_ENABLED=0` 默认值保持。

### 全量测试与三次 production 原始汇总

全量退出码 1；仅两项新增的第四轮红灯失败，既有 2604 项通过。13 个警告包括 12 个 datetime.utcnow 弃用警告和 1 个 Starlette/httpx 弃用警告；没有线程未处理异常。

```text
2 failed, 2604 passed, 9 deselected, 13 warnings in 127.55s (0:02:07)
```

production 第 1 次（未修改的 v1 实现）：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 9.037100011482835, "capture_median_ms": 4.3268000008538365, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 14.010599814355373, "checkpoint_capture_segments_ms": [4.804999916814268, 0.14759995974600315, 0.45950000640004873, 0.9949998930096626, 0.7514999015256763, 1.2999000027775764, 0.8413000032305717, 0.7978000212460756, 0.750900013372302, 0.7674000225961208, 0.7715000538155437, 0.7537000346928835, 0.8694999851286411], "checkpoint_event_loop_ms": 16.060899826698005, "checkpoint_publish_ms": 2.050300012342632, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 54.50329999439418, "prepare_ms": 1.8025999888777733, "routes_ms": 0.6179000483825803, "score_ms": 23.370199953205884}, "checkpoint_worker_wall_ms": 157.19749999698251, "display_cache_entries": 750, "event_loop_max_ms": 7.8743998892605305, "event_loop_median_ms": 5.4564999882131815, "event_loop_p95_ms": 7.375099929049611, "event_loop_total_max_ms": 18.99919961579144, "markets": 12000, "open_event_loop_ms": 18.99919961579144, "open_rows": 1500, "open_worker_wall_ms": 148.99150002747774, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.067299977876246, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.41796875, "scenario": "production", "select_wall_median_ms": 7.4995000613853335, "system_cpu_before_pct": 27.173913043478258, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 17.46323529411765, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9266000706702471, "prepare_ms": 1.6417000442743301, "routes_ms": 0.5842000246047974, "score_ms": 1.7221999587491155}, "worker_wall_median_ms": 16.49060007184744}
1 passed, 2 deselected in 3.09s
```

production 第 2 次（未修改的 v1 实现）：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 9.957600035704672, "capture_median_ms": 4.81400010176003, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 13.89500021468848, "checkpoint_capture_segments_ms": [4.750100080855191, 0.1314000692218542, 0.4616999067366123, 0.8168000495061278, 0.7272000657394528, 0.7903999648988247, 0.7505000103265047, 0.7743000751361251, 0.7429000688716769, 1.532500027678907, 0.813600025139749, 0.7547999266535044, 0.8487999439239502], "checkpoint_event_loop_ms": 15.937600168399513, "checkpoint_publish_ms": 2.042599953711033, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 59.75709995254874, "prepare_ms": 3.156399936415255, "routes_ms": 0.5519000114873052, "score_ms": 22.80220005195588}, "checkpoint_worker_wall_ms": 164.751400006935, "display_cache_entries": 750, "event_loop_max_ms": 12.053900049068034, "event_loop_median_ms": 5.979900015518069, "event_loop_p95_ms": 13.273000018671155, "event_loop_total_max_ms": 17.94779987540096, "markets": 12000, "open_event_loop_ms": 17.94779987540096, "open_rows": 1500, "open_worker_wall_ms": 156.3069000840187, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1554000666365027, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.296875, "scenario": "production", "select_wall_median_ms": 8.011900004930794, "system_cpu_before_pct": 31.491712707182316, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 16.850490196078432, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9745999705046415, "prepare_ms": 1.7717999871820211, "routes_ms": 0.5970000056549907, "score_ms": 1.8946999916806817}, "worker_wall_median_ms": 17.950800131075084}
1 passed, 2 deselected in 3.25s
```

production 第 3 次（未修改的 v1 实现）：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.151399997994304, "capture_median_ms": 4.412799957208335, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 13.336699805222452, "checkpoint_capture_segments_ms": [4.696300020441413, 0.07670000195503235, 0.41199999395757914, 0.7670000195503235, 0.6977999582886696, 1.294399960897863, 0.7585999555885792, 0.7969000143930316, 0.7719999412074685, 0.7408999372273684, 0.7505000103265047, 0.7239000406116247, 0.8496999507769942], "checkpoint_event_loop_ms": 15.67789982073009, "checkpoint_publish_ms": 2.3412000155076385, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 61.78150000050664, "prepare_ms": 1.8312999745830894, "routes_ms": 1.536700059659779, "score_ms": 27.24660001695156}, "checkpoint_worker_wall_ms": 177.05219995696098, "display_cache_entries": 750, "event_loop_max_ms": 8.695799973793328, "event_loop_median_ms": 5.587999941781163, "event_loop_p95_ms": 9.94889996945858, "event_loop_total_max_ms": 20.01109963748604, "markets": 12000, "open_event_loop_ms": 20.01109963748604, "open_rows": 1500, "open_worker_wall_ms": 155.7891999837011, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.1205000337213278, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 26.74609375, "scenario": "production", "select_wall_median_ms": 7.696500048041344, "system_cpu_before_pct": 25.555555555555554, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 21.139705882352942, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 0.9982000337913632, "prepare_ms": 1.7479999223724008, "routes_ms": 0.6299000233411789, "score_ms": 1.8958000000566244}, "worker_wall_median_ms": 17.51259993761778}
1 passed, 2 deselected in 3.29s
```

## 第四轮 v2 本地交付（2026-11-05，第 26–32 条）

本节取代上一节的停止状态。第 32 条已明确退役不触发冷却；本轮完成 v2 实现、锁定环境全量回归和连续三次 production 基准，全部通过。没有 push、连接服务器、备份、部署、WSL 或公网行情运行；默认 SHADOW_ENABLED=0 未改。

### 分支、提交与范围

- 指定 worktree：taoli1/.worktrees/shadow-ledger；分支 codex/shadow-ledger；起始基线 ce1513edbcb1ad9a94058cc3f4274acc31d8c100。
- 6ae830b test(shadow): cover v2 crossing quotas and retirement rules：先补失败测试，原样纳入用户未提交的第 32 条；原有两项红灯断言未改。
- fed74ff feat(shadow): record v2 crossings with quotas and legacy retirement：实现、回归场景准备和部分平仓后恢复的补充覆盖。
- 6c6f66a test(shadow): prepare v2 benchmark startup baselines：基准仅调整启动/种子准备，未改预算、测量范围或断言。
- 报告和交接另作 conventional docs 提交，提交号见本分支 git log。
- 生产变更仅 shadow_ledger.py、shadow_runtime.py、shadow_stats.py、routes_shadow.py 和 ShadowTradeRepository；没有 schema 迁移、采集调度、Astro、arb-mvp、opportunity_history、前端或配置改动。

### 行为与实现入口

- shadow_ledger.py：策略版本为 shadow-v2；last_above 保留最近 60 秒达到门槛的机会 id，每轮清理过期记录，retry_events 同步收缩，不积累进程生命周期历史。空首轮也完成基线登记；首轮达到门槛的不同 id 计入 baseline_skipped，不开仓。restore 重置内存事件状态。
- 新上穿且无持仓/冷却时才尝试开仓。缺腿、报价过期等暂时问题保留事件；短暂无观测不等于明确跌破门槛，事件可在返回时重试；明确低于门槛取消重试。达到门槛的时间持续更新，重新准入要距离最近一次达到门槛至少 60 秒。持仓中或 cooldown 内的上穿不会在条件解除后自动补开。
- 名额按行：lt500=floor(max_open*30%)，unknown=floor(max_open*10%)，spot/spot=floor(max_open*20%)，symbol=6。两行必须一起容纳才开仓；平仓逐行释放，restore 从实际剩余行重建计数，零计数项删除。总容量/流动性/市场类型/symbol 的检查顺序固定，每次拒绝只记录第一个原因，事件立即消耗；dropped_by_reason 合计等于 dropped。
- shadow_runtime.py：预筛选额外携带低于实际开仓门槛的 id 元组，避免候选集合丢掉“取消重试”信号。保留原混合发布与版本校验。状态从 worker 拷贝发布；status 再复制 dropped_by_reason，调用方不能修改已发布计数。
- repositories.py：只把 v1 未平仓行改为 retired，closed_at=恢复时刻，net_bps=None；使用已有每批至多 200 行写入入口，不删历史。重复 restore 不重写已退役行的时间。恢复 cooldown 排除 retired，v1 真实平仓仍保持原 cooldown（第 32 条只排除退役）。
- routes_shadow.py / repositories.py：buckets 默认参数是当前 STRATEGY_VERSION，显式 strategy_version=shadow-v1 才查询旧版本，SQL 参数绑定；trades 仍能看历史。收益查询以及 shadow_stats.statistics/buckets 都排除 retired，测试还覆盖错误带有数值 net_bps 的 retired 行。
- 平仓原因、止损、收敛、超时、资金费、滑点与流动性标签算法原样保留；没有发现需要新增 PM 决策的账本取舍。

### 测试迁移及复核

先补失败测试后实现。新增覆盖启动存量/空基线、59/60 秒边界、短暂缺失、冷却结束仍需重新上穿、无新鲜报价重试和明确低价取消、全部分类名额及取整、拒绝后容量释放不重试、部分平仓与恢复名额、事件窗口清理、退役/历史/统计/真实冷却保留、API 版本参数与状态快照隔离。实际预筛选 runtime 与测试全量路径逐轮对照基线、上穿、名额、重试取消、持仓、冷却和诊断。

旧断言全部保留；旧测试只改场景准备：
- 针对开平仓/资金费/事务/线程/发布的用例先运行空基线。重启的真实 restore 仍重置基线，未绕过生产判断。
- 202 行分批资金费用例把 101 个机会分到不同 symbol，保留 200+2 中断边界、2/0 恢复为 2/2 的断言。
- 固定种子综合等价序列增加空基线，C 在 A 释放容量时才上穿，以继续覆盖止损，保留原开平仓分项、funding、stale、重启、收益行等价及至少三次开仓时间断言。
- production 用 v2 空基线后开仓种子，再真实恢复 750 对/1500 行；实际测量仍含恢复后首轮、51 个变化轮及 +61 秒分钟全量/检查点。其余两个基准场景仅补空基线，本轮按用户要求只执行 production。
- 初次兼容回归的两项失败分别来自综合序列中 C 的旧式容量释放重试假设、关闭线程测试尚未准备基线，均调整输入场景解决，没有删除或放宽断言。

本轮自查了 DEX 精确键、计数释放、恢复、状态所有权、SQL 参数绑定、禁用路径和 diff；没有启动子代理或另做独立审查。未改前端，本轮不运行前端测试/构建。

锁定环境实测：Windows Python 3.12.10，sys.prefix 为用户指定 venv-lock，venv=True；没有安装/升级依赖。命令均在 backend 目录执行，PYTHONPATH=.，-p no:cacheprovider；basetemp 全部位于 venv 同级 scratchpad。

原始 pytest 汇总（按执行顺序，日志保存在 .pytest-tmp/round4b-*.log）：

```text
round4b-red: 14 failed, 1 passed in 4.28s
round4b-equal-red: 1 failed, 7 deselected in 0.79s
round4b-green: 16 passed, 7 deselected in 3.47s
round4b-target: 2 failed, 142 passed, 3 deselected in 19.81s
round4b-target2: 144 passed, 3 deselected in 11.67s
round4b-partial: 19 passed in 4.35s
2624 passed, 9 deselected, 13 warnings in 135.58s (0:02:15)
```

全量退出 0。13 条均为既有弃用警告（12 datetime.utcnow、1 Starlette/httpx），没有线程未处理异常。默认 network/benchmark 排除规则未改；production 已另行显式运行。

可复现命令（测试选择与各日志对应；完整路径保留）：

```powershell
$env:PYTHONPATH='.'
$python='C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe'
$scratch='C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad'
& $python -m pytest tests -q -p no:cacheprovider --basetemp="$scratch/shadow-r4b-full-20261105"
foreach ($round in 1..3) {
    & $python -m pytest tests/test_shadow_benchmark.py -m benchmark -k production -q -s -p no:cacheprovider --basetemp="$scratch/shadow-r4b-bench-$round-20261105"
}
```

### 三次 production 基准

连续三次均退出 0，未重跑挑选。原预算：事件循环中位 ≤7 ms、p95 ≤15 ms、最大连续片段 ≤80 ms；worker 墙钟中位 ≤30 ms；RSS 增量 ≤30 MiB，全部满足。场景仍为 12000 市场、5500 机会（650 达门槛）、750 对持仓，每轮 1/3 报价变化。

| 次数 | CPU 前负载 % | loop 中位 ms | p95 ms | 最大连续片段 ms | worker 墙钟中位 ms | RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 63.587 | 6.092 | 7.484 | 7.787 | 17.633 | 27.516 |
| 2 | 29.609 | 6.364 | 7.769 | 8.879 | 17.212 | 27.391 |
| 3 | 60.773 | 6.937 | 7.953 | 8.299 | 18.357 | 26.891 |

第三次事件循环中位仅比上限低约 0.063 ms，余量较小；本机基准只防退化，不能推断线上灰度结果。普通轮主要耗时为 capture 中位 4.891–5.722 ms、worker 选择 7.706–7.991 ms 和计算 9.425–10.357 ms。检查点 ledger_flush 51.512–54.732 ms、score 22.318–24.165 ms 在工作线程；没有因此变更中位预算或引入节流。

下面三条 SHADOW_BENCHMARK 与 pytest 汇总直接从各原始日志逐行复制：


production 第 1 次：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 9.845499997027218, "capture_median_ms": 4.8914001090452075, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 15.396700007840991, "checkpoint_capture_segments_ms": [6.228200043551624, 0.048600020818412304, 0.3952999832108617, 0.758300069719553, 0.6812999490648508, 1.3204999268054962, 1.1454999912530184, 0.8086999878287315, 0.8027999429032207, 0.7823000196367502, 0.7687000324949622, 0.7831000257283449, 0.8734000148251653], "checkpoint_event_loop_ms": 17.405200051143765, "checkpoint_publish_ms": 2.0085000433027744, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 53.7764000473544, "prepare_ms": 1.8876999383792281, "routes_ms": 0.6190999411046505, "score_ms": 24.16530007030815}, "checkpoint_worker_wall_ms": 159.70720001496375, "display_cache_entries": 750, "event_loop_max_ms": 7.78679980430752, "event_loop_median_ms": 6.092499941587448, "event_loop_p95_ms": 7.483999943360686, "event_loop_total_max_ms": 19.39239981584251, "markets": 12000, "open_event_loop_ms": 19.39239981584251, "open_rows": 1500, "open_worker_wall_ms": 184.2002000194043, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.0939000640064478, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.515625, "scenario": "production", "select_wall_median_ms": 7.9474999802187085, "system_cpu_before_pct": 63.58695652173913, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 19.607843137254903, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 1.1816000333055854, "prepare_ms": 1.6630999743938446, "routes_ms": 0.5891000619158149, "score_ms": 1.699900021776557}, "worker_wall_median_ms": 17.63280003797263}
1 passed, 2 deselected in 3.27s
```


production 第 2 次：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 9.425300057046115, "capture_median_ms": 5.307400017045438, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 13.836400117725134, "checkpoint_capture_segments_ms": [5.044499994255602, 0.05150004290044308, 0.41770003736019135, 0.7803000044077635, 0.682900077663362, 1.0651000775396824, 0.7257000543177128, 0.7881000638008118, 0.727499951608479, 0.7229999173432589, 0.7613999769091606, 1.0029999539256096, 1.0656999656930566], "checkpoint_event_loop_ms": 16.212700051255524, "checkpoint_publish_ms": 2.3762999335303903, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 51.511800033040345, "prepare_ms": 1.8308000871911645, "routes_ms": 0.5914999637752771, "score_ms": 22.50580000691116}, "checkpoint_worker_wall_ms": 155.64230002928525, "display_cache_entries": 750, "event_loop_max_ms": 8.878799970261753, "event_loop_median_ms": 6.36360002681613, "event_loop_p95_ms": 7.768599898554385, "event_loop_total_max_ms": 19.882399938069284, "markets": 12000, "open_event_loop_ms": 19.882399938069284, "open_rows": 1500, "open_worker_wall_ms": 153.09649996925145, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.0801000753417611, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 27.390625, "scenario": "production", "select_wall_median_ms": 7.706400007009506, "system_cpu_before_pct": 29.60893854748603, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 16.850490196078432, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 1.172599964775145, "prepare_ms": 1.6550000291317701, "routes_ms": 0.5848999135196209, "score_ms": 1.8316000932827592}, "worker_wall_median_ms": 17.211600090377033}
1 passed, 2 deselected in 3.21s
```


production 第 3 次：

```text
SHADOW_BENCHMARK {"asset_index_assets": 6000, "calculate_wall_median_ms": 10.35650004632771, "capture_median_ms": 5.721700028516352, "changed_fraction": 0.3333333333333333, "checkpoint_capture_ms": 13.553000171668828, "checkpoint_capture_segments_ms": [5.053700064308941, 0.04740001168102026, 0.4051999421790242, 0.7727000629529357, 0.6936000427231193, 1.052300096489489, 0.7560000522062182, 0.7792999967932701, 0.8503999561071396, 0.7546000415459275, 0.7682000286877155, 0.7521999068558216, 0.867399969138205], "checkpoint_event_loop_ms": 15.76450013089925, "checkpoint_publish_ms": 2.211499959230423, "checkpoint_worker_phases_ms": {"ledger_flush_ms": 54.731600102968514, "prepare_ms": 1.8915999680757523, "routes_ms": 0.6114999996498227, "score_ms": 22.31750008650124}, "checkpoint_worker_wall_ms": 160.52620008122176, "display_cache_entries": 750, "event_loop_max_ms": 8.298799977637827, "event_loop_median_ms": 6.9373000878840685, "event_loop_p95_ms": 7.952700019814074, "event_loop_total_max_ms": 20.460700150579214, "markets": 12000, "open_event_loop_ms": 20.460700150579214, "open_rows": 1500, "open_worker_wall_ms": 164.8198999464512, "opportunities": 5500, "price_states": 12000, "publish_median_ms": 1.15099991671741, "recomputed_median": 250, "rounds": 51, "rss_delta_mib": 26.890625, "scenario": "production", "select_wall_median_ms": 7.991399965249002, "system_cpu_before_pct": 60.773480662983424, "within_memory_budget": true, "within_time_budget": true, "worker_cpu_mean_ms": 20.833333333333332, "worker_cpu_median_ms": 15.625, "worker_phase_medians_ms": {"ledger_flush_ms": 1.2874000240117311, "prepare_ms": 1.7169000348076224, "routes_ms": 0.5876000504940748, "score_ms": 1.8364000134170055}, "worker_wall_median_ms": 18.357499968260527}
1 passed, 2 deselected in 3.34s
```

交付产物：本轮日志为 .pytest-tmp/round4b-{red,equal-red,green,target,target2,partial,full,bench-1,bench-2,bench-3}.log；临时 SQLite 位于上述 scratchpad/shadow-r4b-*-20261105。全部忽略且不提交，既有测试产物保留。最终只暂存本模块源码、测试、任务书、报告和交接；本地提交完成后用 git status 复核。线上状态未检查；下一步如需独立审查或生产灰度，使用本报告与最新交接另行授权，无依赖模块另开任务。
