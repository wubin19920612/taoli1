# login-2fa 交接（2026-11-03）

- 目标/范围：规格 A、B、C.1；没有执行其他 C 部署步骤、推送、外网访问、WSL 或真实服务启动。
- 工作树：`.worktrees/login-2fa`；分支 `codex/login-2fa`，基线生产 `81585e4`。实现及安全审查提交 `4021791`、`380c426`、`dd51f33`；测试提交 `72e1799`；后续文档提交用 `git log` 查看。
- 实现入口：后端 `app/core/security.py`、`app/core/totp.py`、`app/db/auth.py`、`app/api/routes_auth.py`、`app/cli/totp_setup.py`；前端 `src/api/auth.ts`、`AuthGate`、`LoginPage`、`LoginDevices`。
- 重要规则：浏览器只用 Secure/HttpOnly/Strict Cookie；旧密码头无效；服务器 Token 独立；生产缺任何登录配置均拒绝；TOTP 高水位和失败锁定持久化；会话管理必须 Cookie；Hyperliquid DEX/原始市场身份保留；登录后不自动重试旧写操作。
- 共享文件 `config.py`、`main.py`、`schema.py` 只有追加，可与 `codex/shadow-ledger` 对照合并。认证连接独立，不能替换为共享业务写连接。
- 最终本地结果：后端 2454 passed / 6 deselected / 12 warnings，前端 31 文件 / 422 passed，tsc 退出码 0。测试/原始汇总行/全部调用变化详见 [实现报告](login-2fa-report-2026-11-03.md)。本机非锁定依赖 venv；**PM 必须在锁定 venv 跑全量后端测试**，不能把本地解释器结果当作最终验收。
- 线上状态：未部署、未备份线上库、未推送，仍以部署方实际版本为准。本任务未接触服务器。
- 已知事项：main.py 旧缺密码警告文案保留但已不符合实际行为；当前双层 nginx 需调整可信 real-IP 链；HTTPS 页面无法清除旧 HTTP origin 的存储；既有会话不会因修改密码/密钥自动撤销。
- 输入未跟踪文件：`docs/login-2fa-spec-2026-11-03.md`（用户提供，未修改）。本次未跟踪诊断产物：`.pytest-backend-output.txt`、`.vitest-output.txt`、`.tsc-output.txt`；`.pytest-tmp/` 和 `frontend/node_modules/` 为忽略的本地测试产物。
- 下一任务：由 PM/Claude 新建部署任务，先跑锁定环境测试和独立安全审查，再按报告清点 `output/papercard-20261030.py` 等服务器脚本、配置 HTTPS 与可信 IP 链、备份及部署、电脑/手机和通知验收。不要为兼容旧客户端恢复密码头或关闭 Cookie Secure。
