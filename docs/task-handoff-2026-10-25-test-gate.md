# S9 测试离线化、依赖锁定与 CI 门禁交接

日期：2026-10-25。任务依据：`docs/s9-test-gate-spec-2026-10-25.md`。

## 范围、分支与基线

- 独立 worktree：`.worktrees/s9-test-gate`；分支：`codex/s9-test-gate`。
- 实现前 HEAD：`fb0f3c6a0df2241f185a6e65472a5ab3598cc39e`。
- 开始时唯一未跟踪文件为 Claude 提供的 `backend/requirements.lock`，已原样纳入本次提交。
- 仅修改任务书 §4 的允许文件；没有修改 `backend/app/**`、前端源码/测试、`deploy/`、compose 或任务书。两份修改的测试文件中，原有 887 条 `assert` 的 AST 与基线完全一致。
- 按用户要求只创建本地提交，不 push、不部署、不运行 WSL 或 Docker。交接与实现位于同一提交，以 `git log -1 --oneline` 查询提交号。

## 实现与入口

1. `backend/tests/conftest.py`：autouse 离线护栏默认拦截外部 socket `connect` / `connect_ex` / `create_connection` 及 HTTPX 同步/异步真实传输；允许 `localhost`、`127.0.0.1`、`::1`。保留 ASGI/MockTransport 测试能力及 Windows asyncio 的 loopback socketpair。应用吞掉首次异常时，teardown 仍报错。
2. `backend/pyproject.toml`：只添加 pytest 配置，注册 `network` marker，并默认 `-m "not network"`。显式 `-m network` 选择标记测试时退出离线护栏。本次没有给任何正式测试添加该标记，也没有跳过失败测试；全量无 deselected。
3. `backend/tests/test_api.py`：仅给五个卡片默认设置测试显式注入已确认的 `qualify_opportunity` 结果，包括 `fresh=True` 提交前复核及来自 seeded opportunity 的两腿盘口；保留真实 planner/service。补齐 `FakeExchangeClient.is_closed` 的关闭状态；provider 装配测试注入不启动业务 worker 的 fake，保留其 provider 结构断言。
4. `backend/tests/test_pair_spread_query.py`：四个参数化/窗口回退用例的 fake 补齐 `_fetch_open_interest_history`，返回空历史。本次外连来自新增持仓量历史补充查询，而非已被 fake 覆盖的 K 线路径。
5. `backend/requirements.lock`：Claude 从生产镜像 `sha-f38ced10051b9fa88f41c04ef5d329335b9111bb` 提供的 43 个精确版本，未改版本或文件内容。SHA256：`3fa1a8f3a4144d0ebe3646497bcca7c06aa6c2466c807b0b4b7a4c1b0aba4648`。
6. `backend/requirements-dev.lock`：固定本机验证所用的 `pytest==8.4.2`、`pytest-asyncio==0.25.3`、`ruff==0.16.8`。`pyproject.toml` 中运行及开发依赖的兼容范围均未改变。
7. `backend/Dockerfile`：复制生产锁，先 `pip install --no-cache-dir -r requirements.lock`，再 `pip install --no-cache-dir --no-deps .`。
8. `.github/workflows/build-production-images.yml`：新增 `test` job，使用 Python 3.12、两份锁和可编辑后端安装运行默认离线全量测试，再用 Node 22 执行 `npm ci`、`npm test`、`npm run build`。`build` 增加 `needs: test`。两 job checkout 并核对相同完整 SHA，push 与 workflow_dispatch 均受门禁约束；原触发分支、镜像标签、构建矩阵保持不变。

## 30 / 60 秒慢测试调查

调查用分批 `-x --durations=15`，并用 `-o faulthandler_timeout=10` 采样栈。

