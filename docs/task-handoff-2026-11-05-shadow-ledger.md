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


## 第三轮补充二已实现；按第 17 条停止（2026-11-05）

本节取代前文“仅有测试、尚未实现预筛选、等待容量决策”的继续入口。

- 范围：影子账本生产预筛选、相关资产扩展、分钟分片历史、容量优先准入、混合列表发布；第 3/10 条组合等价性及前后端回归。无其他模块扩展。
- 基线 `929a3f1`，分支 `codex/shadow-ledger`，指定 worktree `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/shadow-ledger`。本地提交 `1c76a16` 为实现（含用户未提交的补充二），`c443c8b` 为分片/发布回归和基准计量；本交接和报告另有文档提交，见 git log。
- 入口：`backend/app/services/shadow_runtime.py` 的 submit / _select_markets / _capture_round / _finish_round；`fake_opportunity.py` 的优先准入与 history 参数；`test_shadow_prefilter_equivalence.py` 的生产 runtime 全量对照；`test_shadow_selection_runtime.py` 的所有权、准入、分片及混合 API/告警回归；前端 `OpportunityTable.test.tsx` 混合列表回归；benchmark 新增连续片段及子阶段计量。
- 规则：PriceState 上限 20000，相关资产不可被非相关市场挤掉；相关已准入市场全部参与 median。普通轮全市场 key 查找在 worker，分钟轮构建资产索引；每片最多 1000 来源市场并让出循环，新轮跳过计数。非相关对象原样发布，持仓 id/key 是不可变 worker 发布值。低于门槛的机会仍传清除 id，防止 plateau 历史残留。
- 允许差异：从未相关资产 changed 最多滞后 60 秒，分钟内往返/极值可能遗漏；分片时间差只用于 frozen 历史；同资产新市场最多晚 60 秒进入 median，精确开平仓腿从本轮查找直接取得。生产无全量路径开关。
- 行情引用依据：collector 从 pack/unpack_async 每轮重建模型，身份 annotate 返回 model_copy；发布 tuple 只读。相关数据仍冻结，原有 mutable-source 隔离测试未放宽。
- 测试：影子+权限 121 passed/3 deselected；后端全量 2597 passed/9 deselected/12 弃用警告；前端全量 33 文件/428 测试通过；npm.cmd run build（含 tsc）通过。Windows Python 3.12.10，venv=False，未安装依赖；不是锁定 venv 独立验收。
- 三次完整 benchmark 均 `2 failed, 1 passed`。production loop 中位 6.204/6.232/6.141 ms >5，首次分钟片段 70.711 ms >40；worker 墙钟中位 18.552/18.764/18.376 ms 和 RSS 28.105/27.969/27.168 MiB 达标。stress 最大 36.349/38.333/38.322 ms >20；realistic 全通过。详见报告末节九条原始汇总行与子阶段分布。
- 停止原因：第 17 条，全部实现后 production 仍不达标；第 16 条 RSS 未触发。未修改预算、未重跑挑选结果、未自行节流或引入独立进程。后续先由 PM 决定性能方案（是否允许固定周期），不得在无新决策时继续扩展优化或启用生产。
- 线上未检查。没有 push、服务器连接、备份、部署、WSL 或公网行情运行；SHADOW_ENABLED 默认 0。禁止范围（schema/repositories、采集调度、Astro/arb-mvp、opportunity_history）未改。
- 本次 `.pytest-tmp/round3b-*` 日志/SQLite 已忽略且未提交；此前 `.pytest-tmp/round3-*` 等产物保留。交付时无本任务未跟踪源文件，不清理其他产物。初始唯一用户 WIP 补充二已随首个提交纳入。
- 后续改动需保留全部断言，重跑相称回归与连续三次基准；独立只读审查/锁定环境验收尚未执行。无依赖的新模块另开任务。

## 第三轮补充三已完成（2026-11-05，第 18–20 条）

本节是当前继续入口。本轮基准要求由第 20 条覆盖为“仅一次 production，只记录、不因未达标停止”，不沿用上一节的三次运行交付门槛；性能预算数值和既有断言均保持。

