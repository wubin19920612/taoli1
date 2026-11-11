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
