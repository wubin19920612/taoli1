# M1 内存剖析 A：测量探针交接（2026-10-25）

## 目标、范围与交付状态

- 规格：`docs/m1-memory-profiling-spec-2026-10-25.md` §3 A1/A2/A3、§4。
- 分支：`codex/memory-probe`，基线及当前 HEAD：`0bdf7ade530e367d0ad34e4fcbdf5f43aaf3040c`。未切换或新建分支。
- 只实现测量探针；没有业务判断、缓存治理、B 的环境设置或 Compose 改动。
- **未暂存、未提交、未推送、未 SSH、未备份线上数据库、未部署、未验证线上。** 线上版本与运行状态没有在本任务中查询或改变。
- 三个配置项保持代码默认值；没有改 `.env`。tracemalloc 测试使用伪造模块和时钟，没有实际开启追踪。

## 改动文件与实现入口

| 文件 | 实现 |
|---|---|
| `backend/app/runtime/memory_probe.py`（新） | A1 `read_process_status`、`MemoryProbe`；A2 `TaskRSSDeltas`；A3 `TracemallocWindow`；安全 JSON 日志 |
| `backend/app/runtime/supervisor.py` | `TaskStatus.last_rss_anon_mb/last_rss_delta_mb`；`enable_memory_probe`、`_beat_with_memory`、`task_rss_deltas` |
| `backend/app/services/snapshot_store.py` | 只读 `sizes()`，锁内读取三个长度；保留 `all_markets is None` 时回退到 markets 的语义 |
| `backend/app/core/config.py` | 三个配置项、环境读取与有限数值校验 |
| `backend/app/main.py` | lifespan 中启用 A2，注册非 critical 的 `memory-sampler` 和 `memory-tracemalloc` |
| `backend/tests/test_memory_probe.py`（新） | 配置、关闭路径、采样、GC、脱敏、任务窗口、定时、性能、生命周期和伪造 tracemalloc 测试 |
| 本文（新） | 本地交接、测试与限制 |

`snapshot_consumers.py` 无需修改：继续使用 S2 的 `SnapshotConsumer` 心跳。A1 直接观察同一个 supervisor 的 `last_iteration_at`，可以关联 `history-recorder`、`index-components` 等任务。缓存规模直接调用 S1 已有 `realtime_cache_stats()`；没有修改 `pair_spread_query.py`。

## 配置与行为

| 环境变量 | 默认值 | 行为 |
|---|---|---|
| `MEMORY_PROBE_ENABLED` | `false` | 同时控制 A1 采样和 A2 心跳 RSS；布尔解析复用现有 `bool_env` |
| `MEMORY_PROBE_INTERVAL_SECONDS` | `10` | 浮点秒，必须有限且大于 0；采样任务开始后立即做首轮采样 |
| `MEMORY_TRACEMALLOC_SECONDS` | `0` | 浮点秒，必须有限且不小于 0；大于 0 时启动独立、一次性的 A3 窗口 |

- 与现有数值配置一致，通过 `os.getenv` 和 `float` 读取。A3 与 A1 开关独立；`start_background_workers=False` 时两个任务都不启动。
- 两个开关关闭时，main 不导入探针模块。`TaskSupervisor.beat()` 原函数体没有变化；只有显式启用时才替换该 supervisor 实例的 `beat`，没有默认路径上的开关检查、额外调用或 `/proc` 读取。已与 HEAD 对比 AST 确认。
- A1 仅保存任务名、心跳时间及三个 GC 代的计数。快照 getter 会复制，因此改用 `sizes()`；不访问 `gc.get_objects()`、不复制行情列表、不持有采样时的快照列表或缓存内容。
- Linux 逐行读 `/proc/self/status` 的 `VmRSS`、`RssAnon`、`Threads`。RSS 数值除以 1024，实际单位为 MiB；线程数包含原生线程。非 Linux/读取失败时三个字段为 `null`，格式错误的字段也为 `null`。
- `gc_counts` 是三个代的当前 `gc.get_count()`；`gc_collections` 是三个代自上次成功采样的 collections 增量，首轮以探针构造时为基线。
- `active_tasks` 仅包含两次成功采样之间心跳时间变化的任务名；首轮以探针构造时为基线。失败心跳也计入活动；不能由此证明任务独占了内存尖峰。
- A2 比较**同一个任务前后两次心跳时整个进程的匿名 RSS**。第一次及不可读时 delta 为 `null`，不可读会重置该任务的比较基线。它是相关性信号，**不是任务分配量或泄漏的精确归因**，也不是相邻两个不同任务心跳之间的 delta。
- 各任务保留最近 **N=120 个有效 delta**，`deque(maxlen=120)` 有界；每 60 秒输出 `samples/max/p50`，p50 复用 S1 的 nearest-rank 分位数。未获得有效 delta 的任务也输出零样本与 `null`。60 秒汇总独立于采样间隔；定时器提前唤醒不会虚增采样心跳。
- `memory-sampler` 可由 supervisor 重启，单轮测量异常只输出数字错误标记并继续。A2 异常不会阻断原业务心跳。两个新增任务都非 critical。
- A3 用 `start(1)`，开始时取基线快照，此后每 60 秒输出 top 25 `lineno` 及相对前一快照的 top 25 差异；差异行的 `size_kb/count` 是有符号增量。到期、取消或取快照失败均在 finally 调用 stop 并释放快照引用；stop 本身失败只记数字标记。窗口不自动重启；已有外部追踪会跳过，避免停止别人的 session。

## 日志格式

logger 名 `memory_probe`，INFO。无应用日志 handler 时安装一个 stderr handler，兼容默认 Uvicorn；每条消息是紧凑单行 JSON。所有消息包含数字标记 `"memory_probe":1`，可按规格用 `docker logs` 的输出筛选。以下均为虚构计数示例。

