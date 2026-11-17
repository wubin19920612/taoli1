# taoli1 实盘页：登记外部负债额度（2026-11-17）

## 背景
crossex-exec 新增（另一分支在做，接口如下）：
- `GET {CROSSEX_EXEC_URL}/api/v1/external-liabilities` → `{revision, items:[{exchange_type, coin, cap, reason, registered_at, liability|null, within_cap|null}]}`
- `POST /api/v1/external-liabilities/register` body `{exchange_type, coin, cap, reason, expected_revision}`（cap 为原生币数量字符串，0<cap≤1000）
- `POST /api/v1/external-liabilities/unregister` body `{exchange_type, coin, expected_revision}`
- admin token；成功 200，revision 不符 409，参数错 400，非 admin 403。
语义：登记后，该交易所/币种的账户负债 ≤ 额度时不再触发 account_liability 停开锁；超额仍停开。负债由用户自己负责。

## 要做
1. 后端 backend/app/api/routes_live_exec.py：照抄现有 `external-positions` 的只读代理与 `/external-positions/{action}` 的管理写代理，新增 `GET /external-liabilities` 和 `POST /external-liabilities/{action}`（action ∈ register|unregister，require_admin_session），body 字段白名单校验，409 透传，日志脱敏。
2. 前端（frontend/src/api/liveExec.ts、frontend/src/pages/liveExec/ 下新组件，挂在实盘页“外部持仓”区块附近，沿用 LiveExternalPositions / useLiveExecAdmin 的模式）：
   - 区块标题「外部负债额度」。表格列：交易所、币种、当前负债、登记额度、状态（额度内 / 超额度 / 未知）、备注、登记时间、操作（注销）。
   - 「登记额度」表单：交易所（下拉，默认带出当前快照中出现负债的交易所，如 HYPERLIQUID）、币种（默认 USDC）、额度（数字，>0 且 ≤1000）、备注（必填）。提交前确认弹窗写明：“登记后，该负债在额度内不会再阻止程序开仓；负债由你自行负责，超出额度程序仍会停开。”
   - 注销也要确认弹窗。
   - 请求带 expected_revision；409 → 提示“已被修改，已刷新”并重新拉取；请求中禁用按钮；无管理员会话禁用写操作（沿用现有处理，不得触发全局登录跳转）。
   - 文案全部中文，风格与现有实盘页一致。
3. 不改与此无关的组件行为。

## 验收
- 后端测试（照 test_live_exec_admin.py / test_live_exec_routes.py）：读代理、写代理 admin 会话要求、action 白名单、body 校验、409 透传、上游错误映射。
- 前端测试（vitest）：列表渲染（额度内/超额度/未知）、登记表单校验与确认、注销确认、409 刷新、请求中禁用、无管理员会话禁用。
- 跑：`cd frontend && npx vitest run --no-cache` 与 `npx tsc --noEmit`；`cd backend && PYTHONPATH=. python -m pytest tests/test_live_exec_admin.py tests/test_live_exec_routes.py -q -p no:cacheprovider`（锁定依赖的全量后端测试由 Claude 另跑）。
- 只改 backend/app/api/routes_live_exec.py、frontend/src/api/liveExec.ts、frontend/src/pages/liveExec/**、frontend/src/pages/LiveExecPage.tsx 及对应测试；不要 npm install；不要 git commit。
