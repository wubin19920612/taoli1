# Collector yield 交接（2026-10-28）

## 范围与状态

- 任务书：`docs/task-collector-yield-20261028.md`。只做采集后处理协作式让出，不部署。
- 工作树：`.worktrees/collector-yield`；分支：`codex/collector-yield`。
- 基线：`6b146b26fe248fc02c3d20804cb9ab4da48ac537`。最终提交为包含本交接的 `perf(collector): yield event loop during post-processing`，完整 SHA 与实际推送输出见交付回执及远端分支记录。
- **按任务书文末“门槛更正”，12k 基准最大心跳间隔门槛改为 ≤250 ms，重跑通过。** 总耗时增幅 ≤20% 和输出逐字段一致的要求不变。
- CPython 第 2 代 GC 单次暂停超过 100 ms，无法靠 `await` 拆分；保持默认 GC 策略，没有修改全局 GC。本轮审查发现让出期间撤销身份绑定可能被旧机会发布覆盖，补充注册表版本检查及回归测试；因此按用户要求重新执行任务书全部验收命令，详见下文。

## 本地实现与入口

- `backend/app/runtime/cooperative.py`：`YieldBudget(budget_ms=20)`，用 `time.perf_counter` 计时，超预算 `await asyncio.sleep(0)`，恢复后重新计时。
- `backend/app/services/symbol_aliases.py`：同步和异步入口共用 `_apply_symbol_alias`；不改 DEX、原始市场或倍率。
- `backend/app/services/data_filters.py`：同步和异步市场过滤共用谓词。
- `backend/app/services/spread_engine.py`：同步和异步构建共用市场准入、配对迭代；无产出的组/配对也能检查让出预算；保持稳定排序。
- `backend/app/services/market_validation_review.py`：发现集合和碰撞集合在局部计算后整批赋值；注解和碰撞标记循环让出。`reviews.prune` 保持同步完整执行。碰撞失效、候选调度尾段保留原实现，现有源码一致性断言未修改。
- `backend/app/services/collector.py`：改用异步别名、过滤、构建；风险标签和逐机会 validate 循环让出。后处理开始记录身份注册表版本，发布前同步检查，版本变化则放弃本轮并由既有 collector loop 在下一轮重试；`store.set_*` 发布段没有加入 await。
- `backend/tests/test_cooperative.py`：预算计时、同步/异步全字段等价、冻结旧 collector 等价、发布原子性、collision/discovered 集合原子性、10 ms 心跳，以及校验中途撤销绑定不重新发布旧资格的回归测试。
- `backend/tests/collector_yield_reference.py`：实现前冻结基线 `_collect_once` 和 `prepare`，供对应测试使用。现有测试及断言没有改动。
- `output/collector_yield_benchmark.py`：本地基准，不计划提交；运行时从指定 Git 基线读取 5 个旧模块，连接旧路径的依赖，避免拿已重构的同步版本冒充旧性能基线。

没有改 pair_validation 判定、限频、adapter、TTL、前端、部署文件或环境配置；没有新增应用线程/进程。

## 上一轮已执行命令与原始汇总

以下 Python 命令工作目录均为 `backend`。

基线 Ruff（实现前，5 个现有允许修改的源文件）：

```text
python -m ruff check app/services/collector.py app/services/market_validation_review.py app/services/symbol_aliases.py app/services/data_filters.py app/services/spread_engine.py
All checks passed!
```

先红后绿：

```text
python -m pytest tests/test_cooperative.py -q --basetemp=.pytest_yield_red -p no:cacheprovider
8 failed in 0.95s

python -m pytest tests/test_cooperative.py -q --basetemp=.pytest_yield_green -p no:cacheprovider
8 passed in 0.70s
```

红测原因为新接口尚未实现的 ImportError / ModuleNotFoundError。

```text
python -m pytest tests -q -k "collector or spread or validation or alias or filter or cooperative" --basetemp=.pytest_yield_a -p no:cacheprovider
526 passed, 1649 deselected in 38.28s

python -m ruff check app/runtime/cooperative.py app/services/collector.py app/services/market_validation_review.py app/services/symbol_aliases.py app/services/data_filters.py app/services/spread_engine.py tests/test_cooperative.py tests/collector_yield_reference.py
All checks passed!
```

