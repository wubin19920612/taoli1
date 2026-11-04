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
