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

## 外部持仓接入追加（2026-11-12；存在上游字段阻塞，未完成整体验收）

### 范围、固定来源与当前阻塞

- 本轮模块为实盘页外部持仓管理；工作树、分支不变，起始 HEAD 为 `58581df57816d303266215cf097ac0a4df531410`，开始时工作区干净。用户明确限定本地修改、TDD 和独立 test/feat/docs 提交；不改 `.env`，不 push、SSH、WSL 或 amend。
- [接口契约](live-exec-contract-2026-11-11.md) 已完整复制自只读来源 `../crossex-exec-r8` 的 **`bfb28615dc4f6d3d385d6af86fc960faa0646afe`**。复制前后逐字节相等；契约 Git blob 为 `0a811f9f3d952164feaa187218e71a32dbfab68f`，SHA-256 为 `99c21274db3c937862ba641e76d32da062473f2a350ee446504796f0f069250c`；该来源 `management.py` blob 为 `0861206e4de0f63dfe60cea0d7fcf16ca2e26611`。docs 提交信息亦注明来源 SHA。
- **不能宣称需求 3 已全部完成。** 固定来源的 `management.py:116-119` 只返回 `status/contracts/income/flatten_status/projection`；`api_data.py:460-465` 的 summary 元数据白名单没有全户原始仓位、外仓或程序投影，`api_data.py:488-550` 的 summary 返回体也没有远端仓位或外仓浮盈亏。契约第九轮新增的 `account_snapshot` 只有时间/新鲜度，不含持仓内容。
- `engine.py:685-686` 保存的 `program_projection` 只含 `positions/upnl/upnl_by_position/external_positions/captured_at`，而 `upnl_by_position` 是剔除外仓后的程序值。第十一轮契约明确 projection 可能是旧值。归属或收益核实失败时，无法用它列出当前导致停开的未登记远端仓位，也无法据此确认某外仓现在多空均为空。
- 已向用户异步请求新增 summary/完整远端快照的字段约定或来源提交。没有以推测字段、程序持仓列表、旧投影或全户合计补造当前仓位；没有修改来源工作树。执行期间来源第十二轮推进到 `8046f3d`（实现 `0fe7941`）：income 追加原币现金/贡献、external_fx 与 valuation。这些是区间收益贡献的估值证据，不是各当前远端持仓/浮盈亏；`management.py`、`api_data.py` 相对 bfb2861 没有变更，故阻塞仍在。本地契约按用户指定继续固定 bfb2861，未来附加字段可透传，未猜测新的 USD 行字段。

### 已实现的接口、安全约束与界面基础

- `backend/app/api/routes_live_exec.py` 新增固定 GET `/api/live-exec/external-positions` 和 POST `/api/live-exec/external-positions/register|unregister`，仅透传到对应 `/api/v1/` 路径。GET 沿用既有读取鉴权和只读令牌；POST 复用 `require_admin_session`、严格 Origin/端口/CSRF 校验、独立 ADMIN 令牌、JSON 大小/重复键检查与 `admin_proxy`。没有鉴权旁路，没有引入执行器代码、数据库写入或真实账户访问。
- 写 body 必须恰好为 `identity/expected_status/reason`；identity 必须恰好包含 route/exchange_type/business/symbol/quote/dex 六项，并按 bfb2861 的原始 symbol 规则逐项一致。普通合约必须显式空 dex；Hyperliquid 的 io/xyz 等 DEX 不可互代，不按规范化 ticker 匹配。`expected_status` 严格为 `x1:` 加 64 位小写十六进制；reason 为 1–200 字符非空文字且无控制字符。远端持仓、活动订单、历史程序归属与目录有效性仍由上游裁决。
- 写接口沿用独立 stdout 审计 logger；只记录已验证身份的摘要、会话摘要、固定路由和状态，不记录原始身份、reason、cookie、令牌或上游正文。状态冲突和业务拒绝的 **409 状态及原始字节正文** 均原样返回，不透传上游响应头，不重试。
- `frontend/src/api/liveExec.ts` 增加对象型读取结构及 register/unregister 写类型，仍通过 `sessionFetch` 使用会话和 CSRF。完整身份、原始 `expected_status` 与 reason 原样提交。异常登记记录结构返回 invalid_json，不使页面崩溃；未来附加字段可保留，缺失值不假定为零。
- `useLiveExecAdmin.ts` 将外仓列为第五个管理读取，仍串行、30 秒轮询、隐藏/卸载取消。管理 hook 提升到 `LiveExecPage` 共用一次；原管理组件改为接收该实例，没有重复轮询。完整页面回归计数为首次 9、5 秒 11、30 秒 28，恢复增加 9；原四项业务、五项管理分别检查，没有 mock 掉管理组件或过滤总请求数。
- `LiveExternalPositions.tsx` 实现中文远端持仓表与确认组件：保留交易所/通道/原始合约/DEX/方向/原生数量，支持程序、已登记外仓及红色“未登记——导致停开”；原币浮盈亏与 USD 单独显示，USD/折算缺失为“—”。打开弹窗时复制精确身份和状态，二次确认要求说明，重复点击只发一次；409 关闭确认、清除说明、刷新管理数据，无自动重试。提示登记覆盖多空两个方向、永久禁单、取消仍保留禁单和需显式重建收益基线。
- **该组件的远端行和操作目前仅有合成数据测试，尚未有上游数据适配。** `LiveExecPage` 显式传入 `rows={null}`，实际页面展示“远端持仓数据不可用”和登记记录表，不暴露登记/取消按钮。登记记录不会被冒充为当前持仓；此处是待续接点，不是已完成的账户操作界面。
- 执行汇总现从已存在的 external-positions.flatten_status 分开显示“程序仓位已平 / 全账户已平”，并显示核实时间、说明是上次核实结果；缺失/null 显示“—”。没有假装 summary 已发布新字段，也不从 `halted` 或空数组推断全平。

