# 登录 2FA 实现报告（2026-11-03）

范围：按 `docs/login-2fa-spec-2026-11-03.md` 实现 A、B、C.1，并按末尾 PM decisions 1–6 修复安全审查问题。分支 `codex/login-2fa`，原起点 `81585e4`，本轮修复基线 `affad23`。第 5 条仅补部署交接和回归测试，不修改产品代码。没有启动真实服务、访问外网、运行 WSL、推送 Git 或执行 C.2–C.5；只新增宿主机 nginx 配置样例。线上仍由 PM/Claude 在后续任务验收。

## 本地提交

- `4021791` — `feat: add TOTP login, persistent cookie sessions and API token authentication`
- `380c426` — `feat: use same-origin session login and add device management`
- `dd51f33` — `feat: redact authentication settings and verify both login factors`
- `72e1799` — `test: cover TOTP sessions and migrate route and frontend auth regressions`
- `081e509` — `fix: address login 2FA security review decisions`（本轮代码与代理配置）
- `72e974e` — `test: cover login review regressions without weakening security checks`（本轮回归测试）
- 文档为随后 `docs:` 提交；所有提交仅在本地。

## 后端实现

- `app/core/totp.py`：标准库 base32、HMAC-SHA1、动态截断；登录使用 6 位、30 秒和前后各一窗。保存最近成功计数，拒绝相同及更早计数；支持 RFC 6238 的 8 位向量测试。不新增依赖。
- `app/db/auth.py`：`auth_sessions` 仅存 32 字节随机令牌的 SHA-256 哈希，记录创建/到期/最近使用/IP/浏览器/撤销时间。Cookie 原文只通过 `Set-Cookie` 交给浏览器，不作为 JSON 返回，不写数据库和通知。
- 附加 `auth_totp_state`、`auth_failures`、`auth_locks`，以及本轮的 `auth_login_attempts`、`auth_rate_notice`。验证码消费、IP 锁定、全局滚动限速和创建会话在独立连接的 `BEGIN IMMEDIATE` 事务中完成，支持多进程并发及重启后保留状态。不会回滚其他模块未提交的数据。
- 密码和 API Token 使用 `hmac.compare_digest` 比较字节；验证码也用常量时间比较。合法形状的验证码检查不依赖密码正确与否，减少因子检查路径差异。
- IP 15 分钟内失败 5 次仍锁 15 分钟；锁定请求不延长锁期、不重复发送锁定通知。删除全局失败 20 次硬锁，改为所有来源合计滚动 60 秒最多接纳 30 次登录尝试，超出返回 429 和 `Retry-After: 60`；拒绝请求不进入窗口、不消费 TOTP。成功、错误、非法 JSON、字段类型错误、验证码重放及已被 IP 锁定的请求均计入接纳预算；预算内认证失败仍统一 401 与“密码或验证码错误”。存储故障返回脱敏 503。全局限速告警每 900 秒最多尝试投递一条，失败也不立即重复投递。
- Cookie 始终为 `taoli1_session; HttpOnly; Secure; SameSite=Strict; Path=/`。默认 30 天（2592000 秒）；无滑动续期。
- `core/security.py` 保留统一路由依赖的旧导入名 `require_dashboard_password`，含义已改为会话/API Token 校验。删除 `verify_dashboard_password`、`dashboard_password_header` 和所有路由内旧密码头检查。
- `ENVIRONMENT=production` 缺密码、缺 TOTP 或 TOTP 无效时，全部受保护路由均拒绝，即使带有效 Cookie、API Token 或开启开发绕过标志。
- 默认所有环境均保护。只有 `development/local/test` 且显式 `DASHBOARD_AUTH_DISABLED=true` 才允许业务路由绕过；会话管理仍要求真实 Cookie。
- `X-Api-Token` 独立于浏览器登录；配置必须为 64 字符且不同于网页密码，空值/短值关闭该路径。不会由前端发送或保存。
- IP 只在连接来源匹配 `TRUSTED_PROXY_IPS` 时采用 XFF 最后一项，并校验为合法 IP；不匹配/非法头回退连接地址。默认已收窄为固定 Compose 子网 `172.30.0.0/24`。`backend/Dockerfile` 保留 `--no-proxy-headers`，防止 Uvicorn 提前改写连接来源。
- 登录成功、新 IP 锁定及全局限速调用既有 `FeishuNotifier.send_text`；消息只有事件、规范化 IP、浏览器家族/平台和 UTC 时间。UA 原文不保存、不通知，防止任意头内容夹带凭据。通知失败仅记录固定警告，不打印异常详情，不撤销成功登录。新增 `LoginResult` 的 repr 不包含令牌字段。
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

