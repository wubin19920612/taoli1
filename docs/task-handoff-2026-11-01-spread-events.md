**数据源调查：按当前源码，任务书给出的 DISABLED_FEATURES 列表不会关闭 opportunity_history 写入；前提是采集器运行且 HISTORY_ENABLED=true。本任务未连接生产库，未实测线上最新写入时间。**

# 价差事件榜交接与返工验收报告

## 目标、范围与基线

- 范围依据 `docs/spread-events-spec-2026-11-01.md` 及本轮用户返工要求；原任务书不修改。
- 分支 `codex/spread-events`；本轮基线 `57ebd10`（首次功能提交），首次实现基线 `e0320f4114628a5ad09cb2443b0df6db9890912e`。
- 本轮仅修正 P2 流式聚合/连接/并发与配对跳转倍率，增加基准和对应测试。
- 用户要求新建本地提交，不 amend、不 push；不部署、不修改生产服务或数据库。
- 所有禁止文件保持不变，包括 `PairMonitorPage.tsx`、价差引擎、机会雷达、runtime 和 FS 文件；本轮也不修改 main.py 或导航。

## 数据源调查四项结论

1. 写入者：`MarketCollector` 发布行情后调用 `OpportunityHistoryRecorder.record`；后台发布模式经 `SnapshotConsumer("history-recorder", ..., collector.record_history)` 写入；最终由 `OpportunityHistoryRepository.insert_many` 批量落库。`core/config.py` / `models/settings.py` 默认每 **120 秒**采样、保留 **3 天**，可由 HISTORY 配置覆盖。
2. 所给禁用列表中的 `opportunity_radar` 控制独立雷达，不控制基础采集器历史 recorder。所给九项不会关闭这条写入链；`HISTORY_ENABLED=false`、采集器停用或没有符合选样条件的机会仍可能导致无新增。本结论来自源码，非线上实时核验。
3. 已有 `idx_opportunity_history_time ON opportunity_history(observed_at DESC)`，以及 symbol/time、opportunity_id/time、type/time 组合索引。主查询保留裸时间范围条件，按 observed_at 索引顺序读取；SQLite EXPLAIN 测试确认使用时间索引且没有临时排序 B-tree。没有改 schema。
4. `PERCENT_SCALE = 10_000`；0.3% 存成 3000，读取时除以该常量。

历史表并非全市场连续采样：默认仅保存原始开仓价差至少 0.5%、满足最低成交额等条件的前 100 条机会，还受上游风险筛选影响。因此可能缺少回落点；默认 3 天保留期也不会因为允许查询 7 天而延长。榜单只能描述已经记录的样本。

## 流式聚合、连接与并发

- `query_spread_events` 接收数据库文件路径；整个 SQLite 打开、查询、聚合、补齐展示字段、关闭过程均在 `asyncio.to_thread` 内完成，使用 `sqlite3.connect(file_uri + "?mode=ro", uri=True)`。不使用共享 aiosqlite 连接、不创建缺失库；`:memory:` 路由明确返回 503。
- 独立连接开启读事务，主扫描、超限探测及展示字段读取使用同一快照；连接用 closing 保证退出和异常时关闭。
- 主扫描仅 SELECT opportunity_id、observed_at、所选 scaled 指标。游标 `fetchmany(5000)`；不 fetchall，不逐样本读取身份 JSON。
- 每序列只保存当前事件、累计值、上一样本时间、最多 31 个采样间隔及其估计。已完成事件通过 heapq 仅保留排序需要的前 limit 个，最多 500；最后排序这些候选。
- **新 max_gap 口径：每序列前 31 个正间隔的中位数，收满后冻结；没有间隔时按默认采集周期 120 秒估计。max_gap = max(120 秒, 3 × 估计间隔)。判断当前缺口时使用此前间隔的估计，再把本间隔加入估计；当前缺口不会抬高自己的判定阈值。**
- 上述口径是用户批准的流式估计，替代原全窗口中位数；不追溯重算过去事件。冷启动 max_gap 为 360 秒；后续采样频率大幅变化或前 31 个间隔偏斜时可能影响缺口划分。
- 缺口优先于回落、结束于缺口前样本；回落结束于首个低阈样本；ongoing 结束于最后观察样本，不外推查询尾部。仅超阈样本进入 peak/mean/samples；保留原 duration、阈值、排序、limit 和时间上限规则。
- 最多扫描 500000 行；到上限后用同条件 EXISTS/OFFSET 探测是否截断，返回 truncated。展示身份只对入选序列查询一次该序列最后一条记录，时间不晚于已扫描尾部，同一序列多个入选事件复用结果。
- 模块级 `asyncio.Semaphore(1)` 限制同一应用进程的查询并发；排队超过 5 秒返回 HTTP 429：`价差事件查询繁忙，等待超过 5 秒，请稍后重试。`
- 请求取消不会提前释放许可：shield 保留工作任务，只有后台查询真正结束并关闭连接后才释放。测试覆盖取消期间第二个请求仍排队超时、完成后可继续查询。