### 本地提交与 TDD 证据

- `bd1465a`：后端测试，新增端点鉴权、CSRF、原始身份、白名单、日志脱敏、并发和业务 409。旧路由红灯 `136 failed, 178 deselected`；实现后管理接口 `314 passed`。
- `ae1d7f1`：前端 API 与完整页面轮询测试。API 旧实现 `4 failed / 5 passed`（对象被当数组、错误走 mappings 路径等）；新增轮询请求计数旧实现 2 项失败。
- `a4b4a4c`：确认组件 15 项测试。红灯为组件尚不存在的模块加载失败；实现后 15 项全部通过。覆盖多 DEX、缺失与零值、原始身份与方向、二次确认、取消、说明校验、冻结状态、409、不重试和单次在途请求。
- `ff7f049`：真实页面旧契约接入 3 项测试，先 3 项红灯后通过；核对两种全平状态分开、缺失/null 为“—”、旧投影不授予取消权限。
- `a378bab`：异常登记记录 4 项先红灯的补充测试。
- feat 提交为 `9325a97`；本文及契约为独立 docs 提交，其 hash 用 `git log -1 --format=%H -- docs/task-handoff-2026-11-12-live-exec-admin.md` 查询；全部提交可用 `git log 58581df..HEAD --oneline` 查询，没有 amend。提交时显式暂存本模块文件，禁用本次提交 hooks/GPG；Git 索引位于父仓库而申请写权限，自动审批通过，无自动审批拒绝。

### 验证与运行边界

- 后端相关：`372 passed in 63.26s`，退出码 0，包括 routes/admin/deployment 三套测试。
- 后端全量：`3333 passed, 11 deselected, 12 warnings in 342.16s (0:05:42)`，退出码 0。11 项为 pyproject.toml 原有 network/benchmark 排除，12 条为既有 datetime.utcnow 弃用提示；未新增跳过或放宽断言。
- 前端相关（响应结构补测前）：5 文件、74 项通过，40.13 秒。最终全量：`Test Files 38 passed (38)`、`Tests 531 passed (531)`、`Duration 376.08s`，退出码 0，包含随后新增的 4 项异常记录回归。使用 `npm.cmd test -- --maxWorkers=2 --minWorkers=2`，没有筛选或跳过用例；既有 antd 弃用和 React act 提示保留。
- 最终 `npm.cmd run build`（TypeScript + Vite）退出码 0，`built in 10.98s`。PowerShell 将 npm 更新通知标为 NativeCommandError 文本，但进程退出码为 0，构建实际成功；没有安装或更新 npm。
- 当前验证环境：Python 3.12.10、pytest 8.4.2、Uvicorn 0.41.0、FastAPI 0.135.1、Pydantic 2.12.5、Starlette 0.52.1、httpx 0.28.1，Node 24.14.0、npm 11.9.0。仍存在前文记录的本机与生产锁定依赖差异。
- 修改的后端测试 Ruff 无新增问题；路由只有基线既存 FURB162（UTC 时间的 `.replace("Z", "+00:00")`），未作无关修复；`git diff --check` 通过。
- 日志在已忽略的 `.pytest-tmp/live-exec-external-*.log`，红灯、相关、全量和构建日志均保留。未删除已有 `.pytest-tmp`、output、node_modules、dist、tsbuildinfo 等产物。没有新增依赖、修改锁文件或真实配置。
- 本轮仅有离线合成账户/令牌和本机依赖验收，未运行上游 Engine 或真实鉴权接口，未执行生产锁定依赖/浏览器现场验收；没有 push、SSH、WSL、线上备份、部署或线上请求。线上版本未变，本轮也未新增交易授权。

