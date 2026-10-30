# M2-3a FS 规格缓存瘦身：任务书（Claude，2026-10-26，待用户确认）

来源：`docs/m2-3-governance-list-2026-10-26.md`（在分支 `codex/memory-fix` 上）的 #1。线上普查：`fs_monitor.providers.1.cache` 有 6 个条目，共 **71MB**。

## 1 现状（已核实，`backend/app/services/fs_public.py`）

- `binance_specs(futures)`（354–364 行）：把 Binance 的 `/api/v3/exchangeInfo`（现货）或 `/fapi/v1/exchangeInfo`（合约）**整份解码**，存成 `{symbol: 原始 dict}`，缓存在 `self.cache[("binance_specs", futures)] = (dict, at)` 里，TTL 20 秒；**旧条目会一直常驻**，直到下一次刷新把它覆盖。
- 调用方（只有这两处）：
  - `borrow_reference()`（161 行，只用现货）：用到 `baseAsset`、`quoteAsset`、`symbol`、`isMarginTradingAllowed`；并把返回的 `at` 当作 `margin_at`。
  - `current_instruments()` 的 Binance 分支（299–329 行，现货+合约）：用到 `symbol`、`status`、`baseAsset`、`quoteAsset`、`contractType`、`marginAsset`、`filters` 中 `LOT_SIZE` 的 `stepSize`/`minQty`，`NOTIONAL` 或 `MIN_NOTIONAL` 的 `minNotional`/`notional`；返回的 `at` 被写进 `row.source_times.spot_specs/future_specs`。
- **TTL 不能延长**：`fs_economics.py:129` 要求所有 `source_times` 都在 60 秒以内，否则抛 `stale_input`。必须保持 20 秒刷新，`at` 的含义不变（仍是本次 HTTP 响应的时间）。

## 2 目标

- 缓存里**只保留上面列出的字段**，用紧凑结构（例如每个 symbol 一个小 dict 或 `__slots__` 对象；filters 只保留用到的两类、四个字段），预期常驻从约 71MB 降到 5MB 以下。
- 解码出来的原始 payload 在转换完成后**立刻释放**（不能被闭包、异常 traceback 或局部变量长期引用）。
- 行为**逐字节等价**：`borrow_reference()` 和 `current_instruments()` 对同一份上游数据的返回值（包括 `at`）与旧实现完全相同，包括缺少字段、`filters` 缺少 `NOTIONAL`、状态不是 TRADING 等边界情况。

## 3 设计约束

- 只改 `binance_specs` 内部的数据形状，以及两个调用方读取字段的方式；保持读取方的判断逻辑、异常类型（`KeyError` 这类会被上层捕获的异常，在相同的输入下仍然以同样的方式触发）、缓存键、TTL、锁和限速逻辑不变。
- 不改 `get()`、其他交易所的分支、`books()`、`meta()`、`public_snapshot()`、`fs_monitor.py`、`fs_economics.py`、`fs_astro*`，也不改任何 FS 判断逻辑。
- 可选（改动 ≤10 行时才做）：`self.cache[symbol]`（每个 symbol 的 meta，很小）保持原样，不要动。

## 4 文件范围与测试

- 修改：`backend/app/services/fs_public.py`（只改 `binance_specs` 和 Binance 分支里读取字段的那几行，以及 161–166 行）
- 新建：`backend/tests/test_fs_public_spec_cache.py`、`docs/task-handoff-2026-10-26-fs-spec-cache.md`
- 禁止修改：除上面以外的所有文件。

测试（先写测试；fake HTTP，不联网）：
1. **新旧对照**：把旧实现原样复制到测试里作为参照，用同一批 fake exchangeInfo（包含正常、缺 `NOTIONAL` 只有 `MIN_NOTIONAL`、两者都缺、状态非 TRADING、`contractType` 非 PERPETUAL、`marginAsset` 非 USDT、`baseAsset` 不匹配、symbol 不存在）比较 `borrow_reference()` 和 `current_instruments()` 的返回值，结果必须完全相等，`at` 也要相同。
2. 缓存体积：用 3000 个现货 symbol + 600 个合约 symbol 的 fake 数据（每个 symbol 带上真实 exchangeInfo 里常见的冗余字段，如 `orderTypes`、`permissions`、多个 filters），测得缓存的深度体积（可以用 `app.runtime.cache_census.deep_size`）至少比旧实现小 80%。
3. 20 秒内重复调用只发一次请求；超过 20 秒重新请求，`at` 更新。
4. 原始 payload 释放：转换完成后用 `weakref` 或 `gc` 确认解码出来的原始顶层对象已经不可达（如果实现方式不适合用 weakref，就换一种等价的断言方式，并在交接里说明）。
5. 回归：`tests/test_fs_borrow_reference.py`、`tests/test_fs_multi.py`、`tests/test_fs_monitor.py`、`tests/test_fs_astro.py`，以及后端全量（`--basetemp=<worktree 内目录>`）。

## 5 上线验收（Claude，部署需用户批准）

- 和裁剪开关错开上线（先上一个，采 1 小时，再上另一个），这样各自的降幅可以分开看清楚。
- 探针采 1 小时，与 2026-10-26 17:34–18:35 的数据对比：底部预期下降 55–65MB；FS 页面中 Binance 行的状态与上线前一致（不能出现新的 `stale_input` 或 `spot_market_missing`）。
- 回滚：revert 提交。

## 6 派发

- Codex：gpt-6-astra high（FS 链路敏感，要求等价）。worktree `.worktrees/m2-3a-fs-specs`，分支 `codex/m2-3a-fs-specs`，基于 `cfd79d6`。
