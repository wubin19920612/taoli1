# 影子引用冻结（PM 46–47）修复与交接

**性能目标达到；整体验收尚未通过。** 最终三次 production 的 loop 中位 9.05–9.38ms、单段 1.65–1.74ms、RSS 增量 16.63–20.95MiB，均满足第 47 条。原 7ms 断言未改，三次基准仍失败。全量唯一失败来自新增 AST 守护：按 46c 字面禁止所有模型赋值，会命中 5 处现有发布前构造赋值。没有添加豁免、跳过测试、修改范围外代码，也没有声称这些是已发布对象的修改；等待确认守护是否应允许可证明尚未发布的新建对象赋值。

## 范围、基线与提交

- worktree：`taoli1/.worktrees/shadow-ledger`；分支：`codex/shadow-ledger`。
- 基线：`6797c72f038fb77ec7c09c50b579186d26ba2ba8`；开始时工作区干净。
- 已先读 `task-handoff-2026-11-06-shadow-review42.md`。42–44 的关闭通知、期限、统计协议与断言保持；只增加新目录缓存的正常回收。
- test：`d35608c`，引用替换回归、AST 守护及基准生产者替换语义。
- feat：`5b25561`，引用冻结、目录弱引用复用和不可变机会发布缓存。
- 本文与 spec 追加 46–47 原文单独 docs 提交，hash 见最终回复或 `git log`。
- 未运行 WSL、SSH、push、部署、线上备份或线上验证；未改前端、Compose、`.env`、生产者/store 或默认开关。

## 实现与关键入口

`backend/app/services/shadow_runtime.py`：

- `submit` 在返回或第一次让出前只捕获 markets/opportunities 的 tuple 和 catalog 版本、entries 引用。删除逐对象字典/嵌套列表复制和前后两代冻结字典；捕获仍计入原计时。
- 阈值、目录、机会、报价的分片及长度均取自捕获元组；显示标注也使用捕获的机会引用，原 generation/version 条件发布不变。
- 已发布 MarketSnapshot/Opportunity 及其列表字段视为不可变；catalog 同样依赖整体替换。生产者更新必须发布新对象/列表，不能原地修改已经发布的实例。
- 目录保存弱引用：同一不可变对象可跳过八个字段比较；对象替换时仍比较字段，不能只凭 timestamp。弱引用不会保留上一代对象，也不会将复用的整数 id 误当成旧对象。重同步、删除槽位、dispose 都清理对应弱引用。
- 发布缓存复用不可变机会：先比较对象身份和标注，替换对象时再比较字段，避免每轮构建完整字段 tuple。收到完整轮次确认后释放输入引用，回收耗时仍计入发布计时。
- 目录与报价差量协议不变，保留 DEX、raw_symbol、两种 multiplier 等原始身份字段。42–44 的 worker/API 文件完全未动。

## 测试调整及理由

这是 PM 46 明确变更的生产者契约，不是放宽数值、身份、生命周期或交易行为断言。未删除任何测试函数所覆盖的功能；原地修改型用例改写如下：

1. `test_shadow_shutdown_stats_freeze.py` 的旧真实 spawn 原地改价用例改为三个参数场景：分别在 thresholds/directory/quotes 分片期间通过 store setter 替换整批市场与机会，同时替换 catalog。包含 1203 个旧市场、502 个旧机会，报价变化、市场增加/删除、阈值和 routes 变化。核对 submit 的 tuple 与对象引用身份、所有旧目录/报价字段、阈值数组、机会 wire 字段、两行开仓及旧版弃发；下一轮分钟全量逐字段确认新值和删除槽位。42–44 的回归保持原样。
2. `test_shadow_incremental.py`：同 timestamp 元数据变化改为新对象；全量/差量等价序列改为对象替换，保留原差量数量、重同步、开平仓逐字段等价断言。
3. `test_shadow_selection_runtime.py`：旧 harness 分片用例改为替换市场对象并调用 set_markets，保留分片宽度、让出次数、single-flight、旧报价断言。真实 spawn 的对应保证由第 1 项覆盖。
4. `test_shadow_performance_safety.py`：旧 harness 的阻塞计算、嵌套列表缓存用例改为新实例/新列表/新 catalog，保留旧值、目录、线程、不可变 wire 和过期发布断言。
5. `test_shadow_benchmark.py`：迭代报价与分钟 checkpoint 改为 model_copy 替换。生产者准备仍在 runtime 计时之外，替换分配造成的 RSS 仍包含在原 RSS 测量内。通过 AST 比较确认该文件所有 assert 与基线完全相同：`BENCHMARK_ASSERTIONS_UNCHANGED`。没有修改 7ms、5ms、3ms 或其他预算。
6. 新增 `test_shadow_snapshot_contract.py`：扫描 backend/app，排除 tests 和 shadow 模块。最佳努力追踪类型注解、构造器、带注解的函数返回值、store getter、容器索引/迭代、变量及列表字段别名；检测属性赋值/删除、增强赋值、列表修改方法、下标修改和 setattr/delattr。另有 20 个正反向检测用例。它不是完整类型检查器，动态反射、未标注外部 helper 和复杂跨模块别名仍需审查。