全量验收：

```text
python -m pytest tests -q --basetemp=.pytest_yield_full -p no:cacheprovider
2171 passed, 4 deselected, 12 warnings in 153.62s (0:02:33)
```

12 条 warning 来自既有 `datetime.utcnow()` 弃用提示；没有修改相关文件。

仓库根目录 `git diff --check`：exit 0，无输出。

## 本轮并发发布修复与重新验收

审查在新增让出点复现：第一个机会完成身份校验后，绑定修改 API 可撤销绑定并清空 store 中的旧机会；collector 恢复后仍会发布此前已接受的旧版本机会。修复复用 `MarketValidationService._mutation_epoch`，在 prepare 开始记录，publish 的第一步同步比较；若变化则抛 `ValueError("mapping changed during collection")`，既有 `run_collector_loop` 捕获后在下一轮重试，不覆盖 API 刷新的 store，也不增加发布 generation。

新增回归测试先红后绿，测试通过检查点撤销绑定并调用实际 `_refresh_store`，确认没有重新发布机会。为保留上一轮 `.pytest_yield_a` / `.pytest_yield_full` 数据库证据，本轮只更换 pytest 的 `--basetemp` 目录名；测试范围和其余参数与任务书一致。

```text
python -m pytest tests/test_cooperative.py -q -k registry_change --basetemp=.pytest_yield_guard_red -p no:cacheprovider
1 failed, 8 deselected in 1.29s

python -m pytest tests/test_cooperative.py -q -k registry_change --basetemp=.pytest_yield_guard_green -p no:cacheprovider
1 passed, 8 deselected in 0.83s

python -m pytest tests -q -k "collector or spread or validation or alias or filter or cooperative" --basetemp=.pytest_yield_guard_a -p no:cacheprovider
527 passed, 1649 deselected in 38.89s

python -m pytest tests -q --basetemp=.pytest_yield_guard_full -p no:cacheprovider
2172 passed, 4 deselected, 12 warnings in 134.29s (0:02:14)

python -m ruff check app/runtime/cooperative.py app/services/collector.py app/services/market_validation_review.py app/services/symbol_aliases.py app/services/data_filters.py app/services/spread_engine.py tests/test_cooperative.py tests/collector_yield_reference.py
All checks passed!
```

12 条 warning 仍来自既有 `datetime.utcnow()` 弃用提示。Python 审查与代码复审均确认上述并发问题已解决；仓库根目录 `git diff --check` 通过。

## 12k 基准：旧 100 ms 门槛的历史结果

```text
python ../output/collector_yield_benchmark.py
BENCHMARK_SUMMARY {"old": {"median_total_ms": 2055.33, "worst_heartbeat_ms": 2585.507}, "new": {"median_total_ms": 2088.886, "worst_heartbeat_ms": 166.885}, "increase_pct": 1.633, "equal_outputs": true}
AssertionError
```

当时最后一个断言要求新路径最大心跳间隔 <=100 ms，进程 exit 1，因此上一轮没有提交或推送；该门槛现由任务书文末明确更正为 ≤250 ms。

- 固定 12,000 个市场，3,000 个 symbol，每组 1 spot + 3 future，产生 18,000 个机会。
- 使用实际 prepare、别名、过滤、机会构建、标签和逐机会校验；无真实网络或数据库，已采集输入直接注入，排除 adapter 和 compact unpack 开销。
- 时钟冻结，旧新各预热一次，5 轮交替顺序；总耗时取中位数，心跳取所有轮次最差值；GC 保持默认开启。
- 每轮同时校验全部市场、过滤市场和机会的 JSON 字段哈希，包括 validation 和 risk_labels；10 次哈希一致：`ed7a6a239e5bb339ddd8d4b723f9e4d71789b45d14a27b8f66ceede5410e8b53`。
- 另一次单轮只读诊断在该脚本的运行进程内加入 `gc.callbacks` 和预算日志：新路径打印 `LONG_GC 2 68.654`、`LONG_GC 2 82.917`、`LONG_GC 2 104.603`、`LONG_GC 2 128.251`；最后一次相邻预算检查打印 `LONG_WORK 131.103 _collect_once 261`，该行是 validate 循环检查点。该次新路径心跳最大值 151.237 ms。
- 单独测量同步 `filter_opportunities` 的 18,000 个机会仅耗时 15.698 ms，不能将 GC 暂停误归因为这个循环。
- `asyncio.sleep(0)` 只能在 Python 检查点让出，无法拆分正在执行的原生 GC。上一轮没有通过关闭 GC、降低机会数量、放宽性能断言或减少测试负载使基准转绿。

