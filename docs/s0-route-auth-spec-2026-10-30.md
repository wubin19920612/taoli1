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

## 实施记录（2026-10-30）

- 范围：S0 路由鉴权；分支 `codex/s0-route-auth`，基线 `f70d4cb`。按本次指令不运行 WSL、不访问生产服务器、不部署；交接与验证记录保留在本任务书，避免新增范围外文档。
- `security.py` 集中维护精确 HTTP 方法与路径的白名单；其余请求从应用 settings 和请求头取密码，复用 `verify_dashboard_password`，采用 UTF-8 字节的 `hmac.compare_digest`，保留空密码放行语义。
- `main.py` 的 31 个 router 统一注册鉴权依赖，原有路由内部校验不变；启动时空密码仅记录一条 WARNING，不输出密码。
- 白名单均为 GET：`/api/health`、`/api/health/live`、`/api/health/ready` 用于现有健康检查；`/api/features` 供 `AppShell` 在输入密码前初始化菜单，仅返回禁用功能名称。没有独立密码校验接口需要公开。
- `/api/health` 保持现有响应，仍公开行情/机会数量、交易所错误和状态；`/api/health/detail`、账户持仓、观察列表及全部业务接口继续受保护，不因页面初始化会调用而加入白名单。
- 前端代码核实：`client.ts` 从 localStorage 的 `dashboard_password` 自动构造请求头；收到 `401 / Invalid dashboard password` 映射为“本设备未保存有效的面板密码，请验证密码后重试。”，由页面现有错误展示处理，没有全局跳转登录页。
- 已有输入入口：设置页“面板访问”输入密码后点击“应用”；账户连接页输入密码后“保存并刷新”；FS 页面错误提示中“验证并重新读取”；配对预设写入遇到 401 还有密码弹窗。前端不修改、不新增登录页，不运行 vitest。
- 未找到前端使用 EventSource 或 `/api/stream`；SSE 同样受请求头鉴权保护，query 参数密码不能放行，无需修改 `stream.py`。
- 动态枚举结果：178 个 API 路由注册项、147 个不同路径、178 个 HTTP 方法/路径组合，其中 4 个公开、174 个受保护。测试遍历全部注册路由及其方法，不把当前列表硬编码为测试范围；保留 178 的下限断言与实际计数输出。
- 新测试覆盖全部接口缺失/错误密码、代表性接口正确密码及空密码行为、401 先于业务 404/503 和缺失参数 422、SSE query 密码拒绝、Unicode 密码比较、空密码启动警告。
- 首次回归验证：新增权限测试在实现前复现 `/api/account-connections` 未鉴权先返回 503；实现后 `python -m pytest -q -s -p no:cacheprovider tests/test_route_permissions.py` 输出 `24 passed in 13.22s`。
- 全量验证在 Windows 原生 Python 3.12.10、`backend` 目录运行：`python -m pytest -q -p no:cacheprovider`（设置 `PYTHONDONTWRITEBYTECODE=1` 避免写入字节码）。首轮：`31 failed, 2257 passed, 6 deselected, 12 warnings in 163.58s (0:02:43)`；失败均为原测试设置了密码但读取/预览请求未带密码头。
- 仅给 11 个受影响旧测试文件中的 43 次请求补正确密码头：`test_account_positions.py`、`test_api.py`、`test_astro_instrument_routes.py`、`test_astro_preadd.py`、`test_floating_watch.py`、`test_market_identity_routes.py`、`test_multi_exchange_premium.py`、`test_negative_basis_monitor.py`、`test_oil_news.py`、`test_pair_spread_presets.py`、`test_second_level_sampling.py`。AST 对比确认去除请求头差异后与基线完全一致，未放宽或删除任何原有断言，未认证写请求保留。
- 补头后再次运行同一全量命令，退出码 0，最终汇总：`2288 passed, 6 deselected, 12 warnings in 161.39s (0:02:41)`。6 项由现有 pytest 的 `not network and not benchmark` 默认筛选排除；12 条均为已有指数成分测试触发的 `datetime.utcnow()` 弃用警告。
- 最终范围复核：2 个应用文件、1 个新增权限测试、11 个受影响旧测试及本任务书，共 15 个文件；31 个 router 与原注册顺序一致，`git diff --check` 通过。开始时工作区干净，无其他任务未跟踪产物；新增权限测试随本次提交纳入版本控制。
- 线上状态：本任务没有访问生产服务器，未进行备份、部署或线上验收；如需上线，应在独立部署任务中继续，并执行现有部署检查与备份流程。