复现命令（分别在 backend / frontend 目录运行）：

```powershell
$env:PYTHONPATH='.'
python -m pytest tests/test_live_exec_routes.py tests/test_live_exec_admin.py tests/test_live_exec_deployment.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-external-related-rerun
python -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-external-full-rerun
# frontend
npm.cmd test -- --maxWorkers=2 --minWorkers=2
npm.cmd run build
```

### 继续本模块所需输入

提供上游追加读取字段的契约及固定提交后，继续本模块，不先扩展到其他模块：

1. 需要完整远端持仓快照（含未登记/不可核实仓位）、完整性/采集时间、每行原始六项身份和方向/原生数量，以及可核实的程序/外仓归属依据。缺失身份不得按 ticker 补齐。
2. 需要各外仓当前原币浮盈亏及币种；第十二轮 USD 及折算状态若尚未发布，保留“—”，不能以原币数值或静态 1:1 代填。
3. 约定在完整当前快照中证明某已登记合约多空均无仓位的方式，再为相应行提供取消登记；上游仍做活动订单与身份核验。没有完整快照时保持不可用。
4. 添加真实形状的页面适配与集成 TDD，连接 `LiveExternalPositions` 的行数据和操作，并在成功/409 后刷新当前远端与所有管理数据；保留已冻结的状态、身份，不自动重试。
5. 按用户要求重跑受影响测试、后端相关/全量（若后端变动）、前端 npm test/build，单独提交 test/feat/docs 并追加本交接。部署仍需另行授权，不沿用历史默认部署规则。

## 第十三轮远端持仓接通追加（2026-11-12）

### 本轮范围、基线与固定来源

- 模块仍是实盘页外部持仓管理。工作树 `taoli1/.worktrees/live-exec-admin`、分支 `codex/live-exec-admin`，起始 HEAD `314cce78aa23078965d04945e943abb6f14701bf`，起始工作区干净。前节因缺少完整远端快照而无法接通按钮的阻塞，本轮用正式只读端点解决。
- [接口契约](live-exec-contract-2026-11-11.md) 完整复制自只读来源 `../crossex-exec-r8` 的 **`7d63c7d3e334f905e45a6cf9939cb96e45b4ed9f`**；通过共享仓库的固定 Git 对象读取，未使用持续变化的工作区正文。复制后逐字节一致，Git blob 为 `bf9b9b8905c60b023d757abeaec08900cb20b7df`，SHA-256 为 `c8be9471bd569bb647e68f1399edb4cf430ff779ff51386d6bdae8f1138050f0`。docs 提交信息注明完整来源提交。
- 只读核对同一来源的 `tools/crossex_exec/api.py`、`management.py`、`api_data.py`、`remote_view.py`，对应 blob 依次为 `886ea7f2426dfb6ee93d34b5ee54bbd3dfab01e8`、`0861206e4de0f63dfe60cea0d7fcf16ca2e26611`、`4fd7e44e0ae617c8a1e31530a7180c9ae17ff0b2`、`ffa5f29917006f5c9b0db0e8b5584b3ac4496c16`。没有启动执行器、调用真实账户或修改来源工作树。
- 用户说明第十四轮仍进行中，将调整负债及程序风险口径。本轮没有复制其未定稿内容、猜测字段或在 taoli1 重新计算风险；删除界面中“外仓仍计入全账户风险”的固定断言，风险结果继续来自执行服务。新增未知字段保留透传，已展示的可选字段缺失/null 显示“—”，不代填 0。

### 实现入口与业务边界

