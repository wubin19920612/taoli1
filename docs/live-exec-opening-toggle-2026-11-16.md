# taoli1 实盘页：暂停/恢复开仓按钮（2026-11-16）

## 背景
crossex-exec 新增管理写接口（另一个分支在做）：
- `POST {CROSSEX_EXEC_URL}/api/v1/opening/pause`、`/api/v1/opening/resume`
- 请求头 `X-Api-Token: <CROSSEX_EXEC_ADMIN_TOKEN>`，`Content-Type: application/json`，body `{"expected_paused": <bool>}`
- 成功 200；状态不符 409（body 含当前 `paused`）；非 admin 403
- `GET /api/v1/summary` 新增 `opening_paused`(bool) 与 `mode`("live"/"shadow")；已有 `stop_file` 字段等价于 opening_paused。

语义：按钮只控制是否开新仓，已有持仓照常管理平仓；live/shadow 由服务器 env 决定，页面不能改。

## 要做
1. 后端 backend/app/api/routes_live_exec.py：新增 `POST /opening/{action}`（action ∈ pause|resume，其它 404/422），`dependencies=[Depends(require_admin_session)]`，完全照抄现有 `/external-positions/{action}` 的代理写法（admin token、超时、错误映射、409 透传、日志脱敏）。body 校验 `expected_paused` 为 bool。
2. 前端 frontend/src/api/liveExec.ts 增加调用；实盘页（LiveExecPage / liveExec 组件，按现有管理写操作的 hook 模式 useLiveExecAdmin）顶部状态区加：
   - 状态徽标：「开仓：运行中」/「开仓：已暂停」，旁边显示模式「实盘」/「影子」。
   - 按钮：运行中显示「暂停开仓」，已暂停显示「恢复开仓」。
   - 两个操作都要确认弹窗；「恢复开仓」在 mode=live 时弹窗文字写明“恢复后程序会用真实资金自动开仓”，需要再点一次确认按钮。
   - 发请求带 expected_paused=当前显示状态；409 时提示“状态已变化，已刷新”并刷新 summary；其它错误显示中文错误。请求中按钮禁用，防重复点击。
   - 没有管理员会话时按钮不可用（沿用现有管理功能的处理方式）。
   - summary 缺 `opening_paused` 时回退用 `stop_file`。
3. 文案全部中文，风格与现有实盘页一致。

## 验收
- 后端测试（照现有 test_live_exec_admin.py 写法）：admin 会话要求、action 白名单、body 校验、代理到正确 URL 和 token、409 透传、上游错误映射。
- 前端测试（vitest，照 LiveExecAdmin.test.tsx）：两种状态按钮文案、确认弹窗、实盘恢复提示、409 刷新、请求中禁用、无管理员会话禁用。
- 跑：`cd frontend && npx vitest run` 与 `npx tsc --noEmit`；后端 `cd backend && PYTHONPATH=. python -m pytest tests/test_live_exec_admin.py tests/test_live_exec_routes.py -q -p no:cacheprovider`（锁定依赖的全量后端测试由 Claude 另跑）。
- 只改 backend/app/api/routes_live_exec.py、frontend/src/api/liveExec.ts、frontend/src/pages/liveExec/**、frontend/src/pages/LiveExecPage.tsx 及对应测试；不要提交 git commit。
