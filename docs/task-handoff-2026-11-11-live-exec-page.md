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

## 2026-11-12 追加：第八轮实盘管理

### 范围、基线和契约来源

- 本轮工作树为 `taoli1/.worktrees/live-exec-admin`，分支 `codex/live-exec-admin`；起始 HEAD `6dcf2f2fb8778781132372c22e7a76c0c277d38c`，工作区干净。仅实现实盘页管理功能及相关风险显示修正。
- 将 `../crossex-exec-r8/docs/live-exec-contract-2026-11-11.md` 原样覆盖到本工作树同名文件。来源分支 `codex/crossex-exec-r8`，来源提交 `b24f5aff42e9aac5e68eeae1345b3f3deb811ddc`；来源提交 blob、来源工作树文件及本地文件 blob 均为 `c64cc476a8670283bf0a5ad7d33f0642de803847`。来源工作树保持干净，没有修改或启动执行器。
- 来源任务书 `docs/crossex-exec-round8-2026-11-12.md` 的“taoli1 侧”和来源 `tools/crossex_exec/{api,management,settings,universe}.py` 均已只读核对；接口语义以最新契约第八轮为准。
- 交付复核时来源分支已由其他任务前进到 `33933b145e86b4f3fb238b6a87eff9bc96f77594`，工作区仍干净。对比 `b24f5af..33933b1`：契约、management/settings/universe 均无变化，api.py 只增加一行 Python 3.10 空查询解析的说明注释，行为未变；契约 blob 仍与本地一致。本轮不修改或合并其执行器锁顺序/发送恢复等其他模块改动。
- 契约 docs 提交 `80c13c4`；测试先行提交 `a851a62`，边界及夹具补充 `2158d07`、`bc6adab`；实现提交 `7955916`。最终交接为单独 docs 提交，可由 `git log -1 --format=%H -- docs/task-handoff-2026-11-11-live-exec-page.md` 查询。全部为独立本地提交，没有 amend。

### 代码入口与接口

| 入口 | 本轮职责 |
| --- | --- |
| `backend/app/api/routes_live_exec.py` | 四个新 GET、六类写操作、会话与来源校验、严格输入校验、管理令牌、中文错误、操作日志 |
| `backend/app/core/config.py` | 新增默认空的 `crossex_exec_admin_token`，读取 `CROSSEX_EXEC_ADMIN_TOKEN` |
| `frontend/src/api/liveExec.ts` | 新契约类型、管理读写请求、中文错误、同源会话和 CSRF 请求头 |
| `frontend/src/pages/liveExec/LiveExecAdmin.tsx` | 参数及新旧值确认、交易范围与人工分类、映射二次确认、参数审计 |
| `frontend/src/pages/liveExec/useLiveExecAdmin.ts` | 管理数据每 30 秒顺序刷新、隐藏/卸载取消、写后刷新、独立错误状态 |
| `frontend/src/pages/LiveExecPage.tsx` | 接入管理区、配置版本、六项第八轮放弃原因和实际账户回撤阈值 |
| `frontend/vite.config.ts` | 仅对 `/api/live-exec` 开发代理保留浏览器 Host，满足同源校验 |

- 页面仍为 `/?page=live-exec`，原汇总、持仓、成交和图表保持原入口；管理区显示在原页面下方。
- 新 GET：`/api/live-exec/config`、`config/audit`、`universe`、`mappings`，转发到对应 `/api/v1/` 路径。沿用原来的 `available/data/error` 信封及只读令牌，未配置管理令牌不影响读取。新 GET 不接受查询参数。
- 新写入：`PUT config`；`POST universe/{base}/exclude|unexclude|classify`；`POST mappings/{id}/confirm|reject`。仅生成这些固定路径，不提供通用 URL、方法或订单转发。base 为 1–40 位大写字母/数字；映射 id 为 24 位小写十六进制。
- 成功返回 `{"available":true,"data":<上游原样 JSON>}`。上游 4xx/5xx 保留 HTTP 状态，已知错误码翻译为对应中文；重定向及连接/非法响应为 502，超时为 504，不执行自动重试。超时提示先刷新核实是否已生效。