`/api/health` 未认证时严格只返回 `{status: "ok"}`；真实会话或 API Token 才能得到原有完整字段和过滤结果，开发绕过也不开放这份诊断；响应统一 `Cache-Control: no-store`。`/health/live` 和 `/health/ready` 只返回 `status`，保留原来的 200/503 判定。SSE 每轮（正常循环 2 秒）重新鉴权，包括无快照变化时；失效或鉴权存储故障直接关闭，不把错误详情作为 SSE 事件发出。

删除旧参数/重复校验的路由文件：`routes_account_connections.py`、`routes_admin.py`、`routes_alerts.py`、`routes_astro.py`、`routes_funding_arbitrage.py`、`routes_gate_twap.py`、`routes_health.py`、`routes_hyperliquid_trade_status.py`、`routes_index_components.py`、`routes_market_identities.py`、`routes_pair_spread.py`、`routes_phone_alerts.py`、`routes_settings.py`、`routes_trade_availability.py`。账户凭据和服务控制已有的“必须配置非空密码”额外业务限制保留。

- `frontend/src/api/client.ts` 的所有请求（含原直接 fetch 分支）改走 `sessionFetch`，固定同源 `/api`、`credentials: same-origin`；不再支持跨源 `VITE_API_BASE_URL`。任何 401 触发统一登录导航；API 错误不显示旧密码提示。
- `AuthGate` / `LoginPage`：`/login` 两个输入框，恢复登录前完整 pathname/query/hash，禁止站外 return URL；提交完成清空输入。初次挂载分别清除 localStorage 和 sessionStorage 的已知旧项 `dashboard_password`，保留无关偏好，兼容存储被禁用。
- `SettingsPage` / `LoginDevices`：访问与服务页显示设备、当前标志、IP、最近使用、到期时间，并提供刷新/退出当前/退出全部。
- `AccountConnectionsPage`：移除面板密码保存/修改区，账户操作使用当前会话。交易所 API 密钥等业务凭据表单保留。
- `FloatingWatchPanel`：移除基于本地密码值的轮询恢复逻辑，改为会话 401 状态；保留手动重试、切换模式和过期异步响应隔离。
- `PairMonitorPage` / `utils/floatingWatch.ts`：删除密码弹窗、候选密码参数及保存密码逻辑；预设合并/保存/删除/加入浮窗均使用 Cookie。401 后保留本地预设缓存，登录返回后由用户重新发起写操作，不自动执行登录前的写请求。原始 DEX 和市场身份不变。
- 全仓扫描（含未跟踪源码、排除依赖和测试产物）未发现生产代码残留旧密码头或密码存储读取/写入。历史文档与明确验证旧头无效/清理旧存储的测试可保留其字面名称。

## PM decisions 1–6 逐条修复与测试

1. **SSE 会话复查**：`backend/app/api/stream.py` 每轮在读取/发送快照前调用统一认证；正常 2 秒循环，无快照变化也复查。会话过期、logout、revoke-all、API Token 更换/关闭、生产配置失效及认证存储故障均关闭流。`test_login_2fa_review.py` 使用真实认证库、实际注销接口和假时钟，分别覆盖 idle/active 两种流，断言失效后不再发送下一条快照且在 ≤30 秒内结束；另验证有效会话每次推送都复查。没有新增 SSE 错误事件或凭据日志。
2. **双层代理真实 IP**：固定 Compose 子网/网关，两层 nginx 均覆盖 XFF，前端只信任网关 `/32`；后端默认仅信任固定 `/24`，仍取合法的最后一跳。`test_proxy_defaults_match_fixed_compose_network` 核对两处 Python 默认值、IPAM、loopback 端口和 Uvicorn 参数；`test_double_proxy_chain_overwrites_client_xff` 将配置契约与双层链模型绑定，覆盖 IPv4/IPv6、单项/多项/非法伪造头；原 `test_trusted_proxy` 保留最后一跳、非法回退和不可信来源断言，新增旧 Docker 私网及 loopback 默认不可信的断言。没有启动 nginx 或 Docker；实际网桥来源仍需部署验收。
3. **公开健康响应最小化**：匿名、旧密码头、错误 Cookie/Token、过期/撤销会话、缺配置、认证存储故障、开发绕过均只能得到精确 `{status}`；匿名分支不调用诊断和风控配置读取。真实 Cookie/API Token 保留完整诊断；公开 live/ready 精确只含状态，ready 的 200/503 不变。新增以上各分支及 `no-store` 测试；原健康过滤和交易所状态断言不删减，仅补真实鉴权夹具。
4. **全局限速替代硬锁**：移除全局 20 次失败锁定；30 次/滚动 60 秒的接纳预算在同一 SQLite 事务中共享，超出 429，不延长窗口、不消费验证码。按 IP 规则不变，旧全局锁清理不影响 IP 锁。新增第 30/31 次、20 次失败不封禁其他来源、成功也计数、畸形请求及已锁 IP 也计数、59.999/60 秒恢复、拒绝后 TOTP 仍可用、40 个跨连接并发请求只接纳 30 个、重开连接共享限速、899.999/900 秒告警冷却及旧锁迁移测试。原全局硬锁测试按 PM 新政策改成“20 次失败后另一 IP 正确登录成功”，不是删除覆盖；IP 锁、安全错误、防重放断言保留。前端增加 429 下停留登录页、清空输入、不保存/回显凭据且不自动重试的测试。
5. **旧 3000 origin 清理**：产品代码未改。新增 `frontend/tests/LegacyOriginCleanup.test.tsx`，jsdom 固定 `http://legacy.test:3000`，验证首次挂载清除两种存储的旧密码项、保留其他偏好、无需发出登录请求。真实用户访问旧地址一次再关闭 3000 的流程已写入部署交接；此测试不替代真实浏览器操作。
6. **误导日志**：仅替换 `main.py` 授权的一行文案。原启动日志测试从旧文案包含断言更新为新文案完整等值断言，仍验证空密码时恰好一条 WARNING、有密码时无警告；新增无后台 worker 的 ASGI lifespan 测试，对照生产即使开启绕过仍 401、测试环境默认 401、测试环境显式绕过 200。

