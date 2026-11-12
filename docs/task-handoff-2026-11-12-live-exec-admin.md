# 实盘管理安全修复交接（2026-11-12）

## 范围、基线与提交

- 工作树：`taoli1/.worktrees/live-exec-admin`；分支：`codex/live-exec-admin`；起始 HEAD：`ff6cda86b9a5185c83079740f8d1cf629697b92f`。
- 本轮仅修复 [审查报告](live-exec-admin-review-2026-11-12.md) 的三项 P1，并恢复完整页面测试的覆盖范围。开始时唯一未跟踪文件是该报告，已原样先提交为 `8a4e876`，保留其原始重复正文。
- 测试提交：`c01c017`（反代、审计、配置冲突、页面测试恢复），`9f5c784`（交易范围和映射并发），`c7f1384`（测试 fixture、弹窗关闭可见性），`a409af7`（同版本冲突审计记录）。实现提交 `2d87c4c`；本文另作 docs 提交，不 amend。
- 用户明确限制本地交付：没有 push、SSH、WSL、修改 `.env`、备份线上数据库或部署；没有改锁文件、compose、执行器或其他模块。
- 上游并发结构只读核对 `../crossex-exec-r8` 提交 `6fe888400a981f07277016a42a41ad08777d1e05`。其中 management.py blob 为 `7766d9d4d494fe4c28790561fce2b5ef3cf0e0ca`，universe.py blob 为 `2e55ebe598b5f5c039395a2385019f490fc2cd3c`。未启动或修改来源工作树；只将本轮必需契约增补到本地 [接口契约](live-exec-contract-2026-11-11.md)，未复制来源其他轮次功能。

## 修复结果与代码入口

### P1-1：保留 8443 Host

- `frontend/nginx.conf` 两个 API location 的 `proxy_set_header Host` 改为 `$http_host`，保留浏览器原始 Host 的非标准端口。
- `deploy/nginx-https.conf.example` 已监听 `8443 ssl` 且使用 `$http_host`，无需修改；写法与审查报告记录的服务器外层代理一致。本轮禁止 SSH，因此没有声称重新读取过服务器现场配置。
- `require_admin_session` 继续严格比较 scheme、主机和有效端口；没有放宽来源校验，没有新增对 Forwarded/X-Forwarded-Host/X-Forwarded-Proto 的信任。Dockerfile 的 `--no-proxy-headers` 保留。
- `backend/tests/test_live_exec_deployment.py` 读取上述两份实际仓库配置及 Dockerfile，按 Host 指令传递公开 authority，再请求真实路由。正确 8443 来源通过，省略端口、错误端口、错误 scheme/host 拒绝，伪造转发头不能绕过。该测试是配置绑定的离线链路模拟，不是 nginx/Docker 的现场运行验收。

### P1-2：审计输出

- `backend/app/api/routes_live_exec.py` 使用独立 `taoli1.audit.live_exec` logger、INFO 级别和命名 stdout handler，`propagate=False` 避免重复输出；初始化不会重复添加同名 handler。无需依赖 root logger 的 INFO 配置。
- 审计仍包含会话哈希前 16 位、固定路由模板、已验证的参数/目标指纹与状态。保留已有脱敏：不记录 cookie、任何令牌、请求头、异常文本、任意目标字符串或上游错误正文。
- 新部署回归从 `backend/Dockerfile` 读取 Uvicorn CLI 参数，在新进程中仅替换为隔离 app factory、回环地址和空闲端口。保留其默认日志配置及 `--no-proxy-headers`；真实路由、会话验证及 httpx 模拟上游运行。直接读取 stdout，验证成功 200、输入拒绝 400、上游拒绝 403 各有且仅有一条审计，且没有合成秘密值。测试没有用 caplog 提高 logger 级别。
- `backend/tests/live_exec_uvicorn_fixture.py` 仅为上述子进程提供内存会话和模拟上游，不启动生产 lifespan 或任何执行器。独立进程验证前的红灯证据为 `0` 条审计记录（预期 `3`）。

### P1-3：确认快照与服务端条件

