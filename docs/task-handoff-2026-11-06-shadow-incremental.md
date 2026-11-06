# 影子增量 IPC 编码报告与交接（2026-11-06）

验收结论：**未通过，已停止性能修改。** 增量传输、子进程镜像、分片和恢复回归已实现，后端全量通过；production 三次及 stress 一次未达标，realistic 通过。不修改预算，不继续挑选重跑，不自行节流。当前仍是 O(N) 目录字段检查，尚未实现足够便宜的目录版本检测，不能把传输字节减少宣称为主线程预算达标。

## 范围、分支与提交

- 工作区：taoli1/.worktrees/shadow-ledger；分支：codex/shadow-ledger。
- 基线：63795d5e645b970dd00a1b8350d37b71e3ac1746；开始时工作区干净。
- 52714cd548e4d147bb8553fae4324f4ecae3304a：test，初始增量协议/全量等价测试。
- cecd80a1d38c2cd1eda866d64390035b6a1cbbf9：test，恢复边界、原地修改回归、分片与新增墙钟预算。
- 5e0b9129345d2a147cf944cf3c469e04b371acd0：feat，镜像、差量、分片、序号校验和状态。
- 本报告与任务书补充另作 docs 提交；最终 docs hash 见交付回复与 git log。
- 没有 push、WSL、SSH、部署、线上备份或线上验证；没有改前端、Compose、.env、采集器、store、账本规则或数据库 schema。线上仍保持原状，实际版本未查询。

改动文件：

| 文件 | 责任 |
| --- | --- |
| backend/app/services/shadow_runtime.py | 主进程镜像缓存、紧凑阈值、分片差量编码、恢复、占用计时与关闭等待 |
| backend/app/services/shadow_ipc.py | 协议说明、500 条分块常量、删除不再使用的阈值 tuple getter |
| backend/app/services/shadow_worker.py | 子进程目录/报价/ID/执行目录镜像、合并、连续序号校验与 ACK |
| backend/tests/test_shadow_incremental.py | 四项真实 spawn 回归，包含强制全量路径等价、断档、队列失败和修改/删除/重排 |
| backend/tests/test_shadow_benchmark.py | 按语义阶段统计，新增单段及整轮墙钟断言，原断言全部保留 |
| docs/shadow-ledger-spec-2026-11-03.md | 原文追加 PM 第 39–41 条 |
| docs/task-handoff-2026-11-06-shadow-incremental.md | 本报告、原始验收输出、后续边界 |

## 协议和关键行为

队列继续是 spawn 上下文的 maxsize=1、单飞模式；主进程不持有 ShadowLedger，不做筛选或 shadow 表写入。消息外层仍为 (kind, token, payload_bytes)，token 同时用于请求/响应匹配。独立的 round sequence 只在轮次发送时递增，statistics 不改变镜像序号。

1. round：`(sequence, full, published_at, optional_id_table, array('d', spreads), optional_catalog)`。阈值使用 float64 顺序数组；ID 表仅在顺序/集合变化或 full 时发送，不再生成每条 (id, spread) 元组。catalog 的版本由 (updated_at, entries 对象身份) 构成，仅版本变化或 full 时 pickle。生产 ExecutionRoutes.refresh 会替换 entries 并更新 updated_at。
2. 子进程根据阈值和持仓返回相关机会索引。
3. directory：`(size, directory_delta_chunks, opportunity_chunks)`。差量元素是 `(slot, directory_fields_tuple)`，每块最多 500 行。size 截断即删除尾部；位置替换会更新 slot 并使其报价缓存失效。主进程直接比较缓存的八个字段，只有变化时构造目录元组；不再为全部 12,000 行构建 tuple 后比较。
4. slot 是本轮目录的整数句柄，**不是 ticker**。字段仍保留 exchange、market_type、raw_symbol、DEX、两种 multiplier、asset_id、symbol。目录重排按 slot 更新处理，子进程业务匹配仍由完整 MarketKey 决定，不会把不同 DEX 合并。
5. 子进程在自己的镜像上选市场，返回报价索引。quotes 是差量字节块列表，每条 `(slot, market_fields_tuple)`；子进程按 slot 合并，相关市场从合并结果读取。本实现保留原策略：普通轮取相关/持仓报价，分钟轮取全市场供无关资产历史使用。未请求的报价在分钟轮或下次被请求时补齐，不参与本轮相关腿计算。
6. 报价比较采用含 timestamp、upstream_timestamp、盘口、funding 等全部消费字段的内容版本；保留同 timestamp 修价语义。estimated_fields 转成 tuple，不持有生产者可变列表。仍需对请求范围做字段投影，尚不是纯版本计数读取。
7. 每 500 条捕获/序列化后 await asyncio.sleep(0)。单飞在首次 yield 前占位；取消调用者不释放在途任务。关闭在统一的 10 秒期限内先等待已接收轮次，再通知空闲子进程退出，避免首帧尚未发出就停子进程。
8. 正常 ACK 携带 mirror_sequence；只有序号与发送轮次相符才允许继续增量。full 仅清空传输镜像，不重置现存 worker 的 scorer/ledger；真正重启仍遵守原有数据库恢复和首轮只登记规则。

