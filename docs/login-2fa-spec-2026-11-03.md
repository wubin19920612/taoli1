# 任务书：taoli1 网页登录加动态验证码 + HTTPS（2026-11-03）

背景：`D:/吴彬工作/claude/套利/research/security/security-review-2026-11-03.md`。3000 端口公网明文，网页密码每次请求都原样发送。用户决定（11-03）：**保留公网和手机访问；登录 = 密码 + 动态验证码（TOTP，用手机验证器 App）；登录有效 30 天；只有登录时输验证码，下单类操作不再二次验证；没有域名**。

分工：A、B 由 Codex 实现（分支从最新生产提交拉 `codex/login-2fa`）；C 部署由 Claude 做，其中开 80 端口由用户在腾讯云控制台操作。

## A. 后端
1. **TOTP**：RFC 6238，SHA1、6 位、30 秒，允许前后各 1 个时间窗；同一个时间窗的验证码**只能用一次**（记住最近一次成功的计数）。用标准库 `hmac` 实现，不新增依赖。密钥来自环境变量 `DASHBOARD_TOTP_SECRET`（base32）。
2. **登录**：`POST /api/auth/login`，body `{password, code}`。密码比较用常量时间。成功后发会话：
   - 32 字节随机令牌；库里只存 SHA-256 哈希。新表 `auth_sessions`：id_hash、created_at、expires_at（30 天，`DASHBOARD_SESSION_DAYS=30`）、last_seen_at、ip、user_agent、revoked_at。
   - Cookie `taoli1_session`：`HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=2592000`。
3. **鉴权依赖**：现有的路由权限矩阵（S0，`core/security.py`）改为认会话 Cookie。**删除浏览器用的 `X-Dashboard-Password` 请求头鉴权**（它绕过验证码）。服务器上的脚本改用新的 `X-Api-Token` 请求头，令牌来自 `DASHBOARD_API_TOKEN`（64 位随机，和网页密码不同，只放在服务器 `.env`）；该变量为空时这条路径关闭。
4. **安全默认值**：`DASHBOARD_PASSWORD` 或 `DASHBOARD_TOTP_SECRET` 为空时，生产环境（`ENVIRONMENT=production`）**拒绝所有需鉴权的请求**，不再"密码为空就放行"。测试和本地开发可以通过显式配置放行。
5. **防暴力破解**：同一 IP 15 分钟内失败 5 次锁 15 分钟；全局 15 分钟失败 20 次锁所有登录 15 分钟。失败原因对外统一返回"密码或验证码错误"。
6. **客户端 IP**：只有当请求来自 `TRUSTED_PROXY_IPS`（默认 `127.0.0.1,172.16.0.0/12`）时才信任 `X-Forwarded-For` 的最后一跳，否则用连接地址。
7. **通知**：登录成功、触发锁定都推飞书（复用现有飞书发送），内容含 IP、浏览器标识、时间；**不含**密码、验证码、令牌。
8. **会话管理接口**：`POST /api/auth/logout`（注销当前会话）、`GET /api/auth/sessions`（列出未过期会话，标出当前）、`POST /api/auth/sessions/revoke-all`（注销全部，包括当前）。
9. **验证码设置命令**：`python -m app.cli.totp_setup` 生成新的 base32 密钥，打印 `otpauth://totp/taoli1:admin?secret=...&issuer=taoli1` 和密钥本身，**不写任何文件、不写日志**。由 Claude 在服务器上运行，再把密钥写进 `.env`。

## B. 前端
- 登录页：密码 + 6 位验证码两个输入框；登录成功后跳回原页面。
- 所有请求改为同源带 Cookie；**删除把密码存在 localStorage / sessionStorage 里的逻辑**，并在首次加载时清掉旧的存储项。
- 任何接口返回 401 → 跳登录页。
- 设置处加"登录设备"列表、"退出登录"、"退出所有设备"。

## C. 部署（Claude 做）
1. **端口**：`docker-compose.yml` 的 3000、8000 改为只绑 `127.0.0.1`（这一行由 Codex 在 A/B 同分支里改）。
2. **HTTPS 入口**：宿主机装 nginx，监听 `8443`（443 已被 xray 占用），证书见下，反代到 `127.0.0.1:3000`，带 `X-Forwarded-For`。入口地址变为 `https://43.155.177.41:8443`。`CORS_ORIGINS` 相应更新。
3. **证书（没有域名）**：Let's Encrypt 2026-01 起正式支持 **IP 地址证书**，必须用 `shortlived` 档（有效约 6 天），验证方式只能是 http-01（80 端口）或 tls-alpn-01（443 端口，已被占用）。所以：
   - 用户在腾讯云安全组**放行 80 端口**（只用于证书验证，nginx 在 80 上只回应 ACME 验证、其余一律 404）；
   - 用支持 ACME profile 和 IP 证书的新版 certbot（snap 版；部署前核实版本），`standalone` 或 webroot 方式申请 `43.155.177.41`；
   - systemd 定时器每天检查续期，续期后 reload nginx；续期失败推飞书。
   - 兜底：IP 证书申请不下来，就用自签证书并在电脑和手机上安装信任（体验差，只作备选）。
