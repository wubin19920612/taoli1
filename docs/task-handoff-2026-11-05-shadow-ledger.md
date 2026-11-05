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


## PM 第三轮停止交接（2026-11-05，覆盖上述下一步状态）

- 目标：执行第三轮预筛选和补充 8–11；先测试红灯，再实现，分步本地提交。基线 `b6c999f`，当前分支 `codex/shadow-ledger`；工作树不变。
- 第一提交 `824f771`：原任务书的两段未提交 PM 决策原样纳入，新增测试内全量/候选路径等价性反例、production 基准和运行前 CPU 负载。没有生产实现提交。
- **停止原因：第 11 条额外依赖已复现。** 默认 scorer 全局容量 `max_markets=6000`，同一分钟内不相关新市场的准入会占满名额，改变后来相关资产的 median_outlier，进而改变开仓行的 fake_score/fake_flag/fake_reasons。不是 frozen_leg 采样豁免。
- 复现入口：`backend/tests/test_shadow_prefilter_equivalence.py`。固定 seed 20261105，5997→6000 个无关市场→新相关资产 100/104/100；全量 fake_score=0，候选=2。容量 6 的最小反例也失败，低容量占用对照通过。失败用例保持严格断言，不加 xfail/skip。
- 关键代码：`fake_opportunity.py:104/149` 为容量和丢弃判断；`shadow_runtime.py:35/186/220` 为捕获、显示模型及评分/账本；本轮均未修改。
- 最终 production 基准：5500 机会/12000 市场/650 达门槛/750 对持仓，真实 bid/ask 构造；每轮 1/3 报价变化，含 +61 秒分钟全量轮，计入事件循环 p95/max。仅按第 5 条调整 stress 两个预算，其余原断言保留。
- 三次 production 全部失败：事件循环中位 28.149 / 124.785 / 34.048 ms，p95 149.625 / 158.780 / 167.282 ms，最大 162.866 / 172.807 / 171.254 ms；worker 墙钟中位 198.400 / 121.657 / 232.198 ms；RSS 70.539 / 70.820 / 70.273 MiB。全部场景、CPU 前负载、原始汇总、初版夹具历史和命令见详细报告。
- 影子+权限：2 failed, 111 passed, 3 deselected；两失败仅为新容量反例。全量原始汇总见下方。
- Windows Python 3.12.10 非锁定依赖 venv；默认 marker 排除 network/benchmark，基准已单独跑。没有修改既有测试排除或放宽断言。
- 未完成：预筛选生产实现、完整固定种子生命周期等价性序列、frozen_leg 60 秒偏差与 BMT 首轮回归、plateau 清除、混合显示/消费者/前端测试，以及优化后三次基准。因停止规则，不继续扩展实现。
- PM 必须先决定：12,000 市场的 PriceState 容量/准入/淘汰，以及容量满时精确评分等价性的规则。不能把 median_outlier 差异归入 frozen_leg 豁免；只提高容量不能证明动态市场集永不饱和。当前不选择任何实现方案。
- 线上：未检查、未变更。默认 SHADOW_ENABLED=0；未 push、未连接服务器、未备份部署、未运行 WSL。
- 产物：完整日志/临时数据库和报告生成辅助脚本保存在忽略的 `.pytest-tmp/round3-*`、`.pytest-tmp/write_round3_report.py`；保留既有文件，不提交测试输出。首个提交后工作区干净，最后文档提交后以 git status 复核。
- 下一步仍属于本模块：先取得 PM 对上述业务冲突的决策，再从严格红灯测试继续。其他模块另开任务。

后端全量本轮原始汇总（退出码 1）：

```text
2 failed, 2587 passed, 9 deselected, 12 warnings in 182.35s (0:03:02)
```
