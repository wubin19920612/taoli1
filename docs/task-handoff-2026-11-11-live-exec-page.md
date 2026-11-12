# 实盘页面交接（2026-11-11）

## 目标、范围与基线

- 工作树：`taoli1/.worktrees/live-exec-page`；分支：`codex/live-exec-page`。
- 基线：`3f275eb`（任务书和接口契约）；唯一接口依据：`docs/live-exec-contract-2026-11-11.md`。
- 本模块只实现 taoli1 只读透传、实盘页面、离线契约测试；没有接入、修改或启动 crossex-exec，也没有下单能力。
- 按用户要求仅本地交付：不 push、不 SSH、不运行 WSL；未修改 compose、`.env`、部署脚本或依赖清单/锁文件。
- 开始时工作区干净，没有需避开的既有修改或未跟踪文件。

## 已完成功能与代码入口

| 入口 | 职责 |
| --- | --- |
| `backend/app/api/routes_live_exec.py` | 固定的 summary、positions、trades、stats GET 透传及参数校验、3 秒超时、错误降级 |
| `backend/app/core/config.py` | 读取 `CROSSEX_EXEC_URL`、`CROSSEX_EXEC_API_KEY`，均默认空 |
| `backend/app/main.py` | 在既有看板认证依赖下注册新路由 |
| `frontend/src/api/liveExec.ts` | 契约类型、同源会话请求、可用性响应 |
| `frontend/src/pages/LiveExecPage.tsx` | 中文汇总、风险/账户、持仓、平仓记录、分组统计和模拟对比 |
| `frontend/src/pages/liveExec/useLiveExec.ts` | 5/30 秒轮询、可见性与取消请求、历史分页和去重 |
| `frontend/src/pages/liveExec/ProfitHistogram.tsx` | lightweight-charts v5 净收益 bp 桶直方图与图表释放 |
| `frontend/src/components/AppShell.tsx` | “影子账本”之后的“实盘”菜单、懒加载、旧菜单顺序迁移 |

页面地址：`/?page=live-exec`。菜单首次加入时紧跟影子账本，用户已有的其他菜单相对顺序不变；之后可沿用现有菜单排序功能调整。

## 重要规则

- taoli1 的 `/api/live-exec/{summary,positions,trades,stats}` 仅映射到上游对应 `/api/v1/` 路径，不接受任意路径；匿名请求先由现有认证返回 401。
- `CROSSEX_EXEC_URL` 填服务根地址，不含 `/api/v1`、用户名密码、查询或片段；`CROSSEX_EXEC_API_KEY` 经请求头 `X-Api-Token` 发送，值应匹配上游 `CROSSEX_EXEC_API_TOKEN`。浏览器不会收到该配置或请求头。
- URL 或 Key 缺一时 HTTP 200 返回 `available:false, error:not_configured`。正常时 `available:true, data:<上游原样 JSON>`，保留 null 和原始市场字段。
- 不可达、超时、非 200、非法 JSON 均返回 HTTP 200 的不可用信封；不输出上游错误体、凭证或异常文本。重定向关闭，也不读取环境代理设置。
- `trades` 默认 `limit=200`，范围 1–500，`before` 需为 UTC ISO8601；`stats` 默认 7 天，范围 1–90。未知、重复或越界查询参数返回 422，不访问上游。
- summary/positions 每 5 秒，trades/stats 每 30 秒；隐藏时停止请求并取消在途请求，恢复可见立即刷新，卸载清理定时器、监听器和图表。迟到的取消结果被丢弃。
- 分页使用最早一条记录的 `closed_at` 作为 `before`；按 id 合并去重并保持倒序，刷新保留已加载历史；同一资源不并发请求。空页或游标不再前移时停止加载更多。
- 任一接口不可用时，显示中文“实盘服务未连接（原因）”，所有数据区块为空态；恢复成功后重新显示数据。
- 权益跌幅达到契约 `equity_drop_max_usd` 的 80% 时标红（默认契约 150 USD）；保证金倍数 null 显示“无保证金占用”；单腿状态/事件标红；平仓原因按任务书七项中文映射。通道费率保留四位百分数小数，避免把 0.048% 显示为 0.0%；上游有熔断原因时保留展示。
- 图表直接使用上游的 bp 桶和计数，横轴显示 bp 桶区间；不把记录时间当作收益分布。时间字段在界面按浏览器本地时区显示。