- `backend/app/api/routes_live_exec.py` 新增固定 GET `/api/live-exec/remote-positions`，只转发至 `/api/v1/remote-positions`。复用既有路由读取鉴权、只读令牌、3 秒超时、禁止重定向及 `trust_env=False`；无查询参数/请求体，空查询串可用，非法查询/请求体沿用代理层 422，非 GET 为 405。响应设置 `Cache-Control: no-store`，不复制上游头或泄露上游错误正文。上游原生接口的 400 与 taoli1 代理的既有 422 不混为一谈。
- `frontend/src/api/liveExec.ts` 新增远端对象类型与运行时结构校验。外层 `{available:true,data:...}` 只表示代理读取成功，内层 `data.available=false` 仍表示尚无完整快照。错误对象、空数组冒充对象、错误字段类型均返回 `invalid_json`，不崩溃或伪造空仓；未来未知字段不会被剔除。
- `frontend/src/pages/liveExec/remotePositionView.ts` 适配全部远端行，保留精确通道、交易所、原始合约、DEX、方向、原生数量，以及 `program / external_registered / unregistered` 归属。身份未核实的原始行和 Bitget 行也展示；未登记显示红色“未登记——导致停开”。Bitget 不获得本轮外仓登记权限，普通合约必须有显式空 DEX，不按 ticker 合并或补身份。
- 数量、原币浮盈亏和上游保守 USD 下界保持十进制字符串原精度；没有前端浮点折算、USDC 1:1 假设或缺失补零。`valuation_verified=false` 不展示 USD 数值；公开现货下界和原生 USD 报告口径分开标示。账户快照过期与折算证明失效分别处理，不从折算成功推导账户新鲜。
- `LiveExecPage.tsx` 将真实快照接入 `LiveExternalPositions.tsx`，不再传固定 `rows={null}`。展示 UTC 快照时间、持久化版本、无快照/过期/完整新鲜状态；未来时间、超过 10 秒、上游 `stale=true`、缺失时间/版本都不能支持操作。页面每秒更新年龄，提交前还用当前时钟重新检查；组件快照参数为必需，运行时省略同样禁用操作。
- 登记要求当前非过期快照、原始六项身份符合原白名单、`identity_verified=true`、归属未登记、行时间/版本/方向/数量与快照一致，且登记表没有同一原始合约仍登记的相反证据。程序归属或不可核实身份不能登记。
- 登记记录单独保留，不伪装成当前远端持仓。只有完整非过期快照中该精确 `route + symbol` 两个方向均不存在，且记录仍登记时，才在登记记录表提供“取消登记”；即使同一原始合约行的 quote/DEX 无法核实，也会阻止取消。取消后的永久禁单墓碑不会被当成有效登记。
- `useLiveExecAdmin.ts` 将远端读取加入同一串行控制器：远端每 5 秒，原五项管理数据每 30 秒，统一定时器在 30 秒边界只读取远端一次。隐藏/卸载中止请求且忽略迟到结果；不增加独立的并行上游通道。完整页面请求计数为首次 10、5 秒 13、30 秒 35，恢复可见增加 10；原四项业务读取仍分别核对。
- 登记和取消继续走原写接口、会话、CSRF、独立 ADMIN 令牌与审计。二次确认显示精确六项身份和方向，保存打开时的身份副本及 `x1:...` 状态，提交不拿新轮询状态覆盖、不把 `snapshot_version` 当作乐观并发标记。打开后仓位/新鲜度失去前提时拒绝提交；成功或 409 后刷新全部六项管理读取。409 关闭旧弹窗、清空说明、提示刷新重确认，不自动重试；重复点击仍只允许一个在途写请求。
- 上游仍最终检查目录、历史程序归属、远端活动订单和并发事务。未改动既有写接口白名单、Origin/Host/端口检查、审计脱敏或 409 原样传递机制，没有新的管理豁免、订单入口或交易授权。

### TDD、复核与提交记录

