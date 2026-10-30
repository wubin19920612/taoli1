# S7-B 通用建卡：共享锁 + 尝试账本（2026-10-30）

## 背景（output/s7-survey-20261030.log 第 3/4/5/6 项 + 生产开关）
生产：ASTRO_DRY_RUN_ONLY=false（真实写 Astro），告警自动建卡开、手动建卡开，open_enabled=false（都建暂停卡），live-pilot 关，preadd 关，FS 开（FS 走 fs_astro.py，已有 attempt 保护，本任务不动）。
`AstroAlertService`（backend/app/services/astro_alerts.py，main.py 里只有一个实例）的 `_handle` 被告警、live-pilot、手动 API、preadd 共用：
- **没有共享锁**：`list_pairs`（约 422/435 行）→ 计算 missing_variants → fresh 复核 → `add_pair`（约 556/558 行）之间无互斥，告警与手动/双击并发时可各自 add 一次，产生重复卡。
- **没有尝试记录**：`add_pair` 超时/网络错误被 SDK 包成 AstroClientError，记 failed/add 或 add_partial（约 563–590 行），但远端可能已建；下次靠 list 判断，列表滞后或并发时会再 add。
- **审计不全**：只有 alert_events 文本，compact 模板只剩“卡片：已创建”摘要。

参考实现：`fs_astro.py:64-133` + `fs_repository.py:20,89-110`（实例锁覆盖查询到写入；先提交 pending 再 add；pending 且远端未见 → uncertain 不重发；远端已见 → reconciled）。

## 方案
### 1. 共享锁（astro_alerts.py）
- `AstroAlertService` 内按 `pair_name` 加 asyncio.Lock（dict 懒创建；名字集合有限，不需要回收，但写明理由）。从 `list_pairs` 开始到最后一个 variant 的 add（含 add 后的 restart 等待）都在锁内。四个入口都经过 `_handle`，所以都受保护；不同名字互不阻塞。
- 锁内的 list 就是“等待锁之后的重新 list”，保证后到者能看到先到者建的卡。

### 2. 尝试账本（新增 backend/app/services/astro_card_attempts.py + schema）
- 表 `astro_card_attempts`：`route_key TEXT PRIMARY KEY`（由 `_same_route` 判等所用的字段生成的规范化字符串，必须包含 name、type、buyEx、sellEx、以及 HL DEX/具体市场等区分字段——先读 `_same_route`/`_route` 确定，**不同 HL DEX 不能合并**），`pair_name`，`source`（alert/live_pilot/manual/preadd），`state`（pending/created/failed/unknown/reconciled），`open_enabled`（bool），`request_json`（planned dict；先确认不含密钥），`result_message`，`created_at`，`updated_at`。按项目现有 schema 初始化方式建表。
- 仓库方法：get(route_key)、mark_pending(...)、mark(route_key, state, message)；每次写立即 commit（与现有仓库一致）。
- 保留：created/failed/reconciled 超过 30 天的行在合适时机清理（可放在 mark_pending 时顺带、按天节流）；pending/unknown 不自动删。

### 3. 每个 variant 的流程（锁内）
1. 远端列表里已有该路线：若账本是 pending/unknown → 记 reconciled；按现有逻辑当作 existing。
2. 远端没有，但账本是 pending 或 unknown：
   - 自动入口（alert/live_pilot/preadd）：**不 add**，返回 status="skipped"、action="uncertain"，消息说明“上次提交结果未知（时间），远端暂未看到，未重复创建；请在 Astro 核实”。超过 24 小时的 unknown 视为过期，可以再建（写进消息和代码注释）。
   - 手动入口：允许继续创建，但在 manual_warnings 里加一条“上次提交结果未知，可能产生重复卡”。
3. 现有 fresh 复核通过后：mark_pending（已 commit）→ add_pair：
   - 成功 → created。
   - 明确被 Astro 拒绝（HTTP 4xx 且有响应体，先读 astro_client.py 的 AstroClientError 能否区分；不能区分就加一个最小字段，如 status_code）→ failed。
   - 超时、网络错误、5xx、无法判断、或被取消（CancelledError：在 finally/except BaseException 里尽量标 unknown 后重新抛出）→ unknown；返回给调用方的结果 action 用 "uncertain"（不再是 failed/add），消息说明结果未知。
   - mark_pending 本身写失败 → 不 add，返回 failed（宁可不建也不在无记录的情况下写远端）。
4. 现有部分成功（add_partial）语义保留，账本逐 variant 记录。

### 4. 审计查询（routes_astro.py）
- 新增只读 `GET /api/astro/card-attempts?limit=100&state=`：按 updated_at 倒序返回账本（受 S0 全局密码保护，不用另加）。不改前端。

## 不改
告警判断、消息模板、AstroAlertService 的卡片内容/参数计算、fs_astro.py、astro_preadd 的调度、S3-A 的 list_pairs_cached（建卡路径继续用实时 list_pairs）。

## 测试
- 同名路线并发 20 次（告警+手动+preadd 混合）→ 每个 variant 只 add 一次。
- 不同名字并发互不阻塞（用可控的 fake client 证明并行）。
- 不同 HL DEX 的同名路线 route_key 不同，不互相阻挡。
- add 超时 → 账本 unknown、结果 action="uncertain"；下一次自动触发且远端未见 → 不 add；远端已见 → reconciled 且不 add；手动触发 → 允许并带警告；unknown 超过 24h → 自动入口可再建。
- 明确 4xx 拒绝 → failed，下一次可正常重试。
- mark_pending 写失败 → 不调用 add。
- add 期间被取消 → 账本 unknown，异常继续传播。
- /api/astro/card-attempts 无密码 401、有密码返回记录。
- 现有 astro_alerts、astro 路由、preadd、live-pilot、手动建卡测试不放宽。

## 范围
可改：astro_alerts.py、新增 astro_card_attempts.py、routes_astro.py、schema 初始化处（只加这张表）、astro_client.py（仅当需要给 AstroClientError 加状态码字段时）、main.py（仅把仓库注入 AstroAlertService）、测试、本任务书。commit 信息 `fix: serialize astro card creation and record attempts`。

## 测试环境（必须）
CI 用 backend/requirements.lock 锁定依赖（FastAPI 0.162、Starlette 1.10、pydantic 2.14.1）。全量测试用：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe -m pytest -q -p no:cacheprovider`（在 backend 目录）。日志保留命令和汇总行。不要运行 WSL，不要访问生产或真实 Astro/飞书/交易所。**不要 git push**（只在本地分支提交）。