- `frontend/src/api/liveExec.ts` 的写类型强制携带预期字段；`LiveExecAdmin.tsx` 在打开对应确认框时保存值，提交不重新读取已被轮询替换的数据。
- 配置：顶层 `expected_version` 为非负整数，其余字段仍为非空部分更新。读到新版本时本地阻止旧确认；未轮询到的并发变更由上游原子比较并返回 409。
- 映射：`expected_status` 取 pending/confirmed/rejected；`expected_multiplier` 直接取 GET 新增的 multiplier 字符串。它是按精确 market_key 排序的两腿数量倍率比，不能用原生价格比 ratio、某一条腿的倍率或本地浮点除法替代。
- 交易范围：classify/exclude/unexclude 均携带 GET 新增的 status（`u1:` 加 64 位小写十六进制摘要），不从 category/excluded 推算；分类弹窗保留开窗时的状态。
- 后端新增这些字段到严格白名单并逐项校验；原 body 原样转发。三类冲突 HTTP 409 的正文及状态原样返回，不复制上游响应头，不包装为 HTTPException detail 或 available 信封。
- 前端收到任意 409 后显示“数据已被其他人修改，请刷新后重新确认”，关闭旧确认框、清除配置草稿，刷新四项管理数据；不自动重发。重新打开后使用新快照。旧服务缺失状态/倍率字段时保留读取展示，禁用相关写按钮。
- 新测试覆盖六类写操作、所有预期字段缺失/类型/白名单、原样 409、不重试，以及“50 → 30 确认时另一会话已收紧到 5”的轮询前竞态。交易范围三类及映射两类操作各覆盖轮询前变化和开窗后轮询变化；映射状态和倍率分别单独变化也覆盖。
- 来源第十轮契约随后补充：配置审计含 CONFIG_CONFLICT，version=current_version、old/new 为空，可能有多条同版本同时间记录。本轮同步消除以版本号作为唯一行键的冲突，使用版本/时间/列表序号区分；页面显示“版本冲突，未更新（预期 … / 当前 …）”，不把空 old/new 当成成功修改。回归覆盖同版本同时间的一条成功、两条冲突记录全部保留。

### 原页面测试范围恢复

- `frontend/tests/LiveExecPage.test.tsx` 不再 mock 掉 LiveExecAdmin，也不再通过 businessRequests 过滤总请求数。
- 保留原业务 5/30 秒频率、隐藏/恢复、卸载、初始隐藏、故障、迟到取消、分页和展示断言。首次全部 8 请求（原业务 4 + 管理 4）、5 秒全部 10、30 秒全部 26，管理四项分别计数；隐藏/卸载检查全部请求没有增长，恢复检查全部增加 8。
- 故障和迟到响应注入明确绑定 summary，防止挂载管理组件后“第一个请求”变为 config 而丢失原场景。共享管理假数据位于 `frontend/tests/liveExecAdminFixtures.ts`，没有屏蔽真实管理组件。
- 新冲突测试检查 antd 关闭弹窗的实际 `style.display=none`；全局 getComputedStyle 测试 stub 没有返回 display，因此不把保留但隐藏的 DOM 误判为仍打开。未改全局测试配置或其他页面测试。

## 验收结果

- 后端相关：`236 passed in 34.39s`，退出码 0；包含原读路由、管理路由及部署配置回归。
- 前端最终相关：`Test Files 2 passed (2)`，`Tests 47 passed (47)`，`Duration 22.81s`，退出码 0。
- 最终构建：`npm run build`（TypeScript + Vite）退出码 0，`built in 11.92s`。npm 更新通知不属于构建失败。
- 新/修改后端测试 Ruff 全通过；路由文件只保留基线已有的 `FURB162`（UTC ISO8601 的 `.replace("Z", "+00:00")`），未为此改动无关逻辑。`git diff --check` 通过。
- 后端全量：`3197 passed, 11 deselected, 12 warnings in 297.17s (0:04:57)`，退出码 0，一次通过。11 项为 pyproject.toml 原有 network/benchmark 标记排除，12 条为既有 datetime.utcnow 弃用警告；没有新跳过项或放宽断言。
- 前端全量：`Test Files 35 passed (35)`、`Tests 500 passed (500)`、`Duration 312.01s`，退出码 0。使用既有 npm test 脚本，仅限制 worker 数为 2，没有筛选或跳过用例；保留既有 antd 弃用和 React act 提示，没有修改全局测试配置。

复现命令（PowerShell）：

```powershell
# backend 目录
$env:PYTHONPATH='.'
python -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-security-full
# frontend 目录
npm.cmd test -- --maxWorkers=2 --minWorkers=2
npm.cmd run build
```

## 依赖、产物、线上状态与下一步

- 本机 Python 3.12.10、pytest 8.4.2；Uvicorn 0.41.0 / FastAPI 0.135.1 / Pydantic 2.12.5 / Starlette 0.52.1 / httpx 0.28.1。生产锁分别为 Uvicorn 0.54.0 / FastAPI 0.162.0 / Pydantic 2.14.1 / Starlette 1.10.0，httpx 一致；没有安装或升级依赖。本轮证明 Dockerfile 启动方式下的本机运行结果，不宣称完成生产锁定依赖验证。
- 日志和临时目录位于已忽略的 `.pytest-tmp/live-exec-security-*`，包含红灯、定向、全量、构建和依赖版本记录。既有 `.pytest-tmp`、output、node_modules、dist、tsbuildinfo 均保留，不提交。最终 Git 状态另行核对。
- 本轮没有推送、线上备份、部署或线上请求，线上版本不因本轮改变。三项修复本地交付不等于线上已恢复；真实 nginx 两层链路、生产锁定依赖、真实上游事务并发和有效凭证联调仍需后续验收。
- 下一阶段另开任务，从本交接继续；在明确授权联网/部署后，先确认 crossex-exec 已包含同一并发契约，再核对外层 8443 Host、使用锁定镜像验证审计 stdout、执行备份和上线检查。本轮没有新增交易授权。
