# 影子第五轮补充二（PM 42–45）修复与交接

**验收未通过。** 四项功能修复及全量正确性测试通过，但同步冻结使 production 明显退化，不能声称达到“没有变差”。按要求只跑一次 production，保留失败数据，不放宽预算、不挑选重跑、不扩展其他设计。

## 范围和基线

- worktree：`taoli1/.worktrees/shadow-ledger`；分支：`codex/shadow-ledger`。
- 基线：`3f1e6112a6e593bcbe3b73f223d060ea57b01157`；开始时工作区干净。
- 先阅读 `task-handoff-2026-11-06-shadow-incremental.md` 与 spec 33–41；spec 末尾原文追加 42–45。
- 仅处理关闭通知、关闭期限、统计 IPC 和真实 spawn 输入冻结。没有修改账本规则、前端、Compose、部署文件或 `.env`。
- `main.py` 只调整影子 aclose 周围的异常隔离和 finally；`test_route_permissions.py` 只调整影子鉴权测试的业务桩，不修改鉴权断言。
- 不执行 WSL、SSH、push、部署、线上备份或线上验证。线上版本未查询。

## 分离的提交

- `9376e2d`：test，持锁退出、挂起、kill 升级、lifespan、十万历史行和真实 spawn 冻结回归，调整测试 harness 的统计参数入口。
- `463fc7d`：test，线程池排队期限与影子鉴权隔离桩。
- `c4ae0b8`：feat，四项修复。
- 本文和 spec 另作 docs 提交，hash 见最终回复或 `git log`。

## 修复方式和关键入口

42. `shadow_runtime.py::_spawn/_shutdown/_dispose`、`shadow_worker.py::run_child`：不再创建或访问 multiprocessing Event。通过已有 incoming 队列 `put_nowait(("stop", 0, b""))` 通知空闲子进程退出；接收 stop 后仍执行原落盘 finally。回收清空队列和旧通知槽位；退避时对象为空，不访问已回收对象。保留第四个 spawn 参数为 None，兼容现有注入测试入口，不持有共享通知锁。

43. `aclose` 在等待监督任务前建立绝对期限：10 秒优雅期（含监督等待和已接受轮次排空）+ 2 秒 terminate join + 2 秒 kill join + 最多 0.5 秒 reader 收尾，总预算 14.5 秒。所有阻塞 join 在线程中，外层 wait_for 同时约束线程池排队时间。故障回收也使用同一 terminate/kill 阶梯；dispose 通过 asyncio.Lock 串行化。关闭异常限频记 error；`main.py` 的影子关闭调用使用 try/except/finally 保证继续关闭其他资源。已有调用者取消后等待子进程清理、再传播 CancelledError 的行为保留。

44. `routes_shadow.py::get_buckets` → `ShadowRuntime.statistics` → `shadow_worker.py::_statistics`：只发送 since/until/dims/include_fake/strategy_version。子进程开独立只读 SQLite 连接，沿用 `closed_events` 的时间、fake、版本和 retired 过滤，再调用原 buckets。成功输出字段、数值、完整曲线均保持；JSON 和 pickle 各限制 256 KiB，超限沿用 503，不截断结果。反序列化在已有 reader 线程完成。主进程没有历史行读取、遍历或 pickle；最终 API 序列化只面对有上限的结果。统计不推进行情 sequence，也不因统计错误破坏行情镜像。

45. `ShadowRuntime.submit` 在返回及第一次让出前脱离本轮市场、机会和 catalog 输入。市场字典内容未变化时复用上轮已脱离的副本；estimated_fields 独立复制，机会嵌套列表转 tuple。同一冻结数据供阈值、目录、机会和报价的所有分片使用；仍只发送目录/报价差量，保留 DEX、raw_symbol、两种 multiplier 等身份字段。冻结开销纳入 capture/main CPU/单段计时，没有藏到计时之外。

## 新增回归和现有测试适配

`test_shadow_shutdown_stats_freeze.py` 共 9 个参数展开用例：

- 子进程持有通知/队列读锁时被杀，分别在立即关闭和已进入重启退避时验证关闭，心跳间隔 <250ms，总时长 <15s。
- Windows 使用 NtSuspendProcess 挂起真实子进程；POSIX 分支发送 SIGSTOP。当前环境实际执行 Windows 分支，不把它说成已执行 POSIX 信号测试。
- terminate 无效果时实际 kill，验证 process.join 在非主线程调用且单次 timeout ≤2s。
- 监督异常仍回收、线程池无法调度仍有期限、lifespan aclose 抛异常仍关闭其他资源。
- 真实 spawn 分片期间原地修改后面的报价、estimated_fields、机会阈值和 routes，仍按原输入开两行仓；分钟全量包含 1203 市场。
- SQLite 插入 100,000 条闭仓历史，禁止主进程 read_committed，验证统计请求 <1024 字节、查询期间心跳间隔 <100ms、超限返回 503 且无需重启；删除大数据夹具后逐字段比较原 buckets 的完整结果。

测试 harness 继续承载子进程核心故障注入，其统计入口改接查询参数，保留原只读隔离和错误计数断言。影子鉴权测试同步 mock 新统计边界，原 200/401 和响应断言不变。既有基准文件和断言完全未修改。

## 验证环境与原始输出

Windows Python 3.12.10，venv，pytest 8.4.2。核对 requirements.lock 和 requirements-dev.lock：除 Windows 不支持的 uvloop 缺失外，所有已列版本一致。正式解释器为原有 scratchpad/venv-lock/Scripts/python.exe；没有安装或升级依赖。

