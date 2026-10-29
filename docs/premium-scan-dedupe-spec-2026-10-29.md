# 多交易所溢价监控：减少重复全量扫描（任务书，Claude，2026-10-29）

基线：`8950523`（部署分支最新提交，生产 ddda20b + 文档）。worktree `.worktrees/premium-scan-dedupe`，分支 `codex/premium-scan-dedupe`。调查报告见主检出目录 `output/cpu-hotspots-investigation-20261028.log`（热点 2）。

## 1 现状（`backend/app/services/multi_exchange_premium.py`）
- `run_once`（259 行）先调一次 `eligible_groups(store.get_markets(), settings, now)`，对约 1.24 万行做 `filter_markets`、条件判断、`MarketKey.from_snapshot(...).key` 并分组。之后每批最多 `BATCH_SIZE` 组并发取点，**每组完成后**，`_evaluate_results`（296 行）会重新读取 settings，再调一次 `eligible_groups`（全量扫描），并且对全部市场重新计算 key，建立 `by_market`。所以一批最多 17 次全量扫描。
- py-spy 结果：`sample_group`、`_evaluate_results`、`eligible_groups` 合计约 0.04 核。

## 2 设计
1. 新增一个模块内的小缓存（实例属性即可），**按输入身份**复用全量扫描的结果：key 为 `(snapshot_store.generation, settings 的值指纹)`。settings 用 `model_dump_json()`；如果它本身开销大，可以换一个更便宜但等价的指纹，需要在交接文档里说明。缓存的值是一个**不含时间判断**的中间结果：按组分好的行情（`filter_markets` 和除 `binding_valid_until <= now` 以外的所有条件都已经应用），以及 `MarketKey.key -> (group, market)` 的索引。
2. `eligible_groups(markets, settings, now)` 的公开签名和返回值不变（测试和其他调用方会用到），但在内部拆成两步：“与时间无关的分组”和“按 now 过滤过期绑定，再按 ≥2 个交易所筛组”。`run_once` 和 `_evaluate_results` 走带缓存的路径；每次都用**当时的 now** 重新做过期过滤和 ≥2 交易所判断，结果必须和直接调用 `eligible_groups(store.get_markets(), settings, now)` **完全相同**（包括组内的顺序）。
3. `_evaluate_results` 里的 `by_market` 直接用缓存里的索引，不再对全部市场调用 `MarketKey.from_snapshot`。对 `results` 中每个 market 的 key 计算保持不变。
4. settings 仍然每次都读（`get_risk_settings()`，语义不变）。generation 或 settings 有变化时，缓存自动失效；缓存只保留最新的 1 份。
5. 不做：修改 `matching_points`、告警消息、冷却、BATCH_SIZE、取点逻辑、并发结构和其他文件。

## 3 约束
- **行为等价**：对任意的行情、settings、now 序列，新旧实现产生的告警（发送内容、AlertEvent、冷却状态 `_next_allowed`、status 字段）完全相同。
- 网络等待期间：行情 generation 变化、黑名单变化、绑定变化、开关关闭、阈值变化、`binding_valid_until` 恰好在 now 时过期，都必须得到和旧实现相同的结果。
- 缓存不能持有超过 1 份全量结果（内存）。

## 4 文件与测试
- 修改：`backend/app/services/multi_exchange_premium.py`
- 新建：`backend/tests/test_multi_exchange_premium_scan_cache.py`、`docs/task-handoff-2026-10-29-premium-scan-dedupe.md`
- 禁止修改其他文件；`tests/test_multi_exchange_premium.py` 只能运行，不能修改。
测试要先写（不联网，fake store、settings repo、notifier 和 `_point`）：
1. 等价性：测试文件里保留旧版 `eligible_groups` 和 `_evaluate_results` 作为参照，构造多组、多交易所的数据，16 组错峰完成；在完成之间插入 §3 列出的各种变化，逐步比较新旧实现的通知、AlertEvent、`_next_allowed` 和 status。
2. 扫描次数：用计数器包装 `filter_markets` 或全量分组函数。同一 generation 和 settings 下的一批里，全量扫描只发生 1 次；generation 变化后重新扫描 1 次。
3. 过期边界：`binding_valid_until == now` 算过期；一条腿过期导致组内少于 2 个交易所时，这个组消失。
4. 回归：`tests/test_multi_exchange_premium.py`、`tests/test_premium_index_query.py`，以及后端全量（在 backend 下 `mkdir -p ../.pytest-tmp`，`python -m pytest -q --basetemp=../.pytest-tmp/x`）。
5. 微基准：1.24 万行、16 组错峰完成时，打印新旧实现的总扫描耗时。

## 5 派发
- Codex：gpt-6-astra high（涉及异步等待期间的状态变化，等价性要求高）。不要跑 WSL，不要联网。上线需另行申请批准。