### 鉴权、CSRF、输入与审计

- 写路由额外调用现有 `require_session`，最终由 `AuthStore.authenticate` 核查登录/2FA 发放的会话。匿名、失效会话、仅 `DASHBOARD_API_TOKEN`、开发免登录开关均不能写。未改变全局登录、2FA 或原只读路由的鉴权规则。
- 项目原先没有通用 CSRF 机制。此处要求唯一 `Origin`、唯一 `X-Live-Exec-CSRF: 1`、唯一 Host；若带 `Sec-Fetch-Site`，必须为 `same-origin`。对 scheme、主机、有效端口作严格比较，拒绝缺失/空来源/跨源/重复头。不会信任任意 `X-Forwarded-Host` 或 `X-Forwarded-Proto`。
- 生产环境按既有 Secure 登录 cookie 和 HTTPS 部署要求，将公开来源解释为 `https://Host`。这是适配现有两层反代、后端 `--no-proxy-headers` 的显式约定：反代须保留外部 Host；非标准外部端口也必须保留。开发环境使用请求自身 scheme，Vite 的专用代理不改 Host。真实 HTTPS 反代部署尚未联调。
- 只发送后端的管理令牌，经 `X-Api-Token` 到 crossex-exec；不转发用户 cookie、浏览器令牌或 CSRF 头。管理令牌/服务地址缺失返回明确 503“未配置”，管理令牌与只读令牌相同也返回 503。所有请求保留 3 秒超时、禁重定向、禁环境代理；未改 `.env`。
- 写 body 必须为 `application/json`、单一明确 Content-Length、1–8192 字节，不接受 Transfer-Encoding。流式累计也检查实际大小；拒绝非法 UTF-8、重复 JSON 键、非对象、非有限数、未知字段和错误类型；超长返回 413，其余非法 body/参数返回 400，未知查询参数沿用 422。
- 参数共 22 项，与来源 `settings.py` 的键、下限、上限和 integer 标记逐项一致；布尔值、数字字符串、整数参数的 JSON 浮点数均拒绝，数值不钳制、不部分转发。分类只允许 category/markets/underlying_market；股票必选 US/KRX，非股票不得带标的市场，合约键不归一化且不得重复。目录归属、不可改为 crypto 的类别、映射新鲜度等仍由 crossex-exec 最终裁决。
- `live_exec_admin` 日志记录会话哈希前 16 位作为操作者、方法/路由模板、参数摘要、结果状态。现有登录只有共享管理员会话，没有用户名字段。参数更新记录已验证的数值；币种/映射目标和精确合约列表记录指纹及数量，避免任意字符串和秘密进入日志；已登录但非法 body/字段的尝试记录 `validation=rejected`。不记录 cookie、任何令牌、请求头、异常文本或上游错误体。
- 上游配置审计保持 `{version,at,source,old,new}`，页面显示版本、时间、来源、新旧值；taoli1 操作者追踪在本服务操作日志中，不伪造或新增上游审计字段。

### 页面行为和业务语义

