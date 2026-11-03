# 登录 2FA 实现报告（2026-11-03）

范围：按 `docs/login-2fa-spec-2026-11-03.md` 实现 A、B、C.1。分支 `codex/login-2fa`，起点 `81585e4`。没有启动真实服务、访问外网、运行 WSL、推送 Git 或执行 C.2–C.5。线上仍由 PM/Claude 在后续任务验收。

## 本地提交

- `4021791` — `feat: add TOTP login, persistent cookie sessions and API token authentication`
- `380c426` — `feat: use same-origin session login and add device management`
- `dd51f33` — `feat: redact authentication settings and verify both login factors`
- `72e1799` — `test: cover TOTP sessions and migrate route and frontend auth regressions`
- 文档为随后 `docs:` 提交；所有提交仅在本地。

## 后端实现

- `app/core/totp.py`：标准库 base32、HMAC-SHA1、动态截断；登录使用 6 位、30 秒和前后各一窗。保存最近成功计数，拒绝相同及更早计数；支持 RFC 6238 的 8 位向量测试。不新增依赖。
- `app/db/auth.py`：`auth_sessions` 仅存 32 字节随机令牌的 SHA-256 哈希，记录创建/到期/最近使用/IP/浏览器/撤销时间。Cookie 原文只通过 `Set-Cookie` 交给浏览器，不作为 JSON 返回，不写数据库和通知。
- 附加 `auth_totp_state`、`auth_failures`、`auth_locks` 三张表。验证码消费、锁定判断和创建会话在独立连接的 `BEGIN IMMEDIATE` 事务中完成，支持多进程并发及重启后保留状态。不会回滚其他模块未提交的数据。
- 密码和 API Token 使用 `hmac.compare_digest` 比较字节；验证码也用常量时间比较。合法形状的验证码检查不依赖密码正确与否，减少因子检查路径差异。
- IP 失败 5 次 / 全局失败 20 次均按 15 分钟滚动窗口计数并锁 15 分钟。已经锁定的请求不会延长锁期，也不会重复发送锁定通知。错误、非法 JSON、错误字段类型、验证码重放和锁定都返回 401 与“密码或验证码错误”。存储故障返回脱敏的 503。
- Cookie 始终为 `taoli1_session; HttpOnly; Secure; SameSite=Strict; Path=/`。默认 30 天（2592000 秒）；无滑动续期。
- `core/security.py` 保留统一路由依赖的旧导入名 `require_dashboard_password`，含义已改为会话/API Token 校验。删除 `verify_dashboard_password`、`dashboard_password_header` 和所有路由内旧密码头检查。
- `ENVIRONMENT=production` 缺密码、缺 TOTP 或 TOTP 无效时，全部受保护路由均拒绝，即使带有效 Cookie、API Token 或开启开发绕过标志。
- 默认所有环境均保护。只有 `development/local/test` 且显式 `DASHBOARD_AUTH_DISABLED=true` 才允许业务路由绕过；会话管理仍要求真实 Cookie。
- `X-Api-Token` 独立于浏览器登录；配置必须为 64 字符且不同于网页密码，空值/短值关闭该路径。不会由前端发送或保存。
- IP 只在连接来源匹配 `TRUSTED_PROXY_IPS` 时采用 XFF 最后一项，并校验为合法 IP；不匹配/非法头回退连接地址。默认 `127.0.0.1,172.16.0.0/12`。`backend/Dockerfile` 增加 `--no-proxy-headers`，防止 Uvicorn 提前改写连接来源。
- 登录成功及新锁定调用既有 `FeishuNotifier.send_text`；消息只有事件、规范化 IP、浏览器家族/平台和 UTC 时间。UA 原文不保存、不通知，防止任意头内容夹带凭据。通知失败仅记录固定警告，不打印异常详情，不撤销成功登录。
- `python -m app.cli.totp_setup` 仅向终端打印新密钥及 otpauth URI，不写文件/日志。测试捕获输出，未实际执行服务器设置。
- Settings 的 `repr` 统一脱敏，避免调试/异常表示暴露配置。

## 路由和前端调用变化

新增接口：

| 方法 | 路径 | 权限/行为 |
| --- | --- | --- |
| POST | `/api/auth/login` | 公开；密码 + TOTP；创建 Cookie |
| GET | `/api/auth/sessions` | 真实会话；仅未到期且未撤销设备；`current` 标记；不返回哈希 |
| POST | `/api/auth/logout` | 真实会话；撤销当前并清 Cookie |
| POST | `/api/auth/sessions/revoke-all` | 真实会话；撤销全部，包括当前 |

原有公开方法/路径不变：GET `/api/health`、`/api/health/live`、`/api/health/ready`、`/api/features`。所有其他已注册 API（包括 `/health/detail`、`/stream`、账户、Astro、价差、资金费率、公告、指数、设置及管理接口）继续受 S0 统一守卫保护。路由矩阵保留原 118 个基线路由的集合断言，并覆盖新增接口。