- 范围：仅修复发布异常丢失持仓快照、身份刷新后进行中的影子任务恢复旧机会；不扩展性能优化。起始基线 `6051fe0`，分支 `codex/shadow-ledger`，工作树 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/shadow-ledger`。
- 本地分步提交：`5f1d4a3 fix(shadow): preserve held snapshots when publication fails`，包含初始唯一用户 WIP（任务书补充三原文）；`4d24716 fix(shadow): reject publication after opportunity version changes`。报告和本交接另作 conventional docs 提交，见 git log。
- H1 入口：`shadow_runtime.py::_finish_round`。先接收 worker 的不可变持仓 id/key，再隔离展示发布异常，继续更新 status 和耗时。开仓后发布失败、下一轮机会消失而盘口收敛时，两变体正常平仓。异常仍用原有限频日志和计数。
- H2 入口：`snapshot_store.py::set_opportunities` / `opportunities_version` / `set_opportunities_if_version`，以及 runtime 的 submit → 分片捕获 → finish。每次 setter 都推进版本，影子在首次 yield 前记录版本，发布前不同则放弃展示；锁内再次校验并经原 setter 写回，影子自己的发布也递增。status 新增累计 `skipped_publications`，账本、持仓快照和写库不受跳过展示影响。
- 新测试：`test_shadow_publication_regressions.py`。第 18 条先红 `1 failed` 后绿 `1 passed`；第 19 条先红 `3 failed, 1 deselected` 后与 H1 合跑 `4 passed`。真实 `_refresh_store` + `MarketValidationService` 分别在市场选择和 worker dispatch 阶段清空机会，断言旧任务不能回填，持仓继续处理；另外覆盖所有 setter 调用、重复/空列表、旧消费者返回行为和连续影子发布版本。
- 影子 + `test_route_permissions` + 快照消费者 + 身份路由：`154 passed, 3 deselected in 21.49s`。backend 全量：`2601 passed, 9 deselected, 12 warnings in 150.60s (0:02:30)`，警告均为 datetime.utcnow 弃用，没有线程未处理异常。默认 network/benchmark 排除未变。Python 3.12.10 / Windows，venv=False，未安装依赖；不是锁定 venv 验收。未改前端，本轮未重跑前端测试/构建。
- production 仅运行 1 次，原严格断言退出 1：`1 failed in 4.01s`。CPU 前负载 40%；loop 中位 5.761 ms >5，p95 8.223 ms，最大连续片段 7.135 ms（累计整轮最大 19.175 ms）；worker 墙钟中位 17.508 ms，RSS 28.574 MiB。报告已包含完整原始 SHADOW_BENCHMARK 行、pytest 汇总、命令和耗时分布。按第 20 条完成记录，未重跑、放宽断言或调整预算。
- 改动边界：生产仅 `shadow_runtime.py` / `snapshot_store.py`，测试仅新增回归文件，文档为任务书/报告/本交接。未改数据库 schema/repositories、采集调度、Astro/arb-mvp、opportunity_history、配置或基准文件。
- 线上状态未检查、未变更；没有 push、服务器连接、备份、部署或 WSL。默认 SHADOW_ENABLED=0。本地功能验证通过不代表生产性能通过，不开启生产。
- 产物：`.pytest-tmp/round3c-*` 的日志/临时 SQLite 已忽略且不提交；所有既有产物保留。没有本轮未跟踪源文件遗留，提交后用 git status 复核。
- 下一步：可从上述提交做独立只读审查；后续性能方案仍需 PM 决策，并另行取得锁定环境验收。不要自行节流、更改预算或启用生产。无依赖的新模块另开任务，本模块连续性使用本交接和详细报告末节。

## 第三轮补充五本地交付（2026-11-05，第 23、25 条）

本节取代前文的当前继续入口；第 24 条不改代码、不加补发。

- 范围：快照发布时绑定机会版本，避免旧快照入队后身份刷新、消费时恢复旧机会；按 PM 第 25 条调整 stress 两项预算。指定工作树和分支不变，起始基线 `778817288167e092b6f28b2cab2e35acce33d771`。初始唯一用户 WIP 是任务书补充五，已原样纳入。
- 分步本地提交：`9a64cbc test(shadow): reproduce stale queued snapshot publication`（先红及任务书）；`0019c3c fix(shadow): bind opportunity versions to published snapshots`；`8e38a59 test(shadow): apply approved stress timing budgets`。本交接与报告另作 docs 提交，见 git log。
- 入口：`snapshot_consumers.py::PublishedSnapshot.opportunities_version`、`collector.py::_publish_to_slots`、`shadow_runtime.py::submit/run_inline_observer`。两个发布入口在无 await 的构造段捕获版本；submit 不再读取当前 store 版本。条件发布失配只计 `skipped_publications`，账本和持仓状态照常处理。默认版本 None 保留其他消费者的旧构造方式，但影子不允许缺版本快照回写展示。
- 测试入口：`test_shadow_publication_regressions.py` 新增真实队列入队 → 真实 `_refresh_store` 清空 → 消费旧快照、无版本拒绝回写、inline 绑定版本；`test_snapshot_consumers.py` 检查版本不可变及历史消费者不变。既有 shadow 合成快照增加显式版本，旧断言全部保留。基准每轮断言没有弃发，保留真实发布开销。
- 预算：stress 去掉 CPU 中位断言但保留该指标，墙钟中位 ≤30 ms，事件循环最大 ≤50 ms；其他预算不变。第 24 条保持持续弃发，不补发；本机基准只防退化，开启判断看生产灰度。
- 验证原始汇总：先红 `1 failed, 4 deselected in 1.99s`；定向 `72 passed in 21.50s`；backend 全量 `2604 passed, 9 deselected, 12 warnings in 202.73s (0:03:22)`。警告均为既有 datetime.utcnow 弃用。Windows Python 3.12.10、venv=False，未安装依赖；默认 network/benchmark 排除不变。未改前端，未运行前端测试/构建。
- 三次完整基准：`3 passed in 7.51s`、`3 passed in 7.40s`、`2 failed, 1 passed in 10.05s`。第三次 stress 墙钟中位 32.856 >30、loop 中位 5.863 >5、p95 11.514 >10；production loop 中位 7.474 >7。realistic 三次通过，所有 RSS <30 MiB。第二次 production 连续片段 71.940 ms（整轮累计 87.942 ms）按现有连续片段 ≤80 ms 口径通过。全部九条原始 benchmark 行、CPU 前负载、汇总与耗时分项见详细报告末节。
- 当前结论：第 23、25 条实现及要求的验证/记录完成，但三次全部达标的性能门槛未通过；不重跑挑选、不继续放宽预算、不自行节流。下一步由 PM 决定性能方案；独立审查、锁定环境验收尚未做。无依赖的新模块另开任务。
- 线上未检查、未变更。未 push、未连接服务器、未运行 WSL，未备份/部署/灰度。默认 SHADOW_ENABLED=0；未改数据库、Astro/arb-mvp、机会历史写入、配置和身份刷新业务逻辑。
- 产物：`.pytest-tmp/round3e-*` 完整日志及临时数据库已忽略、未提交；既有产物保留。无新增未跟踪交付源文件；仅显式暂存本轮文件，最终以 git status 复核。
