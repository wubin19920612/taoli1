# GC 调参：启动后冻结 + 提高分代阈值（2026-11-02）

## 背景（生产 bd9a6ce，只读调查）
- 上一项上线了 ae241bf，它的诊断日志在 1 小时里显示：
  - 有 283 条 gen2 停顿 ≥200ms（日志限频每分钟 5 条，几乎每分钟都记满）；中位 391ms，最大 648ms。
  - 8 次 loop_stall 中，每次正好包含一次 gen2，耗时 420–546ms，约占卡顿时长的一半。
- memory_probe 每 10 秒统计一次 GC 次数，结果为 `gc_collections` ≈ [550, 50, 2–3]，即 gen0 每秒约 55 次，gen2 每分钟约 15 次。按每次约 0.4 秒算，事件循环每分钟约有 6 秒停在全量 GC 上。RSS 约 690–710MB。
- 原因：CPython 3.12 默认阈值是 (700, 10, 10)。采集循环每轮都会生成大量 pydantic 对象（约 9.5k 个市场、3–5k 个机会），它们只要活过约 0.2 秒就会被升进 gen2，下一轮又被替换掉，于是 gen2 的“待处理”比例很快超过 25%，触发全量回收。每次全量回收都要扫描整个堆，包括启动后就不再变化的模块、schema、缓存。

## 要做的事
1. 新增 `backend/app/runtime/gc_tuning.py`，提供一个函数，在 lifespan 里、后台任务启动之后调用一次：
   - 读环境变量：
     - `GC_TUNING_ENABLED`（默认 true）。设为 false 时完全不碰 GC。
     - `GC_THRESHOLDS`（默认 `10000,20,10`），格式为三个正整数。解析失败时保持 CPython 默认值，打一条 warning。
     - `GC_FREEZE_AFTER_STARTUP`（默认 true）。
     - `GC_FREEZE_DELAY_SECONDS`（默认 60，取值 0–600）。
   - 启动时立即调用 `gc.set_threshold(*thresholds)`。
   - 冻结做成延迟的一次性任务，由 supervisor 启动（restart=False，critical=False）：等 `GC_FREEZE_DELAY_SECONDS` 秒，让首轮加载、目录、缓存先建好，然后执行 `gc.collect()`、`gc.freeze()`。用独立的 `gc_tuning` logger 打一条 JSON：`{"gc_tuning":1,"thresholds":[...],"frozen":<gc.get_freeze_count()>,"collect_ms":...}`。logger 的写法和 gc_pause 一样。
   - lifespan 关闭时调用 `gc.unfreeze()`，并恢复原来的阈值，保证测试和多次 lifespan 能还原。只冻结一次；同一进程里重复启动不能重复冻结。
2. 扩展 `gc_pause` 日志：在停顿记录里加上 `collected`（回调 info 里的 `collected`）。回调内仍然只存标量。
3. 在 memory_probe 已有的 JSON 里加两个字段：`gc_frozen`（`gc.get_freeze_count()`）和 `gc_thresholds`（`gc.get_threshold()`），便于上线后对比。

## 不改
- 不改业务代码，不改采集循环，不改各个缓存的上限。
- 不调用 `gc.disable()`，也不手动定时触发 `gc.collect()`。冻结前那一次 collect 例外。

## 验收
- 新测试：
  - 默认值生效、环境变量解析、非法值回退到默认并打 warning、`GC_TUNING_ENABLED=false` 时完全不调用 `gc.set_threshold` 和 `gc.freeze`（用 monkeypatch 检查）；
  - 冻结只在延迟到期后执行一次；lifespan 关闭后阈值恢复、`gc.get_freeze_count()==0`；任务被取消时不会留下冻结。
  - gc_pause 记录里带 `collected`；memory_probe 输出里有两个新字段。
- 全量测试通过（CI 锁定依赖的 venv）。测试结束后进程的 GC 阈值和冻结状态必须还原，不能影响其他测试。
- 本地基准（写进交接记录）：写一个模拟脚本，持有约 300 万个常驻对象（模拟模块和缓存），同时以生产速率循环生成、替换约 1.5 万个 pydantic 对象。分别统计默认 (700,10,10) 和新设置（阈值 + 冻结）在 60 秒内的 gen2 次数、gen2 最大和中位耗时、RSS 峰值。

## 范围
- 可改：新增 `backend/app/runtime/gc_tuning.py`；`backend/app/runtime/gc_pause.py`；`backend/app/runtime/memory_probe.py`；`backend/app/main.py`（只限注册调用那几行）；对应的测试文件；本任务书。
- 不可改：其他文件。

