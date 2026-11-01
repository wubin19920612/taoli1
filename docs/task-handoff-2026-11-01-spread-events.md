**数据源调查：按当前源码，任务书给出的 DISABLED_FEATURES 列表不会关闭 opportunity_history 写入；前提是采集器运行且 HISTORY_ENABLED=true。本任务未连接生产库，未实测线上最新写入时间。**

# 价差事件榜交接与验收报告

## 目标、范围与基线

- 任务书：`docs/spread-events-spec-2026-11-01.md`，随实现一并提交。
- 分支：`codex/spread-events`。
- 起始提交：`e0320f4114628a5ad09cb2443b0df6db9890912e`。
- 实现历史超阈样本的事件聚合、受现有鉴权保护的查询接口、榜单筛选与精确配对跳转。
- 用户明确要求本地提交、不执行 `git push`。未部署、未执行生产备份或修改线上服务。

## 数据源调查四项结论

1. **写入者、频率和保留期。** `backend/app/main.py` 的采集器启动分支创建 `OpportunityHistoryRecorder(history_repo, app_settings.history_settings)`。`MarketCollector` 发布行情后在同步路径调用 `history_recorder.record(filtered_opportunities)`；后台发布模式通过 `SnapshotConsumer("history-recorder", ..., collector.record_history)` 写入。`services/history.py` 的 recorder 选样，`db/repositories.py` 的 `OpportunityHistoryRepository.insert_many` 批量插入。`core/config.py` 和 `models/settings.py` 默认每 **120 秒**采样，保留 **3 天**，均可由 `HISTORY_*` 配置覆盖；超过采样间隔后才会记录下一批，实际间隔也受采集耗时和快照消费影响。
2. **所给禁用列表的影响。** `opportunity_radar` 控制的是独立机会雷达及其告警引擎，不控制采集器的基础机会计算和历史 recorder。所给九个禁用项不会关闭 `history_repo`、采集器和 recorder 的构造或写入调用。`HISTORY_ENABLED=false`、采集器未运行、无符合选样条件的机会等情况仍可能导致没有新增。结论来自本分支源码和本地回归，未读取生产容器环境或生产数据库。
3. **时间索引。** `db/schema.py` 已创建独立的 `idx_opportunity_history_time ON opportunity_history(observed_at DESC)`；另有 `(symbol, observed_at DESC)`、`(opportunity_id, observed_at DESC)`、`(type, observed_at DESC)` 组合索引。本次未改 schema。查询保留裸 `observed_at` 时间范围条件，不用函数包裹索引列。
4. **scaled 倍数。** `db/repositories.py` 的 `PERCENT_SCALE = 10_000`；写入 `round(百分比值 * 10_000)`，读取除以该常量。例：`0.3%` 对应 `3000`，不是 `0.003`。新服务直接复用此常量。

额外发现：历史表不是全市场无筛选采样。默认仅保存原始开仓价差至少 `0.5%`、双方成交额满足最低 `100K USDT` 条件的前 **100 条**机会，且还受上游风险筛选影响。默认 `0.3%` 阈值下可能缺少回落点，未入选不代表价差已回落；榜单只能描述已记录样本。7 天查询许可也不会恢复已被默认 3 天保留期删除的数据。若后续要求完整市场事件，应在新任务中评估独立、连续、同时记录阈值上下样本的价差采样源。

## 实现与业务规则

- `GET /api/spread-events` 返回 `events / scanned_rows / series_count / truncated / params`，复用所有 API 的 `require_dashboard_password`。默认最近 24 小时，区间大于 0 且最多 7 天；非法时间、非有限数值、负阈值/持续时间、非法枚举或 limit 返回 422。数据库未初始化返回 503。
- 按稳定的 `opportunity_id` 分组。`spread_engine.opportunity_id` 对模式、规范化 symbol 和两腿的原始市场身份生成哈希；身份包含交易所、市场类型、原始 symbol、DEX、价格倍率和可选合约倍率，不含观察时间。本次只读调查，未改引擎。
- 阈值比较包含等号；仅超阈样本计入 `samples / peak / mean`。首个低阈样本关闭事件，`end_at` 为该样本时间；缺口严格大于 `max(120 秒, 3 × 该序列正采样间隔中位数)` 时，结束在缺口前最后一个样本，缺口优先于其后的回落判断。单点事件持续 0 秒，尾部超阈事件标为 ongoing，结束时间使用最后观察样本，不外推到查询截止时间。
- 时间条件同时限制起止；可按币种精确过滤。最多读取 500000 条历史行；恰好到上限时用带同样时间和币种条件的 `EXISTS ... OFFSET` 探测额外记录，不读取第 500001 条历史对象。超限返回 `truncated=true`，保留按时间从早到晚读取的前 500000 条。前端明确提示缩小范围。
- 聚合在工作线程执行，避免大量样本计算阻塞事件循环；排名使用最多保留 `limit` 个候选的堆，最终按 peak/duration/start 降序输出，最多 500 条。
- 页面地址：`?page=spread-events`，导航入口“价差事件榜”紧邻“价差查询”。提供 1h/6h/24h/7d、阈值、指标、最短持续、排序和币种筛选；币种输入按 Enter 或搜索图标应用。
- 只在初次打开、筛选应用或手动刷新时请求；隐藏页面不会发起查询，没有轮询定时器。卸载或参数变化时取消旧请求，防止旧响应覆盖当前结果。时间显示为 UTC+8，结束原因显示“回落 / 行情中断 / 进行中”。
- 复用 `OpportunityTable.openPairSpread` 现有 URL 协议：`page=pair-monitor`、`leg1_* / leg2_*`、`leg2_multiplier=1`、`hours=4`、`interval_minutes=5`，以及 `history.pushState` + `taoli1:navigate` 事件。没有修改 `PairMonitorPage` 或现有机会表。
- 两腿的原始 symbol 和 DEX 从历史行 `market_identity_json.buy_market / sell_market` 读取。Hyperliquid 历史样本缺少原始 symbol 时仍展示事件，并提示无法精确跳转，不猜测 DEX。普通市场在没有原始 symbol 时沿用现有机会页的 symbol 回退方式。跳转清除 URL 残留的旧腿身份和倍率。

