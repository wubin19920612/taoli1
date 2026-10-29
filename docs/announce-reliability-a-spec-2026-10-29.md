# 公告抓取可靠性 A：Gate 超时丢结果 + Bitget/OKX 失败诊断（任务书，Claude，2026-10-29）

基线：`dd981ae`（当前生产版本）。worktree `.worktrees/announce-a`，分支 `codex/announce-reliability-a`。排查报告：主检出目录 `output/traceback-triage-20261029.log`。

## 1 现状（`backend/app/services/announcements.py`）
- `poll_source`（announcement_polling.py:72）用 `asyncio.timeout(20)` 包住 `provider.fetch_headlines()`。`GateAnnouncementProvider.fetch`（2051 行）先**串行**请求 5 个分类，general 不可用时再并发请求两个 reader 页（各自的 `httpx.Timeout(15)`）。如果分类请求已经耗掉几秒，reader 很容易超出 20 秒的总时限。外层一取消，**本轮已经解析好的分类结果全部丢失**，日志里还会出现 `TimeoutError`/`CancelledError` 的 traceback。
- reader 每次失败都会打一条带 traceback 的 warning（`_fetch_reader_page`，2126 行）。
- Bitget 网站（约 1940–1975 行）和 OKX（约 1400–1420 行）只有在所有子来源都失败时才抛出汇总 `ValueError`。日志里看不到每个子来源失败的原因，所以无法判断是网络问题、挑战页还是页面结构变了。

## 2 设计（只改 announcements.py）
1. Gate 内部预算：新增模块常量 `GATE_FETCH_BUDGET_SECONDS = 17.0`（比外层 20 秒少，留出余量）。`fetch` 开始时计算截止时间（用已有的 `self._monotonic_fn`）。
   - 每个分类请求前检查剩余时间：剩余不足 1 秒就停止请求后面的分类（打一条 debug），已经拿到的结果保留。
   - reader 请求的超时时间改成 `min(15, 剩余 - 0.5)`；剩余不足 1 秒时不再发起 reader，直接返回已有结果，并打一条 debug。
   - 用 `asyncio.timeout(剩余)` 包住两个 reader 的 gather；超时后**返回已累积的 announcements**，不能向外抛出。服务关闭时的外层取消（`CancelledError`）必须照常向外传播，不能吞掉。
2. reader 失败日志去重：沿用 `_category_failures` 的做法，用 `("reader", category)` 加错误签名作为 key。首次失败或签名变化时打 warning 并带 exc_info；签名相同时打 debug；恢复时打 info。
3. Bitget 网站和 OKX 的诊断：抛出汇总 `ValueError` 之前，确保 `self.last_source_errors` 记录了每个子来源的 `{来源标识: "异常类型: 简短消息(截断到 200 字符)" 或 "HTTP <status>"}`。`ValueError` 的消息里也附上简短摘要（例如 `all OKX sources failed: api_latest=HTTPStatusError:403, web_new=ValueError:no payload`）。只补充诊断信息，**不改变**抓取、解析、合并和抛出的逻辑。
4. 不做：修改 UA 和请求头（禁止任何绕过风控的改动）、换接口、改分类列表、改其他交易所和 `announcement_polling.py`。

## 3 文件与测试
- 修改：`backend/app/services/announcements.py`
- 新建：`backend/tests/test_announce_reliability_a.py`、`docs/task-handoff-2026-10-29-announce-reliability-a.md`
- 禁止修改其他文件；已有测试只能运行，不能修改。
测试要先写（用 httpx.MockTransport 加可注入的时钟，不联网）：
1. 分类请求累计耗时把预算用到只剩不到 1 秒时：不再请求 reader，返回已经解析的分类结果。
2. reader 很慢、超过剩余预算时：fetch 在预算内返回，结果里有分类数据，不抛 TimeoutError。
3. 外层取消（对 fetch 任务调用 cancel）时，CancelledError 照常向外传播。
4. reader 连续 3 次以同样的错误失败：只有 1 条 WARNING；恢复后有 1 条 INFO。
5. OKX 和 Bitget 网站所有子来源都失败时：`last_source_errors` 里每个子来源都有原因，`ValueError` 消息里带摘要；部分成功时行为与旧实现相同。
6. 回归：`tests/test_announcements.py`、`tests/test_announcement_public_sources.py`、`tests/test_gate_announcement_noise.py`，以及后端全量（在 backend 下 `mkdir -p ../.pytest-tmp`，`python -m pytest -q --basetemp=../.pytest-tmp/x`）。

## 4 派发
- Codex：gpt-6-astra high（超时、取消语义和并发）。不要跑 WSL，不要联网。上线需另行申请批准。
