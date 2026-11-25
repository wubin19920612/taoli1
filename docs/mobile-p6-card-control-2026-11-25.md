# 手机 App P6（后端）：改卡片三个开关（2026-11-25）

设计稿（已拍板，必读）：`D:/吴彬工作/claude/套利/research/mobile-app/card-control-design.md`。本任务书是它的实现约定，冲突以本任务书为准。**涉及真钱卡片和鉴权，宁可拒绝也不能误写。**

## 允许修改
- `backend/app/core/config.py`（新开关 `mobile_card_control_enabled`，env `MOBILE_CARD_CONTROL_ENABLED`，默认 False）
- `backend/app/core/security.py`：`_mobile_route` 白名单只追加下文列出的 POST 路径（精确匹配）
- `backend/app/db/schema.py`、`backend/app/db/repositories.py`、`backend/app/db/auth.py`（只为复用验证码防重放/失败计数而加公开方法，不改原有登录行为）
- `backend/app/main.py`（挂 state）
- `backend/app/api/routes_mobile.py`、`backend/app/models/mobile.py`
- 新文件 `backend/app/services/mobile_card_control.py`、`backend/tests/test_mobile_card_control.py`
- `backend/app/services/mobile_push.py`：只允许加一个“立即推送给所有已注册设备（紧急渠道）”的方法
- 依赖：用已有的 `cryptography` 验签（先确认 requirements 里有；没有就停下报告，不要自行加依赖）
- 不得放宽已有测试。

## 开关与前置条件
`mobile_card_control_enabled` 为 False，或 `astro_dry_run_only` 为 True 时：所有写接口返回 503「手机改卡未启用」，不碰 Astro。`GET /api/mobile/ops/status` 照常返回 `enabled=false`。

## 接口（全部 `Depends(require_session)`；POST 都加进白名单）
1. `GET /api/mobile/ops/status` → `{enabled, unlocked_until: float|null, unlock_minutes: int, devices: [{device_id, label, created_at}]}`（只列未吊销的设备）。
2. `POST /api/mobile/ops/devices` `{code, public_key, label}`：验证码绑定本机。`public_key` = base64 的 DER SubjectPublicKeyInfo，必须是 EC P-256，否则 422。`label` ≤40 字。未吊销设备最多 3 台，超出 409。返回 `{device_id}`（服务器生成 22 位 URL 安全随机串）。
3. `POST /api/mobile/ops/devices/revoke` `{device_id}`：必须在解锁窗口内；设 `revoked_at`。
4. `POST /api/mobile/ops/challenge` `{device_id}`：设备须存在且未吊销。返回 `{challenge_id, nonce, expires_at}`；nonce 为 32 字节随机数的 base64；60 秒有效、只能用一次；与当前会话绑定（存会话 hash）。内存保存即可，最多保留 100 个，超出丢最旧的。
5. `POST /api/mobile/ops/unlock`，body 二选一：
   - `{code}`：验证码，必须与登录**共用**同一个防重放计数（`auth_totp_state.last_counter`）和失败计数（`auth_failures`，按 IP，15 分钟 5 次即锁定）。
   - `{device_id, challenge_id, signature}`：signature = base64 DER ECDSA-SHA256，签名内容为 UTF-8 字节 `"astro-mobile-unlock\n" + challenge_id + "\n" + nonce`。challenge 须属于当前会话、未过期、未用过、device_id 一致；无论成败 challenge 都作废。验签失败也计入失败计数。
   - 成功：当前会话解锁窗口 = now + unlock_minutes×60（内存，按会话 hash）。返回 `{unlocked_until}`。
6. `GET /api/mobile/ops/settings` → `{unlock_minutes}`；`POST /api/mobile/ops/settings` `{unlock_minutes: int 1..30}`，必须在解锁窗口内。默认 5，存数据库单行表。
7. `POST /api/mobile/cards/control` `{card_id, field: "status"|"disable_open"|"disable_close", value: bool, expected: bool}`：
   - card_id 校验同 P5（`^[A-Za-z0-9_-]{1,64}$`）。
   - 免解锁只有一种：`field=="disable_open" and expected is False and value is True`。其余都须在解锁窗口内，否则 403「请先解锁」。
   - 流程：`astro_alert_service.pair_lock(<卡片名>)` → `astro_client.list_pairs()`（不用缓存）→ 按 `id` 找卡（找不到 404）→ 当前字段值 != expected 则 409「卡片已变化，请刷新」→ 已等于 value 则直接成功（不写）→ 以最新卡片为底 `dict(pair, **{astro字段: value})` 只改这一个字段 → `update_pair` → 审计。字段映射：status→`status`，disable_open→`disableOpen`，disable_close→`disableClose`。
   - 审计：`astro_card_update` 日志（卡名、字段、旧→新、免解锁/解锁方式）；写一条 `mobile_alert_events`（kind `card_control`，中文标题如「手机修改：OPENAI 禁止开仓 关→开」）；立即推送紧急渠道给所有已注册设备。推送失败不影响返回成功。
   - 限频：每个会话每分钟最多 20 次（内存），超出 429。
   - 返回 `{ok: true, card_id, field, value, changed: bool}`。
   - Astro 报错：用其状态码与消息返回（参考 `routes_astro.update_astro_instrument_card` 的处理）。

## 表
- `mobile_ops_devices(device_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, label TEXT NOT NULL, created_at REAL NOT NULL, revoked_at REAL)`
- `mobile_ops_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1), unlock_minutes INTEGER NOT NULL)`

## 错误信息
全部中文，422 不回显输入（参照 P5 的 `_MobileInputRoute`）。

## 测试（先写）必须覆盖
开关关/dry-run 时 503 且不调 update_pair；验证码绑定成功/错码/重放/锁定；超过 3 台 409；非 P-256 公钥 422；challenge 跨会话、过期、重用、设备不符都失败；正确签名解锁成功（测试里用 cryptography 生成密钥对）；免解锁只放行 disable_open false→true（其余 5 种组合未解锁均 403）；expected 不符 409 且不写；已是目标值不写；只改一个字段（断言 update_pair 收到的其余字段与最新读到的完全一致）；用 list_pairs 而非缓存；审计事件与推送被调用、推送异常不影响成功；限频 429；settings 范围校验与需解锁；只读会话能访问上述 POST，但仍不能访问其他非白名单路径。

## 验收
- `pytest backend/tests/test_mobile_card_control.py backend/tests/test_mobile_pins.py backend/tests/test_api.py -q --basetemp=<scratchpad>`，再跑全量 backend。
- `git commit -m "feat(mobile): card control switches with device unlock (P6)"`，不要 push。报告改动文件、测试命令与汇总行、未完成项。
