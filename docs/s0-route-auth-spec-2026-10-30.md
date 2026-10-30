# S0 路由权限：默认需要密码（2026-10-30）

## 背景（10-30 只读核实）
生产前端 3000 端口对公网开放，会把 /api 代理到后端。`DASHBOARD_PASSWORD` 已经设置，但鉴权是各路由自己调用 `verify_dashboard_password`（全仓 67 处），漏掉的路由不需要密码就能访问。已核实：从公网访问 `/api/account-positions` 不带密码返回 200（含 Gate 账户信息），`/api/index-components/watchlist` 返回 200（也有 POST/DELETE 写接口）。完整漏网清单见 docs/architecture-review-2026-10-23-claude.md §4 P0-1，但以代码枚举为准。
前端 `frontend/src/api/client.ts` 的 `authHeaders()` 已经给所有请求带 `X-Dashboard-Password`（含 GET），所以后端改成默认拒绝，前端不用改请求逻辑。

## 方案：默认拒绝 + 公开白名单
1. 在 `backend/app/core/security.py` 新增全局依赖 `require_dashboard_password(request)`：从 `request.app.state.settings.dashboard_password` 取期望值、从请求头取密码，复用 `verify_dashboard_password`（密码为空时放行的现有语义不变）。用 `hmac.compare_digest` 做比较。
2. `main.py` 里所有 `app.include_router(..., prefix="/api")` 统一加 `dependencies=[Depends(require_dashboard_password)]`，**公开白名单**除外。白名单只放：`/api/health`、`/api/health/live`、`/api/health/ready`（compose 健康检查和部署脚本用），以及前端在输入密码前必须调用的接口（先读前端确认，例如 `/api/features`、密码校验接口；若某个接口返回敏感数据就不能放进白名单，写进报告）。白名单集中定义成一个常量，带注释。实现方式自选（拆 router、或依赖内按 path 判断），要求白名单一处维护。
3. 各路由里原有的 `verify_dashboard_password` 调用保留不动（重复校验无害，改动面最小）。
4. SSE `/api/stream`：同样需要密码（EventSource 不能带头，前端也没用它；如果前端确实没用，403/401 即可，不要为它加 query 参数密码）。
5. 启动时如果 `DASHBOARD_PASSWORD` 为空，打一条 WARNING 说明全部 API 未受保护。
6. 前端：401 时的现有表现先读清楚（是否有输入密码的入口）。若已有入口，不改；若没有，在报告里说明，**不要**自行加登录页。

## 测试（新增 backend/tests/test_route_permissions.py）
- 遍历 `app.routes` 中所有 `/api` 路由（包含所有 HTTP 方法），设置密码后：不带头请求 → 非白名单全部 401（路由需要的 path 参数用占位值；校验应在业务逻辑和 422 之前发生，确认依赖顺序让 401 先于 422/404/503）；白名单不是 401。
- 带正确密码头访问若干代表路由 → 不是 401。错误密码 → 401。
- 密码为空时行为与现在一致（不 401）。
- 测试里打印/断言枚举到的路由总数，防止以后新增路由漏网（新增路由自动被覆盖，因为是默认拒绝）。
- 现有测试全部通过，不放宽已有断言；若现有测试因为新增鉴权而失败，给测试客户端加正确密码头或保持测试 settings 密码为空，不要改业务语义。

## 范围与验收
- 可改：backend/app/core/security.py、backend/app/main.py、必要时 backend/app/api/stream.py、新增测试、受影响的现有测试、本任务书。不改前端（除非只是测试）。
- `cd backend && python -m pytest -q -p no:cacheprovider` 全量通过，日志保留命令和汇总行；前端不改则不用跑 vitest。
- 报告：白名单清单及理由、枚举到的路由数、前端 401 的现有表现。
- commit 到当前分支 codex/s0-route-auth，信息 `fix: require dashboard password on all api routes by default`。