## 门槛更正后的首次 12k 基准：通过（并发修复前）

按任务书文末“门槛更正”和本轮用户指令，将 `output/collector_yield_benchmark.py` 最后一个断言从 `<=100` 改为 `<=250`；保留原数据量、预热、5 轮交替测量、默认 GC 和输出哈希校验。工作目录为 `backend`，命令及原始汇总：

```text
python ../output/collector_yield_benchmark.py
BENCHMARK_SUMMARY {"old": {"median_total_ms": 2630.853, "worst_heartbeat_ms": 2809.773}, "new": {"median_total_ms": 2659.93, "worst_heartbeat_ms": 216.427}, "increase_pct": 1.105, "equal_outputs": true}
```

- 进程 exit 0，三个断言全部通过：新路径最大心跳间隔 216.427 ms ≤250 ms；总耗时中位数增幅 1.105% ≤20%；新旧输出一致。
- 旧路径总耗时中位数 2630.853 ms，最大心跳间隔 2809.773 ms；新路径分别为 2659.930 ms、216.427 ms。
- 10 次样本均为 12,000 个市场、18,000 个机会，输出 SHA256 仍为 `ed7a6a239e5bb339ddd8d4b723f9e4d71789b45d14a27b8f66ceede5410e8b53`。
- 随后审查补充了上述并发发布检查和回归测试；最终源码的重新验收与基准结果另行记录，不以此轮数字替代最终测量。

## 最终源码 12k 基准：通过

并发发布修复后、全部测试完成后，再次在 `backend` 执行原基准脚本，原始汇总：

```text
python ../output/collector_yield_benchmark.py
BENCHMARK_SUMMARY {"old": {"median_total_ms": 2146.159, "worst_heartbeat_ms": 2383.465}, "new": {"median_total_ms": 2091.865, "worst_heartbeat_ms": 172.126}, "increase_pct": -2.53, "equal_outputs": true}
```

- 进程 exit 0。新路径最大心跳间隔 172.126 ms ≤250 ms；总耗时中位数从 2146.159 ms 变为 2091.865 ms，增幅 -2.530%（下降 2.530%，满足 ≤20%）。
- 旧路径最大心跳间隔 2383.465 ms；10 次样本仍均为 12,000 个市场、18,000 个机会，所有输出 SHA256 与前述一致。
- 本次为最终验收数字；首次运行和上一轮 100 ms 门槛失败数字保留作历史证据。

## Git、线上与文件保留

本轮重新执行只读远端查询，确认目标分支仍为指定基线：

```text
git ls-remote origin refs/heads/codex/frontend-localization-polish
6b146b26fe248fc02c3d20804cb9ab4da48ac537 refs/heads/codex/frontend-localization-polish
```

交付提交消息和非 force 推送命令如下；实际结果与最终完整 SHA 以本轮交付回执为准。若推送前再次查询发现远端目标不再是基线，则停止并报告。

```text
git commit -m "perf(collector): yield event loop during post-processing"
git push origin HEAD:codex/collector-yield
git push origin HEAD:codex/frontend-localization-polish
```

按用户要求不部署，未执行线上备份、容器更新或健康检查；本地性能结果不是线上验收。任务书中的线上基线为既有记录，本轮未连接服务器核实当前版本。

功能提交仅包含 5 个现有源文件、`cooperative.py`、2 个新测试文件、本交接和 `docs/next-task.md`。保留原有未跟踪的 `docs/task-collector-yield-20261028.md`，不将其视为本任务创建；测试生成的 `.pytest_yield_*` 目录及更正断言后的本地基准脚本保留，不清理或提交。