## 倍率来源与跳转规则

- `OpportunityHistoryRepository.row_from_opportunity` 保存 `opportunity.validation` 到 `market_identity_json`。其中 buy_market/sell_market 是 `MarketKey`（exchange、market_type、dex、raw_symbol），另有 buy_revision/sell_revision；**不直接保存价格倍率或合约倍率**。
- 同库 `market_identity_bindings.payload` 保存 `MarketBinding.price_multiplier` 和 `contract_size`。对入选序列按完整 MarketKey + 历史 revision 查询，并要求 `unit_status=verified`；对应响应字段为 `price_multiplier` 和 `contract_size_multiplier`。不按规范化 ticker 猜测。
- 当前绑定 revision 已改变、身份缺失、单位待核实或字段缺失/非正数/非有限数时，对应倍率为 null。自动发现的待核实绑定含默认 1，但不视为可用倍率证据。此版本不扫描无对应组合索引的 audit 表来重建旧 revision；当前绑定无法对应的旧记录标为未知。
- 复用既有 URL 的 `leg1_price_multiplier / leg2_price_multiplier` 和 `leg1_contract_size_multiplier / leg2_contract_size_multiplier`。只传已知正有限值；清理旧 leg 参数，删除原先固定写入的 `leg2_multiplier=1`。后者是配对页自定义相对倍率，不由历史推测。
- 任一腿的价格或合约倍率缺失时，该行显示 **倍率未知**。部分已知时只携带已知项；全部未知时链接没有任何倍率参数，仍生成跳转。
- 继续保留精确 Hyperliquid DEX/raw_symbol；缺少原始市场身份时仍沿用原来的禁跳转提示，不用 ticker 猜 DEX。没有改 PairMonitorPage；目标页面自身的默认行为保持原样，榜单标记不能当作历史单位已核实。
- 前端继续仅在打开、改筛选或手动刷新时请求，不轮询；结束原因沿用“回落 / 行情中断 / 进行中”。

## 本轮改动文件

- `backend/app/services/spread_events.py`：流式状态机、只读连接、并发限制、入选序列身份和倍率补齐。
- `backend/app/api/routes_spread_events.py`：传入配置中的数据库路径、503/429 映射。
- `backend/app/models/spread_events.py`：腿上的两个可空倍率字段。
- `backend/tests/test_spread_events.py`：文件数据库与 ASGI 测试、流式间隔估计、索引和分批、连接只读/线程关闭、版本匹配、排队与取消。
- `backend/scripts/benchmark_spread_events.py`：可复现的合成 SQLite 查询基准。
- `frontend/src/api/spreadEvents.ts`：可空倍率类型、未知判断、跳转参数。
- `frontend/src/pages/SpreadEventsPage.tsx`：倍率未知标签。
- `frontend/tests/SpreadEventsPage.test.tsx`：非 1 倍跳转、未知/部分未知标记、清除旧倍率参数。
- `docs/task-handoff-2026-11-01-spread-events.md`：本交接报告。

