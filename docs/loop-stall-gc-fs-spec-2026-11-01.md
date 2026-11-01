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

## Codex 本地交接记录

- 日期：2026-11-01；模块：事件循环停顿诊断与 FS 响应解析。
- 分支：`codex/loop-stall-gc`；开始基线：`14b62ae`，开始时工作区干净、无未跟踪文件。
- 交付方式：仅本地修改、验证和一次提交，提交信息为 `perf: move fs json parsing off the loop and log gc pauses`。未运行 WSL，未执行 git push，未连接或部署生产环境。

### 实现与边界

- 新增 `GcPauseMonitor`：使用 `gc.callbacks` 和 `time.perf_counter` 累计所有代的 GC 耗时及 gen2 次数；回调不写日志，最多保留 5 个轻量停顿记录。独立任务延后输出紧凑的 `gc_pause` JSON，默认阈值 200 ms，滚动 60 秒最多 5 条，失败的写入也消耗限频额度。环境变量按任务书实现，关闭停顿日志仍保留 watchdog 所需计数。
- watchdog 在心跳时保存 GC 累计快照，恢复后在原有 JSON 中加入区间差值 `gc_ms` 和 `gc_gen2`，覆盖观察线程取得 GIL 前已经完成的回收，并排除恢复心跳之后的回收。独立 GC 任务不依赖 watchdog 开关，随应用 lifespan 注册和注销回调。
- 两个 FS `get()` 均读取响应 `content` 后通过 `asyncio.to_thread(json.loads, content)` 解码。Binance 规格提取为纯函数并在线程中执行；异常仍在原读取位置暴露。以一次性 holder 交接原始字典，避免 `to_thread` 的 traceback 参数留住整份数据；调用方和提取函数均保留 `finally` 释放逻辑。
- 缓存键、20 秒 TTL、规格时间戳及 `source_times`、请求锁和限频、错误映射保持原样；未调用 `gc.freeze()` 或调整 GC 阈值，未修改采集、校验或市场身份模块。

### 红灯 / 绿灯记录

先只修改测试，再运行新增及相关回归测试，之后才修改实现。以下命令均在 `backend/` 下执行，`PYTHONPATH` 为该目录；`$scratchpad` 为本任务书指定 venv 的父目录，`$python` 为 `$scratchpad\venv\Scripts\python.exe`。

```powershell
$env:PYTHONPATH = (Get-Location).Path
& $python -m pytest -q --tb=short --basetemp="$scratchpad\pt-lsg1-red" tests/test_gc_pause.py tests/test_loop_stall_watchdog.py tests/test_fs_threading.py tests/test_fs_public_spec_cache.py
```

- 红灯：`7 failed, 112 passed, 20 errors in 26.77s`。7 条失败为 watchdog 缺少 GC 计数、5 个交易所 JSON 解码仍在主线程、Binance 规格提取仍在主线程；20 条 setup error 为尚未实现的 `app.runtime.gc_pause`。错误映射、逐字段兼容和畸形 filters 读取行为的回归用例已通过。
- 首次实现检查：`2 failed, 137 passed in 25.14s`。异常 traceback 仍通过 `to_thread` 的参数保留原始字典；保留原断言并修复引用交接。
- 最终绿灯：上述命令将 basetemp 改为 `$scratchpad\pt-lsg1-green`，结果 `140 passed in 24.40s`。包含额外的 GC 心跳区间边界测试。
- 覆盖：真实 `gc.collect(2)` 的累计耗时与 gen2 次数、各代停顿、阈值与滚动限频、开关和非法配置回退、回调及日志异常隔离、回调生命周期、watchdog 关闭时独立 GC 监控、解码及提取线程 ID、逐字段兼容、畸形 filters、TTL、错误映射和原始数据释放。
- 本地原始日志位于 `$scratchpad\lsg-red.txt`、`lsg-green.txt`、`lsg-full.txt`、`lsg-benchmark.txt`、`lsg-ruff-main.txt`，未纳入 Git。

