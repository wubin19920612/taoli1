# T2-3 前端区间回测交接

- 范围：价差图拖选、回测请求及结果面板；所有修改均在 frontend 内。
- 分支：codex/zuudot-quote-age；实现前基线：5ce33af。
- 依据：根目录 docs/zuudot-borrow-design-2026-11-01.md 第三节；时间轴提交 9c2c25b；后端模型提交 5ce33af。
- 用户限定本地提交，不推送、不部署；交接文件也按 frontend 范围放置。

## 已完成与入口

- src/pages/PairMonitorPage.tsx：PairSpreadChart 使用 tOf 将绘图区横坐标换算为时间；显示半透明选区，鼠标左键松开且选区至少 8 个屏幕像素时回调。
- 单击、过窄选区、右键不触发回测；悬停仍显示最近采样点。支持反向拖选、绘图区外松开、Esc 和清除按钮。
- PairSpreadBacktest 连接图表与面板；配对身份或图表倍率变化时清除旧选区。
- src/api/client.ts：rangeBacktest；沿用现有认证与 ApiError，支持 AbortSignal。
- src/api/rangeBacktest.ts：请求、响应、结算、端点、缺口类型，与后端模型对齐。
- src/components/RangeBacktestPanel.tsx：展示价格盈亏、资金费逐笔与合计、手续费、点差成本、总收益、收益率、数据质量和警告。

## 业务规则

- 默认每腿名义金额 1000 美元，金额或方向改变后重新请求；当前页面买左卖右，对应默认做多左腿、做空右腿。
- 拖动过程中不请求；保留具体交易所、市场类型、DEX 与原始市场标识，不按 ticker 合并。
- 覆盖率来自 funding_pnl.coverage；小于 1 时黄色提示，null 保留未知或现货不适用。
- spread_cost_est 为 null 时明确总收益未扣除点差成本，不把未知显示为零。
- 收益率直接展示后端 total_pct_of_notional，以单腿名义金额为分母；结果明确标注估算。
- 清除、参数变化或卸载时取消旧请求，较早响应不会覆盖当前结果；相同字段的图表刷新不重复回测。
- 422 提示时间、30 天范围、金额及端点价格限制；网络和服务错误显示中文提示。

## 验证与状态

- frontend 下 npm test：Test Files 32 passed (32)；Tests 362 passed (362)。
- frontend 下 npm run build：TypeScript 与 Vite 构建通过，built in 12.31s。
- 新测试覆盖真实时间换算、反向拖选、缩放后的 8px 边界、过窄选区、取消选区、面板响应、警告、加载、错误、422、修改金额或方向、旧请求隔离和页面联动。
- 测试日志含 Ant Design / React act 警告；无测试失败。
- 未执行真实交易所接口或线上浏览器验收；本次未修改后端，未推送、备份或部署。
- 开始前已有未跟踪文件：根目录 .pytest_tmp_quoteage/、docs/zuudot-borrow-design-2026-11-01.md，保持原状且不纳入提交。
- 下一步如需线上验收或其他模块，请新建任务，并沿用项目备份、部署与验证流程。