## 唯一验收阻点：46c 的字面范围

严格守护实际输出：

```text
api/routes_astro.py:207: opportunity.comparison_kind
services/collector.py:274: item.validation
services/collector.py:277: item.risk_labels
services/collector.py:590: item.risk_labels
services/collector.py:622: item.risk_labels
```

源码检查：routes_astro 在 build_directional_opportunity 新建对象后设置 comparison_kind；collector 在本轮新建机会发布到 store 前设置 validation/risk_labels。这些位置没有证据表明正在修改已发布对象，因此不推翻 PM 46 的已发布不可变前提。但它们确实是 46c 字面描述的实例属性赋值；当前测试如实失败，没有暗中豁免。

已发出澄清：建议守护针对已发布/借入/无法证明所有权的实例，允许能够证明尚未发布的新建实例赋值；另一选择是严格禁止所有赋值并报告这些现有违规。答复尚未收到。未擅自将等待视为批准，未改范围外文件。

## 实际验证

Windows Python 3.12.10，pytest 8.4.2；使用此前的 scratchpad/venv-lock 环境。重新核对 requirements.lock 与 requirements-dev.lock，唯一差异是 Windows 不支持的 uvloop 缺失。没有安装或升级依赖。最初默认 Python 的快速 spawn 3 例通过仅作辅助结果；下面正式测试和基准均用锁定环境。

专项（弱引用优化前）：

```text
27 passed in 68.15s (0:01:08)
```

最终目录弱引用及真实替换专项：

```text
5 passed, 10 deselected in 9.34s
```

最终全量（唯一失败为上述新增 AST 生产扫描）：

```text
1 failed, 2676 passed, 9 deselected, 13 warnings in 204.55s (0:03:24)
```

默认 network/benchmark marker 排除规则保持。13 warnings 是既有 Starlette/httpx 与 datetime.utcnow 弃用警告。变更文件 Ruff `--select F` 和 `git diff --check` 通过；已做本地差异复核，没有启动子代理。

最终三次 production 与全量串行运行，原始汇总行附后。每次都首先在原 7ms 断言失败，后面的单段/更严格 5ms/CPU 等断言未执行；第 47 条判断来自已打印的原始指标，不能称整个 benchmark 通过。

| 最终轮次 | loop 中位 ms | 单段中位 ms | 主进程 RSS 增量 MiB | P95 ms |
| --- | --- | --- | --- | --- |
| 1 | 9.278700 | 1.649900 | 16.671875 | 15.634100 |
| 2 | 9.054900 | 1.663500 | 16.628906 | 10.230200 |
| 3 | 9.384700 | 1.739400 | 20.953125 | 16.429300 |

Windows thread_time 的 15.625ms 量化仍存在；不能用 0ms CPU 中位证明达标，以 perf_counter 墙钟为准。两次 P95 仍高于原 15ms 目标，原 7ms/5ms 及 CPU 目标未被宣称通过。

修复过程另跑了两组三次基准，全部保留在原始日志并附后：第一组去深拷贝后 loop 18.24–19.66ms、单段 3.18–3.52ms；第二组减少阈值/发布重复工作后 loop 17.15–18.50ms、单段 2.66–2.81ms；两组均未达到第 47 条。因这两组暴露的性能问题继续修改实现，最终增加目录弱引用复用后重新执行三次，未挑选或删除失败数据。

运行命令（backend，shadowPython 指向已核对的旧 venv）：

```powershell
$env:PYTHONPATH='.'
& $shadowPython -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/review46-full-first
& $shadowPython -m pytest tests/test_shadow_benchmark.py -m benchmark -k production -s -q -p no:cacheprovider --basetemp=../.pytest-tmp/scratchpad/review46-weakref-production-1
# 同一代码串行重复 production-2 / production-3。
```

## 剩余风险与下一步