脱敏回归保留并扩展：把合成密码、验证码、Token、会话令牌和 TOTP 密钥放入恶意 UA/通知异常，再断言所有通知、响应和捕获日志均不包含它们；新结果对象 repr 不输出 Token。没有生成认证数据快照或写入真实配置。`tests/conftest.py` 的外网阻断和项目 pytest 默认标记未改；不增加 skip/xfail，不放宽原有安全断言。

## 本轮最终验证与原始汇总行（PM decisions 修复）

Windows 本机 Python **3.12.10**，依赖版本与下方首轮记录一致，仍然**不是锁定依赖 venv**。`backend` 目录先设置 `$env:PYTHONPATH='.'`，再执行用户指定命令；临时目录解析为本工作树 `.pytest-tmp`。原始汇总行：

```text
python -m pytest tests -q -p no:cacheprovider --basetemp ../.pytest-tmp
2499 passed, 6 deselected, 12 warnings in 245.39s (0:04:05)
```

退出码 0；较首轮增加 45 个通过用例。6 个排除用例来自原有 `not network and not benchmark` 默认配置，未修改标记、选择范围或网络阻断夹具。12 个警告仍是 `datetime.utcnow()` 弃用提示。

`frontend` 目录执行 `npm.cmd test -- --maxWorkers=2 --minWorkers=2`，原始汇总行：

```text
 Test Files  32 passed (32)
      Tests  424 passed (424)
   Duration  338.55s (transform 2.13s, setup 208.63s, collect 114.45s, tests 277.82s, environment 60.44s, prepare 4.69s)
```

退出码 0；增加旧 HTTP origin 清理和 429 登录失败两个用例。存在 React `act(...)` 及 Ant Design 弃用警告，未屏蔽警告或降低断言。本轮使用已安装依赖，没有安装包或访问外网。

同在 `frontend` 执行 `.\node_modules\.bin\tsc.cmd --noEmit -p tsconfig.json`：**退出码 0，无输出**。`git diff --check`、分步暂存的 `git diff --cached --check` 通过。

本轮两份全量输出已检查合成认证敏感值、异常哨兵和 64 位十六进制 Token 形状，匹配数均为 0；新测试未生成快照。原有三份输出未覆盖。代码/测试已分别提交，文档随后提交；没有推送、WSL、真实服务、外网请求、线上备份或部署。nginx 配置只做静态契约与模拟链验证；锁定依赖 venv、真实 HTTPS/IP 链/浏览器/SSE/飞书投递仍由部署任务验收。

## 首轮验证与原始汇总行（fb7f125，历史记录）

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

