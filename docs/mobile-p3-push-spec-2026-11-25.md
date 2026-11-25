# 手机 P3：锁屏推送（FCM）后端（2026-11-25）

基于 `codex/mobile-p1` @ 462e9b3（= 生产）。设计稿 `D:/吴彬工作/claude/套利/research/mobile-app/design.md` §3.3。

## 范围
可新建：`backend/app/services/fcm.py`、`backend/app/services/mobile_push.py`、对应测试。
可修改：`api/routes_mobile.py`、`models/mobile.py`、`core/security.py`（仅 `_mobile_route`）、`core/config.py`（新增配置）、`db/schema.py`（只新增表）、`db/repositories.py`、`main.py`（注册任务/仓库）、`services/feishu.py`（仅在已有 `_record` 成功后追加推送钩子）。
不可修改：任何下单/建卡逻辑；已有测试断言。

## 1. 配置
- `FCM_SERVICE_ACCOUNT_JSON_B64`：服务账号 JSON 的 base64（放服务器 `.env`）。为空时推送整体关闭（接口仍可用、注册仍保存，但不发送、不报错，日志 info 一次）。
- 解析出 `project_id`、`client_email`、`private_key`；**任何日志/异常信息都不得包含私钥或完整 token**。

## 2. FCM 发送（`services/fcm.py`）
- OAuth：用 `cryptography` 做 RS256 JWT（iss=client_email，scope=`https://www.googleapis.com/auth/firebase.messaging`，aud=`https://oauth2.googleapis.com/token`，exp=1h），换 access token，缓存到过期前 5 分钟。不新增依赖。
- 发送：`POST https://fcm.googleapis.com/v1/projects/{project_id}/messages:send`，httpx 异步，超时 10s。消息：`notification{title, body}` + `data{kind, card_id, card_name}` + `android{priority:"high", notification{channel_id: "urgent"|"normal", tag: "<kind>:<card_id>"}}`。
- 返回 404/`UNREGISTERED`/`INVALID_ARGUMENT`(token) → 删除该设备；其它失败只记日志（脱敏），不重试风暴（最多 1 次重试）。

## 3. 设备注册
- 新表 `mobile_push_devices(token TEXT PK, session_hash TEXT, kinds TEXT(json), threshold_pp REAL, created_at REAL, updated_at REAL, last_error TEXT NULL)`。
- `POST /api/mobile/push/register`：`{token, kinds:{near_open,near_close,fill,leg_alert,big_spread,stale}: bool, threshold_pp: 0.01–5}`，upsert；token 长度 ≤ 4096。
- `DELETE /api/mobile/push/register`：`{token}`。
- `POST /api/mobile/push/test`：给请求体里的 token 发一条普通测试通知（每 token 每分钟最多 1 次），返回是否发送成功（中文原因）。
- `_mobile_route` 放行：GET `/api/mobile/*` 不变；**新增**允许 `POST /api/mobile/push/register`、`DELETE /api/mobile/push/register`、`POST /api/mobile/push/test`（精确匹配，其它 POST 仍 403）。
- 会话注销/过期时：该会话注册的设备在下次扫描时删除（按 session_hash 关联）。

## 4. 事件检测（`services/mobile_push.py`，后台任务每 10 秒）
复用 summary 构建（同一份缓存，不新增交易所请求）。只记上一轮的轻量快照（card_id → gap/qty/state/imbalance）。
| kind | 触发（边沿触发：从不满足 → 满足） | 渠道 |
|---|---|---|
| near_open | 未持仓、state≠paused、open_auto、`-threshold ≤ gap_to_open < 0` | normal |
| near_close | 持仓、close_auto、`-threshold ≤ gap_to_close < 0` | normal |
| fill | 任一腿数量变化（开仓/加仓/减仓/平仓，标题写清方向和数量） | normal |
| leg_alert | 持仓卡 `leg_imbalance` 变为 true，或持仓卡 state 变为 paused/close_disabled | urgent |
| stale | summary `stale=true` 连续 ≥60 秒；恢复时发一条 normal | urgent |
| big_spread | `feishu._record` 记录 kind=big_spread 时同步推送（在后台任务里发，不阻塞飞书） | normal |
- 同一 `card_id+kind` 10 分钟内只推一次；首次启动的第一轮只建快照不推送。
- 按每个设备的 kinds/threshold 过滤。
- 标题/正文全中文，例：`OPENAI 接近清仓` / `清仓价差 3.42%，条件 3.50%，差 0.08pp`；`OPENAI 单腿持仓` / `bitget 0.059 / rh-lighter 0`。
- 任务异常只记日志，不影响主循环；单轮耗时 > 1s 记 warning。

## 5. 测试
JWT 生成（假密钥）、token 缓存、发送成功/UNREGISTERED 删除/其它失败、配置为空时关闭、注册/删除/测试接口与只读会话白名单（含其它 POST 仍 403）、每种事件的边沿触发与 10 分钟去重、首轮不推送、阈值过滤、feishu big_spread 钩子不阻塞发送。

验收：上述测试 + 后端全量（Windows 去 uvloop 锁定依赖、`--basetemp` 指工作树 `.pytest-tmp`）贴汇总行；`git diff --name-only 462e9b3` 只含范围内文件；提交 `feat(mobile): FCM push ...`。不要 push、部署、跑 WSL。
