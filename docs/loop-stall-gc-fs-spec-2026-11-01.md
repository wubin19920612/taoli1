# 剩余事件循环卡顿：GC 停顿诊断 + FS 大响应移出主循环（2026-11-01）

## 背景（生产 5d53df6，只读调查已确认）
- 5d53df6 运行约 3 小时：loop_stall 共 5 次，每次 1.04–1.19 秒。单次采样的栈分别落在：
  - `fs_monitor.load → fs_public.public_snapshot → borrow_reference → binance_specs(394)`
  - `public_snapshot → borrow_reference(164)`
  - `market_validation_review.validate → pair_validation.validate_pair → MarketKey.from_snapshot → normalize`
  - `validate_pair → block`
  - uvicorn `main_loop sleep`
- 采集循环已经每 20ms 让出一次（`runtime/cooperative.py` YieldBudget），所以一次 `validate` 不应该占用 1 秒。上面这些栈都在大量分配对象的地方（pydantic 构造、构建字典），看起来像是 Python **全代 GC（gen2）停顿**：进程约 550MB，memory_probe 显示每个采样区间有 gen2 回收。**这一点还没证实**，所以本任务先加诊断。
- 有一处确定在主循环上做重活：`fs_public.get()` 的 `r.json()` 和 `binance_specs` 的规格提取都在事件循环里同步执行。Binance 现货 `/api/v3/exchangeInfo` 是几 MB 的 JSON，缓存 20 秒，过期后整份重新解析。`fs_bybit.get()` 的 `response.json()` 也一样。

## 要做的事
1. **GC 停顿诊断**（`backend/app/runtime/loop_stall_watchdog.py`，必要时加一个小的 runtime 模块）
   - 用 `gc.callbacks` 记录每次回收的代数和耗时（start/stop 用 `time.perf_counter`）。回调只做计数和加法，不分配大对象，也不打日志。
   - 写进 loop_stall 日志：在已有 JSON 里加 `gc_ms`（卡顿区间内 GC 的总耗时）和 `gc_gen2`（区间内 gen2 次数）。
   - 另加一条独立的 JSON 日志 `{"gc_pause":1,"gen":2,"ms":...}`：只在单次停顿 ≥200ms 时输出，并按分钟限频（每分钟最多 5 条）。logger 写法和 `loop_stall` 一致（setLevel INFO、propagate=False、自带 StreamHandler）。
   - 开关：`GC_PAUSE_LOG_ENABLED`（默认 true），阈值 `GC_PAUSE_LOG_THRESHOLD_MS`（默认 200）。
2. **FS 大响应移出主循环**（`backend/app/services/fs_public.py`、`backend/app/services/fs_bybit.py`）
   - `get()`：读取 `r.content` 后用 `await asyncio.to_thread(json.loads, content)` 解析。httpx 已经把响应体读完了，这里只把解析挪进线程。小响应也一样处理，保持写法统一。
   - `binance_specs`：把从 `data["symbols"]` 提取规格的部分（现在 370–395 行）提成一个纯函数，用 `asyncio.to_thread` 执行。异常语义保持不变（畸形条目照样在读取方报错，不能在共享缓存刷新时报错），也保留 `finally` 里释放原始数据的写法。
   - 缓存键、20 秒 TTL、限频（`self.lock` / `_pace`）、错误码映射都不改。

## 不改
- 不加 `gc.freeze()`，也不调 GC 阈值。等诊断数据出来、确认是 GC 之后，再另开任务决定。
- 采集循环、`market_validation_review`、`pair_validation`、`MarketKey` 都不改。
- FS 规格的 TTL 和 `source_times` 不改（见 taoli1-fs-spec-ttl：时间戳超过 60 秒会被判为 stale_input）。

## 验收
- 新测试：
  - gc 回调能累计耗时：手动 `gc.collect(2)` 之后，watchdog 区间内 `gc_ms>0`、`gc_gen2≥1`；
  - 停顿日志的阈值和限频都生效，回调里抛异常不会影响应用；
  - `fs_public.get` / `fs_bybit.get` 的 JSON 解析在非主线程执行（mock `asyncio.to_thread` 或检查线程 id）；
  - `binance_specs` 的输出与改之前逐字段一致（用现有测试 fixture，外加一个畸形 filters 用例）；
  - 原有错误映射测试全部通过。
- 全量测试通过（CI 锁定依赖的 venv）。
- 基准（写进交接记录）：本地用一份真实大小的 exchangeInfo（可以用 fixture 复制扩充到约 3000 个 symbol），对比改前和改后主线程上阻塞的毫秒数。

## 范围
- 可改：`backend/app/runtime/loop_stall_watchdog.py`、新增 `backend/app/runtime/gc_pause.py`（可选）、`backend/app/main.py`（只限注册/启动 gc 监控那几行）、`backend/app/services/fs_public.py`、`backend/app/services/fs_bybit.py`、对应的测试文件、本任务书。
- 不可改：其他文件。

## 测试环境（必须）
- venv：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad\venv`（CI 锁定依赖）
- 运行：在 `backend/` 下设 `PYTHONPATH` 为 backend 目录，用 `<venv>\Scripts\python -m pytest -q --basetemp=<scratchpad>\pt-lsg1`
- 不要跑 WSL；不要 git push。

## 输出（最后给我）
- 提交号（信息用 `perf: move fs json parsing off the loop and log gc pauses`），改动的文件清单，红灯/绿灯测试记录，全量测试汇总行，基准数字。