## 改动文件与入口

新增：

- `backend/app/models/spread_events.py`：请求参数、事件、腿身份及响应模型。
- `backend/app/services/spread_events.py`：时间范围 SQL、读取上限、事件聚合、排名。
- `backend/app/api/routes_spread_events.py`：参数校验和查询接口。
- `backend/tests/test_spread_events.py`：27 项服务/真实 SQLite/ASGI 路由用例。
- `frontend/src/api/spreadEvents.ts`：类型、鉴权请求及已有配对 URL 协议。
- `frontend/src/pages/SpreadEventsPage.tsx`：筛选、榜单、错误/截断提示和配对导航。
- `frontend/tests/SpreadEventsPage.test.tsx`：8 项渲染/中文标签/导航/请求行为用例。
- `docs/spread-events-spec-2026-11-01.md`：原任务书，原样提交。
- `docs/task-handoff-2026-11-01-spread-events.md`：本报告。

共用文件的最小改动：

- `backend/app/main.py`：仅在 router 注册区域添加导入和注册项，共 3 行。
- `frontend/src/components/AppShell.tsx`：页面键、懒加载路由、导航入口，共 5 行。
- `frontend/tests/AppShell.test.tsx`：默认导航顺序夹具增加一个页面键，共 1 行。

任务书禁止的 runtime、FS、价差引擎、查询引擎和配对页文件均未改动。

## 验证结果

- 后端全量：`PYTHONPATH=. python -m pytest tests -q` → **2700 passed, 6 deselected, 12 warnings in 221.36s**。warnings 为已有 `datetime.utcnow()` 弃用提示。
- 聚合候选内存限制优化后，相关后端回归再次执行 → **27 passed in 15.27s**。
- 新增 Python 文件 Ruff 检查 → **All checks passed!**
- 前端全量：`npm test` → **33 passed (33) files; 370 passed (370) tests; 119.66s**。
- 页面专项：**8 passed (8)**；全量中也包含这些测试及导航回归。
- 最终前端构建：`npm run build` → **TypeScript 检查通过，Vite built in 8.74s**。
- `git diff --check` 通过。未变更依赖清单或锁文件。
- 后端使用本机 Python 3.12 / FastAPI **0.135.1** / Pydantic 2.12.5，未创建锁依赖 venv。依任务书，FastAPI **0.162** 锁依赖复跑交由 Claude；新增路由测试走 ASGI HTTP 请求，不依赖遍历 `app.routes` 中的 APIRoute。
- 前端现有测试仍有 React act / Ant Design 弃用等 stderr 输出，全量退出成功；未为消除既有日志修改无关模块。

## 线上状态、限制与下一步

- 本地实现与验证完成；按用户要求只创建本地 conventional commit，不推送、不部署。线上版本、数据库和服务未更改，本任务没有生产备份或线上接口验收结果。
- 工作区初始仅有本任务书未跟踪。新建的业务/测试/交接文件均属于本任务；`frontend/node_modules`、`frontend/dist`、测试日志 `output/spread-events-*.log` 为忽略产物，不提交。未回滚、覆盖或删除其他任务文件。
- 历史表缺少独立原始腿和倍率列，JSON 身份缺失时无法重建精确 Hyperliquid 市场；历史倍率也不能完整恢复。配对页是当前市场查询入口，不能视为对历史峰值、历史倍率或可成交性的重放证明。
- 本榜单不是交易执行或当前可成交结论；费率估算质量取决于历史采样，未补充盘口、手续费、成交额和资金费周期的实时核验。
- 下一步：Claude 在锁依赖环境复跑本报告命令，审核历史表筛选对事件完整性的影响；需要发布或改进采样源时另建任务。任何后续推送/生产部署仍需用户另行授权。
