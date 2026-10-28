# M2-4a 采集器原始快照紧凑存储：任务书（Claude，2026-10-28，待用户确认）

来源：`docs/m2-3-governance-list-2026-10-26.md`（分支 `codex/memory-fix`）的 M2-4a。基线：`4ad88b7`。worktree `.worktrees/m2-4a-collector`，分支 `codex/m2-4a-collector`。

## 1 调查结论（只读 Codex 实测，报告在主检出目录 `output/m2-4a-investigation-20261028.log`）

- 采集器 `_exchange_snapshots`（`backend/app/services/collector.py:162`）按交易所保存原始 `MarketSnapshot`，原因是各交易所轮询节奏不同：未到期、冷却中或失败的交易所沿用旧快照（写入在 `:432`、`:449`，读取只在 `_combined_exchange_markets` `:505`，启动时由 `_seed_exchange_snapshots_from_store` `:367` 用 store 中的对象播种）。
- 本地用 1.2 万行真实字段分布实测：原始快照和打过标记的副本共享的部分只有 4MiB，**原始快照独占约 36.4MiB**。主要开销是每个 pydantic 对象的字段字典（约 1.6KB/行），所以即使叶子值共享也省不了多少。
- **不能只保留标记后的结果，然后在下一轮重新处理**：`apply_symbol_aliases` 不幂等（价格会被重复乘 ×10）；binding 删除、FX 过期、盘口缓存失效之后，旧的标记结果无法恢复成原始值。所以必须保留原始数据，但不一定要保留完整的原始模型。
- store 的 `_all_markets` 和 `_markets` 共享行对象，发布到 slots 时只构造 tuple，没有额外复制。

## 2 设计：用紧凑记录代替原始 pydantic 模型（实测可省约 30MiB）

1. 新建模块 `backend/app/services/compact_markets.py`：
   - `CompactMarkets`：保存一批行。每行是一个 tuple，按 `MarketSnapshot.model_fields` 的固定顺序存放全部字段值，再加上 `model_fields_set` 的位图（int）。字段顺序在模块加载时从 `MarketSnapshot.model_fields` 取得，不手写。
   - `pack(markets: Sequence[MarketSnapshot]) -> CompactMarkets`，以及 `CompactMarkets.unpack() -> list[MarketSnapshot]`。unpack 用 `MarketSnapshot.model_construct(_fields_set=..., **values)` 逐行重建，值本来就来自已经校验过的模型。
   - **可变值隔离**：pack 时把 list/dict/set 类型的值转成不可变形式（tuple、frozenset，或者在 pack 时做一份拷贝），unpack 时每行都生成**新的**容器；绝不能让多次 unpack 出来的行共享同一个可变对象。
   - `__len__`，以及给 `cache_census` 用的普通属性即可，不需要额外接口。
2. `collector.py` 只改这几处：`_store_adapter_result` 的两处写入改成存 `pack(result.markets)`；`_seed_exchange_snapshots_from_store` 改成按交易所 pack；`_combined_exchange_markets` 改成按 adapter 顺序 unpack 并拼接，返回的列表内容、顺序和类型与旧实现相同。`_exchange_snapshots` 的类型标注改为 `dict[str, CompactMarkets]`。
3. 不做：复用上一轮的标记结果、改 aliases/prepare/filter/store/slots、改 `cache_census` 的路径名、改轮询调度。

## 3 设计约束

- **行为等价**：对同一串 adapter 返回序列（多轮，包括到期和未到期、冷却、部分成功、带错误的成功、空成功、播种），新旧 collector 每轮发布到 store 的 `all_markets`/`markets`/`opportunities`/`exchange_errors`，`model_dump()` 必须逐项相等，顺序也相同。
- unpack 出来的每一行，`model_dump()` 和 `model_fields_set` 都与 pack 之前的原对象相同，字段类型也相同（datetime 仍是 datetime，list 仍是 list）。
- 每轮 unpack 1.2 万行的 CPU 开销要在测试中给出数字（本机 1.2 万行 unpack 的耗时），上限 150ms。超过上限就在交接文档里说明，并提出分批方案，但不要擅自改设计。

## 4 文件范围与测试

- 新建：`backend/app/services/compact_markets.py`、`backend/tests/test_compact_markets.py`、`backend/tests/test_collector_compact_equivalence.py`、`docs/task-handoff-2026-10-28-m2-4a-collector.md`
- 修改：`backend/app/services/collector.py`（只改 §2-2 列出的几处）
- 禁止修改：其他所有文件。

测试先写（不联网）：
1. **往返**：用覆盖所有可选字段的 MarketSnapshot（现货、期货、HL dex、带 estimated_fields、带 datetime 和 None、带 alias multiplier），pack 后 unpack，`model_dump()` 和 `model_fields_set` 相等；修改 unpack 结果里的 list，不会影响下一次 unpack 的结果。
2. **collector 等价**：在测试文件里保留旧版 collector 的存储逻辑作为参照（子类覆盖那 3 个方法，或者复制旧方法），用 fake adapters 跑至少 6 轮，覆盖 §3 列出的各种状态，并接入真实的 `apply_symbol_aliases` 和 `MarketValidationService.prepare`（fake repository/adapters），逐轮比较 store 的输出。
3. **体积**：1.2 万行，用 tracemalloc 比较 `pack` 后的结果与 `list[MarketSnapshot]` 的保留内存，至少小 60%；同时打印 unpack 的耗时。
4. 回归：`tests/test_collector*.py`、`tests/test_snapshot_*.py`、`tests/test_market_validation_*.py`、`tests/test_cache_census.py`，以及后端全量（`--basetemp=../.pytest-tmp/x`，先 `mkdir -p .pytest-tmp`）。

## 5 上线验收（部署需用户批准）

- 排在 M2-4b 和 Gate 降噪之后，单独上线、单独测量，用 `output/sqlitefix-isolated-deploy-20261028.sh` 副本部署。
- 探针：平台期预计再降约 25–30MB（RSS 降幅会小于 Python 堆的降幅）；collector 的 stage_timings 中 `prepare` 阶段耗时上升不超过 50ms。
- 回滚：revert 这个提交。

## 6 派发

- Codex：gpt-6-astra high（跨模块数据流，等价性要求高）。不要跑 WSL。
- 验收：Claude 核对改动范围和 Codex 日志里的全量汇总；代码审阅交给只读 Codex（重点：可变值隔离、字段顺序、`_fields_set`、播种路径）。