- 修复前，`test_account_connection_api_requires_nonempty_dashboard_password` 实测 **57.66 秒**，`test_astro_preview_endpoint_uses_saved_card_defaults` 实测 **18.47 秒**并返回 409。
- 两次超时栈均停在 **TestClient 的进入/启动阶段**：`test_account_connections.py` 的 `TestClient.__enter__` → `app/main.py:818` 的 `default_exchange_adapters()` → `app/services/collector.py:116` 构造 11 个 adapters → `app/exchanges/base.py:145` 的 `httpx.AsyncClient(...)` → HTTPX `_init_proxy_transport` / `_config.create_ssl_context` → Python `ssl.create_default_context` 的 CA 加载。
- 独立、无网络的客户端构造计时：本机一个默认 HTTPX 客户端构造了 **4 个** SSLContext；SSL 总计 **2.38 秒**，客户端初始化 **3.052 秒**，关闭仅 **0.0261 秒**。本机代理配置放大了每个客户端的证书解析次数；重复 lifespan 又放大到约 60 秒。
- 本次定位到的主要原因是测试中反复构造默认客户端、解析相同 CA，并非某个 `wait_for(..., 30)`、不响应取消的 fake 或客户端关闭等待。`app/main.py:1160` 设置 stop event，随后 supervisor 取消/收集任务；未发现本组耗时由该关闭路径固定等待 30 秒造成的证据。
- 测试侧处理：conftest 为 HTTPX 默认验证模式复用 session 内的 SSLContext，按 `trust_env`、`SSL_CERT_FILE`、`SSL_CERT_DIR` 区分缓存。仍启用证书验证；显式 verify/cert 参数走原工厂。没有更改 runtime 客户端、超时或 supervisor。
- 同样的 70 个账户/FS/路由/supervisor 测试全部通过，共 **68.59 秒**（包含收集/导入）。其中账户双 lifespan 用例 **2.84 秒**，shutdown 异常关闭用例 **0.40 秒**；路由锁恢复测试 **5.74 秒**，其 SQLite busy/恢复等待符合该测试目标。
- 全量最慢为子进程导入探针 **9.72 秒**、路由锁恢复 **5.58 秒**、10000 市场内存验收 **2.49 秒**；未再出现 30 / 60 秒测试。session fixture 启用前的模块导入仍会创建少量真实客户端对象，只初始化，不发起业务请求。
- 此结论来自本机 Windows 环境；没有据此认定 Linux 生产机具有同样初始化耗时。后续如需调查生产启停，可由 Claude 单独计时，本任务未改业务代码。

## 验证结果

本机 Python **3.12.10**、Node **24.14.0**、npm **11.9.0**；CI/Docker 对应 Python 3.12、Node 22。

| 检查 | 结果 |
| --- | --- |
| 任务书重点启动/Astro 用例，`-x --durations=15` | 6 passed，73.51 秒（含首次导入） |
| 四个历史查询错误用例，`-x --durations=5` | 4 passed，3.61 秒 |
| 四组慢测试，`-x --durations=15` | 70 passed，68.59 秒 |
| 最后一次后端全量 `cd backend; python -m pytest -q` | **1745 passed，0 failed / error，12 warnings，148.13 秒（2 分 28 秒）** |
| `npm ci --no-audit --no-fund` | 243 packages 安装成功，lockfile 未改 |
| `npm test` | **27 文件、266 tests 全通过，115.62 秒** |
| `npm run build` | TypeScript 与 Vite 均成功；Vite 阶段 26.78 秒 |
| `git diff --check`、TOML/YAML 解析、build needs 检查 | 通过 |
| 两测试文件原有 assert AST 对比 | 887 条保持一致 |
| conftest Ruff | 通过 |

全量命令通过进程环境 `PYTEST_ADDOPTS=--durations=15` 追加计时，未指定 opt-in 离线插件或额外 PYTHONPATH；执行主体为要求的 `python -m pytest -q`，只运行了一次最终全量。完整输出保留在本机临时目录 `%TEMP%/taoli1-s9-backend-full-2026-10-25.log`，未提交日志。

护栏负向验收：创建临时 `test_s9_offline_probe.py`，分别尝试 HTTPX 同步/异步、socket connect/connect_ex 和吞异常后的 create_connection。结果为预期的 **4 failed + 5 teardown errors**（吞异常场景 call passed，但 teardown error），退出码 1；随后删除文件。另用临时 smoke 测试确认 loopback HTTP/socket 可用、network marker 默认 deselect，显式 `-m network` 时可退出护栏（该 marker 验证不做实际外连）。两个临时文件均已删除。

## 未解决项与后续验收

- **Docker build 和镜像内 pip freeze 一致性未运行**，按用户安排由 Claude 验证；整个任务没有运行 WSL / Docker。
- 本机使用既有 Python 环境（例如 FastAPI 0.135.1、Pydantic 2.12.5、HTTPX 0.28.1），未向 Windows 安装含 Linux `uvloop` 的生产锁。**不能把本机通过等同于已验证生产锁版本环境**；该环境需通过 Linux CI 和 Claude 的 Docker 验收。
- **GitHub CI 尚未触发**，因为用户明确要求不 push。Claude 接续时推送本分支，以本提交完整 SHA 触发 workflow_dispatch，验证 test 成功后两个 build 矩阵作业才开始，且失败时 build 被阻断。
- 后端 12 条为 `datetime.utcnow()` 弃用警告；前端存在 React act / Ant Design 弃用提示，均未导致失败，前端文件未修改。
- `test_api.py` Ruff 的 I001、PIE807、RUF100、F841 四项在基线源码同样存在，本次未扩大范围修复；没有新增 Ruff 规则门禁。
- 线上没有备份、部署或健康检查操作，生产状态未作本次验收声明。
- 工作区原有的未跟踪生产锁已纳入提交；生成的 `node_modules`、`dist`、Python/pytest 缓存和 tsbuildinfo 均受已有忽略规则保护，不暂存、不清理。没有遗留临时测试或其他新增未跟踪文件。
- 后续生产启动性能或其他模块请另开任务；继续 S9 验收直接使用本交接和任务书即可。