## resync 触发和计数

- 初次启动：full；不计入 resync_count。
- 序号断档、镜像尚未初始化、ACK 不匹配：拒绝该增量轮，不进入账本；主进程下一次可接收轮次发送 full。
- 子进程异常导致镜像不可确认：返回 resync_required；下一轮 full。
- 队列 Full、捕获/序列化失败、传输故障：沿用监督器隔离/重启；下一次成功接收的轮次 full。
- 子进程崩溃、超时或重启：父端清镜像并强制 full；原重启次数/RSS 上限保持不变。
- resync_count 统计初次 full 之后成功交给队列的 full 次数，表示尝试，不等于业务轮次完成数；进入 status。
- 忙时跳过的采集快照没有向 IPC 发消息，因而不会制造序号断档，也不需额外全量；下一次按上次已接收镜像比较。statistics 与行情序列隔离。

## 正确性验证与计时口径

新增测试对比增量路径与每轮强制 full 的路径，在同一输入序列逐轮比较全部存储字段（仅排除随机 trade id，保留 tracking），覆盖启动基线、上穿、收敛、重新上穿、序号断档重同步、目录删除和重排。另有不变目录/报价零差量、替换对象但字段相同零目录差量、同对象且时间戳不变的目录字段修改、执行目录更新、ID 集合变化、队列失败重启、分片期间忙轮跳过。既有真实 IPC 与完整账本 oracle 的多轮等价测试亦包含在全量中。

最初只按对象身份跳过目录比较的尝试，会遗漏原地修改；正式版本已去掉该捷径并覆盖回归。不能用该中间版本较快的数字作为验收。

capture_segments_ms 按每次主动 yield 之间的连续墙钟占用记录，包含捕获循环、分配和清理；capture_phases_ms 合计 thresholds / directory / quotes 三阶段，directory 包括相关机会编码；publish_ms 单独记录结果发布。last_duration_ms = capture_ms + publish_ms，不含等待子进程和主动让出的时间。

Windows thread_time 的量化粒度为 15.625ms；本轮 production 的 main_cpu_median_ms=15.625 并不是精确的抽取 CPU 耗时，stress 的 0 也不代表没有开销。按 PM 40 以 perf_counter 墙钟判断本轮结论。原 main_cpu <5 断言仍保留，新增整轮 ≤5ms 和单段中位 ≤3ms，原 production ≤7、stress ≤5、realistic ≤3、P95/最大/内存等断言均未放宽。

## 实际验收环境和命令

正式验收沿用上一交接中的 scratchpad/venv-lock/Scripts/python.exe，Windows Python 3.12.10、venv=True、pytest 8.4.2、Pydantic 2.14.1、aiosqlite 0.22.1。核对 requirements.lock 与 requirements-dev.lock，Windows 环境只有 uvloop 缺失，其余安装版本匹配；没有安装/升级依赖。默认 Python 的辅助全量也通过，但不混用其基准作为正式验收。

在 backend 下设置 PYTHONPATH=.，所有正式测试禁用 cacheprovider。临时数据库位于本工作区 .pytest-tmp/scratchpad，日志在 output，均忽略、不提交。等价命令（$shadowPython 指向上述已有解释器）：

```powershell
$env:PYTHONPATH='.'
& $shadowPython -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/delta-locked-full
& $shadowPython -m pytest tests/test_shadow_benchmark.py -m benchmark -k production -s -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/delta-final-production-1
# 同命令依次 production-2、production-3、stress-1、realistic-1；-k 使用对应场景。
```

正式全量原始汇总（退出 0）：

```text
2645 passed, 9 deselected, 13 warnings in 162.78s (0:02:42)
```

默认排除的 network/benchmark marker 未改变；三种影子 benchmark 单独执行如下。13 条警告为已有 Starlette/httpx 和 datetime.utcnow 弃用。辅助环境结果为 2645 passed, 9 deselected, 12 warnings in 214.53s (0:03:34)。定向回归曾通过 21 passed in 69.48s (0:01:09)。git diff --check 和五个 Python 文件的 Ruff --select F 通过；未将完整 Ruff 风格规则作为通过声明。

## 五次正式基准原始输出

顺序串行执行，同一 feature 提交，不与全量测试并跑，不因失败挑选重测：