- 参数表使用 GET config 的 schema 展示当前值、默认值、下限、上限及修改值；前端拦截无效/越界/非整数输入，提交前弹窗逐项显示旧值 → 新值，只发送修改项。轮询发现版本变化后拒绝旧弹窗提交；同一次操作在途期间阻止重复点击。最终以后端校验为准。
- 参数 fallback=true 时显示默认值回退告警并禁用提交。新开仓使用新版本；已有持仓保留开仓快照，账户级损失门槛按活动持仓取最严格值。没有解除停开/熔断的接口。
- 交易范围显示原始 `market_key`、交易所写法、目录/生效类别、标的市场、24 小时 USDT 报价成交额、采集时间、人工选择与类别资格。OKX 的基础币成交量 × 24 小时最低价明确标注为保守下界；null 显示“—”，不冒充零。
- 排除/取消排除均弹窗确认；人工分类弹窗要求类别和精确合约选择，股票必选 US/KRX。取消排除不会自动确认类别或映射；资格通过不表示全部开仓条件已通过。pre_ipo/commodity 不交易，已识别股票/禁止类别不能通过改成 crypto 绕过限制。
- 映射显示精确合约键、原生基础币写法、数量倍率、发现时原生价格比（按上游排序第一腿/第二腿）、采集时间与状态。确认/拒绝弹窗再次展示详情。契约只提供发现时快照，页面没有把它伪称为即时盘口；超过 300 秒的候选由上游拒绝，开仓前仍会以新鲜盘口复核。
- 参数审计最近最多 500 条，按上游从旧到新的顺序展示。管理四个读取按顺序发出，每 30 秒刷新；隐藏页面停止并取消，恢复立即刷新，迟到的已取消结果不会更新状态，卸载清理监听器和请求。
- 新管理区读取失败只清空相应区块，不清空原持仓/成交页；写错误保留在确认弹窗内，成功后刷新管理四项数据。网络不确定结果不会自动重发。
- 同步六个新放弃计数 `size_too_small/oi_missing/mapping_ratio_mismatch/volume_too_low/volume_unknown/category_unconfirmed`，兼容旧服务缺失/null。账户回撤优先使用 `effective_risk_limits.max_equity_drop_usd`，旧服务回退原 caps；另展示 `config_version`，明确名义额/持仓对数上限是代码硬上限。

### TDD、依赖与验收

- 首次实现前：后端新管理测试 `102 failed, 6 passed in 30.86s`；前端 `14 failed (14)`，失败对应尚未实现的管理功能。新增的拒绝审计及实际风险阈值测试也分别先失败，再实现。
- 后端最终定向：`185 passed in 36.61s`，含原路由 52 项和管理 133 项。覆盖六类写操作的会话限制、开发旁路、来源/重复头、所有参数边界、严格 JSON/大小、操作白名单、精确路径/令牌/请求体、未配置 503、错误状态/中文/脱敏、无自动重试及审计。
- 前端最终定向：`Test Files 2 passed (2)`、`Tests 32 passed (32)`、`Duration 19.15s`。包括确认/取消与新旧值、边界拦截、股票市场及精确合约选择、排除/取消、映射确认/拒绝、中文错误、旧版本弹窗拒绝、重复点击、可见性/取消、风险阈值与原页面回归。
- 最终 `npm run build`（TypeScript + Vite）退出码 0，`3088 modules transformed`、`built in 12.32s`。前端依赖使用本工作树 package-lock 执行 `npm ci --offline --no-audit --no-fund`，没有修改依赖清单/锁文件。
- 历史锁定 venv 不存在；后端仓库确有 `requirements.lock` 和 `requirements-dev.lock`，但离线 `pip install --dry-run --no-index` 无法找到锁定的 `aiohappyeyeballs==2.7.1`，运行锁还包含 Windows 不支持的 uvloop。本轮未安装/升级全局 Python 依赖，使用现有 Python 3.12.10、pytest 8.4.2；这是非锁定环境验收。FastAPI 0.135.1（锁 0.162.0）、Pydantic 2.12.5（锁 2.14.1）、Starlette 0.52.1（锁 1.10.0），httpx 0.28.1 与锁一致。完整差异保存在 `.pytest-tmp/live-exec-r8-dependency-versions.log`，离线检查在 `live-exec-r8-lock-check.log`。
- 只读 AST 比较已确认本地 `CONFIG_RULES` 与来源 22 个参数规则完全相同。`git diff --check` 通过；新后端测试 Ruff 通过。路由文件的 Ruff `FURB162` 是原有 UTC 时间解析表达式，未为此改动历史逻辑。
- 前端全量：`Test Files 35 passed (35)`、`Tests 485 passed (485)`、`Duration 422.88s`，退出码 0。仅将 worker 数限制为 2，没有筛选或排除测试。保留既有 antd 弃用/React act 警告；构建日志的 npm 更新提示不是构建错误。
- 后端首次全量：`1 failed, 3145 passed, 11 deselected, 12 warnings in 374.62s (0:06:14)`，退出码 1。唯一失败为 `test_shadow_shutdown_stats_freeze.py::test_terminate_failure_escalates_to_kill_with_threaded_joins[1]`：join timeout 实际约 `0.5149999999`，超过该用例的 0.5 秒断言，继而未清理进程状态。此用例与 `shadow_runtime.py` 均与起始基线完全相同，blob 分别为 `fdf3d0ce49f2ba7ee480ed93e75c635f5d96c199`、`7a00eb2caec26147566307d234dd6af9aa382a93`。没有修改其他模块或放宽断言。
- 该失败用例单独复测为 `1 passed in 3.34s`，退出码 0；这是与既有交接一致的时序波动证据，不据此声称已修复或确定根因。首次全量日志保留在 `.pytest-tmp/live-exec-r8-backend-full.log`，单项复测在 `live-exec-r8-shadow-recheck.log`。
- 后端第二次全量在前端全量结束后执行，使用同一 Python/依赖/测试命令，单独 basetemp 和 `live-exec-r8-backend-full-recheck.log`。最终原始汇总：`3146 passed, 11 deselected, 12 warnings in 319.54s (0:05:19)`，退出码 0。11 项 deselected 来自项目原有 network/benchmark 标记过滤，12 条警告为既有 `datetime.utcnow()` 弃用警告；没有新增跳过或排除项，也没有为了通过测试更改计时断言。