## 基准结果

从 backend 运行 `PYTHONPATH=. python scripts/benchmark_spread_events.py`。采用真实 schema，7 天 / 120 秒 × 100 序列 = **504000 行**，含身份 JSON；每 5 个采样制造回落，产生大量完成事件。数据生成不计入测量；测量整个 async 查询，包括线程和连接建立、扫描、聚合、排名和展示字段读取。

| sort | 扫描行数 | 返回事件 | 峰值额外内存 MiB | 耗时秒 |
| --- | ---: | ---: | ---: | ---: |
| peak | 500000 | 500 | 2.530 | 4.841 |
| duration | 500000 | 500 | 2.471 | 5.208 |
| start | 500000 | 500 | 2.471 | 5.585 |

三项均为 100 个序列、limit=500、truncated=true；峰值均远低于 **100 MiB**，脚本会对此断言。内存是 tracemalloc 测得的额外 Python 分配峰值，**不等于进程 RSS，也不包含 SQLite 原生页缓存**；耗时为本机一次运行结果，非生产延迟承诺。

## 验证结果

- 后端全量 `PYTHONPATH=. python -m pytest tests -q`：**2709 passed, 6 deselected, 12 warnings in 198.99s (0:03:18)**；12 条 warning 为已有 datetime.utcnow() 弃用提示。
- 后端本模块专项：**36 passed in 43.71s**。
- 前端全量 `npm test`：**Test Files 33 passed (33); Tests 372 passed (372); Duration 220.52s**。
- 前端全量通过时，在该命令进程中设置 `VITEST_MAX_FORKS=2 VITEST_MIN_FORKS=1 VITEST_MAX_THREADS=2 VITEST_MIN_THREADS=1`，限制最多两个 worker；没有筛选测试、提高超时、修改 npm script 或 Vitest 配置。
- 保留复跑记录：默认 worker 配置的前两次全量分别有 SettingsPage 的不同用例触发既有 15000ms 超时，均为 **1 failed / 371 passed**（155.03s、102.12s）。限制 worker 后 SettingsPage 全部 16 项及整个套件通过。相关代码未改动，默认并发下的测试稳定性仍可能受机器负载影响。
- 前端本模块专项：**10 passed (10)**。
- `npm run build`：TypeScript 检查通过，**Vite built in 28.46s**。
- 本轮 Python 文件 `python -m ruff check ...`：**All checks passed!**；`git diff --check` 和暂存差异检查通过。
- 本机 Python **3.12.10** / FastAPI **0.135.1** / Pydantic **2.12.5**；未修改依赖或锁文件。依任务书，锁定 FastAPI 0.162 环境仍由后续审核方复跑；路由测试通过 ASGI 发 HTTP 请求，不依赖遍历 app.routes。
- 前端保留已有 React act / Ant Design 弃用等 stderr 提示；全量与构建最终退出码均为 0。

## 线上状态、产物与下一步

- 本轮只提交当前本地分支，未推送、未部署、未备份或改动生产数据库；线上版本与运行状态未核验。
- 本轮工作区初始干净；未覆盖或删除其他任务的文件。本轮代码、测试、基准与报告归本任务。
- `output/spread-events-rework-*.log`、frontend/dist、node_modules、Python 缓存均为忽略产物，不提交。基准数据库位于脚本创建的临时目录，结束自动清理。
- 7 天范围内序列数量本身仍影响内存，基准验收是指定的 100 序列；采样源的筛选和保留期限制未改变。
- 完成本地返工后交由只读审核和锁依赖复跑；若需改采样源、增加历史绑定恢复或部署，请另开对应模块任务，不扩展本次范围。