```text
production-1: FAIL
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [1.5931000234559178, 7.07709975540638, 1.5543000772595406], "child_rss_mb": 89.859375, "event_loop_median_ms": 12.905899551697075, "event_loop_segment_median_ms": 2.2649000165984035, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 19.526627218934912, "worker_wall_median_ms": 16.01749984547496}
1 failed, 2 deselected in 5.80s

production-2: FAIL
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [1.645299606025219, 6.803000112995505, 1.5786000294610858], "child_rss_mb": 88.97265625, "event_loop_median_ms": 13.087899540551007, "event_loop_segment_median_ms": 2.390200039371848, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 25.543478260869566, "worker_wall_median_ms": 15.486099990084767}
1 failed, 2 deselected in 5.64s

production-3: FAIL
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [2.6895999908447266, 8.111800067126751, 1.7471000319346786], "child_rss_mb": 89.8671875, "event_loop_median_ms": 14.966199873015285, "event_loop_segment_median_ms": 2.8161000227555633, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 29.83425414364641, "worker_wall_median_ms": 16.437700018286705}
1 failed, 2 deselected in 6.04s

stress-1: FAIL
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [0.4073000745847821, 3.2203999580815434, 3.596099908463657], "child_rss_mb": 68.27734375, "event_loop_median_ms": 9.06139996368438, "event_loop_segment_median_ms": 1.4670999953523278, "main_cpu_median_ms": 0.0, "scenario": "stress", "system_cpu_before_pct": 29.885057471264364, "worker_wall_median_ms": 23.410700145177543}
1 failed, 2 deselected in 5.45s

realistic-1: PASS
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [0.17260003369301558, 0.8952001808211207, 0.759500078856945], "child_rss_mb": 53.32421875, "event_loop_median_ms": 2.5557000190019608, "event_loop_segment_median_ms": 0.5987999029457569, "main_cpu_median_ms": 0.0, "scenario": "realistic", "system_cpu_before_pct": 32.02247191011236, "worker_wall_median_ms": 4.503800068050623}
1 passed, 2 deselected in 2.73s
```

## 瓶颈测量与停止状态

各项为各自中位数，分段中位数之和不必等于整轮中位数。单位 ms。

| 场景 | 整轮 | 最大单段中位 | 阈值 / 目录与机会 / 报价 | 发布 | 结果 |
| --- | ---: | ---: | --- | ---: | --- |
| production-1 | 12.906 | 2.265 | 1.593 / 7.077 / 1.554 | 2.265 | FAIL |
| production-2 | 13.088 | 2.390 | 1.645 / 6.803 / 1.579 | 2.390 | FAIL |
| production-3 | 14.966 | 2.816 | 2.690 / 8.112 / 1.747 | 2.816 | FAIL |
| stress-1 | 9.061 | 1.467 | 0.407 / 3.220 / 3.596 | 1.467 | FAIL |
| realistic-1 | 2.556 | 0.599 | 0.173 / 0.895 / 0.760 | 0.476 | PASS |

production 主要瓶颈是目录字段比较与相关机会抽取/序列化这一合并阶段，6.803–8.112ms，单独已超过整轮 5ms。当前计时没有再将这一阶段拆为目录扫描与机会编码，不能将合计全部归因于某一项。stress 报价阶段 3.596ms，加上目录/机会 3.220ms，整轮 9.061ms。单段中位全部满足 3ms，但总占用不足以达标。

production 三次 P95 为 17.752 / 15.446 / 16.912ms，亦超过原 15ms；stress P95 10.697ms，超过原 10ms。各正式场景主进程 RSS 增量 4.988–13.703MiB，均在 30MiB 内；子进程 RSS 53.324–89.867MiB，在 250MiB 内；worker 墙钟中位均满足原场景门槛。不能因为内存、子进程或分段合格就认定整轮通过。

未解决风险与下一步：

- 仍逐轮检查所有目录字段，以兼容生产对象替换和已有原地修改语义；避免全量 tuple/pickle 并不意味着 O(变化数) 抽取。若继续，应先确定可绑定到快照的可靠目录 revision，再考虑用户允许的最小 store 变更计数；本轮未擅自改公开行为或引入新的采集器协议。
- 目录采用位置句柄；删除中间行或整体重排会产生更多 slot 替换差量，正确性已覆盖，但性能可能更差。
- 报价差量有内容比较成本；没有将 timestamp 不变等同于报价不变，避免漏掉修价/funding/身份字段变化。
- catalog 版本假设沿用现有 refresh 行为；外部原地修改 entries 必须同时更新 updated_at，不能在不更新版本时期待传播。
- 五次正式基准已完成并停止优化；不得将本提交作为性能验收通过或生产开启依据。下一轮由 PM 决定目录版本设计及是否继续性能工作，不自行降频。
- 真实服务器、生产灰度、前端均未执行；没有产生新的线上状态结论。

## 产物与交接

完整日志：output/shadow-delta-locked-full.log、output/shadow-delta-final-{production-1,production-2,production-3,stress-1,realistic-1}.log。辅助日志 output/shadow-delta-{full,correctness,profile,profile2}.log 保留，不用其较快或不同环境结果替代正式数据；最初红测和临时库在 .pytest-tmp/scratchpad 下。全部为忽略产物，未删除旧文件或其他任务产物。

后续接手先读本报告与 spec 第 33–41 条，再从 5e0b912 实现基线分析上述阶段；保留全部断言和本轮失败数字。本模块仍停留在性能未验收状态，无直接依赖的新模块应另开任务。

