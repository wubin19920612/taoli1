# login-2fa 交接（2026-11-03）

- 目标/范围：规格 A、B、C.1，以及 `affad23` 末尾 PM decisions 1–6。第 5 条不改产品代码，只增加旧 HTTP:3000 清理测试和部署说明。没有执行其他 C 部署步骤、推送、外网访问、WSL 或真实服务启动。
- 工作树：`.worktrees/login-2fa`；分支 `codex/login-2fa`，原生产基线 `81585e4`，本轮基线 `affad23`。首轮提交 `4021791`、`380c426`、`dd51f33`、`72e1799`、`fb7f125`。本轮 `fix: 081e509`、`test: 72e974e`，随后为 `docs:` 提交；均只在本地。
- 实现入口：后端 `app/core/security.py`、`app/core/totp.py`、`app/db/auth.py`、`app/api/routes_auth.py`、`app/api/routes_health.py`、`app/api/stream.py`、`app/cli/totp_setup.py`；前端 `src/api/auth.ts`、`AuthGate`、`LoginPage`、`LoginDevices`；代理 `frontend/nginx.conf`、`deploy/nginx-https.conf.example`、`docker-compose.yml`。
- 重要规则：浏览器只用 Secure/HttpOnly/Strict Cookie；旧密码头无效；服务器 Token 独立；生产缺任何登录配置均拒绝；TOTP 高水位和 IP 失败锁定持久化；会话管理必须 Cookie；Hyperliquid DEX/原始市场身份保留；登录后不自动重试旧写操作。SSE 每轮复查，失效直接结束。匿名 health/live/ready 仅状态；health 详情需要真实会话或 API Token。
- 全局限速：所有 IP 合计 30 次/滚动 60 秒，超限 429，拒绝请求不延长窗口、不消费验证码；没有全局硬锁。SQLite 共享时间戳预算和 900 秒通知冷却，以保证多个 worker/重启不能绕过；旧 global 锁打开库时删除，IP 锁保留。飞书/日志/异常/测试快照仍禁止输出任何认证凭据。
- 共享文件：本轮按 PM 授权改 `config.py` 两处可信代理默认值、`main.py` 一行警告；`schema.py` 未改。与 `codex/shadow-ledger` 合并时保留修复。认证连接独立，不能替换为共享业务写连接。
- 最终结果：后端 `2499 passed, 6 deselected, 12 warnings in 245.39s (0:04:05)`；前端 `32 passed (32)` 文件、`424 passed (424)` 用例；tsc 退出码 0，无输出。原始汇总行和逐条修复见 [实现报告](login-2fa-report-2026-11-03.md)。本机非锁定依赖 venv；**PM 必须在锁定 venv 跑全量后端测试**，不能把本地解释器结果当作最终验收。既有安全断言保留，旧全局硬锁/旧日志断言仅按 PM 明确新行为更新；网络阻断和测试标记未改。
- 线上状态：未部署、未备份线上库、未推送，仍以部署方实际版本为准。本任务未接触服务器。
- 已知事项：main.py 警告和双层代理配置已修复，但没有运行真实 nginx/Docker。Compose 固定 `172.30.0.0/24`、网关 `172.30.0.1`；前端只信任该网关 `/32`，后端默认只信任该 `/24`。部署前核对网段冲突、实际 socket peer、既有 `.env` 覆盖和网络重建计划；不得随意扩大信任范围或删除持久卷。宿主机样例需替换实际证书路径并检查全局 real-IP 继承配置，公网边缘必须使用真实连接地址覆盖 XFF。
- PM decision 5：先把新前端同时挂到仍开放的旧 HTTP:3000 origin，让用户打开一次清掉旧 localStorage/sessionStorage 密码，再关闭 3000。旧地址只做清理，不提交 HTTP 登录。HTTPS 不能清理旧 origin；本轮的 jsdom 测试不替代实际用户操作。
- 既有会话不会因修改密码/密钥自动撤销；需要立即下线时先退出所有设备。SSE 会复查过期/撤销及 Token 配置。全局 429 仍显示现有前端通用登录失败提示，后端 `Retry-After: 60` 明确重试上限；没有修改登录页产品代码。
- 原有未跟踪诊断 `.pytest-backend-output.txt`、`.vitest-output.txt`、`.tsc-output.txt` 保留。本轮新输出 `.pytest-backend-review-output.txt`、`.vitest-review-output.txt`，tsc 无输出；`.pytest-tmp/` 和 `frontend/node_modules/` 为忽略的本地测试产物。规格已在 `affad23` 跟踪，本轮未改。
- 下一任务：由 PM/Claude 新建部署任务，先跑锁定环境测试和独立安全审查，再按报告清点 `output/papercard-20261030.py` 等服务器脚本、配置 HTTPS/证书/双层 IP 链、备份及部署。验收电脑/手机登录、伪造 XFF、SSE 撤销、公开 health 最小字段、30/分钟限速和飞书脱敏通知，并完成旧 origin 清理后关闭 3000。不要为兼容旧客户端恢复密码头或关闭 Cookie Secure。
