# collector 提前释放中间行情引用：任务书（Claude，2026-10-29）

基线：`8950523`（部署分支最新提交，生产 ddda20b）。worktree `.worktrees/collector-release-refs`，分支 `codex/collector-release-refs`。调查报告：主检出目录 `output/ddda20b-memory-investigation-20261029.log`。

## 1 背景
ddda20b（collector 后处理期间让出事件循环）上线后，rss_anon 平台期从约 556MB 升到 603–613MB。离线 tracemalloc 测量：新旧实现每轮的分配峰值几乎相同（约 145MiB），但 `_collect_once` 有两批已经不再使用的对象，要等整轮结束才释放；有了 yield 之后，它们和其他任务的分配交错，并被采样采到。离线试改结果：两处提前释放后，峰值 145.16→87.92MiB（−57MiB），所有 yield 都保留。

## 2 设计（只改 `backend/app/services/collector.py::_collect_once`）
1. `await self._store_poll_results(due_adapters, results, now)` 之后立刻 `del results`（只在 `due_adapters` 分支内）。确认 `_store_poll_results` 已经把需要的数据转成 compact 存储，不再引用 `results`。
2. `apply_symbol_aliases_async(markets, ...)` 返回之后立刻 `del markets`，后面只使用 `aliased_markets`。检查函数后面的代码（包括异常和提前返回的路径）都不再读取 `markets`。
3. 每处加一行注释说明原因（yield 期间不要持有已经消费过的整批行情）。
4. 不做：修改 prepare、annotate、发布、slot、consumer 和任何其他函数；不修改 yield 的位置。

## 3 文件与测试
- 修改：`backend/app/services/collector.py`
- 新建：`backend/tests/test_collector_release_refs.py`、`docs/task-handoff-2026-10-29-collector-release-refs.md`
- 禁止修改其他文件。
测试要先写（不联网，复用现有 collector 测试里的 fake adapters 和 store）：
1. **释放测试**：用 weakref 跟踪某一轮 adapter 返回的原始 `MarketSnapshot`（`results` 里的对象），以及 `_combined_exchange_markets` 解包出来的对象。在 prepare（或者 market_validation 的 fake prepare）里 await 一个可以控制的 Event，在等待期间执行 `gc.collect()`，断言原始对象和解包对象（没有被别名或标注复用的那部分）已经被回收。修复前这个测试必须失败。
2. **行为等价**：现有的 `tests/test_collector*.py`、`tests/test_cooperative.py` 和 `tests/collector_yield_reference.py` 相关测试全部通过，输出不变。
3. **内存微基准**：1.24 万行、2500 个机会，用 tracemalloc 打印修复前后单轮的峰值（参照调查报告的做法），标记为 benchmark，不进默认全量。
回归：后端全量（在 backend 下 `mkdir -p ../.pytest-tmp`，`python -m pytest -q --basetemp=../.pytest-tmp/x`）。

## 4 派发
- Codex：gpt-6.1-sol xhigh。不要跑 WSL，不要联网。上线需要另行申请批准。