### 全量测试

严格使用任务书指定的 CI 锁定依赖 venv，命令如下：

```powershell
$scratchpad = 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\fb63363d-eacd-45e5-80aa-d9d6ede68e74\scratchpad'
$python = "$scratchpad\venv\Scripts\python.exe"
$env:PYTHONPATH = (Get-Location).Path
& $python -m pytest -q --basetemp="$scratchpad\pt-lsg1"
```

全量汇总原文：

```text
2716 passed, 6 deselected, 13 warnings in 131.21s (0:02:11)
```

- 6 条 deselected 来自仓库默认排除的 `network` / `benchmark` 标记。13 条 warning 是测试输出中的依赖弃用提示。
- 补充检查：`git diff --check` 通过。两个 runtime 文件、两个 FS 文件及四个对应测试文件的 Ruff 检查均为 `All checks passed!`。
- `main.py` 的独立 Ruff 检查有 13 项既有问题：`B023 × 7`、`I001 × 1`、`RUF100 × 3`、`SIM114 × 1`、`UP035 × 1`。对 `git show 14b62ae:backend/app/main.py` 和当前内容使用相同 Ruff 配置检查，规则及消息的多重集合完全一致，无新增问题；未扩大范围修复。

### 本地基准

- 环境：同一指定 venv，Windows Python `3.12.10`。
- 输入：既有 `exchange_info` fixture 扩为 3000 个 symbol，紧凑 UTF-8 JSON 共 `6,123,870` bytes。使用相同的预生成响应字节及 httpx MockTransport，无外网、无请求限频等待，每轮清空规格缓存。旧实现通过 `git show 14b62ae:backend/app/services/fs_public.py` 加载，与新实现依次在同一进程比较。
- 方法：预热 3 对、测量 15 对，交替先后顺序；每次调用前在计时区间外 `gc.collect()`，未更改 GC 配置。独立 `asyncio.sleep(0)` 心跳测量一次冷缓存 `binance_specs(False)` 调用中的最大调度间隔；包装 `json.loads` 记录执行线程和耗时。每对规格结果逐字段相等（响应接收时间不参与比较）。

| 指标（ms） | 改前中位数 | 改后中位数 | 改前最大值 | 改后最大值 |
| --- | ---: | ---: | ---: | ---: |
| 主循环最大调度间隔 | 49.519 | 30.713 | 55.143 | 32.519 |
| 完整规格调用耗时 | 49.485 | 50.759 | 55.108 | 60.816 |
| JSON 解码直接在主线程执行的耗时 | 30.138 | 0.000 | 32.997 | 0.000 |

主循环最大调度间隔的中位数降低约 38%。JSON 解码及规格提取的线程 ID 测试确认移到工作线程，但 CPython JSON 解码仍存在 GIL 竞争，不能据此声称主循环阻塞归零或整次调用加速。这是本机合成大响应基准，不是生产卡顿成因或改善幅度的证明；生产是否由 GC 导致仍需后续独立任务采集诊断日志确认。

### 改动文件

1. `backend/app/runtime/gc_pause.py`（新增）
2. `backend/app/runtime/loop_stall_watchdog.py`
3. `backend/app/main.py`（仅 GC 监控注册及与 watchdog 共享实例）
4. `backend/app/services/fs_public.py`
5. `backend/app/services/fs_bybit.py`
6. `backend/tests/test_gc_pause.py`（新增）
7. `backend/tests/test_loop_stall_watchdog.py`
8. `backend/tests/test_fs_threading.py`（新增）
9. `backend/tests/test_fs_public_spec_cache.py`
10. `docs/loop-stall-gc-fs-spec-2026-11-01.md`（仅追加本记录）

后续若需上线或根据 GC 数据决定优化方案，请另开任务；本任务的验收边界为上述本地验证与一次提交。
