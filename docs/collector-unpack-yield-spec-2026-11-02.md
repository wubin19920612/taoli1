# 采集器解包分段让出（2026-11-02）

## 背景
生产 1f331d8 + `COLLECTOR_MAX_DUE_ADAPTERS_PER_CYCLE=10` 后 loop_stall 10 次/小时（1.1–1.5s）。
12 次中 8 次堆栈在 `collector.py:244 _collect_once → _combined_exchange_markets:533 → compact_markets.py:44 unpack`：
每轮同步解包约 1.24 万条 MarketSnapshot，中途不让出事件循环，并叠加解包触发的 GC（300–650ms）。

## 目标
解包改为协作式：与后续 `apply_symbol_aliases_async` / `filter_markets_async` 共用同一个 `YieldBudget`（20ms），
结果与现在逐条完全一致（顺序、字段、fields_set、可变字段深拷贝语义都不变）。

## 改动范围（只改这些文件）
- `backend/app/services/compact_markets.py`：新增 `async def unpack_async(self, budget: YieldBudget) -> list[MarketSnapshot]`，
  每行前 `await budget.maybe_yield()`，行构造逻辑与 `unpack()` 共用（抽一个私有的单行构造函数，不要复制粘贴两份）。保留 `unpack()`。
- `backend/app/services/collector.py`：
  - `_combined_exchange_markets` 改为 `async def _combined_exchange_markets(self, adapters, budget: YieldBudget)`，内部用 `unpack_async(budget)`。
  - `_collect_once` 里把 `budget = YieldBudget()` 提前到解包之前，`markets = await self._combined_exchange_markets(active_adapters, budget)`，后面别名/过滤/build 继续用同一个 budget。
  - 其余逻辑（validation_epoch 检查、del markets、错误合并）不变。
- 测试：
  - `backend/tests/test_cooperative.py:153`、`:256` 的 `collector._combined_exchange_markets = lambda _: rows` 改为等价的 async 替身（接收 adapters, budget）。
  - `backend/tests/test_collector_release_refs.py:54-63` 的 `track_unpacked` 改成 async 包装，断言不变。
  - `backend/tests/collector_yield_reference.py` 是基线副本，**不要改**。
  - `backend/tests/test_compact_markets.py` 新增：`unpack_async` 与 `unpack` 结果逐条相等（含 fields_set、可变字段互不共享）；空数据返回新列表。
  - 新增 collector 测试：budget 设为 0ms（或可计数的替身）时，解包 ≥N 行会多次让出（`asyncio.sleep(0)` 被调用多次 / 其他任务能在解包中途运行）。
- 不允许放宽任何已有断言。

## 不做
- 不缓存解包结果（违背内存治理，且每轮 10 家都会刷新）。
- 不改 GC 参数、不改阈值、不改调度上限。

## 验收命令（用锁定依赖 venv）
```
cd backend
<venv>/Scripts/python -m pytest tests/test_compact_markets.py tests/test_cooperative.py tests/test_collector_release_refs.py tests/test_collector.py tests/test_collector_compact_equivalence.py -q
<venv>/Scripts/python -m pytest -q -p no:cacheprovider
```
