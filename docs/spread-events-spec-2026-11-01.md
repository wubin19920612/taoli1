# 任务书：价差事件榜（taoli1，2026-11-01）

借鉴 zuudot 的 OppSpreadEvents：把全市场价差超阈的时段聚合成“事件”，每个事件有开始、结束、峰值、持续时长和中断原因，可以跳到配对页查看。

## 范围
- 新增：`backend/app/services/spread_events.py`、`backend/app/api/routes_spread_events.py`、`backend/app/models/spread_events.py`、`frontend/src/pages/SpreadEventsPage.tsx`、`frontend/src/api/spreadEvents.ts`，以及对应的 pytest / vitest 测试。
- 共用文件只允许改动：router 注册处（`main.py` 或现有 api 汇总处，只加 include 那几行）、前端路由和导航配置（加一个入口，放在“机会 / 价差”相关分组下）。
- 禁止改动：`runtime/loop_stall_watchdog.py`、`runtime/gc_pause.py`、`services/fs_public.py`、`services/fs_bybit.py`（另一个会话正在改）；`main.py` 中 router 注册以外的部分；`spread_engine.py`、`pair_spread_query.py`、`opportunity_radar.py`、`PairMonitorPage.tsx`（只读引用，不改）。
- 不要 git push。

## 数据源（先调查，写进报告）
- 用 `opportunity_history` 表（observed_at、opportunity_id、symbol、买卖两腿、open_spread_scaled、fee_adjusted_open_scaled……）。
- 先查清楚：①这张表由谁写入、采样间隔多少、保留多久；②生产环境 `DISABLED_FEATURES=squeeze,negative_basis,oil_news,opportunity_radar,symbol_spread,fat_finger,gate_twap,second_level_sampling,funding_research` 下它是否还在写入；③observed_at 上有没有索引；④scaled 的倍数常量。
- 如果②的结论是生产不写入：照常实现，但在报告第一行醒目写明，并给出改用哪张表的建议。

## 事件规则
- 按序列分组：同一 `opportunity_id`（若它不稳定，就用 symbol + 买腿交易所/市场类型 + 卖腿交易所/市场类型），按 observed_at 排序。
- 指标 `metric`：`fee_adjusted`（默认，取 fee_adjusted_open）或 `raw`（取 open_spread），单位为百分比。
- **开始**：样本值 ≥ `threshold`（默认 0.3%）。
- **结束**：出现第一个低于阈值的样本，end_reason=`below`；或者两个相邻样本的间隔超过 `max_gap`，end_reason=`gap`（行情中断），此时事件结束在缺口前的最后一个样本。max_gap 取该序列采样间隔中位数的 3 倍，且不小于 120 秒。查询区间结束时仍在阈值以上：end_reason=`ongoing`。
- 每个事件输出：series key、symbol、两腿、start_at、end_at、duration_s、peak 值、peak_at、mean、samples、end_reason。
- 过滤掉 duration 小于 `min_duration_s`（默认 0）的事件。按 `sort=peak|duration|start`（默认 peak）降序，取 `limit`（默认 100，最大 500）。

## 接口
`GET /api/spread-events?start_at&end_at&threshold&metric&min_duration_s&sort&limit&symbol?`
- 默认查最近 24 小时，最长 7 天，超出返回 422；参数非法返回 422；鉴权与现有 api 一致。
- SQL 必须带时间范围条件，读取行数上限 500000，超出时 `truncated=true`。
- 返回：`{events:[...], scanned_rows, series_count, truncated, params}`。

## 前端
- SpreadEventsPage：筛选栏（时间范围预设 1h/6h/24h/7d、阈值、指标、最短持续、排序、币种）；表格列：币种、两腿、开始时间、持续、峰值、均值、结束原因（用中文标签“回落 / 行情中断 / 进行中”）。
- 点一行跳到配对页并带上两条腿：先找现有的跳转方式（例如机会表跳配对页的 URL 参数），**复用它**，不要改 PairMonitorPage。
- 页面不可见时不轮询；只在手动刷新或改筛选时请求。

## 测试
- pytest 手工构造序列：一个完整事件（回落结束）、缺口中断、进行中、峰值和均值、两个序列互不干扰、min_duration 过滤、排序、7 天上限返回 422、truncated。
- 路由测试：鉴权、参数校验。
- vitest：表格渲染、结束原因的中文标签、点击行生成的跳转链接。
- Codex 沙箱里建不了锁定依赖的 venv：用本地环境跑 pytest 即可，Claude 会再用锁定依赖（FastAPI 0.162）复跑。路由测试不要依赖 `app.routes` 里有 APIRoute（0.162 会把 router 包一层）。

## 验收
后端 `PYTHONPATH=. python -m pytest tests -q`，前端 `npm test` 和 `npm run build`。在分支 `codex/spread-events` 上提交（conventional commit），最后输出报告：数据源调查四点结论、改动文件、测试汇总行。
