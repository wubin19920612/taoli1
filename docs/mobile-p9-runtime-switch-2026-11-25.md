# 手机 App P9：手机改卡运行时开关（后端 + taoli1 网页，2026-11-25）

背景：P6 手机改卡已上线，总开关只能改 `.env` 重启。现加一个**存数据库的全局运行时开关**：任何一台手机可以一键停用（不用解锁），重新打开只能在 taoli1 网页（完整登录会话）或服务器上做。

## 规则
- 生效条件改为：`settings.mobile_card_control_enabled is True and settings.astro_dry_run_only is False and 运行时开关为开`。`.env` 仍是上限。
- 运行时开关默认**开**（保证现有行为不变）。读开关出错（库异常等）一律按**关**处理（失败即关闭），并写 warning 日志。
- 所有用到 `control_enabled(...)` 的地方（`routes_mobile.py` 写接口拦截、`mobile_card_control.py` 的 status 和锁内复核）都要用新的判断；锁内复核必须重新读开关，不能用缓存。

## 允许修改
- 后端：`backend/app/db/schema.py`、`backend/app/db/repositories.py`、`backend/app/services/mobile_card_control.py`、`backend/app/api/routes_mobile.py`、`backend/app/models/mobile.py`、`backend/app/core/security.py`（白名单只加 `("POST", "/api/mobile/ops/kill")`）、`backend/app/main.py`（如需）；新路由文件 `backend/app/api/routes_mobile_admin.py` 及在路由注册处挂载；新测试 `backend/tests/test_mobile_runtime_switch.py`。
- 网页：`frontend/src/pages/SettingsPage.tsx`（加一个卡片）、`frontend/src/api/client.ts`（加两个函数）、新组件 `frontend/src/components/MobileCardControlSwitch.tsx` 与其测试 `MobileCardControlSwitch.test.tsx`。**不要往 `SettingsPage.test.tsx` 里加用例**（它在 CI 上已经很慢）。
- 不放宽已有测试。

## 表
`mobile_ops_switch(singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL, changed_at REAL, changed_by TEXT)`；没有行时视为开。

## 接口
1. `POST /api/mobile/ops/kill`（`require_session`；只读会话与完整会话都可用；**不需要解锁**；**即使当前已关或 env 关也返回成功**，不受 P6 的“未启用即 503”拦截）：把开关设为关，`changed_by` 记 `"mobile:<浏览器/平台描述>"`（用现有 `security.py` 里的 UA 描述函数）。若原来是开：写一条 `mobile_alert_events`（kind `card_control`，标题「手机改卡已紧急停用」，正文含来源与时间），立即紧急推送给所有已注册设备，并清空内存里所有会话的解锁窗口。返回 `{enabled: false}`。限频：每会话每分钟 10 次。
2. `GET /api/settings/mobile-card-control` 与 `PUT /api/settings/mobile-card-control` `{enabled: bool}`（路径**不在** `/api/mobile/` 下，只读会话会被现有中间件拒绝；完整会话或 API Token 可用）：GET 返回 `{env_enabled, dry_run, runtime_enabled, effective, changed_at, changed_by}`；PUT 设开关，`changed_by` 记 `"web"`，开→关、关→开都写审计事件并推送（标题「手机改卡已在网页停用/重新打开」）。
3. `GET /api/mobile/ops/status` 增加字段：`runtime_enabled`、`env_enabled`、`changed_at`、`changed_by`；`enabled` 改为最终生效值。

## 网页
设置页加「手机改卡」卡片：显示 .env 是否允许、dry-run、运行时开关、最终是否生效、最后一次由谁何时修改；一个开关（.env 不允许或 dry-run 时禁用并说明原因）；打开前确认框「打开后，已绑定的手机可以修改 Astro 卡片的暂停/禁开/禁清」。组件测试覆盖：展示、禁用原因、确认后调用 PUT、失败提示。

## 测试（先写）
默认开；kill 后 status.enabled=false、所有写接口 503、锁内复核读到关会拒写（模拟在拿锁后被关）；kill 不需要解锁、只读会话可调、重复调用不重复推送；kill 清空解锁窗口；读开关异常按关处理；PUT 只读会话 403、完整会话可开可关并推送；env 关时 PUT 开也不生效（effective=false）。

## 验收
后端：`pytest backend/tests/test_mobile_runtime_switch.py backend/tests/test_mobile_card_control.py -q --basetemp=<scratchpad>` + 全量 backend。前端：`cd frontend && npx vitest run src/components/MobileCardControlSwitch.test.tsx` 和 `npx tsc --noEmit`（若本机 node_modules 存在）。
提交：`git commit -m "feat(mobile): runtime kill switch for mobile card control (P9)"`，不要 push。报告改动文件、测试命令与汇总行、未完成项。