## 后续

本任务以更正后的 ≤250 ms 基准门槛交付，不再以旧 100 ms 门槛阻断。本地代码与测试保持在指定工作树；下一模块请新建任务。

线上效果需在后续明确授权的部署任务中验收：按部署文档备份、使用已验证镜像更新后，观察采集后处理路径的 `loop_stall` 和 `/api/health/live`。如以后需要低于 100 ms 的目标，应另行明确 GC / 对象分配治理范围。


## 审查后修复交付（2026-10-29，任务书 2026-10-28）

本节为最新状态，覆盖前文历史记录。本轮基线为 `4c4241114ed853edf12af824f727de1735b26290`，分支 `codex/collector-yield`。依据任务书末尾“审查后修复”及主仓 `output/collector-yield-review.log`，只修复映射快照与采集重入/设置一致性，不部署。

### 五个改动文件

- `backend/app/services/market_validation_review.py`：每次尝试固定 epoch 与 registry 读者快照（共享不可变 bindings）；refresh 在读完设置和绑定后原子替换 registry，避免提前改变 mode。注解之后、碰撞计算提交之前检查 epoch；失败尝试不 prune、不提交 discovered/collision、不调用失效写入。最多计算三次，第三次不执行协作检查点。接受快照后才执行发现 I/O；若该 I/O 期间映射再次变化，则抛出 `mapping changed during preparation`，不提交过期碰撞或调用失效写入，交由既有采集循环下一轮重试。
- `backend/app/services/collector.py`：collect_once 的 asyncio.Lock 覆盖整轮；设置加载后固定局部 risk，alias/prepare/filter/build/validate/风险标签/机会过滤沿用同一对象。保留发布前 epoch 检查和无 await 发布段。
- `backend/tests/test_cooperative.py`：新增三例，覆盖一次刷新重算、两次刷新后同步兜底、两轮排队且外部替换设置不影响当前轮；核对每次注解的资产版本、发现与失效调用、市场输出，以及 validate 和风险标签收到的设置对象。
- `backend/tests/test_collector.py`：原测试的“重入立即返回旧快照”与新要求冲突，改为验证排队等待、返回首轮完成后的 BTC 快照，仍验证 adapter 只轮询一次。
- 本交接文档。没有修改其他源文件、docs/next-task.md、任务书或基准脚本。

### 命令与原始汇总

Python 命令均在 backend 执行。为保留原有四个 .pytest_yield_* 目录中的数据库证据，只将验收命令 basetemp 改为新的 .pytest_yield_review_*；测试选择及其他参数保持不变。

```text
python -m pytest tests/test_cooperative.py -q -k 'retries_mapping_epoch or queues_rounds' --basetemp=.pytest_yield_review_red -p no:cacheprovider
3 failed, 9 deselected in 0.94s

python -m pytest tests/test_cooperative.py tests/test_collector.py -q --basetemp=.pytest_yield_review_green -p no:cacheprovider
1 failed, 35 passed in 173.83s (0:02:53)

python -m pytest tests -q -k "collector or spread or validation or alias or filter or cooperative" --basetemp=.pytest_yield_review_a -p no:cacheprovider
530 passed, 1649 deselected in 36.85s

python -m pytest tests -q --basetemp=.pytest_yield_review_full -p no:cacheprovider
2175 passed, 4 deselected, 12 warnings in 137.71s (0:02:17)
```

红测证据：映射刷新前已经提交 discovered；并发第二轮在第一轮未完成时加载设置，loader.await_count 为 2 而非 1。修改后的组合测试中三个回归用例均通过；唯一失败为既有心跳测试 elapsed=6.636749899997085 超过 5 秒（心跳间隔断言通过）。未改代码或放宽阈值，随后规定范围及全量验收均通过。一次误从仓库根目录执行测试返回 `no tests ran in 0.00s`，已改在 backend 执行，不计作验收。

12 条 warning 来自既有 datetime.utcnow() 弃用提示。Ruff 修改前四个相关文件检查有 1 条 I001；最终检查仍仅有 tests/test_collector.py:282 的同一条导入排序错误，无新增错误。

