# 影子账本：合入部署分支交接（2026-11-05）

- 工作树：`taoli1/.worktrees/shadow-ledger`；分支：`codex/shadow-ledger`。合并前基线：`6d4389db6279b2bc9b66541ee0f9e9886257b96b`，初始工作区干净、无未跟踪交付文件。
- 目标：以 `--no-ff` 合入已 fetch 的 `origin/codex/frontend-localization-polish`，固定目标 `9a362a824276ec3c6cf6e560fc39e11bd88a1790`（含密码加验证码登录），同时保留影子账本与新鉴权语义。最终本地两父提交见本分支 `git log -1 --format=fuller` / `git rev-list --parents -n 1 HEAD`。
- 本轮仅授权本地合并、测试、记录与提交。没有 push、服务器连接、备份、部署、线上检查或 WSL 操作；也没有安装或更新依赖。

完成内容与入口：

- `backend/app/core/config.py`：两边配置合并，保留影子配置校验、会话天数校验及 Settings repr 脱敏。默认 `SHADOW_ENABLED=0`；测试补齐缺省、显式 0、显式 1 的行为。
- `backend/app/main.py`：两边初始化与路由注册冲突合并，同时保留影子运行时及认证存储的建立/关闭；影子路由和登录路由统一经中央鉴权守卫。
- `backend/app/core/security.py`：与部署分支完全一致，历史命名 `require_dashboard_password` 已委托新会话 Cookie / `X-Api-Token` 鉴权，旧密码头不再放行。生产缺配置拒绝访问；非生产显式绕过规则保持原样。
- `backend/tests/test_route_permissions.py`：动态检查所有 `/api/shadow*` 的路由和方法，固定保留三条现有 GET 接口，分别在影子开启/关闭时验证有效 Cookie、有效 Token 返回 200，缺少凭据、旧密码头、无效 Cookie / Token 返回 401。原 118 对端点基线、公开集合及逐方法断言保持。
- `test_shadow_api_runtime.py`、`test_shadow_review_regressions.py`：只迁移认证夹具，使用真实会话检查；保留业务、事务、单一只读连接及线程等原断言。
- `frontend/src/api/client.ts`：自动合并保留影子 API 方法，统一走 `sessionFetch` 的同源 Cookie；影子中文页面、菜单和路由保留。
- `docker-compose.yml`：固定 `172.18.0.0/24`、网关 `172.18.0.1`，与部署分支相同；前端 nginx 信任网关 `/32`、后端可信代理默认 `/24` 保留，已有登录配置契约测试通过。

业务规则：本轮未改影子账本计算、报价/DEX 精确身份、账本持久化或预算阈值。默认关闭；资金费、流动性、滑点仍为估计口径；`executable` 仍不代表账户可交易或下单授权。

验证结果：本机 Windows / Python 3.12.10，非锁定依赖 venv。影子相关、权限矩阵、全部 `test_login_2fa*.py` 合并定向运行：`183 passed, 2 deselected in 22.64s`；后端全量：`2586 passed, 8 deselected, 12 warnings in 155.34s (0:02:35)`；tsc 退出 0、无诊断；前端 Vitest 全量 `33 files / 427 passed`，耗时 234.28 秒。所有命令和原始汇总见[详细报告的本轮追加章节](shadow-ledger-report-2026-11-03.md#合入已上线部署分支与登录鉴权回归2026-11-05)。项目默认排除的两项影子 benchmark 已另行独立运行。

已知问题：压力性能基准两次均未通过，中位耗时分别 3.245 / 3.192 ms，超过原 3 ms 上限；第二次最大耗时 43.992 ms 也超出原 20 ms 预算。真实规模场景两次通过。相关 runtime/ledger/benchmark 源码与合并前一致，原因未定位；没有为通过测试放宽断言，也未扩展为性能优化任务。两次失败及首次鉴权夹具迁移失败均有完整记录，不能声称所有性能验收已通过。

工作区与产物：本轮只显式暂存合并修复、测试与文档；测试原始输出保存在被忽略的 `.pytest-tmp/merge-2fa-*.log`，临时数据库位于对应同名前缀目录，全部保留且不提交。既有 `.pytest-tmp` 内容和 `frontend/node_modules` 保留。没有提交未跟踪的测试输出文件。

下一步：本模块后续先复核压力性能预算及锁定依赖环境；本次没有生产开启授权。需要处理其他无依赖模块时另开任务，使用本交接与详细报告继续，避免扩大本次合并范围。
