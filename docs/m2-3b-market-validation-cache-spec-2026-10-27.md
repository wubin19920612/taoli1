# M2-3b 市场审核缓存瘦身：任务书（Claude，2026-10-27，待用户确认）

来源：`docs/m2-3-governance-list-2026-10-26.md`（分支 `codex/memory-fix`）的 #6 至 #8。线上普查结果：`market_validation.books` 常驻 **27MB**（1000 条，已到上限，每条约 27KB）；`market_validation.reviews` 7.6MB（1000 条，已到上限）。基线：`f7f56a9`（当前生产版本）。

## 1 现状（已核实，`backend/app/services/market_validation_review.py`）

- `self.books`（34 行）只在 `_book()`（332–379 行）中写入。写入的是**完整的 20 档**归一化订单簿，以元组 `(book, binding_revision, binding_fingerprint, fx_bid, fx_ask)` 存储，通过 `_bounded_put`（limit 1000）维护。
- `self.books` 唯一的读取方是 `_cached_book()`（141–157 行）→ `_book_row()`（159–174 行），**只用到** `book.bids[0]`、`book.asks[0]`、`book.timestamp`、`book.timestamp_source`。另外 626 行只读取条目数。
- 20 档深度只在 `_review()` 内部使用：`_book()` 的**返回值**被放进局部变量 `first`/`second`，再交给 `OrderBookDepthValidator`，不会从缓存读取。
- `self.reviews`：写入点在 502 行。读取点有两处：282 行（`cached and cached.expires_at > now`）和 509 行的 `validate()`（`passed` 要求 `cached.expires_at > now`）。`expires_at` 不会晚于写入时间加上 `review_ttl_seconds`（≤30 秒）。**已过期的条目永远不会命中**，但会一直常驻，直到被 1000 条上限挤出。
- `self._last_incident`（308–319 行）：只用于 60 秒去重。距今 ≥60 秒的条目，效果等同于不存在。
- `self.review_attempted`：它的值参与 `prepare()` 的调度排序（273 行）。删除条目会改变调度顺序，**本次不改**。

## 2 目标与设计

1. **books 只存盘口第一档**：`_book()` 写入缓存时，存一份只含 `bids[:1]`/`asks[:1]` 的 `OrderBookSnapshot` 副本（其他字段不变）；`_book()` **返回值仍然是完整的 `normalized`**，`_review()` 的深度校验不受影响。上限仍是 1000。预期 27MB 降到 3MB 以下。
2. **reviews 清理过期条目**：在写入 `self.reviews` 时（或在 `prepare()` 开头，二选一，以实现简单为准），删除 `expires_at <= now` 的条目。命中语义不变：凡是现在能命中的条目都必须保留。
3. **_last_incident 清理**：写入时删除距今 ≥60 秒的条目，保留现有的 1000 条上限。

不做：`review_attempted`、`failures`、`registry.bindings`（M2-4b）、`books` 的 TTL（`stale_after_seconds` 来自每次调用传入的 `risk`，没有统一的上界，按 TTL 删除可能改变语义）。

## 3 设计约束

- 行为必须等价：对同一串调用序列，`prepare()`、`annotate()`、`validate()`、`review()` 的返回值、`_cached_book()` 的命中与未命中、`record_incident` 的调用次数和参数，都要与旧实现完全相同。
- `refresh()`/`invalidate()` 中的清空逻辑、`_mutation_epoch`、锁、信号量、`review_inflight` 都不改。
- 不改 `OrderBookDepthValidator`、`pair_validation`、`market_identity`、任何 adapter，也不改 `cache_census.py`（普查的路径名保持不变）。

## 4 文件范围与测试

- 修改：`backend/app/services/market_validation_review.py`
- 新建：`backend/tests/test_market_validation_cache_slim.py`、`docs/task-handoff-2026-10-27-market-validation-cache.md`
- 禁止修改：除上面以外的所有文件。

测试要先写（使用 fake adapter，不联网）：
1. **盘口第一档等价**：fake adapter 返回 20 档、乱序、带 multiplier/fx 的订单簿。分别比较 `_book()` 的返回值（必须仍是 20 档，并与旧逻辑相同）、缓存中的条目（只有 1 档），以及之后 `_cached_book()`/`prepare()` 产生的行（与“缓存完整订单簿”的参照实现**完全相等**，包括 `data_source`、`quote_kind`、`book_timestamp`）。还要覆盖 revision/fingerprint/fx 不匹配，以及超过 stale_after 时的未命中情况。
2. **深度校验不受影响**：`review(twice=True)` 在深度不足和深度充足两种场景下，`DEPTH_UNCONFIRMED` 的判断结果与旧实现相同。
3. **reviews 过期清理**：写入若干条 `expires_at` 不同的结果，推进时间（monkeypatch `datetime` 或注入 now）后，过期条目被删除、未过期条目保留，282 行和 `validate()` 的命中结果与旧实现相同。
4. **_last_incident**：60 秒内重复调用只记录一次；超过 60 秒的条目被清理，下一次调用仍会记录，次数和参数与旧实现相同。
5. **体积**：1000 个 20 档订单簿写入后，`app.runtime.cache_census.deep_size(service.books)` 至少比旧实现小 80%。
6. 回归：`tests/test_market_validation_review.py`、`tests/test_market_validation_prepare_copies.py`、`tests/test_market_validation_astro.py`、`tests/test_market_identity_validation.py`、`tests/test_orderbook_validator.py`，以及后端全量（`--basetemp=<worktree 内目录>`）。

## 5 上线验收（Claude，部署需用户批准）

- 上线顺序：feature-flags 平台期测完 → M2-3a 上线并采集 → 再上 M2-3b。每次上线之间间隔至少 1 小时，各自单独测量。
- 探针对比：`market_validation.books` 的贡献约 −25MB，reviews 在平台期再降几 MB；不能出现新的审核失败或 `DEPTH_UNCONFIRMED` 异常增多（对比 incident 表中 `SPREAD_REVIEW` 的 open/resolved 比例）。
- 回滚：revert 提交。

## 6 派发

- Codex：gpt-6.1-sol xhigh（边界清晰、单文件、语义已核实）。worktree `.worktrees/m2-3b-books`，分支 `codex/m2-3b-books`，基于 `f7f56a9`。