## 测试与提交

- `backend/tests/test_live_exec_routes.py`：从唯一契约文档读取四份 JSON 示例；覆盖认证、未配置、原样透传、合法参数边界、参数白名单、超时/不可达/非 200/重定向/非法 JSON，以及错误路径的凭证日志和响应检查。
- `frontend/tests/LiveExecPage.test.tsx`、`liveExecFixtures.ts`：假数据展示、null、不可用清空、5/30 秒频率、隐藏/恢复、卸载、迟到取消结果、分页与刷新互斥、空页终止、空记录后的新成交恢复。
- `frontend/tests/AppShell.test.tsx`：新增菜单位置、旧菜单迁移和懒加载；其余既有行为一并回归。
- test 提交：`4c27a8e`（初始契约和页面测试）、`f183dae`（认证夹具、取消/分页边界）、`b7f92da`（费率精度、保留熔断原因）。不 amend。
- feat 提交：`dae17d1`（主功能）、`5e60805`（费率精度、保留熔断原因）。本交接文档另作 docs 提交，提交号可由 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-live-exec-page.md` 查询。
- 后端首次全量原始汇总：`1 failed, 3002 passed, 11 deselected, 13 warnings in 288.02s (0:04:48)`。唯一失败是既有影子账本测试 `test_shadow_shutdown_stats_freeze.py::test_terminate_failure_escalates_to_kill_with_threaded_joins[1]`：线程 join 预算约 0.515 秒触发不超过 0.5 秒的断言；其生产代码和测试文件与基线 blob 完全相同，未修改。本次实盘 42 项测试全部通过。
- 失败用例单独重跑原始汇总：`1 passed in 3.60s`。因此记录为一次时序波动，不据此声称稳定根因；首次日志保存在 `.pytest-tmp/live-exec-full-first.log`，单项复测日志为 `live-exec-shadow-recheck.log`。
- 前端全量结束后，按任务书同一 venv、环境及完整命令再次跑后端全量，退出码 0，最终原始汇总：`3003 passed, 11 deselected, 13 warnings in 258.78s (0:04:18)`。13 条警告为既有 Starlette/httpx、datetime.utcnow 弃用警告；11 项 deselected 来自 pyproject.toml 已有的 network/benchmark 标记过滤，没有新增跳过或排除项。
- 前端全量原始汇总：`Test Files  34 passed (34)`；`Tests  462 passed (462)`；`Duration  377.87s`。只用了已有 `npm test` 命令并限制 worker 数，没有排除测试。
- 最后两处页面显示修正后重跑相关套件：`Test Files  2 passed (2)`；`Tests  40 passed (40)`；`Duration  39.77s`。其中实盘页面 10 项、AppShell 30 项；最终定向日志为 `.pytest-tmp/live-exec-frontend-final-focused.log`。
- 前端 `npm run build`（TypeScript + Vite）已通过；最终显示修正后原始构建汇总：`✓ built in 14.84s`，退出码 0，包含独立的 LiveExecPage 懒加载产物。前端测试保留现有 antd 弃用及 React act 警告，未因此修改其他页面或全局测试配置。

复现后端全量（Windows PowerShell，在 backend 目录）：

```powershell
$env:PYTHONPATH='.'
& 'C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe' -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-full
```

前端在 frontend 目录：`npm run build`；既有测试命令 `npm test -- --maxWorkers=2 --minWorkers=2`，只限制并发量，不筛选用例。本次在 frontend 缺少 node_modules 时执行了用户允许的 `npm ci --no-audit --no-fund`。

## 线上状态、产物与下一步

- 本次没有推送、备份、部署或线上验证，线上版本不因本次任务改变；验收证据仅为本地假数据/契约测试和生产构建。
- 本地验证日志位于已忽略的 `.pytest-tmp/live-exec-full.log`、`live-exec-frontend-full.log`、`live-exec-frontend-build.log`；测试临时目录、node_modules、dist 和 tsbuildinfo 不提交。交付后没有应提交的未跟踪产物。
- 真实上游的服务可达性、凭证配置、数据质量和运行时表现尚未联调；上游尚在另一个分支开发，本任务不以它为验收依赖。
- 下一模块应另开任务，先核对 crossex-exec 是否已满足本契约，再安排明确授权的联调和部署；沿用现有上线备份、版本核对流程。

## 2026-11-12 追加：同步第六、七轮数据契约

### 范围、基线和提交

- 本次仍只处理“实盘”只读页面模块；分支 `codex/live-exec-page`，起始 HEAD `49328ade5c9e27989c5094892068257d4e533cd2`，起始工作区干净。
- 从另一个 worktree `../crossex-exec/docs/live-exec-contract-2026-11-11.md` 原样复制覆盖契约。来源提交 `1647b12f66426db7a8dbc02fc5c854e74a90826f`；来源提交、来源文件与本地文件的 Git blob 均为 `fefda566ce9474bccdb60fa6a78a0c29bc65e201`。没有改动来源 worktree。
- 独立契约提交：`92ffb17`，提交说明注明来源 `1647b12`。
- 先补测试提交：`2f4dbba`；实现提交：`347d658`。没有 amend。

### 功能和业务语义

- `frontend/src/api/liveExec.ts`：summary 的三个组合计数和 `opening_skip_counts` 可缺失或为 null，原因计数允许部分键缺失；持仓和成交共用五个可选的快照/盘口时间差字段类型。
- `frontend/src/pages/LiveExecPage.tsx`：当前持仓及已平仓记录分别展示 `baseline_bps`（开仓基准）、`excess_bps`（开仓超额价差）、`funding_cost_bps`（预估两小时资金费成本）、`open_book_skew_ms`（开仓盘口时间差）、`close_book_skew_ms`（平仓盘口时间差）；单位为 bp 或毫秒，扩大表格横向滚动宽度。
- 前三项是开仓复核快照，超额价差尚未扣除费用，资金费成本是两小时预估，不替代实际 `funding_usd`、净损益或成交价差；基准负值原样显示。盘口时间差不是订单发送或成交时间差，平仓值可能远超 300 毫秒；没有本地重算或历史回填。
- summary 显示 `baseline_ready_pairs`（基准预热完成数）、`structural_spread_pairs`（结构性拉黑数）、`cooldown_pairs`（冷却数）；说明按基础币和无方向交易所组合去重，三项可重叠，不表示可立即开仓。
- 新增“累计放弃开仓原因”区块，展示契约全部六项，每项附一句中文解释：

| 字段 | 中文标签 | 解释 |
| --- | --- | --- |
| `book_skew` | 盘口不同步 | 两腿盘口时间差超过开仓允许范围。 |
| `book_stale` | 盘口陈旧 | 盘口时间信息缺失、无效或已过期。 |
| `funding_diff` | 资金费率检查未通过 | 资金费率数据无效，或归一化后的费率、费率差超限。 |
| `baseline_not_flat` | 基准不平坦 | 历史基准偏移或波动过大，未通过平坦性检查。 |
| `not_single_venue_outlier` | 非单一交易所异常 | 价格偏离未能归因于单一交易所，未通过异常模式检查。 |
| `too_few_venues` | 交易所数量不足 | 有效且及时的参考交易所不足，无法判断单所异常。 |

- 放弃计数为自第七轮启用后数据库内累计机会数（含 shadow），跨日及重启保留，按机会去重；不回填升级前历史，多条件失败只计首先判定的原因。页面不把它当作今日统计或成交笔数。
- 所有新增字段缺失或 null 显示“—”；真实 0 显示为 0（bp 保留两位），不将未知冒充为零。兼容整个 `opening_skip_counts` 缺失/null，以及其中部分原因键缺失/null。
- 已检查 `backend/app/api/routes_live_exec.py`：仅请求参数有白名单，响应通过 `json.loads` 原样透传，没有响应字段白名单，不需要修改生产后端代码；仍只有原来的四个 GET 路由，没有新增写接口。

### TDD 与本地验证

- 后端测试 `backend/tests/test_live_exec_routes.py` 新增 10 项：显式固定契约新增键、保留原样透传的零值/null/缺失字段；原有四个契约 JSON 示例透传测试继续适用。
- 前端 `frontend/tests/LiveExecPage.test.tsx` 新增 5 项，配合 `liveExecFixtures.ts` 覆盖持仓/成交的独立字段映射、负基准、零成本和零时间差、平仓 2300 毫秒、六种原因与中文说明、三项计数为零、旧版缺失/null 和部分原因缺失，且不改变实际净利润或订单时间差。
- 实现前先运行测试：前端原始汇总 `Test Files  1 failed (1)`、`Tests  5 failed | 10 passed (15)`、`Duration  26.89s`，五项失败均来自新增展示尚未实现。后端已有透传实现，定向测试原始汇总 `52 passed, 2972 deselected in 36.80s`，退出码 0。
- 实现后前端定向原始汇总：`Test Files  1 passed (1)`、`Tests  15 passed (15)`、`Duration  21.41s`，退出码 0。
- `npm run build`（`tsc -b && vite build`）通过，原始汇总 `✓ built in 12.34s`，退出码 0。
- 后端全量原始汇总：`3013 passed, 11 deselected, 12 warnings in 325.94s (0:05:25)`，退出码 0。11 项 deselected 来自 pyproject.toml 原有 network/benchmark 标记过滤，没有新增跳过或排除项；12 条警告为既有 `datetime.utcnow()` 弃用警告。复现：在 backend 目录设置 `$env:PYTHONPATH='.'`，执行 `python -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-round7-full`。
- 前端全量通过，原始汇总：`Test Files  34 passed (34)`、`Tests  468 passed (468)`、`Duration  335.22s`，退出码 0。在 frontend 目录运行已有脚本 `npm test -- --maxWorkers=2 --minWorkers=2`，仅限制 worker 数，没有筛选或排除用例；保留既有 antd 弃用与 React act 警告，没有为此修改其他页面或全局测试配置。
- 历史记录所列的锁定 venv 已不存在。本轮使用本机 `C:/Users/wubin/AppData/Local/Programs/Python/Python312/python.exe`（Python 3.12、pytest 8.4.2），没有安装或修改依赖。定向命令在仓库根目录执行：`PYTHONPATH=backend python -m pytest backend/tests -q -k live_exec -p no:cacheprovider --basetemp=.pytest-tmp/live-exec-round7-focused`（PowerShell 用 `$env:PYTHONPATH='backend'` 设置环境）。

### 交付边界、产物和下一步

- 按本次要求只做本地提交；没有 push、SSH、WSL、备份、部署或线上验证，没有修改 `.env`、compose、部署脚本、依赖清单/锁文件或其他模块。
- 本次验证日志新增于已忽略的 `.pytest-tmp/live-exec-round7-*`，保留旧轮次日志及产物；测试临时目录、node_modules、dist 和 tsbuildinfo 不提交。
- 交接提交前已复核 diff、契约 blob、改动范围和工作区；只有本任务的七个 live-exec 文件发生变化，没有应提交的未跟踪文件。最终交接提交号可用 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-live-exec-page.md` 查询。
- 本轮仍以离线契约和假数据验收；真实上游可达性、配置及真实数据联调尚未验证，线上版本未因本次任务改变。下一个模块请另开任务，明确授权后再安排 crossex-exec 联调与部署。
