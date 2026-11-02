# 删除不用功能：第二阶段 A 包（前端）任务书（2026-11-02）

依据：清单 `docs/remove-unused-inventory-2026-11-02.md`，已由 Claude 审核。清单文件在分支 `codex/remove-unused` 上，路径为 `C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/remove-unused/docs/remove-unused-inventory-2026-11-02.md`，只读参考。按其中“A. 前端入口与调用清理”一节实施，行号以基线为准，实施前重新定位。

本分支基于 `codex/settings-tabs`，设置页已经改成标签页，实盘实验面板及其 API、类型也已删除。你只需要核对是否还有残留，不要重复改写设置页。

## 范围
- **只改 `frontend/**`。** 后端由另一个分支删除。
- 删除 11 个页面及其专属代码：乌龙回测、资金研究、负基差埋伏、原油新闻、机会雷达、1s 采样、挤仓结构（含两个子视图和 css）、跨所价差、价差事件榜（含 `api/spreadEvents.ts`）、TradFi 价差、FS 观察（含 `FsMonitorPage.css`、`api/fsTypes.ts`）。范围包括页面、专属 api 函数、类型、样式和测试。
- AppShell 改动：
  - 删除这 11 页的 PageKey、lazy import 和导航项。
  - `pageFeatures` 只删 8 个退役 key，保留 `gate-twap`。
  - `OptionalFeature` 类型收窄为 `gate_twap`。
  - 旧的 navigation-order 里如果有已删页面，忽略即可；旧 URL 指向已删页面时，回退到“实时机会”，不能白屏。
- **必须保留：**
  - `querySymbolExchangeSpreads` / `SymbolSpread*` 类型（标的查询走势在用）；
  - `FundingOpportunityType`；
  - Astro/arb 目标选择、Gate 定时减仓页；
  - 共享组件：OpportunityTable、QuoteAgeTag、FloatingWatchPanel、RangeBacktestPanel、MarketIdentityPanel、AstroCardVariantSelector、useRadarStore、pageVisibility 等；
  - 报价年龄、窗口完整性、真实时间轴、区间回测估算这几项，它们挂在实时机会和价差查询上，不属于删除范围。
- CSS：按完整选择器删除专属前缀，包括媒体查询里的规则和逗号分组里的单项。不能整段删除响应式规则，不能伤到其他页面。特别注意：`pair-`、`instrument-`、`dashboard-`、公告、资金费率、指数成分、溢价指数、账户连接这些页面的样式，另有分支正在改，这里一律不要动。
- 测试：删除专属测试文件；共享测试（AppShell、FeatureFlags、SettingsPage 等）只删退役场景，不得放宽其余断言。补充测试：导航里没有这 11 页；旧 URL 和旧导航顺序不白屏；标的查询走势仍然调用 symbol-query。
- 不要 git push。

## 验收
在 `frontend` 目录运行：
1. `npx tsc -b`，不能有错误；
2. `npm test`，全部通过，贴出汇总行；
3. `npx vite build`，构建成功；
4. 最后在仓库根目录运行 `git diff --check`，不能有输出。

在当前分支提交，提交信息用 `refactor(frontend): remove retired feature pages`。报告需包括：删除的文件清单、保留的共享项、AppShell 兼容处理、每个测试改动的原因。