4. **切换顺序**：先上 HTTPS 入口并验证登录可用 → 再改端口绑定关掉 3000 公网 → 安全组删掉 3000。期间旧入口一直能用，不会把用户锁在外面。
5. **服务器脚本**：`output/papercard-20261030.py` 等用密码请求头的脚本改用 `X-Api-Token`。

## 测试（Codex）
1. TOTP：RFC 6238 附录的测试向量；时间窗边界；同码重复使用被拒。
2. 登录：正确 / 密码错 / 验证码错 / 锁定与解锁 / 统一错误信息；常量时间比较。
3. 会话：Cookie 属性；过期；注销；注销全部；库里只有哈希。
4. 鉴权：路由权限矩阵测试全部改为会话；`X-Dashboard-Password` 不再有效；`X-Api-Token` 有效且为空时关闭；生产环境缺配置时全部 401。
5. 客户端 IP：可信代理与不可信来源。
6. 前端：登录流程、401 跳转、旧存储清理、设备列表。

## 验收
- 锁定依赖 venv 全量后端测试（Claude 跑）、前端测试和 tsc 全部通过；现有测试只允许因鉴权方式改变而改，不许放宽断言。
- `git diff --name-only` 在范围内。
- 只读 Codex 审查：常量时间比较、验证码防重放、Cookie 属性、可信代理判断、日志与飞书里没有任何密钥 / 令牌。
- 部署后实测：电脑和手机都能用 `https://43.155.177.41:8443` 登录；`http://43.155.177.41:3000` 连不上；带旧密码请求头访问接口返回 401。

## PM decisions（2026-11-03，回应只读安全审查打回 fb7f125）
1. **P1 SSE 会话失效**（`api/stream.py`）：推送循环里至少每 30 秒、以及每次推送前（30 秒内有缓存即可）重新校验会话；会话过期、注销、revoke-all 后关闭连接。用 API Token 建立的连接同样复查。补测试：建立 SSE 后撤销会话，连接在 ≤30 秒（测试里用假时钟）内关闭。
2. **P1 双层代理真实 IP**：
   - `frontend/nginx.conf`：`set_real_ip_from` 只信任宿主机代理所在的 docker 网关网段，`real_ip_header X-Forwarded-For; real_ip_recursive on;`，转发给后端时 `proxy_set_header X-Forwarded-For $remote_addr;`（**覆盖**，不再追加）。
   - 宿主机 nginx（部署时由 Claude 配置）同样以 `$remote_addr` 覆盖 `X-Forwarded-For`，丢弃客户端自带的值。把这段宿主机配置样例写进 `deploy/nginx-https.conf.example`。
   - 后端仍只认可信来源的最后一跳；`TRUSTED_PROXY_IPS` 默认收窄为前端容器所在的 compose 网段（在 compose 里给网络固定子网，例如 `172.30.0.0/24`），文档写清楚。
   - 补测试：模拟"客户端 → 宿主机 nginx → 前端 nginx → 后端"的请求头链，断言后端拿到真实客户端 IP；客户端伪造 XFF 无效。
3. **P2 公开健康接口**：未登录访问 `/api/health` 只返回 `{status}`；详细诊断（采集错误原文、交易所状态）需要会话或 API Token。`/api/health/live`、`/api/health/ready` 保持公开且只返回状态码和最少字段（部署脚本依赖它们）。
4. **P2 全局锁定被滥用**：删除"全局失败 20 次锁死所有登录"。改为：按 IP 锁定不变；全局只做限速——所有来源合计每分钟最多 30 次登录尝试，超出返回 429，不持久化锁定；全局超限时飞书告警，15 分钟内最多一条。理由：密码 32 位 + 6 位验证码，暴力破解不可行，全局硬锁定只会被用来拒绝服务。
5. **P2 旧 3000 端口里的明文密码**：不改代码。部署时先把新前端同时挂在旧 3000 上，用户用旧地址打开一次（新前端会清掉该站点保存的密码），然后再关闭 3000。
6. **P3** 更新 `main.py` 里"all API routes are unprotected"的误导日志，改为与实际行为一致（允许改这一行）。