- 先明确 46c 的发布前构造边界，再收口 AST 守护；若允许，必须有相应正反例验证，不能仅按文件名或变量名豁免。随后重新跑全量；若 runtime/基准未变，无需为静态守护调整重复运行 performance。
- 引用冻结明确依赖已发布对象不可变，不再保证对违约原地修改隔离；真实生产者的动态修改仍是静态守护无法完全证明的边界。
- 本轮不构成生产开启依据。原预算、P95 和生产灰度仍待后续验收，Linux/WSL 路径未实际运行。
- 全部提交仅在本地，未 push、未部署、未查线上版本、未备份线上数据库。
- 新日志在忽略目录 `output/shadow-review46-*.log`，测试数据在 `.pytest-tmp/scratchpad/review46-*`；旧日志、数据库与其他忽略产物未清理。初始无其他 WIP，本次提交不包含运行产物。
- 下一个任务先读本交接与 spec 46–47；当前影子模块验收未收口。无直接依赖的新模块另开任务。

## Production 原始汇总行（按运行顺序，包含中间失败实现）

第一组：引用冻结初版

```text
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [3.4792997175827622, 9.521500091068447, 2.104999963194132], "child_rss_mb": 89.76953125, "event_loop_median_ms": 18.241599551402032, "event_loop_segment_median_ms": 3.1796999974176288, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 29.83425414364641, "worker_wall_median_ms": 24.413000093773007}
1 failed, 2 deselected in 7.96s
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [3.917700028978288, 9.618500247597694, 2.158899907954037], "child_rss_mb": 89.6015625, "event_loop_median_ms": 19.328700262121856, "event_loop_segment_median_ms": 3.448300063610077, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 39.77900552486188, "worker_wall_median_ms": 26.809200062416494}
1 failed, 2 deselected in 9.40s
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [4.030100302770734, 10.00729959923774, 2.208599937148392], "child_rss_mb": 88.14453125, "event_loop_median_ms": 19.663400016725063, "event_loop_segment_median_ms": 3.524899948388338, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 21.54696132596685, "worker_wall_median_ms": 26.87329996842891}
1 failed, 2 deselected in 8.62s
```

第二组：阈值与发布缓存优化

```text
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [3.0842996202409267, 9.32829943485558, 2.1125999046489596], "child_rss_mb": 88.17578125, "event_loop_median_ms": 17.15480035636574, "event_loop_segment_median_ms": 2.6556000811979175, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 41.62162162162162, "worker_wall_median_ms": 24.19459994416684}
1 failed, 2 deselected in 8.24s
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [3.5166002344340086, 9.793799952603877, 2.2554999450221658], "child_rss_mb": 88.44140625, "event_loop_median_ms": 18.50280025973916, "event_loop_segment_median_ms": 2.799500012770295, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 24.71910112359551, "worker_wall_median_ms": 27.662800042890012}
1 failed, 2 deselected in 8.93s
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [3.210099646821618, 9.705800097435713, 2.180099836550653], "child_rss_mb": 88.046875, "event_loop_median_ms": 17.845399910584092, "event_loop_segment_median_ms": 2.8068000683560967, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 23.976608187134506, "worker_wall_median_ms": 28.127800091169775}
1 failed, 2 deselected in 9.01s
```

最终组：目录弱引用复用

```text
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [1.9125001272186637, 4.098499775864184, 1.3145999982953072], "child_rss_mb": 89.03125, "event_loop_median_ms": 9.278699988499284, "event_loop_segment_median_ms": 1.649899990297854, "main_cpu_median_ms": 15.625, "scenario": "production", "system_cpu_before_pct": 15.384615384615385, "worker_wall_median_ms": 15.442800242453814}
1 failed, 2 deselected in 5.90s
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [1.8298998475074768, 4.1071000741794705, 1.2650000862777233], "child_rss_mb": 88.3046875, "event_loop_median_ms": 9.054900263436139, "event_loop_segment_median_ms": 1.663499977439642, "main_cpu_median_ms": 0.0, "scenario": "production", "system_cpu_before_pct": 27.118644067796616, "worker_wall_median_ms": 15.405199956148863}
1 failed, 2 deselected in 6.14s
SHADOW_BENCHMARK_SUMMARY {"capture_stage_medians_ms": [1.996499951928854, 4.20749990735203, 1.3377999421209097], "child_rss_mb": 88.05859375, "event_loop_median_ms": 9.38470009714365, "event_loop_segment_median_ms": 1.7393999733030796, "main_cpu_median_ms": 0.0, "scenario": "production", "system_cpu_before_pct": 28.48837209302325, "worker_wall_median_ms": 16.742699895985425}
1 failed, 2 deselected in 6.38s
```