从 backend 执行，`PYTHONPATH=.`，`-p no:cacheprovider`，临时目录限定到本工作区 `.pytest-tmp/scratchpad/review42-*`。日志保存在忽略的 `output/shadow-review42-*.log`，不提交、不删除旧产物。

初次红测在旧实现上实际复现冻结与主进程统计读取：

```text
2 failed, 3 deselected in 10.10s
```

最终专项（新增回归及鉴权测试）：

```text
44 passed in 51.28s
```

第一次全量：

```text
2 failed, 2651 passed, 9 deselected, 13 warnings in 191.75s (0:03:11)
```

两项失败均为影子鉴权测试仍只 mock 原 read_committed 边界，已在 463fc7d 适配新统计边界；未放宽断言。后续最终全量与一次 production 原始结果见下文。

最终全量（feat c4ae0b8）：

```text
2654 passed, 9 deselected, 13 warnings in 198.25s (0:03:18)
```

默认 network/benchmark marker 的排除规则保持不变。13 条 warnings 为既有 Starlette/httpx 与 datetime.utcnow 弃用警告，没有新增未处理线程异常警告。

唯一一次 production（与全量串行、同一 feat 提交）：

```text
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [36.39999986626208, 5.844499566592276, 1.922299969010055], "child_rss_mb": 88.39453125, "event_loop_median_ms": 47.125300276093185, "event_loop_segment_median_ms": 33.79839996341616, "main_cpu_median_ms": 46.875, "scenario": "production", "system_cpu_before_pct": 16.666666666666664, "worker_wall_median_ms": 20.67379979416728}
1 failed, 2 deselected in 8.41s
```

| 指标 | 上轮三次 production | 本轮一次 | 判断 |
| --- | --- | --- | --- |
| 整轮主循环中位 ms | 12.906 / 13.088 / 14.966 | 47.125 | 明显退化 |
| 最大单段中位 ms | 2.265 / 2.390 / 2.816 | 33.798 | 明显退化，超过 3ms |
| 阈值阶段（含新增冻结）中位 ms | 1.593 / 1.645 / 2.690 | 36.400 | 主要新增成本 |
| 目录/机会阶段中位 ms | 7.077 / 6.803 / 8.112 | 5.844 | 内容复用减少目录比较 |
| 报价阶段中位 ms | 1.554 / 1.579 / 1.747 | 1.922 | 有增加 |
| worker 墙钟中位 ms | 16.017 / 15.486 / 16.438 | 20.674 | 增加，仍小于 30ms |
| 主进程 RSS 增量 MiB | 上轮全部场景 4.988–13.703 | 59.770 | 超过 30MiB |

本轮 P95=118.885ms，最大连续占用=141.217ms，分钟全量轮合计=175.172ms；Windows thread_time 中位 46.875ms 有 15.625ms 量化，以上判断以 perf_counter 墙钟为准。基准首先在 RSS 断言失败，后面的预算断言未执行；这里只按已经打印的原始指标比较，不把未执行断言宣称通过。上轮比较来自既有交接，不是本轮重新跑出的对照样本。虽然系统负载不同，当前幅度足以判为退化，不能归结为测量噪声。

运行命令（backend，$shadowPython 为上述已核对的 venv 解释器）：

```powershell
$env:PYTHONPATH='.'
& $shadowPython -m pytest tests/test_shadow_shutdown_stats_freeze.py tests/test_route_permissions.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/review42-target-final
& $shadowPython -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/review42-full-final
& $shadowPython -m pytest tests/test_shadow_benchmark.py -m benchmark -k production -s -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/review42-production-once
```

`git diff --check` 与影子 runtime/worker/IPC/新回归/harness 的 Ruff `--select F` 通过。扩大 Ruff 到鉴权测试时发现该文件已有未使用的 HTTPException 导入（基线 3f1e611 已存在），没有顺手修改；未宣称项目完整 Ruff 检查通过。

## 剩余问题、线上状态与下一步

- 本提交修复了原地修改混入一轮的问题，但当前保留前后两代脱离字典、同步复制机会字段的方案增加了主线程占用和 RSS；它不是可接受的性能修复。不能把本轮当作生产开启依据，也不能说已满足 PM 40 或“没有变差”。
- 后续需要在相同语义下设计廉价、可靠、与快照绑定的输入版本/冻结边界，再恢复有界分片；不能只用对象身份或 timestamp（会漏掉同 timestamp 原地改价与嵌套列表修改）。是否允许最小生产者/store 版本机制，需要 PM 明确范围；本轮没有改这些模块。
- 统计的 256KiB 上限会使大时间范围/高维度查询返回原有 503；没有偷偷采样收益曲线。子进程仍可因超大历史计算触发既有超时或 RSS 隔离，但主进程不随历史行数做同步计算。
- 本机实际验证的是 Windows terminate/NtSuspendProcess；测试保留 POSIX SIGKILL/SIGSTOP 分支，未执行 Linux/WSL/SSH 验收。
- 所有提交仅在本地，未 push；未部署、未备份线上数据库、未查询线上版本。默认开关没有修改。
- 开始时无其他 WIP；新增日志和临时数据库均为忽略产物。既有未跟踪/忽略产物没有清理。交付时 git tracked 工作区应干净。
- 下一任务先读本文、原 incremental 交接和 spec 33–45，明确冻结版本方案和新的验证授权后继续影子模块；其他无直接依赖模块另开任务。