- 首轮共享文件采用追加；本轮按 PM 明确授权改动 `config.py` 的两个可信代理默认值及 `main.py` 的一行警告，`schema.py` 未改。合并 `codex/shadow-ledger` 时保留这些安全修复。
- 比最低规格更严格：TOTP 解码至少 20 字节；会话天数限制 1–30；API Token 不可用于会话管理；Settings repr 脱敏。没有采用降低安全性的兼容密码头、明文 Cookie 或生产空配置放行。
- 会话 `last_seen_at` 最多每分钟更新，SQLite 写忙时仅略过该元数据更新；到期/撤销仍每次真实查询，不缓存授权。这避免鉴权争抢业务写锁。
- IP 锁定、重放高水位、滚动窗口计数及通知冷却时间共享持久存储；不再写入或检查全局锁，打开认证库时清理旧 `global` 锁行，保留 IP 锁。窗口行仅保存时间戳，过期在下一次请求时删除；不保存请求凭据。数据库路径仍是原 `DATABASE_URL`，不引入新持久卷。
- 本轮增加固定 Compose 子网、前端 nginx real-IP 配置和 `deploy/nginx-https.conf.example`；没有配置真实证书、安全组或宿主机，也没有执行部署。
- `main.py` 误导日志已改为“protected API routes deny access unless explicit non-production auth bypass is enabled”，与生产拒绝、非生产显式绕过的行为一致。
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
- **双层 nginx 的真实 IP 必须单独验收**：两层都以 `$remote_addr` 覆盖 XFF；宿主机样例不信任客户端传入 XFF，前端只信任固定网关 `172.30.0.1/32`，开启 `real_ip_header X-Forwarded-For` 和 `real_ip_recursive on`。后端只信任 `172.30.0.0/24` 的最后一项。离线模型和配置契约测试通过不等于真实 nginx/Docker 网络验收。
- 部署方先确认 `172.30.0.0/24` 无网段冲突，现有 Compose 网络需要按计划重建才能应用固定 IPAM；不要执行 `down -v`。确认宿主机到前端连接的实际 peer 为 `172.30.0.1`，如不同，应同步精确调整 nginx 信任来源、Compose 和后端配置，不能扩大为全部私网。已有 `.env` 若还覆盖旧 `TRUSTED_PROXY_IPS` 必须同步收窄。验证伪造 XFF 无效、会话/通知/IP 锁定记录与手机和电脑真实出口一致。
- NTP 时钟须可靠。数据库中的成功计数和锁定状态跨重启保留；回滚数据库可能回滚这些安全状态，应纳入恢复流程。改密码或 TOTP 不自动撤销旧会话；需要立即下线时先使用“退出所有设备”。
- **PM decision 5 的部署顺序**：先让新前端同时挂在仍开放的旧 HTTP:3000 地址，用户用该旧地址打开一次，清除其 localStorage/sessionStorage 旧密码项，再关闭旧 3000 公网入口。HTTPS 不能清除另一个 origin 的存储；旧地址只用于执行新前端清理，不在 HTTP 提交登录、不关闭 Secure Cookie。本轮没有改变这部分产品代码，也没有实际操作旧端口。
- 依次验证 HTTPS 登录、Cookie 标志、旧头 401、Token、设备列表、退出/退出全部、失败锁定和真实飞书通知，再验证旧 3000 公网不可达。HTTPS 证书、端口 80/8443、安全组、续期仍全部属于 C 的后续工作。

首轮原始输出 `.pytest-backend-output.txt`、`.vitest-output.txt`、`.tsc-output.txt` 原样保留。本轮输出使用 `.pytest-backend-review-output.txt`、`.vitest-review-output.txt`、`.tsc-review-output.txt`（tsc 无输出时文件可能不生成），均不提交；临时数据库在仓库内 `.pytest-tmp`，不提交依赖目录。本轮规格已经在 `affad23` 跟踪，未修改其内容；交接文档见 `docs/task-handoff-2026-11-03-login-2fa.md`。

## PM 验收结论（2026-11-03）
- 锁定依赖 venv（fastapi 0.162.0 / starlette 1.10.0 / pydantic 2.14.1）后端全量：`2499 passed, 6 deselected, 13 warnings`。前端 424 passed、tsc 通过（Codex 日志）。
- 只读复审（d3afdf6）：PM decisions 2–6 通过；第 1 条剩余"发送背压时 SSE 停在 yield、撤销后 31 s 才断开"。**PM 接受为已知限制**：阻塞期间客户端收不到新数据，恢复读取后下一次复查即断开，最多送达撤销时已在发送中的一条消息。不再返工。
- 验收通过，可以部署；部署按任务书 C 节，由 Claude 执行，需用户先在安全组放行 80、8443。

## PM decision：兼容现有 arb 宿主机绑定（2026-11-04）

部署前发现生产机 arb-mvp 服务（含 live）绑定在 `172.18.0.1`，后端 `ARB_PAPER_URL` / `ARB_LIVE_URL` 也指向该地址，现有 Compose 网络为 `172.18.0.0/16`。若改用此前固定的 `172.30.0.0/24`，后端将无法连接 arb。PM 决定将固定 Compose 子网改为 `172.18.0.0/24`、网关改为 `172.18.0.1`，并同步前端 nginx 精确信任网关 `/32`、后端两处可信代理默认 `/24` 及对应测试地址（含严格匹配的正则）。本决定取代上文旧网段说明；其他逻辑和断言强度不变。
