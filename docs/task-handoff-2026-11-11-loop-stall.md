# loop-stall 本地交接（2026-11-11）

## 状态与边界

F1/F2/F3 已实现，新增回归和三轮离线基准通过。**完整验收尚未通过**：原有 `test_prepare_pairing_and_scheduling_source_is_unchanged` 要求 `prepare()` 从 collision 分支到返回的源码与旧版逐字相等，和任务书要求在这段加入 `await` 直接冲突。按“不放宽已有断言”，该测试保持原样，没有跳过、xfail、修改 pytest 配置或伪造 `inspect` 结果。已提出这一处澄清，尚未收到授权调整。

本任务只做主循环卡顿修复。任务书和用户最新指令优先于设计稿的上线安排：**未 push、未 SSH、未运行 WSL、未联网、未部署**。未改 GC 阈值、判定规则、原因码、能力、`orderbook_validator.py`、`spread_engine.py`、compose、部署脚本、`.env` 或前端。

## 分支、基线与提交

- 工作树：`C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/loop-stall`。
- 分支：`codex/loop-stall`；起始工作区干净。
- 基线：`00dfa9e1cb4cff77d088ce35c8fbd94e65121496`。
- `test`：`6df6975904abb777bce48d9dae3c4059d27e7ba3`，冻结基线、真实规模假快照、对照与让出回归。
- `test`：`0368596aeebc1b460edc0b4ea77f35811b255375`，精确市场键、异步竞争、完整基准阶段及源码差异审计。
- `feat`：`9990b6cfa5285636ee250c39380e03473e17a96e`。
- `test`：`eb942ef6b50397da369deb01f6d268c69869ff3d`，Gate received_batch 使用真实 reference/估算报价语义。
- 本文单独以 `docs` 提交；本文件自身提交号可用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-loop-stall.md` 获取。没有 amend。

## 文件与实现

| 文件 | 变更 |
|---|---|
| `backend/app/services/market_validation_review.py` | 标注及精确市场键复用；版本化索引和腿市场缓存；同步/异步共用过滤规则；prepare 让出和 epoch 防护 |
| `backend/app/services/pair_validation.py` | 可选 `quote_fingerprint`，未传入时仍计算原指纹 |
| `backend/app/services/snapshot_store.py` | 增加行情 setter 版本 `markets_version`；复用原来每次发布递增的 `generation` |
| `backend/app/services/collector.py` | 构建复核传 `annotated=True`；适配结果存储改为 await `pack_async` |
| `backend/app/services/compact_markets.py` | 同步与异步共用逐字段打包；保留 fields-set 位图与可变字段隔离 |
| `backend/app/services/multi_exchange_premium.py` | 同步扫描保留，异步调用链按预算让出；扫描期间变更则中止 |
| `backend/app/main.py` | 告警循环 await 异步过滤，300 秒窗口聚合 JSON 日志 |
| `backend/tests/loop_stall_reference.py` | 冻结 00dfa9e 的 prepare、validate、filter、index、collector、pack、scan；测试专用 collector 存储方法只适配 async 签名，内部仍同步执行旧逻辑 |
| `backend/tests/loop_stall_fixture.py` | 固定时间、12,000 行情、4,000 机会、假仓库及假后台任务 |
| `backend/tests/test_loop_stall.py` | 24 项新增回归 |
| `backend/tests/test_loop_stall_benchmark.py` | 3 次基准，默认标记为 benchmark，不混入全量普通回归 |
| `backend/tests/test_cooperative.py` | 测试包装器透传新的关键字参数，断言不变 |
| `backend/tests/test_collector_compact_equivalence.py` | 旧测试存储 oracle 适配 async 签名，业务主体和断言不变 |
| 本交接文件 | 本地验证、基准、未通过项和下一步 |

### F1 的边界

`validate(..., annotated=True)` 仅在 prepare 完成时的 epoch 仍有效、输入确为该轮输出对象、FX 未变化且 verified 绑定未跨过过期时刻时复用；否则完整 annotate。无效 DEX 继续抛出原错误。prepare 的 discovery 已校验过精确市场键，保存这些键避免重复构造；不省略 `validate_pair` 的任何业务判定。

collector 确实将 prepare 返回的对象发布到 `set_all_markets(aliased_markets)`；新增整轮对照在 shadow/enforce 均断言逐对象来源及 markets/opportunities 全字段一致。`PairValidationResult` 对照仅排除 `validated_at`，没有排除 `expires_at`、原因码或能力。

索引按 `(generation, markets_version)` 缓存，保留原有“精确市场重复时取时间较新的行”规则。原始 exchange/market_type/raw_symbol/dex 共同决定腿市场查找，不按 ticker 合并；替代拼写仍走 `MarketKey` 校验。腿缓存最多为索引大小的两倍，版本变化时整体替换。行情 setter 额外版本保护映射刷新后未调用 `mark_published` 的情况。

### F2 让出点

以下行号对应 feat 提交：

| 位置 | 预算检查 |
|---|---|
| `market_validation_review.py:433` | 每个 collision symbol，进入其行情扫描前 |
| `market_validation_review.py:439` | 每个 collision 两腿组合，包括后续被跳过的组合 |
| `market_validation_review.py:480` | 每个大价差候选组合，包括后续被跳过的组合 |
| `market_validation_review.py:936` | 每条告警机会，包括缓存命中、拒绝及腿缺失 |
| `compact_markets.py:103`、`:104` | 每 200 行及末尾检查预算；collector `:459`、`:476` 的退化和正常分支都 await |
| `multi_exchange_premium.py:89` | 每条经过原 filter_markets 的行情，正式做资格判断前 |

沿用 `YieldBudget` 默认 20ms；不是每行强制 sleep。prepare 仅 `cooperative` 时新增 await，第三次尝试仍不增加 CPU 循环让出；原来的 discover/invalidate 仓库 I/O 仍保留。让出后 epoch 改变则中止，避免使用旧映射做失效操作或调度复核。告警过滤若在让出期间发生行情/映射更新，整轮抛错由原告警循环隔离，不返回混合版本结果。

### F3 日志

`main.py:657` 的 `_AlertFilterTiming` 每满 300 秒在下一次成功过滤后输出一行：

```json
{"alert_filter_timing":{"count":3,"filter_ms":{"p50":20,"max":30},"recomputed":15}}
```

`filter_ms` 包含通用过滤和身份过滤的墙钟时间（包括让出后等待），不包含后面的规则筛选/告警发送；`recomputed` 是实际调用 validate 的条数，包括最终拒绝的条目。失败或取消的整轮不会计入成功窗口。窗口输出后清空，计数为调用本地统计，不会被其他同步过滤调用覆盖。

## 验证

使用指定的锁定解释器：

```text
C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe
```

环境：Windows、Python 3.12.10、pytest 8.4.2、Pydantic 2.14.1。运行目录 `backend`，`PYTHONPATH=.`。现有 conftest 禁止外部网络；默认排除 network 和 benchmark 标记。没有安装依赖。

全量原始汇总（最终一次）：

```text
1 failed, 2937 passed, 11 deselected, 13 warnings in 221.13s (0:03:41)
```

命令保持任务书原样：

```text
-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/loop-stall-full
```

最终原始日志保存在本地 `output/loop-stall-full.txt`；唯一失败位置为 `backend/tests/test_market_validation_prepare_copies.py:271`，该文件与基线没有差异。

新增测试原始汇总：

```text
24 passed in 21.71s
```

命令：`-m pytest tests/test_loop_stall.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/loop-stall-new-verified`。

覆盖：shadow/enforce、collision、USDC、Gate received_batch/ws、过期绑定、缓存命中/未命中、epoch/FX 变化、旧快照来源、精确 DEX、指纹单次计算、字节对应字段及 fields_set、嵌套可变数据隔离、候选/collision 循环让出、第三次重试、扫描/过滤期间发布变更、真实告警循环调用链、300 秒日志窗口。

另新增严格源码差异审计：只删除精确匹配且计数固定的新增让出/epoch 防护/prepare 缓存记录语句，剩余业务源码与冻结基线相等。**这没有替代或关闭原有逐字相等断言**，所以原断言仍导致全量失败。

## 三轮基准

同一份 12,000 行行情和 4,000 条全部需要重算的机会，冻结 00dfa9e 对照，新旧均接受 3,569 条；没有用已有 item.validation 跳过重算。Gate received_batch 同时使用 reference 和 bid/ask 估算标记。分别记录发布后首次过滤（冷索引）和同快照第二次过滤（热索引）。构建阶段单独计量 4,000 次复核及原有预算检查，不将 fixtures/prepare 算入构建复核耗时。

每个阶段计时前双方都执行一次 `gc.collect()` 统一回收相位；计时期间 GC 始终启用，阈值保持解释器默认 `(700, 10, 10)`，没有设置阈值或禁用 GC。prepare 也使用真实预算检查；最长段包含 prepare/build/冷过滤/热过滤/pack/scan 的首尾部分，等待其他任务的时间不计入下一段。基准单独运行，没有与其他 pytest 进程并跑。

单位均为 ms，表格四舍五入；完整浮点原始输出保存在本地忽略产物 `output/loop-stall-benchmark-complete.txt`。

| 次数 | 构建旧→新 | 冷告警旧→新 | 热告警旧→新 | 全阶段最长段旧→新 |
|---|---|---|---|---|
| 1 | 198.226 → 139.460 | 344.306 → 196.637 | 341.371 → 179.979 | 344.306 → 22.747 |
| 2 | 242.519 → 135.453 | 323.482 → 199.840 | 335.809 → 180.468 | 335.809 → 34.692 |
| 3 | 248.589 → 99.185 | 375.759 → 157.377 | 270.493 → 147.359 | 375.758 → 33.099 |

三轮中位数降幅：构建 **44.147396%**，冷告警 **42.888934%**，热告警 **46.404353%**。三轮全部阶段最大同步段 **34.691500ms**。基准同时断言三项耗时降幅 ≥40%、新路径最大同步段 ≤200ms；没有调整门槛。

原始基准汇总：

```text
LOOP_STALL_BENCH_SUMMARY {"alert_cold_reduction_pct": 42.88893357192944, "alert_reduction_pct": 46.40435268906737, "build_reduction_pct": 44.147395979042855, "new_max_segment_ms": 34.69150001183152}
1 passed in 14.69s
```

复现命令：`-m pytest tests/test_loop_stall_benchmark.py -m benchmark -q -s -p no:cacheprovider --basetemp=../.pytest-tmp/loop-stall-benchmark-complete`。

过程证据也保留：最早未优化完成且未对齐 GC 相位时约 31% 降幅、最大段约 333ms；随后逐步去除重复精确键构造，冷/热索引都纳入最终门槛。没有将失败实验冒充最终验收，也没有通过反复选择最快一轮计算降幅。

## 剩余风险与下一步

1. **源码断言冲突是当前验收阻断项。** 保持用户“不放宽已有断言”的要求。需要用户明确决定是否允许仅把这一条逐字源码断言改为严格业务结构/新增语句白名单检查；收到答复后再改，重跑全量，单独 test commit，不 amend。本次不声明全量通过。
2. 本地基准是合成快照、受控 GC 相位。不能保证任意系统抢占或 gen2 回收下所有同步段都小于 200ms；未修改生产 GC。第三次 prepare 同步回退和候选排序也仍是设计约定的同步部分。
3. 缓存持有最新 prepare 输出和最新已查索引的行引用，可能在更新交界暂时保留旧快照；缓存只保留一版，腿缓存有容量上限。既有内存回归保留，生产常驻内存仍需上线观察。
4. 新增让出后若正好发生映射/行情变更，会中止本轮，依赖原循环下一轮重试；保证不返回旧权限，极端连续更新可能影响告警延迟。
5. 未进行独立第二审查者/Claude 核心 diff 审查，也未进行线上 py-spy 90 秒、24 小时卡顿率/告警 P50/可用内存验收。线上版本、备份和部署状态本次均未访问；设计稿所写历史生产版本不能当作本次核实结果。
6. 工作区原始改动和未跟踪文件：开始时无。验证日志留在被 Git 忽略的 `output/loop-stall-*.txt`，pytest 临时文件在被忽略的 `.pytest-tmp/`；没有清理用户文件。

解决当前验收阻断并完成只读复核后，部署与生产观察应另开服务器运维任务，沿用本交接；本任务没有发布授权。

## 本轮追加：PM 两项决定（2026-11-11）

本节记录本轮最新结果，覆盖上文“源码断言冲突待授权”和“告警过滤变化后只能等下一轮”的旧状态。范围仅为 PM 授权的源码白名单断言和告警过滤有界重试；未扩展到其他模块。工作树、分支不变，本轮起点为 `dd3cf08`，起始工作区干净。

**本轮本地全量已通过，原源码断言阻断项已解决。** 生产验收和独立审查状态仍按本节末尾边界执行。

### 本轮提交与文件

| 类型 | 提交 | 文件与内容 |
|---|---|---|
| test | `37c6778f251f13a5594f8e5c6c7b67ee9c4891a7` | `backend/tests/test_market_validation_prepare_copies.py`：将获授权的断言改为精确白名单剔除后逐字相等，并检查 cooperative 分支 |
| feat | `433765ef6a70c815e4b49492062824cb6d518b49` | `backend/app/services/market_validation_review.py`、`backend/app/main.py`：专用异常、两次立即重试、耗尽后 warning 跳过、重试和跳过计时统计 |
| test | `bbdfc39f8c8b1a32eeb5747daa71b0774dab6e31` | `backend/tests/test_loop_stall.py`：13 个新增回归、专用异常类型附加断言、既有 JSON 预期补零值字段 |
| docs | 本节单独提交 | `docs/task-handoff-2026-11-11-loop-stall.md`；自身提交号用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-loop-stall.md` 获取 |