- `50b1f3f`：后端读取与前端真实形状/页面集成测试。旧路由红灯 `19 failed, 365 passed`；前端首轮红灯 `30 failed, 36 passed`，覆盖对象读取、完整远端行、精确身份、浮盈亏、快照、取消前提、全量轮询计数及成功/409 刷新。
- `34436f2`：异常可选字段、打开确认后的状态/仓位变化回归；12 项异常类型测试先红灯。原交易范围测试改为在其真实卡片内断言“保守下界”，保留原断言含义，避免新增外仓文案造成全页面歧义。
- `ac88904`：复查发现并补测组件省略快照时默认放行的问题，定向先复现 1 项失败，随后移除兼容放行。旧组件测试显式提供快照，冻结身份测试增加原对象后续变更，继续验证不污染确认副本；追加原始合约含换行的拒绝回归。
- 中间实现还通过完整页面计数暴露了 30 秒时两个定时器重复读取远端的问题，已合并为一个调度器；没有放宽预期请求总数。第一轮前端全量开始后复查增加了缺快照回归，该运行加载的旧组件因此为 `1 failed, 582 passed`；它不作为最终验收，最终代码另起全量运行。
- 后端相关已通过 `390 passed in 72.29s`；后端全量 `3351 passed, 11 deselected, 12 warnings in 452.56s (0:07:32)`，均退出 0。11 项为原 `network/benchmark` 排除，12 条为既有 `datetime.utcnow` 弃用警告。
- 前端复查前相关 6 文件、129 项通过（60.09 秒）；最终快照前提收紧后，受影响两文件 47 项通过（19.57 秒）。最终前端全量、构建和 feat/docs 提交记录见本节末尾验收记录。
- `git diff --check` 通过；Ruff 只报告起始 `314cce7` 已存在的路由 `FURB162`、读取测试 `I001/FURB167`，已用基线 Git 对象复核无新增诊断。本轮不修无关基线风格问题。

### 环境、产物、线上边界和下一任务

- 本机验证版本：Python 3.12.10、pytest 8.4.2、Uvicorn 0.41.0、FastAPI 0.135.1、Pydantic 2.12.5、Starlette 0.52.1、httpx 0.28.1，Node 24.14.0、npm 11.9.0。未安装依赖或改动锁文件；与前文生产锁定依赖的差异仍存在。
- 日志保留在已忽略的 `.pytest-tmp/live-remote-*.log`，包括红灯、复查、相关/全量及构建；既有 `.pytest-tmp`、output、node_modules、dist、tsbuildinfo 等产物保留，不提交。启动时没有其他已跟踪修改或未跟踪文件，本轮只暂存明确列出的模块文件。
- 用户明确本地交付：不修改 `.env`，不 push、SSH、WSL 或 amend；没有线上数据库备份、部署或线上请求，线上版本未变。本轮只证明离线合成账户与本机依赖下的页面/接口行为，不宣称真实账户、生产锁定依赖或浏览器现场验收完成。
- 当前快照覆盖范围沿用上游 CrossEx positions 与 Bitget UTA USDT-FUTURES，不能据此宣称覆盖未订阅产品类别。客户端时钟错误会保守禁用操作；最终并发、订单和账户前提仍由上游执行器重新核验。
- 本模块交付后，下一模块另开任务。第十四轮定稿时先取得固定来源提交和字段含义，再同步契约及展示；真实鉴权联调或上线须获得对应授权后另行执行，不从本轮本地交付推导交易/部署授权。

### 最终验收记录

- 实现提交：**`2a1d50b7d95ca6e8f0d009bb4bf710918f39c642`**。测试独立为 `50b1f3f`、`34436f2`、`ac88904`；本交接和契约另作 docs 提交，没有 amend。可用 `git log 314cce7..HEAD --oneline` 查看本轮全部提交；docs 提交号用 `git log -1 --format=%H -- docs/task-handoff-2026-11-12-live-exec-admin.md` 查询。
- 后端相关 **390 passed**；后端全量 **3351 passed, 11 deselected, 12 warnings**；最终前端全量 **39 files / 584 tests passed**，`Duration 423.72s`，无筛选、无新增跳过，退出码 0。前端使用既有 `npm test`，仅设 worker 数为 2，保留既有 antd 弃用及 React act 提示。
- 最终 `npm.cmd run build`（TypeScript + Vite）退出码 **0**，`built in 10.33s`。PowerShell 的 npm 更新通知仍可能标作 `NativeCommandError` 文本，但进程成功；没有更新 npm。
- 最终证据文件：`.pytest-tmp/live-remote-backend-related.log`、`live-remote-backend-full.log`、`live-remote-frontend-full-final.log`、`live-remote-build-accepted.log`。前端最终全量重新启动于本轮实现冻结之后，覆盖缺快照拒绝和原始合约换行两项补充回归；中间运行不代替最终结果。
- 交付仅为本地提交，没有 push、线上备份、部署或真实账户验收。提交后检查工作区，不留下本轮未提交文件；已忽略的诊断与构建产物全部保留。

复现命令（分别在 backend / frontend 目录执行）：

```powershell
# backend
$env:PYTHONPATH='.'
python -m pytest tests/test_live_exec_routes.py tests/test_live_exec_admin.py tests/test_live_exec_deployment.py -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-remote-related-replay
python -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-remote-full-replay
# frontend
npm.cmd test -- --maxWorkers=2 --minWorkers=2
npm.cmd run build
```
