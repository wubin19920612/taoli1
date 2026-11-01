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
