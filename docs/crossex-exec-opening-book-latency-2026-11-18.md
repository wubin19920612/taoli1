# crossex-exec：开仓盘口拉取提速（2026-11-18）

## 实测
- 服务器直连各所 depth 接口 TTFB ≈ 0.09–0.19s（Binance ≈ 0.1s），网络不是瓶颈。
- 实盘诊断（9d7523f 的 book_timing）：
  - 19:02 book_skew：fetch_ms=1055，OKX 腿约 80ms 返回，Binance 腿约 1s 后返回；
  - 19:46 book_stale：fetch_ms=940，Bybit 腿很快，Binance 腿约 0.9s；
  - 19:22 book_stale：fetch_ms=93，但 checkpoint=after_open_interest，review_ms=1199 —— 拉完盘口后串行查两腿 OI 用了约 1.1s，盘口过期。
- 推断：公共请求共用 `tools/fill_sim/scheduler.py` 的 `RequestQueue`（最多 4 个 worker，`pump` 每次最多补到 4 个），基准采样（priority 2，全量 tickers）等请求占满 worker 或 venue 配额时，开仓盘口（`MarketData._books`，priority 0）要排队。`open_interests()` 每腿单独 `batch`，且在拉盘口之后。

## 要做
1. **先确认推断**：读 `tools/crossex_exec/market.py`（`batch`、`_books`、`open_interests`、`baseline_quotes`、pump 循环）和 `tools/fill_sim/scheduler.py`（`RequestQueue`、limiter），在报告里说明开仓盘口实际可能等待的原因（worker 占满 / venue 配额 / 优先级），给出依据（代码行号）。
2. **开仓盘口不排队**：开仓盘口请求（两腿）必须能立即发出，不被低优先级请求占住的 worker 或排队拖延。实现方式自选但要最小改动，例如：为 priority 0 预留独立的发送并发（两腿同时发），或在有 priority 0 待发时允许临时超出 4 个 worker 上限（上限 +2）。**不得绕过 venue 限频（limiter.available / admit_send）**；若被限频挡住，照旧等待或失败，并在诊断里体现。
3. **持仓量（OI）挪到拉盘口之前**：在 `Engine.opening_review` 中，每次尝试先把两腿 OI 的原始数据**在一个 batch 里并发**拉回（不需要盘口），然后再拉盘口；盘口到手后只做本地计算（用盘口中间价把 OI 换算成名义值，逻辑与现有 `parse_oi` 一致），拉盘口之后到 `limits.books` 检查之间**不再有任何网络请求**。重试（BOOK_RETRIES）时可复用本轮已拉的 OI 原始数据（同一次复核内）。OI 拉取失败的语义不变（`oi_missing` 等）。
4. **诊断**：在 `book_timing` 里每腿增加 `queue_wait_ms`（提交到实际发出）和 `wire_ms`（发出到返回）；没有就记 null。
5. 不改：限额、下单、发送前屏障、transport.py、各门槛数值、`limits.books` 的判定逻辑。

## 验收
- 测试：
  - 低优先级请求占满 4 个 worker 时，开仓两腿盘口仍在同一轮立即发出（两腿 sent 时间差很小），且仍调用 limiter（不绕过配额）。
  - venue 配额用尽时开仓盘口不会被强行发出。
  - opening_review 中 OI 请求发生在盘口请求之前；盘口获取到最后一次 `limits.books` 之间没有网络调用；OI 失败仍按原原因拒绝。
  - book_timing 含 queue_wait_ms / wire_ms。
- 全量：PYTHONPATH 设为 tools/crossex_exec/tests，`python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider` 全绿；若改了 tools/fill_sim，同时跑 `python -m pytest tools/fill_sim -q -p no:cacheprovider`（若该目录有测试）。不得放宽已有断言。
- 只改 tools/crossex_exec/market.py、engine.py、tools/fill_sim/scheduler.py（仅当必要）及对应测试；不要 git commit；不要联网、不要对真实交易所发请求。
- 报告：确认的瓶颈原因（带行号）、改动文件与行号、测试汇总行。