删除旧参数/重复校验的路由文件：`routes_account_connections.py`、`routes_admin.py`、`routes_alerts.py`、`routes_astro.py`、`routes_funding_arbitrage.py`、`routes_gate_twap.py`、`routes_health.py`、`routes_hyperliquid_trade_status.py`、`routes_index_components.py`、`routes_market_identities.py`、`routes_pair_spread.py`、`routes_phone_alerts.py`、`routes_settings.py`、`routes_trade_availability.py`。账户凭据和服务控制已有的“必须配置非空密码”额外业务限制保留。

- `frontend/src/api/client.ts` 的所有请求（含原直接 fetch 分支）改走 `sessionFetch`，固定同源 `/api`、`credentials: same-origin`；不再支持跨源 `VITE_API_BASE_URL`。任何 401 触发统一登录导航；API 错误不显示旧密码提示。
- `AuthGate` / `LoginPage`：`/login` 两个输入框，恢复登录前完整 pathname/query/hash，禁止站外 return URL；提交完成清空输入。初次挂载分别清除 localStorage 和 sessionStorage 的已知旧项 `dashboard_password`，保留无关偏好，兼容存储被禁用。
- `SettingsPage` / `LoginDevices`：访问与服务页显示设备、当前标志、IP、最近使用、到期时间，并提供刷新/退出当前/退出全部。
- `AccountConnectionsPage`：移除面板密码保存/修改区，账户操作使用当前会话。交易所 API 密钥等业务凭据表单保留。
- `FloatingWatchPanel`：移除基于本地密码值的轮询恢复逻辑，改为会话 401 状态；保留手动重试、切换模式和过期异步响应隔离。
- `PairMonitorPage` / `utils/floatingWatch.ts`：删除密码弹窗、候选密码参数及保存密码逻辑；预设合并/保存/删除/加入浮窗均使用 Cookie。401 后保留本地预设缓存，登录返回后由用户重新发起写操作，不自动执行登录前的写请求。原始 DEX 和市场身份不变。
- 全仓扫描（含未跟踪源码、排除依赖和测试产物）未发现生产代码残留旧密码头或密码存储读取/写入。历史文档与明确验证旧头无效/清理旧存储的测试可保留其字面名称。

## 验证与原始汇总行

后端在 Windows 当前 Python 3.12 解释器运行，**不是锁定依赖 venv**。工作目录 `backend`，PowerShell 设置 `$env:PYTHONPATH='.'` 后运行：

```text
python -m pytest tests -q -p no:cacheprovider --basetemp ../.pytest-tmp
2454 passed, 6 deselected, 12 warnings in 130.64s (0:02:10)
```

本机包版本：FastAPI 0.135.1、Starlette 0.52.1、Pydantic 2.12.5、aiosqlite 0.22.1、httpx 0.28.1、pytest 8.4.2、pytest-asyncio 0.25.3。锁文件中的 FastAPI 0.162.0 / Starlette 1.10.0 / Pydantic 2.14.1 与本机不同；**锁定依赖 venv 全量测试由 PM 跑，本报告不替代这项验收**。最终 12 条警告均为现有 `datetime.utcnow()` 弃用提示。网络/benchmark 用例遵循项目默认标记排除。只用 ASGI/TestClient/模拟通知和本地临时数据库，没有启动真实服务。

前端依赖通过本机 npm 缓存 `npm ci --offline --ignore-scripts --no-audit --no-fund` 安装，未联网、未改锁文件。`frontend` 目录运行 `npm test -- --maxWorkers=2 --minWorkers=2`：

```text
 Test Files  31 passed (31)
      Tests  422 passed (422)
   Duration  217.74s (transform 1.39s, setup 125.99s, collect 63.60s, tests 181.16s, environment 49.96s, prepare 3.46s)
```

`frontend/node_modules/.bin/tsc.cmd --noEmit -p frontend/tsconfig.json`：退出码 0，无输出。`git diff --check`：通过。

认证新增测试覆盖：RFC 向量、窗口边界、并发连接/重启防重放、Cookie 属性和仅存哈希、锁定及解锁、统一错误、通知脱敏/失败隔离、可信代理、API Token 开关、生产缺配置全路由拒绝、多设备及注销全部、过期、异常 Unicode/请求形状、设置命令。前端覆盖旧存储清理、禁用存储、登录返回、全调用路径 401、禁止跨源、设备列表/注销和 DEX 回归。既有测试仅迁移鉴权夹具/行为，保留业务断言；独立连接回滚白名单增加 auth 并验证连接不共享及生命周期关闭。

## 实现决定、偏离和未完成验收