全部为新增本地提交，没有 amend。实现与测试分别提交，没有修改冻结参考实现或 pytest 配置。

### 源码断言的精确边界

`test_prepare_pairing_and_scheduling_source_is_unchanged` 仍保留原来的比较范围：`prepare()` 从 `if self.collision_symbols:` 之后到返回。对两边源码使用相同的白名单处理，生产侧必须精确匹配以下固定数量的完整语句块，冻结参考侧必须没有这些新增块；剔除后不做 dedent、格式化或空白规范化，直接逐字比较。

- 三个完整的 `if cooperative:` / `await budget.maybe_yield()` / epoch 比较及原文 `ValueError` 块：16 空格缩进两处，20 空格缩进一处。
- 一个独立的 epoch 比较及原文 `ValueError` 块。
- 一组完整的 `_prepared_epoch`、`_prepared_rows`（含与 `market_keys` 的 strict zip）和 `_prepared_fx` 赋值。

`market_keys` 的建立和 append 位于原比较边界之前，本次没有扩大剔除范围。另用 AST 检查整个 `prepare()` 中每个 `await budget.maybe_yield()` 都处于 `if cooperative` 的正分支，else 分支不算。白名单之外的任何尾部改动、块内容/缩进/数量变动仍会失败。其他业务、内存和行为断言保留；两处原 `pytest.raises(ValueError, match=...)` 还追加了精确专用异常类型断言。