复现命令（PowerShell）：

```powershell
# backend 目录，使用本机 Python；默认排除项目原有 network/benchmark 标记
$env:PYTHONPATH='.'
python -m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-r8-full
# frontend 目录
npm test -- --maxWorkers=2 --minWorkers=2
npm run build
```

### 交付边界与下一步

- 按用户本轮限制不 push、不 SSH、不 WSL、不 amend、不改 `.env`；没有备份、部署、线上请求或真实交易操作。没有改 compose、生产部署脚本、数据库 schema、锁文件或来源执行器。本轮未配置管理令牌；代码默认写入关闭。
- `.pytest-tmp/live-exec-r8-*` 为本地验收日志和临时目录，`frontend/node_modules`、`dist`、tsbuildinfo 为忽略产物，不提交且保留。既有日志和其他未跟踪产物未删除。
- 最终交接提交前仅本交接文档未提交；七个实现文件、三个测试文件及契约文件均已分类型提交，没有其他待提交或新增未跟踪文件。提交后用 `git status --short` 核对工作区。
- 本轮证明的是离线代理/页面行为；尚未证明真实 crossex-exec 连通性、真实凭证、线上反代会话/CSRF 及生产锁定依赖下的行为。后续联调/部署请另开任务，从本交接继续；先确认最新上游版本，再配置独立管理令牌并完成已授权的备份、上线及真实页面验收。

## 2026-11-12 追加：管理安全审查修复

从 `codex/live-exec-admin` 的 `ff6cda8` 继续处理三项 P1：8443 Host 端口保留、独立 stdout 审计 logger、六类管理写操作的乐观并发。原页面测试已恢复完整管理组件和全部请求数断言。审查报告先独立提交为 `8a4e876`；测试、实现、交接继续分类型提交。

本轮完整基线、上游字段结构、代码入口、测试结果、日志路径和线上边界见 [2026-11-12 实盘管理安全修复交接](task-handoff-2026-11-12-live-exec-admin.md)。其中并发增补取代本文件前文关于空 `{}` 写请求、仅浏览器缓存版本比较及普通 INFO logger 的旧说明。按本轮用户限制不 push、不 SSH、不 WSL、不 amend、不改 `.env`，未部署；下一阶段请从新交接开启独立任务。
