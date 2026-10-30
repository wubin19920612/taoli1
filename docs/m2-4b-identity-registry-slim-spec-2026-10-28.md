# M2-4b 身份注册表瘦身：任务书（Claude，2026-10-28，待用户确认）

来源：`docs/m2-3-governance-list-2026-10-26.md`（分支 `codex/memory-fix`）的 M2-4b。线上普查：`market_validation.registry.bindings` 常驻 **50MB**，约 1.9 万条，每条约 2.6KB。基线：`4ad88b7`（当前生产版本，平台期约 616MB）。

## 1 现状（已核实）

- `MarketIdentityRegistry`（`backend/app/services/market_identity.py`，96 行）：`replace(bindings)` 把 `{key: b.model_copy(deep=True)}` 存进 `MappingProxyType`。唯一的调用方是 `MarketValidationService.refresh()`（`market_validation_review.py:94`），输入是 `repository.list_bindings()` 刚从库里解析出来的完整 `MarketBinding`，用完就丢。所以 deep copy 只会造成刷新时的短暂双份，**常驻的 50MB 是这 1.9 万个完整 pydantic 对象本身**，包括每条最多 20 条的 `evidence`。
- 注册表的读取方只有 `annotate()`（`market_validation_review.py:167` 通过 `self.registry.annotate`），只用到 binding 的这些字段：`asset_id`、`display_name`、`asset_kind`、`identity_status`、`unit_status`、`revision`、`valid_until`、`fingerprint`、`quote_currency`、`contract_kind`、`tracking_definition`、`contract_size`、`price_multiplier`。**用不到** `evidence`、`verified_at`、`settlement_currency`、`spec_fingerprint`、`market`（`settlement_currency`/`spec_fingerprint` 只参与 fingerprint 的计算）。
- `fingerprint` 是 `@property`：每次读取都要做一次 `json.dumps` 加 sha256。`annotate` 每轮要对约 1.2 万行行情各调用一次，所以这里也有 CPU 开销。
- 其他地方没有读取 `registry.get()` 或 `registry.bindings`（只有 `cache_census.py:262` 按路径名统计大小）。测试只会用 `MarketBinding` 构造注册表，再调用 `annotate`。

## 2 设计

1. 新增模块内的 `_RegistryEntry`：`@dataclass(frozen=True, slots=True)`，字段就是 §1 列出的 13 个（`fingerprint` 在构造时预先算好，存成 `str`）。提供 `classmethod from_binding(b: MarketBinding)`。
2. `replace(bindings)` 改成存 `MappingProxyType({b.market.key: _RegistryEntry.from_binding(b) ...})`，不再 deep copy；frozen 加上不持有原对象的引用，已经能达到原来 deep copy 想要的隔离效果，即调用方之后修改输入不会影响注册表。
3. `get()` 返回 `_RegistryEntry | None`（类型标注随之调整）；`annotate()` 的逻辑和返回值**完全不变**，只把 `binding.fingerprint` 改成读取预先算好的字段。
4. 字符串去重：`tracking_definition`、`display_name`、`quote_currency` 等重复值很多，可以用 `sys.intern`，也可以用模块级 dict 复用相同的值；实现时二选一，以简单为准。
5. 不做：`list_bindings()`/`MarketBinding` 模型、库表结构、`refresh()` 的频率和加锁、`cache_census` 的路径名、API（`routes_market_identities.py` 自己会读库）。

## 3 设计约束

- **行为等价**：对任意 binding 集合和任意行情行，新旧注册表 `annotate()` 的输出必须完全相等，包括 `model_dump()` 和字段类型（`valid_until` 仍是 datetime，`binding_fingerprint` 和原来的 sha256 相同）。
- `mode` 属性和 `annotate_all` 保持不变。

## 4 文件范围与测试

- 修改：`backend/app/services/market_identity.py`
- 新建：`backend/tests/test_market_identity_registry_slim.py`、`docs/task-handoff-2026-10-28-m2-4b-identity.md`
- 禁止修改：其他所有文件。

测试先写（不联网）：
1. **等价性**：保留一份旧实现作为参照（在测试文件里复制旧版 `MarketIdentityRegistry`）。构造覆盖以下情况的 binding 集合：pending、verified（带 evidence、有 valid_until）、已过期、conflict、unit verified 加 price_multiplier≠1、contract_size 与行情不一致、valuation 带 tracking_definition、settlement_currency/spec_fingerprint 不同（fingerprint 不同）。再配上行情行：命中、未命中、`MarketKey.from_snapshot` 报 ValueError、带 alias multiplier、bid/ask/size 为 None。新旧两套 `annotate()` 的 `model_dump()` 必须逐行相等，`now` 要覆盖过期前和过期后两种情况。
2. **隔离**：`replace()` 之后修改输入的 binding（例如 `model_copy` 前后对比，或者直接改字段），注册表的 `annotate` 结果不变。
3. **体积**：构造 2 万个带 5 条 evidence 的 verified binding，`app.runtime.cache_census.deep_size(registry.bindings)` 至少比旧实现小 70%。
4. 回归：`tests/test_market_identity_validation.py`、`tests/test_market_validation_review.py`、`tests/test_market_validation_astro.py`、`tests/test_market_validation_prepare_copies.py`、`tests/test_market_validation_cache_slim.py`、`tests/test_cache_census.py`，以及后端全量（`--basetemp=../.pytest-tmp/x`，先 `mkdir -p .pytest-tmp`）。

## 5 上线验收（部署需用户批准）

- 等锁修复（`4ad88b7`）+4h 观察结束后再上线，用 `output/sqlitefix-isolated-deploy-20261028.sh` 这份副本部署。
- 探针对比 616MB 平台期：预期约 −30~40MB；后端 CPU 不应上升，预计会略降，因为 fingerprint 不用每次重算。
- 不能出现新的 identity_status 分布变化：上线前后各抽一次 `/api/market-identities` 的统计做对比，只读。
- 回滚：revert 这个提交。

## 6 派发

- Codex：gpt-6.1-sol xhigh（单文件，边界清晰）。worktree `.worktrees/m2-4b-identity`，分支 `codex/m2-4b-identity`，基于 `4ad88b7`。不要跑 WSL。