## 测试环境（必须）
- venv：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv`（CI 锁定依赖）。
- 在 `backend/` 下设 `PYTHONPATH` 为 backend 目录，执行 `<venv>\Scripts\python -m pytest -q --basetemp=<scratchpad>\pt-gct1`。
- 不要跑 WSL；不要 git push。

## 输出
- 提交号（提交信息用 `perf: freeze startup heap and raise gc thresholds`）、改动文件清单、红灯/绿灯记录、全量测试汇总行、基准数字。

## 上线后观察（Claude 做）
- 对比 `gc_collections`、`gc_pause` 的条数和耗时、loop_stall 次数、RSS。
- 如果 RSS 比 ae241bf 时上涨超过 15%，或出现内存溢出，就在 .env 里设 `GC_TUNING_ENABLED=false` 并重启。改 .env 前先问用户。

## Codex 本地交接记录

- 日期：2026-11-02；模块：后端 GC 调参与运行时诊断。
- 工作分支：`codex/gc-tuning`；基线：`32eb803eddc73b4c51747c964ff091343f4981ad`；开始时工作区干净。
- 交付范围为本地实现、测试、基准和一次提交；没有运行 WSL、git push 或线上部署，未修改线上配置、数据库或备份。
- 提交信息：`perf: freeze startup heap and raise gc thresholds`。本记录随该提交交付，提交号见 `git log -1 --oneline`。

### 实现与生命周期

- `start_gc_tuning(supervisor)` 立即设置阈值，返回幂等的同步释放函数；`main.py` 只增加导入、后台任务注册后的调用和 finally 中的释放，共三行。
- 默认阈值 `(10000,20,10)`；非法阈值（含溢出、非正数、数量不符）告警并回退 `(700,10,10)`；非法延迟（含 NaN/Inf、越界）告警并回退 60 秒。总开关关闭时不读取或调用 GC API。
- 冻结任务名 `gc-freeze`，`restart=False`、`critical=False`；到期后仅执行一次 collect/freeze，使用独立 `gc_tuning` logger 输出 JSON。
- 活跃的重复启动共享一个任务，通过引用计数在最后一次释放时还原；进程内第一次成功冻结后，即使 lifespan 关闭再开启，也不会再次冻结。冻结前取消不消耗这次机会。
- 退出先同步取消冻结任务、解冻本模块冻结的堆并恢复原阈值，再等待 supervisor 停止，避免等待期间取消导致漏清理。没有关闭自动 GC，也没有周期性手动 collect。
- `gc_pause` 仅增加标量 `collected`，JSON 仍在回调外生成；`memory_probe` 增加 `gc_frozen` 和 `gc_thresholds`。

### 红灯 / 绿灯

所有测试均使用任务书指定的 CI 依赖 venv（Windows CPython 3.12.10），从 `backend/` 执行并设置 `PYTHONPATH` 为该目录。

- 首轮先修改测试、尚未实现运行时代码：`gc_pause` 的 4 项断言因缺少 `collected` 失败，memory probe 的 1 项断言因缺少新字段失败。该轮汇总 `5 failed, 53 passed, 1 deselected, 27 errors in 19.66s`；27 个 setup errors 是首次测试误假定环境冻结数为零，随后修正了隔离方式。
- 修正隔离后，在仍未实现模块时再次验证红灯：子进程 27 项用例全部因 `ModuleNotFoundError: app.runtime.gc_tuning` 报错；外层汇总 `1 failed in 1.90s`，子进程同时确认前后 GC 状态一致。
- 首次实现后：`59 passed, 1 deselected in 20.55s`（含隔离入口，内部另执行 27 项 GC 用例）。
- 复核“同一进程不能重复冻结”后，先加强跨 lifespan 的正常/异常退出测试：`2 failed, 25 deselected in 4.91s`，均因第二次启动仍注册 `gc-freeze` 而失败；再增加进程级成功冻结标记。
- 最终定向验证：`86 passed, 1 deselected in 21.98s`。命令为 `<venv>\Scripts\python -m pytest -q tests/gc_tuning_cases.py tests/test_gc_tuning.py tests/test_gc_pause.py tests/test_memory_probe.py --tb=short --basetemp=<scratchpad>\pt-gct1`。
- 最终全量命令严格使用 `<venv>\Scripts\python -m pytest -q --basetemp=<scratchpad>\pt-gct1`。
- **最终全量汇总行：`2770 passed, 6 deselected, 13 warnings in 146.23s (0:02:26)`。** 全量中的 `test_gc_tuning_isolated_process` 内部执行全部 27 项 GC 用例；6 项 deselected 来自现有 pytest 默认排除的 network/benchmark 标记。13 条 warning 为 Starlette/httpx、`datetime.utcnow()` 的弃用告警。
- 所有本次运行时模块及对应测试文件的 Ruff 检查通过；`git diff --check` 通过；人工复核了文件范围和三行 lifespan 接入。

### 测试 GC 状态还原

- 本机 Python 初始 `gc.get_freeze_count()` 为 377；实测 `gc.unfreeze()` 后为 0，下一次 `gc.collect()` 后又为 377。因此不能把这 377 个 CPython 运行时永久对象误判为本模块残留。
- 全量通过独立 Python 子进程执行真实冻结测试，避免影响父测试进程的永久代。各用例 finally 中释放调参会话，先断言阈值和冻结堆已还原，再做失败兜底清理；仅测试代码通过 collect 恢复运行时永久对象基线。
- 真实 lifespan 的第一次冻结结束后明确断言 `gc.get_freeze_count()==0`，覆盖正常/异常退出；取消用例验证没有执行 freeze，且保留进入时的 GC 状态。
- 隔离入口同时检查子进程最终状态与初始状态相同，以及父进程状态未变；已观察到 `GC_STATE_RESTORED ((700, 10, 10), 377) ((700, 10, 10), 377)`。

### 60 秒本地基准

- 脚本：`backend/tests/benchmark_gc_tuning.py`；复现：在同一 venv、同一 `PYTHONPATH` 下执行 `python tests/benchmark_gc_tuning.py`。默认不会被 pytest 收集。
- 默认组和调参组使用两个独立进程，串行运行；每组保留 3,000,000 个独立、GC 跟踪的常驻 list 对象，每轮创建并替换 15,000 个 Pydantic Snapshot，旧批次活到新批次构造完成。
- 依据任务书的生产 gen0 约 55 次/秒先校准批次间隔，两组均使用 `1.049090909090909` 秒，60 秒各完成 58 轮；默认组实测 gen0 约 55.9 次/秒。
- 调参组使用真实 `start_gc_tuning`，将启动延迟设为 0，在常驻堆及首批快照构造后执行冻结，然后开始 60 秒计时。预热 collect 和一次性冻结不计入稳态 gen2 次数；初始化成本另记。
- RSS 使用 Windows `GetProcessMemoryInfo.PeakWorkingSetSize`，单位 MiB，是各独立进程生命周期峰值，包含初始化。

| 指标 | 默认 `(700,10,10)` | 新阈值 `(10000,20,10)` + 冻结 |
| --- | ---: | ---: |
| 实测窗口（秒） | 60.012 | 60.006 |
| gen0 / gen1 / gen2 次数 | 3354 / 304 / 2 | 222 / 10 / 0 |
| gen2 最大耗时（ms） | 141.746 | 无样本 |
| gen2 中位耗时（ms） | 141.132 | 无样本 |
| RSS 峰值（MiB） | 321.805 | 322.098 |
| 窗口开始时冻结数 | 377 | 3,059,770 |
| 调参任务初始化总耗时（ms） | 不适用 | 169.367 |
| 冻结前 collect 耗时（ms） | 不适用 | 147.180 |
| 清理后阈值 | `(700,10,10)` | `(700,10,10)` |
| 清理后冻结数 | 377（运行时基线） | 0 |

- RSS 峰值增加约 0.09%；调参组没有 gen2 样本，不将其最大/中位耗时虚报为 0 ms。
- 这是本地合成负载，不代表生产堆结构、硬件或真实循环延迟；生产对比和发布仍由 Claude 按任务书后续执行。最终补充的“跨 lifespan 只冻结一次”标记不改变本基准单次启动的执行路径。

### 改动文件与后续

1. `backend/app/runtime/gc_tuning.py`（新增）：环境解析、一次性冻结、进程去重与退出还原。
2. `backend/app/runtime/gc_pause.py`：增加 collected。
3. `backend/app/runtime/memory_probe.py`：增加冻结数和阈值。
4. `backend/app/main.py`：三行生命周期接入。
5. `backend/tests/gc_tuning_cases.py`（新增）：27 项 GC 验收用例，供隔离入口运行。
6. `backend/tests/test_gc_tuning.py`（新增）：隔离子进程及父/子 GC 状态断言。
7. `backend/tests/benchmark_gc_tuning.py`（新增）：独立进程 60 秒模拟脚本。
8. `backend/tests/test_gc_pause.py`：collected 回归断言。
9. `backend/tests/test_memory_probe.py`：新诊断字段回归断言。
10. `docs/gc-tuning-spec-2026-11-02.md`：仅在末尾追加本记录。

无本任务以外的修改或需要保留的未跟踪产物；新增脚本均随本地提交交付。后续在独立运维任务中由 Claude 完成发布和生产观察；本任务没有执行推送、备份、部署或线上验收。
