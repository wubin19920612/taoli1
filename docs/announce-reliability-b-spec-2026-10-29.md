# 公告抓取可靠性 B：Bybit 本地化预算 + 轮询网络错误日志去重（任务书，Claude，2026-10-29）

基线：`dd981ae`（当前生产版本）。worktree `.worktrees/announce-b`，分支 `codex/announce-reliability-b`。排查报告：主检出目录 `output/traceback-triage-20261029.log`。

## 1 现状
- `announcement_polling.py:83` 用 `asyncio.timeout(localize_timeout_seconds=3.0)` 包住 `provider.localize(rows)`。`announcement_localization.py::_fetch`（约 117 行）给每个中文源的预算是 **4 秒**，比外层还长：慢源会导致外层超时，取消整个 `_refresh`，已经完成的中文结果也一起丢掉，日志里出现 `Announcement localization failed ... exc_info=True`。
- `_refresh` 会用本轮结果**整体替换** `self._cache`，所有源都失败时之前的翻译也被清空。
- `poll_source` 的 `except Exception`（约 99 行）每次失败都打 `logger.warning(..., exc_info=True)`。HL 的 ConnectTimeout 这类网络错误会反复刷出完整 traceback。

## 2 设计
1. `announcement_localization.py`：
   - 新增模块常量 `LOCALIZATION_SOURCE_TIMEOUT_SECONDS = 2.5`，替换 `_fetch` 里写死的 4 秒（connect 也取 min）。
   - `_refresh` 改成**合并**：本轮成功的源覆盖对应的 key，失败的源保留之前缓存里的条目。`_expires_at` 的计算方式不变（本轮全部失败时仍然 30 秒后重试）。
   - `_fetch` 里 "Chinese announcement source unavailable" 的 warning 按 url 加错误类型去重：首次或变化时打 warning，重复时打 debug，恢复时打 info。
2. `announcement_polling.py::poll_source`：
   - 对 `Announcement poll failed` 和 `Announcement localization failed` 两处日志，按 provider 维护一个错误签名（异常类型，HTTP 错误再加上 status code）。首次失败或签名变化时打 warning 并带 exc_info（与现在相同）；签名相同时打 warning 但**不带** exc_info，消息里附带连续失败次数；下次成功时打一条 info `recovered after N failures`，并清除签名。
   - `observation` 的记录、`_record_poll`、轮询间隔和异常处理流程都不变。
   - 签名只保存在 `poll_source` 闭包内（每个 provider 一份），不要用全局变量。
3. 不做：修改各 provider 的抓取逻辑、`announcements.py`、localize 外层的 3 秒、fetch 外层的 20 秒、轮询频率。

## 3 文件与测试
- 修改：`backend/app/services/announcement_localization.py`、`backend/app/services/announcement_polling.py`
- 新建：`backend/tests/test_announce_reliability_b.py`、`docs/task-handoff-2026-10-29-announce-reliability-b.md`
- 禁止修改其他文件；已有测试只能运行，不能修改。
测试要先写（不联网，用 fake client、fake provider，时间可以注入）：
1. 一个中文源超过 2.5 秒、另一个正常：localize 在 3 秒内返回，正常源的翻译生效。
2. 上一轮有缓存，本轮所有源都失败：之前的翻译仍然生效。
3. 同一个 provider 连续 3 次 ConnectTimeout：只有第 1 条带 exc_info，第 2、3 条不带，消息里有次数；之后成功时有 1 条 INFO recovered。
4. 签名变化（ConnectTimeout 变成 HTTP 500）时，再打一条带 exc_info 的 warning。
5. 回归：`tests/test_announcement*.py`，以及后端全量（在 backend 下 `mkdir -p ../.pytest-tmp`，`python -m pytest -q --basetemp=../.pytest-tmp/x`）。

## 4 派发
- Codex：gpt-6.1-sol xhigh。不要跑 WSL，不要联网。上线需另行申请批准。