```json
{"memory_probe":1,"rss_mb":625.0,"rss_anon_mb":600.0,"threads":17,"gc_counts":[300,2,1],"gc_collections":[2,0,0],"snapshot_sizes":{"markets":12000,"all_markets":12500,"opportunities":7000},"realtime_caches":{"_REALTIME_PAIR_SPREAD_CACHE":{"keys":5,"points":120},"_REALTIME_PAIR_FUNDING_CACHE":{"keys":2,"points":40},"_REALTIME_PAIR_OPEN_INTEREST_CACHE":{"keys":1,"points":20},"_REALTIME_SYMBOL_SPREAD_CACHE":{"keys":3,"points":60}},"active_tasks":["history-recorder","market-collector"]}
{"memory_probe":1,"task_rss_deltas":{"market-collector":{"samples":120,"max":25.0,"p50":0.5},"history-recorder":{"samples":0,"max":null,"p50":null}}}
{"memory_probe":1,"tracemalloc_top":[{"location":"collector.py:100","size_kb":1024.0,"count":120}],"tracemalloc_diff":[{"location":"collector.py:100","size_kb":-64.0,"count":-8}]}
{"memory_probe":1,"sample_errors":1}
{"memory_probe":1,"beat_errors":1}
{"memory_probe":1,"tracemalloc_errors":1}
{"memory_probe":1,"tracemalloc_skipped":1}
```

除静态 JSON 字段名，动态值只有数字、null、任务名和文件:行号。缓存名称是现有统计函数返回的固定代码标识，不是用户缓存键。不输出异常消息、堆栈源码、行情对象、缓存键、密钥或 URL。A3 只取 frame 的文件名与行号，不调用 traceback 格式化或 linecache。

## TDD 与验证

Windows PowerShell，所有 pytest 均在 `backend` 目录运行，统一先设置：

```powershell
$env:PYTHONPATH='tests'
```

每次运行均使用新的 basetemp，不清理旧目录。下列为完整文件验证命令及结果：

| 命令 | 结果 |
|---|---|
| `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1_n -p s1_offline_guard tests/test_memory_probe.py` | 39 passed（62.07s） |
| `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1_o -p s1_offline_guard tests/test_memory_probe.py -k sampling_cost` | 1 passed，38 deselected（0.55s）；最终将性能断言加强为每次 <5ms 后复核 |
| `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1_e -p s1_offline_guard tests/test_task_supervisor.py` | 18 passed（45.54s） |
| `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1_f -p s1_offline_guard tests/test_snapshot_consumers.py` | 27 passed（46.52s） |
| `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1_g -p s1_offline_guard tests/test_health_routes.py` | 13 passed（40.35s） |
| `python -m pytest -q -p no:cacheprovider --basetemp=.pytest_m1_h -p s1_offline_guard tests/test_collector.py` | 24 passed（17.08s） |
| `python -c "import app.main"` | 退出码 0 |
| `python -m ruff check app/runtime/memory_probe.py tests/test_memory_probe.py --output-format concise` | All checks passed |
| `git diff --check` | 通过；仅 Git 的 LF/CRLF 提示 |

- 先新增测试，`.pytest_m1_a` 在没有实现模块时按预期收集失败；随后实现。后续新增 Uvicorn 日志、60 秒独立时序、null 汇总、提前唤醒和错误隔离测试，均先观察失败再修复。
- 中间目录 `.pytest_m1_b` 到 `.pytest_m1_m` 对应增量验证，含预期红灯以及一次提前唤醒造成的心跳计数失败，均保留。最终状态以上表为准，不隐藏旧失败证据。
- 网络隔离插件用于所有 pytest；未跑 `tests/test_api.py` 整文件，未调用 WSL。
- tracemalloc 到期、取消、失败、top25 及差异、已有 session、单帧启动均用伪造接口验证；未实际启用追踪。
- 五个规定文件合计 121 项通过。大快照测试建立 12,000 项列表，禁止调用复制 getter 和 `gc.get_objects`，计时覆盖 JSON 构建与日志调用；最终性能断言为连续 30 次采样的最大耗时 <5ms。

## 已知限制、与规格的关系及下一步

- 没有功能性范围偏离；选择 N=120，增补数字日志标记与错误标记；仅 basename:行号用于 A3，避免记录宿主完整路径，同名文件需结合代码定位。
- 采样性能只能证明本机固定数据规模的测试结果，尚未测量 Linux 容器真实 `/proc` I/O、生产缓存规模或高负载下的单次开销。`realtime_cache_stats()` 需遍历现有缓存 series，规模增长可能使开销增加；本任务不更改其业务或缓存策略。
- tracemalloc 全量快照及统计会占用额外内存；只保留上一份快照，输出最多各 25 行。单线程事件循环忙时，60 秒报告与到期停止会受调度延迟影响。A3 本轮保持关闭。
- 未采集生产基线，未证明任何 H1–H4 假设，未修复内存增长或 OOM；后续由新的上线任务按规格验证 A，再另开 B 实验或 M1-C。
- 用户 WIP `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 未修改/暂存/还原；SHA256 与开始时一致。规格文件 SHA256 也一致。暂存区保持为空。
- 原有未跟踪内容包括 `.worktrees/`、既有 `.pytest_*` 目录、`docs/architecture-review-2026-10-23-claude.md`、规格文件、旧交接与大量 `output/` 证据、数据库和日志，均保留。新增的本模块文件与测试目录也保持未跟踪；没有删除或清理任何产物。
- 下一模块/上线应使用新任务并先读本文；本任务不执行 Git 交付或线上操作。