### 告警循环与日志

- `market_validation_review.py:33` 定义 `AlertFilterChanged(ValueError)`；`:931` 的异步过滤在让出后发现 `_mutation_epoch`、`generation` 或 `markets_version` 改变时抛此类，保留原错误文字，不返回部分结果。抛出前记录该次实际重算条数。
- `main.py:730` 最多执行三次尝试，即首次加两次立即重试。每次都重新执行 `snapshot_store.get_opportunities()`、通用过滤和异步身份过滤，丢弃失败尝试的结果。
- 第三次仍变化时，记录 skipped 计时样本，跳过规则筛选、引擎评估和后续发送，心跳为失败，继续原来的轮间等待。`main.py:796` 只捕获此专用异常并输出一行 warning：`alert filtering skipped after 2 retries: mapping or snapshot kept changing`，不记录 traceback。
- 普通 `ValueError`（即使文字相同）和其他异常仍走原 `logger.exception("alert loop failed")`；取消继续向上传播。

`_AlertFilterTiming` 仍按 300 秒窗口输出 JSON。新增 `retries` 为窗口内实际重试次数总和（每轮 0–2），`skipped` 为因持续变化而跳过的轮数。`count` 现在包含成功轮和这些跳过轮；`filter_ms` 为整轮所有过滤尝试的墙钟耗时；`recomputed` 累计所有尝试的实际重算条数，包括后来因版本变化而作废的工作。一般异常和取消仍不计入样本。所有字段在窗口输出后清零。

