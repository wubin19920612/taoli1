# S7-A 告警通知状态持久化（2026-10-30）

## 背景（10-30 只读调查 output/s7-survey-20261030.log 第 1 项 + 生产开关核对）
`backend/app/services/alert_engine.py` 的 `AlertEngine` 把冷却（_last_sent）、退避（_retry_after/_retry_attempts）、连续命中（_hits/_observations）都放在内存，启动时新建（main.py:1266 附近）。生产 FEISHU_LIVE_SEND_ENABLED=true，最近几乎每天部署重启，**每次重启后仍在持续命中的告警会在原冷却期内再次发飞书**。
发送顺序（main.py 约 593–620）：飞书 send → record_delivery_result 更新内存 → event_repo.create 落库；落库抛错会让整轮剩余命中中止（外层 except 记 alert loop failed）。

## 目标
1. 重启后冷却与退避保留，不在冷却期内重发。
2. 发送中途崩溃（已开始发送、未记录结果）重启后**不重发**（宁可漏一条，不重复；记为 unknown）。
3. 单条事件落库失败不影响本轮其他命中，并留下错误日志。

## 方案
1. 新表 `alert_delivery_state`（在现有 schema 初始化处按项目惯例加 `CREATE TABLE IF NOT EXISTS`，先读 backend/app/db 下的 schema/迁移方式照做）：主键 `key`（即现有 `f"{rule.id}:{opportunity.id}"`），字段 `last_sent_at`、`retry_after`、`retry_attempts`、`pending_since`（发送开始时间，可空）、`updated_at`。
2. 新增 `backend/app/services/alert_delivery_store.py`：基于共享 db 连接的小仓库，提供 `load_all()`、`mark_pending(key, now)`、`record(key, last_sent_at, retry_after, retry_attempts)`（同时清 pending）、`prune(older_than)`。每次写都立即 commit（与其他 repository 一致）。
3. `AlertEngine`：
   - 构造仍无参（兼容现有测试）；新增 `restore(states, now)`：把 last_sent_at、retry_after、retry_attempts 恢复到内存；**`pending_since` 非空的键视为“结果未知”**：把 last_sent 设为 pending_since（按冷却处理，不重发），并返回这些键供调用方记一条 status="unknown" 的事件（见下）。
   - `_hits`/`_observations`（连续命中计数）**不持久化**，重启后从 0 重新累积（偏向少发，安全方向）。在 docstring 里写明。
   - 新增 `export_state(key)` 或等价方法，供调用方在 record_delivery_result 之后拿到要持久化的值。
4. main.py 告警编排（只改这一段及启动时恢复）：
   - 启动时（创建 AlertEngine 后、告警循环开始前）`load_all()` → `restore()`；对 unknown 键各写一条 AlertEvent(status="unknown", message 说明“上次发送过程中进程退出，结果未知，按已发送处理，不重发”)。先确认 AlertEvent.status 是否有枚举/校验，若有则加入 "unknown" 并确认前端显示不报错（只读前端，若前端按枚举渲染且不认识会报错，在报告里说明，不改前端）。
   - 真正调用 `send_alert` 前 `mark_pending(key, now)`；发送后按现有逻辑 record_delivery_result，再 `store.record(...)` 持久化。muted/failed 也照常持久化退避。
   - `event_repo.create(event)` 和 store 写入各自 try/except：失败 `logger.exception` 后 **continue 下一个命中**，不再让整轮中止。
   - 每天（或每 N 轮）`prune`：删除 `updated_at` 早于 max(规则冷却) + 1 天、且无 pending 的行；简单起见用固定 7 天。
5. 不改：告警判断逻辑、消息模板、Astro 建卡流程、规则模型、飞书 notifier。

## 测试
- 发送成功后重启（新 AlertEngine + restore）→ 冷却期内同一命中不再产生 match；冷却过后可以再发。
- failed/muted 退避在重启后保留。
- mark_pending 后“崩溃”（不调用 record）→ 重启 restore → 该键不重发，并写了一条 unknown 事件；连续命中计数从 0 开始。
- event_repo.create 抛错 → 本轮后面的命中照常处理，store 状态已写入。
- prune 只删过期且无 pending 的行。
- 现有 alert_engine、alert_loop、告警相关测试不放宽。

## 范围
可改：alert_engine.py、main.py（仅告警循环发送/落库段和启动恢复）、新增 alert_delivery_store.py、db schema 初始化文件（只加这张表）、AlertEvent 状态定义（如需加 unknown）、测试、本任务书。commit 信息 `fix: persist alert delivery cooldown across restarts`。

## 测试环境（必须）
CI 用 backend/requirements.lock 的锁定依赖（FastAPI 0.162、Starlette 1.10、pydantic 2.14.1）。全量测试用：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe -m pytest -q -p no:cacheprovider`（在 backend 目录）。日志保留命令和汇总行。不要运行 WSL，不要访问生产或真实飞书/Astro/交易所。