- `config.py`、`main.py`、`schema.py` 仅追加，避免与 `codex/shadow-ledger` 的原内容冲突。
- 比最低规格更严格：TOTP 解码至少 20 字节；会话天数限制 1–30；API Token 不可用于会话管理；Settings repr 脱敏。没有采用降低安全性的兼容密码头、明文 Cookie 或生产空配置放行。
- 会话 `last_seen_at` 最多每分钟更新，SQLite 写忙时仅略过该元数据更新；到期/撤销仍每次真实查询，不缓存授权。这避免鉴权争抢业务写锁。
- 增加三个持久状态表，使锁定和重放防护不因服务重启或多 worker 失效。数据库路径仍是原 `DATABASE_URL`，不引入新持久卷。
- 除 C.1 外唯一容器相关改动是 A 所需的 Uvicorn `--no-proxy-headers`。未改 HTTPS/证书/安全组/nginx，也未执行部署。
- 受“main.py 仅追加”约束，原缺密码启动警告中的 `all API routes are unprotected` 文案保留，**已不代表实际行为**：新守卫默认拒绝，生产不可绕过。部署方不要按这条旧文案判断权限；后续协调共享文件时可更新该遗留文案。
- 本任务完成源码只读自查；PM 锁定 venv、独立审查、电脑/手机 HTTPS 真机验收及飞书真实投递均未执行。没有声称线上已升级。

## 服务器需要同步修改的脚本清单

本工作树没有找到仍发送旧头的可执行脚本；未读取服务器，以下不能当作线上穷尽清单：

1. 规格明确点名的 `output/papercard-20261030.py`：将 `X-Dashboard-Password` 改为 `X-Api-Token`；只从服务器受控环境读取 `DASHBOARD_API_TOKEN`，不要打印头、配置或完整请求。
2. 服务器 `output/`、运维脚本、cron/systemd jobs 和人工验收命令中所有同类头调用：逐一清点迁移。业务只读探针也需要 Token；只有上述四个公开 GET 探针例外。
3. 旧“空密码即可访问”的脚本/探针：生产不再支持；应迁移至 Token 或公开健康端点，禁止为兼容而打开生产绕过。
4. 若启动脚本覆盖 Dockerfile CMD，必须保留 `--no-proxy-headers`，避免 Uvicorn 重写 socket peer。

## 部署交接注意事项（仅说明，不执行）

- 部署前备份 SQLite；同步 A 的环境变量：`DASHBOARD_PASSWORD`、`DASHBOARD_TOTP_SECRET`、可选 `DASHBOARD_API_TOKEN`、`DASHBOARD_SESSION_DAYS`、`TRUSTED_PROXY_IPS`。生产 `ENVIRONMENT=production`；不要开启开发绕过。Compose 已有 `env_file: .env`，新增字段从该文件传入，不把真实值写仓库。
- 用设置命令在受控终端生成 TOTP，妥善保存手机验证器和恢复材料；不要把输出纳入 shell/CI/会话日志。API Token 应独立生成 32 随机字节的 64 字符十六进制字符串，不复用网页密码。启用并核对既有飞书投递配置。
- **必须先准备 HTTPS 入口**。Secure Cookie 在普通 HTTP 业务入口不能正常使用；不能通过关闭 Secure 来临时兼容。C.1 已把 8000/3000 绑定 `127.0.0.1`，部署方须按 C 的顺序安排应用切换，避免更新 Compose 后丢失入口。
- **双层 nginx 的真实 IP 必须单独验收**：当前 `frontend/nginx.conf` 使用 `$proxy_add_x_forwarded_for`，若宿主机 nginx 再转发，末项会是宿主机/网桥，通知及 IP 锁定会合并为代理地址。部署方应在可信边缘覆盖用户传入的转发头；内部 nginx 只对确切宿主机代理来源配置 real-IP 解析，使发给后端的最后一项为真实客户端。不能随意信任任意来源或盲取 XFF 第一项。后端仍严格按规格只认可信 peer 的最后一项。
- `TRUSTED_PROXY_IPS` 默认覆盖 Docker 私网；按实际容器网络确认并尽可能收窄。验证不可信直连伪造 XFF 无效、可信链的最后 IP 与手机/电脑真实出口一致。
- NTP 时钟须可靠。数据库中的成功计数和锁定状态跨重启保留；回滚数据库可能回滚这些安全状态，应纳入恢复流程。改密码或 TOTP 不自动撤销旧会话；需要立即下线时先使用“退出所有设备”。
- localStorage 按 origin 隔离：升级页会清理**当前 origin** 的旧密码项，HTTPS 页面无法删除旧 HTTP:3000 origin 的存储。旧 origin 的浏览器数据需由部署方/用户在切换时清理，不应为此重新暴露旧 HTTP 登录入口。
- 依次验证 HTTPS 登录、Cookie 标志、旧头 401、Token、设备列表、退出/退出全部、失败锁定和真实飞书通知，再验证旧 3000 公网不可达。HTTPS 证书、端口 80/8443、安全组、续期仍全部属于 C 的后续工作。

最终原始测试输出保留在本工作树 `.pytest-backend-output.txt`、`.vitest-output.txt`、`.tsc-output.txt`，不提交日志/临时数据库/依赖目录。输入规格在任务开始时已是未跟踪文件，本任务不修改其内容；交接文档见 `docs/task-handoff-2026-11-03-login-2fa.md`。