```json
{"alert_filter_timing":{"count":3,"filter_ms":{"p50":20,"max":30},"recomputed":15,"retries":3,"skipped":1}}
```

### 本轮验证

仍使用上文同一锁定 venv，运行目录 `backend`，`PYTHONPATH=.`。没有安装依赖或访问外网，沿用现有 conftest 的离线保护和默认 network/benchmark 排除。

白名单测试文件的原始汇总：

```text
46 passed in 2.18s
```

定向回归原始汇总（37 项 loop-stall 回归和 46 项 prepare 回归）：

```text
83 passed in 23.25s
```

定向命令：`-m pytest tests/test_loop_stall.py tests/test_market_validation_prepare_copies.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/loop-stall-new2`。原始日志：`output/loop-stall-new2.txt`。

13 个新增回归覆盖：三种变化来源（发布 generation、映射 epoch、仅 markets_version）分别在第一次/第二次重试恢复或连续三次变化后跳过；逐次重取机会并重跑通用过滤；丢弃旧结果；耗尽后仅一行 warning、无 exception 日志/traceback且不调用下游；非专用异常不重试；取消传播；计时窗口累计和清零。既有完整 JSON 相等断言仅增加需求新增字段的预期零值，未移除其他字段断言。

全量命令保持本轮要求：

```text
-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/loop-stall-full2
```

全量原始汇总（退出码 0）：

```text
2951 passed, 11 deselected, 13 warnings in 202.81s (0:03:22)
```

原始日志：`output/loop-stall-full2.txt`。11 项为现有配置默认排除的 network/benchmark；13 条 warning 来自 Starlette/httpx 和 `datetime.utcnow()` 的弃用提示，没有调整依赖或过滤 warning。

### 交付状态与后续边界

本轮仅本地验证和提交；**未 push、未 SSH、未 WSL、未联网、未部署、未 amend**。没有查询线上版本、备份或容器健康，线上验收仍不在本轮范围内。GC、prepare 业务规则、判定能力/原因码、复核后台 I/O、交易标识规则和其他模块均未改动，既有基准结果保留，本轮没有重跑基准或声称新的生产性能结果。

持续变化仍可能使该轮告警延后；现在最多两次立即重试，然后 warning 跳过，避免无界循环。每轮最坏可承担三次过滤工作，其耗时和重算量可从新字段关联观察。本轮原始日志保留在被忽略的 `output/`，临时文件位于被忽略的 `.pytest-tmp/`；没有删除或清理旧产物。后续独立审查、部署和生产观察请另开任务并沿用本交接。