```text
python -m ruff check app/services/collector.py app/services/market_validation_review.py tests/test_cooperative.py tests/test_collector.py
Found 1 error.
[*] 1 fixable with the `--fix` option.

python -m ruff check app/runtime/cooperative.py app/services/collector.py app/services/market_validation_review.py app/services/symbol_aliases.py app/services/data_filters.py app/services/spread_engine.py tests/test_cooperative.py tests/test_collector.py tests/collector_yield_reference.py
Found 1 error.
[*] 1 fixable with the `--fix` option.

python -m ruff check app/runtime/cooperative.py app/services/collector.py app/services/market_validation_review.py app/services/symbol_aliases.py app/services/data_filters.py app/services/spread_engine.py tests/test_cooperative.py tests/collector_yield_reference.py
All checks passed!

git diff --check
exit 0
```

### 最终 12k 基准

全量测试结束后单独执行原脚本，未修改脚本、阈值、负载或 GC 设置。12,000 个市场、18,000 个机会，旧新各预热一次，然后五轮交替执行。

```text
python ../output/collector_yield_benchmark.py
BENCHMARK_SUMMARY {"old": {"median_total_ms": 2133.415, "worst_heartbeat_ms": 2166.832}, "new": {"median_total_ms": 2165.385, "worst_heartbeat_ms": 170.21}, "increase_pct": 1.499, "equal_outputs": true}
```

exit 0；新路径最差心跳 170.210 ms ≤250 ms，总耗时中位数增幅 1.499% ≤20%。十个样本输出哈希均为 `ed7a6a239e5bb339ddd8d4b723f9e4d71789b45d14a27b8f66ceede5410e8b53`，逐字段结果一致。

### Git、保留文件与线上边界

只读查询已确认两个远端分支均在指定基线。默认 Windows schannel 查询曾返回 SEC_E_NO_CREDENTIALS；使用单次命令的 OpenSSL 后成功，不修改 Git 全局/仓库配置，不改变网络节点或关闭 TLS 校验。

```text
git -c http.sslBackend=openssl ls-remote origin refs/heads/codex/frontend-localization-polish refs/heads/codex/collector-yield
4c4241114ed853edf12af824f727de1735b26290 refs/heads/codex/collector-yield
4c4241114ed853edf12af824f727de1735b26290 refs/heads/codex/frontend-localization-polish

git commit -m "fix(collector): pin mapping epoch and settings across yields"
git -c http.sslBackend=openssl push origin HEAD:codex/collector-yield
git -c http.sslBackend=openssl push origin HEAD:codex/frontend-localization-polish
```

推送前再核实 frontend-localization-polish 必须仍在指定基线；两次推送均禁止 force。最终 SHA 和实际推送原始输出见本轮交付回执及远端记录。本节属于该修复提交，可通过 git log 定位。

保留任务开始时全部未跟踪文件：四个既有 .pytest_yield_* 数据库目录、任务书和本地基准；本轮新生成的测试目录同样保留且不暂存。只暂存上述五个文件。

按用户要求未部署、未做线上备份或线上验收；本地性能不代表线上验证。连续映射更新触发第三次同步兜底时会暂时放弃协作让出，这是任务书指定的有限重试行为。下一模块或部署请另开任务，沿用本交接。

## 发布记录（2026-10-29，Claude 执行部署）
- 08:34 CST 上线 ddda20b，DEPLOY_EXIT=0，live/ready 200。
- /api/health/live 每 0.2s 探测 90s：修复前 9 次 3.46–4.54s；修复后（稳定期）max 1.56s，>1s 1 次，>0.5s 18 次。
- loop_stall 日志：采集路径不再出现；剩余 1 次 1.7s 来自 announcements.py 公告正则解析（fetch_api→_parse_payload→extract_event_schedule/_time）。
- 剩余亚秒延迟主因 CPU 争用：2 核机器 load 2.8–3.4，同机 node 进程 ~/arb-mvp-live（live-mainnet）约 62%、~/arb-mvp（example config）约 50%，taoli1 uvicorn 约 37%。
