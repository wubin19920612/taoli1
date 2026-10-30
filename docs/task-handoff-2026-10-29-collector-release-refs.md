# Collector 已消费行情引用提前释放交接（2026-10-29）

## 目标、范围与基线

- 按 `docs/collector-release-refs-spec-2026-10-29.md` §2、§3 实现 collector 引用释放。
- 工作目录：`.worktrees/collector-release-refs`；分支：`codex/collector-release-refs`。
- 实际起始 HEAD：`4d046cd378a7e2339bb4ea8e1d2656ed4570cfc8`（任务书提交）；其父提交为任务书注明的 `8950523`。
- 开始时工作区干净。仅修改 `backend/app/services/collector.py`，新增本文件和 `backend/tests/test_collector_release_refs.py`。
- 用户要求全程离线、禁止 WSL；本次全部使用 Windows 原生 Python / PowerShell，没有联网或运行 WSL。

## 实现与关键入口

- `MarketCollector._collect_once`：在 due-adapters 分支的 `_store_poll_results` 返回后释放 `results`；别名处理返回后释放 `markets`。两处各加一行解释跨 yield 引用生命周期的注释。
- 已检查 `_store_poll_results` → `_store_adapter_result` → `compact_markets.pack`：行情转为 compact 字段值，内部可变容器被复制，不保留原始 `MarketSnapshot` 对象。
- 已检查 `_collect_once` 的后续分支：不再读取 `markets`，只使用 `aliased_markets` / `filtered_markets`；无 due-adapters 时不执行 `del results`。
- `apply_symbol_aliases_async` 对无需转换的行会复用对象；这些仍被下游需要的行应继续存活，删除局部列表不会破坏它们。
- prepare、annotate、发布、slot、consumer 及其他生产函数均未修改，yield 位置全部保留。
- 结构复核：把新增两处 `del` 从当前 AST 中移除后，与起始 HEAD 的 collector AST 完全一致。

## 测试与原始输出

环境：Windows，Python 3.12.10，pytest 8.4.2，Pydantic 2.12.5。测试复用现有 `MutableAdapter`、`FixedAdapter` 和 `SnapshotStore`，受现有 `conftest.py` 的离线网络拦截保护。

先新增释放测试，生产文件尚未修改时执行：

```powershell
# 以下命令均在 backend 下运行；New-Item 是 Windows 下 mkdir -p 的等价操作。
New-Item -ItemType Directory -Force ../.pytest-tmp | Out-Null
python -m pytest -q tests/test_collector_release_refs.py --basetemp=../.pytest-tmp/red
```

测试只保留弱引用，在 fake prepare 内等待可控 Event；等待期间从测试任务执行 `gc.collect()`。分别验证原始 adapter 批次和被别名替换的解包行已回收，并确认未转换的复用行继续存活。失败后也会释放 Event 并等待采集任务结束。

修复前原始失败输出：

```text
FAILED tests/test_collector_release_refs.py::test_consumed_batches_released_while_prepare_waits[adapter]
FAILED tests/test_collector_release_refs.py::test_consumed_batches_released_while_prepare_waits[unpacked]
2 failed in 1.41s
```

确认失败后，才实现两处引用释放。定向验收（显式展开 PowerShell 不会自动展开的文件通配符）：

```powershell
$collectorTests = @(Get-ChildItem tests/test_collector*.py | ForEach-Object { $_.FullName })
python -m pytest -q @collectorTests tests/test_cooperative.py tests/test_collector_release_refs.py --basetemp=../.pytest-tmp/targeted
```

```text
80 passed, 2 deselected in 2.42s
```

现有 cooperative 测试使用 `tests/collector_yield_reference.py` 的冻结参照，行为等价测试全部通过。

后端全量：

```powershell
python -m pytest -q --basetemp=../.pytest-tmp/x
```

```text
2177 passed, 5 deselected, 12 warnings in 145.81s (0:02:25)
```

12 个 warning 为现有 index-component 测试路径中的 `datetime.utcnow()` 弃用提示，未扩大范围修改。

微基准：

```powershell
python -m pytest -q -s -m benchmark tests/test_collector_release_refs.py --basetemp=../.pytest-tmp/b
```

```text
collector_release_refs: rows=12400 opportunities=2500 before_peak_mib=145.043 after_peak_mib=87.805 saved_mib=57.238
1 passed, 2 deselected in 9.72s
```

基准负载与调查报告一致：binance 2500 行 spot、bybit 2500 行 future、okx 7400 行独立 spot。使用实际 compact、别名、validation 和机会构建流程，仓库替身不持有 discover 入参；固定时钟、阻止 FX 刷新，无网络、数据库或业务消费者。两边分别预热，保留上一轮发布结果，再强制全部 adapter 到期，以 tracemalloc 测量单轮新分配峰值。修复前对照仅在内存中从当前 `_collect_once` 源码移除两处 `del`，其余流程及 yield 完全相同。停止跟踪后对全部发布行情及机会字段计算摘要，确认结果一致。基准标记为 `benchmark`，默认全量不运行。

## 交付状态、产物与下一步

- 本文件与实现、测试一同创建本地提交，主题为 `perf: release consumed market batches before collector yields`，末尾包含用户指定的 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- `git diff --check` 通过；提交仅包含 §3 允许的三个文件。
- 未跟踪的 `.pytest-tmp/` 为本次验收生成的临时目录，包含测试 SQLite 数据库；保留原样，不加入提交。
- 未推送、未备份线上数据库、未部署、未做线上接口验收。任务书记录生产版本为 `ddda20b`，本次没有联网核实当前版本。
- 数字代表 Windows 离线 Python 分配峰值，不代表 Linux RSS 降幅或线上内存验收结果。
- 本模块本地实现及验收完成。后续推送、部署和线上 RSS / 延迟验证应另开任务，取得联网与上线授权后按部署文档完成备份、版本核验及验收。
