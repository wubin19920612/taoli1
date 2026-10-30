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

## 实现与验收记录（2026-10-30）

- 分支：`codex/s7a-alert-delivery-state`；实现前基线：`a167e10`；初始工作区干净。
- 仅改动本任务书，以及 `backend/app/db/schema.py`、`backend/app/main.py`、`backend/app/services/alert_engine.py`、新增的 `backend/app/services/alert_delivery_store.py`，和测试 `test_alert_delivery_state.py`（新增）、`test_alert_loop.py`、`test_api.py`、`test_task_supervisor.py`。既有测试保留原断言，只补充新增仓库依赖；前端、规则模型、消息模板、notifier 和 Astro 流程未修改。

### 持久化与恢复

```sql
CREATE TABLE IF NOT EXISTS alert_delivery_state (
  key TEXT PRIMARY KEY,
  last_sent_at TEXT,
  retry_after TEXT,
  retry_attempts INTEGER NOT NULL DEFAULT 0,
  pending_since TEXT,
  updated_at TEXT NOT NULL
);
```

时间由仓库写为 UTC ISO 8601 文本。`mark_pending`、`record`、`prune` 各自立即提交；`record` 清除 pending。每天清理无 pending 且超过保留期的行，保留期为 7 天与最大规则冷却加 1 天的较大值，避免长冷却规则被提前清理。

`main._restore_alert_delivery_state` 在启动告警循环前读取状态。pending 以发送开始时间恢复 `last_sent`，按已发送处理，在剩余冷却期内不重发；冷却结束后仍可正常触发。连续命中与观察样本不持久化，从零累计。加载失败向上传播，避免以空冷却启动。

每个 pending 写入一条 `AlertEvent(status="unknown")`，包含“上次发送过程中进程退出，结果未知，按已发送处理，不重发”。以 key 和 pending 时间生成稳定事件 ID，防止写事件后、清 pending 前再次崩溃导致重复事件。事件写入失败保留 pending 并记录异常；其他键继续恢复。事件成功后再固化冷却并清 pending。

`AlertEvent.status` 和前端 `AlertEvent.status` 都是字符串，无需扩展枚举。只读核查 `frontend/src/pages/AlertHistoryPage.tsx`：unknown 可显示为红色 `unknown` 标签，消息按原文显示。恢复消息使用已有 `【需评估】` 前缀，防止历史接口按行情重建而覆盖恢复说明；已用 `/api/alerts/events` 离线接口测试验证。状态表不存 symbol，恢复事件标的列显示“未知”，完整告警键与原始 opportunity ID 保留，不推断市场身份。未进行浏览器运行验收。

发送前 pending 写入失败则跳过该条发送；发送结果先更新内存，再持久化冷却/退避。状态写入或事件写入失败各自记异常并继续下一条命中。发送后状态保存失败会保留 pending，重启时按 unknown 恢复。

### 验证与后续边界

- 指定虚拟环境版本实测为 FastAPI `0.162.0`、Starlette `1.10.0`、pydantic `2.14.1`。运行和测试锁文件的 46 项依赖核查中，45 项匹配；Windows 环境未安装 `uvloop==0.22.1`，未更改指定环境或依赖文件。
- 定向测试初轮：`83 passed in 11.47s`；补充接口原文与清理周期断言后，新增测试文件：`17 passed in 8.73s`。
- 新仓库、引擎和新增测试的 Ruff 检查通过；其他改动文件的 Ruff 告警与基线一致（main.py 6 项、test_api.py 4 项，无新增），未扩范围修复；`git diff --check` 通过。
- 最终全量测试工作目录：`backend`。命令：

```powershell
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

最终全量汇总：`2369 passed, 6 deselected, 13 warnings in 124.97s (0:02:04)`，退出码 0。6 项按项目默认配置排除（network/benchmark）；13 项为 TestClient/httpx 与 datetime.utcnow 的弃用提示。原始日志保存在 `%TEMP%\s7a-alert-delivery-state-full-tests-final-20261030.log`（含命令）。

本任务未访问生产、未备份或部署线上、未调用真实飞书/Astro/交易所、未运行 WSL。后续部署验收属于新任务，本地验证不代表生产验收。
