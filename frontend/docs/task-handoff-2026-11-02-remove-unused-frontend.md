# 前端退役页面清理：报告与交接（2026-11-02）

## 目标、范围与基线

- 模块：不用功能删除第二阶段 A 包，按 `docs/remove-unused-frontend-spec-2026-11-02.md` 执行。
- 分支：`codex/remove-unused-frontend`；开始基线：`d0fe32b921d731e599fe9657ef2a87f4ec9ec5b6`。
- 开始时工作区和暂存区干净。本任务只修改 `frontend/**`，因此报告也放在 `frontend/docs/`。
- 本地提交消息：`refactor(frontend): remove retired feature pages`；按用户要求不 push，不部署、不连接生产、不操作数据库。
- `frontend/node_modules` 保留原有 Junction，未重新安装依赖、未删除或重建链接。

## 已完成内容与关键入口

- `src/components/AppShell.tsx` 删除 11 个退役页面的 PageKey、lazy import、菜单项和不再使用的图标；保留其他 10 个菜单项。
- `pageFeatures` 只删除 8 个退役 key，保留 `gate-twap: gate_twap`。`src/api/types.ts` 的 `OptionalFeature` 收窄为 `gate_twap`。
- 旧 navigation-order 经既有 normalize 过滤退役、未知和重复项，保留有效项的相对顺序并补齐保留页。
- 旧 URL 首次打开回退到实时机会；`popstate` 和 `taoli1:navigate` 也统一回退，避免在已经打开其他页面时保留错误页面状态。既有 `history` 到设置页告警历史标签的别名仍保留。
- `src/api/client.ts` 删除 59 个退役专属 API 函数，`src/api/types.ts` 删除 73 个专属类型。
- `src/styles.css` 删除 267 个退役专属选择器，逐项处理 12 个混合分组；没有删除完整媒体查询。通过 PostCSS 比对确认剩余 1,339 个选择器的顺序、声明和媒体条件一致；其中 1,081 条完全保留的规则逐字一致（忽略 Windows 文件换行）。
- 设置页、live-pilot API 和类型在基线已清理，本次核对后未重复修改；保留 SettingsPage 的“不请求 live-pilot”断言。

## 删除文件清单（27 个）

以下路径均相对于 `frontend/`。

| 文件 | 删除原因 |
|---|---|
| `src/pages/FatFingerBacktestPage.tsx` | 乌龙回测退役 |
| `src/pages/FundingResearchPage.tsx` | 资金研究退役 |
| `src/pages/NegativeBasisMonitorPage.tsx` | 负基差埋伏退役 |
| `src/pages/OilNewsPage.tsx` | 原油新闻退役 |
| `src/pages/OpportunityRadarPage.tsx` | 机会雷达退役 |
| `src/pages/SecondLevelSamplingPage.tsx` | 1s 采样退役 |
| `src/pages/SqueezeArbitragePage.tsx` | 挤仓结构观察退役 |
| `src/pages/SqueezeRoutesView.tsx` | 挤仓路由专属子视图 |
| `src/pages/SqueezePaperView.tsx` | 挤仓模拟专属子视图 |
| `src/pages/SqueezeArbitragePage.css` | 挤仓结构专属样式 |
| `src/pages/SymbolSpreadPage.tsx` | 独立跨所价差页面退役 |
| `src/pages/SpreadEventsPage.tsx` | 价差事件榜退役 |
| `src/api/spreadEvents.ts` | 事件榜专属 API、类型及 helper |
| `src/pages/TradfiPerpMonitorPage.tsx` | TradFi 价差退役 |
| `src/pages/FsMonitorPage.tsx` | FS 观察退役 |
| `src/pages/FsMonitorPage.css` | FS 观察专属样式 |
| `src/api/fsTypes.ts` | FS 观察专属类型 |
| `tests/FatFingerBacktestPage.test.tsx` | 只覆盖已删除的乌龙回测页面 |
| `tests/NegativeBasisMonitorPage.test.tsx` | 只覆盖已删除的负基差页面 |
| `tests/OilNewsPage.test.tsx` | 只覆盖已删除的原油新闻页面 |
| `tests/OpportunityRadarPage.test.tsx` | 只覆盖已删除的机会雷达页面 |
| `tests/SecondLevelSamplingPage.test.tsx` | 只覆盖已删除的采样页面 |
| `tests/SqueezeArbitragePage.test.tsx` | 只覆盖已删除的挤仓结构页面 |
| `tests/SqueezeRoutesView.test.tsx` | 只覆盖已删除的挤仓路由视图 |
| `tests/SqueezePaperView.test.tsx` | 只覆盖已删除的挤仓模拟视图 |
| `tests/SpreadEventsPage.test.tsx` | 只覆盖已删除的事件榜及专属 helper |
| `tests/FsMonitorPage.test.tsx` | 只覆盖已删除的 FS 观察页面 |

基线没有 FundingResearchPage、SymbolSpreadPage、TradfiPerpMonitorPage 的独立前端测试文件。

## 保留的共享项与业务规则

- `querySymbolExchangeSpreads`、`SymbolExchangePriceSnapshot` 和全部 `SymbolSpread*` 类型；`InstrumentLookupPage` 展开趋势继续请求 `/api/pair-spread/symbol-query` 并出图。
- `FundingOpportunityType`、资金费率套利 API、设置和页面。
- `CardTarget = astro | arb_paper | arb_live`、Astro/arb 目标选择、手动建卡、preadd；Gate 定时减仓页面、API 和功能开关。
- OpportunityTable、RiskTags、QuoteAgeTag、FloatingWatchPanel、RangeBacktestPanel、MarketIdentityPanel、AstroCardVariantSelector、useRadarStore、pageVisibility、chartTimeScale、queryLimits、fetchJson、buildUrl。
- 报价年龄、窗口完整性、真实时间轴、区间回测估算及其测试。
- `pair-`、`instrument-`、`dashboard-`、公告（含 `announcement-research-*`）、资金费率、指数成分、溢价指数、账户连接样式及媒体条件。
- 保留的 API 声明经 TypeScript AST 比对：client 的 114 个声明、types 的 166 个声明与基线逐字一致；不计明确收窄的 OptionalFeature。

## 每个共享测试改动的原因

| 文件 / 场景 | 改动与理由 |
|---|---|
| `tests/AppShell.test.tsx` 默认菜单、恢复默认、非法旧项 | 移除退役项的期望，以保留页替换旧展示项；继续精确检查完整顺序、数量、持久化、取消及关闭行为 |
| AppShell 拖动菜单 | 用保留的“标的查询”代替退役“资金研究”；保留拖到目标前后及保存顺序的精确断言 |
| AppShell 退役菜单与持久顺序 | 新增含全部 11 个退役 key、重复 key 和未知 key 的存储场景；同时检查侧栏、排序对话框、过滤后的完整顺序和 Dashboard 渲染 |
| AppShell 旧 URL | 对全部 11 页以及原有 minute-signals/new-listing 书签检查 Dashboard 渲染和菜单选中；对两类导航事件逐一从 Gate 页切入退役 URL，确认回退生效 |
| `tests/FeatureFlags.test.tsx` | 只删除 8 个退役开关场景及已删除模块的 mock；将原油页的共享加载、失败和待决请求用例迁到 Gate，保留禁用隐藏、直达拦截、不挂载、菜单排序和失败回退断言 |
| `tests/InstrumentLookupPage.test.tsx` 趋势 | 保留未展开不请求、展开请求并出图的原断言，增加精确端点和 BTCUSDT 参数检查，防止独立跨所页面删除误伤共享接口 |
| InstrumentLookup 建卡成功提示 | 全量测试观察到原 findAllByText 在第一处提示渲染时即返回，随后“两处提示”断言失败；改为 waitFor 等待原有 `toHaveLength(2)` 条件，未放宽数量、文案或请求断言 |

SettingsPage 和其他共享测试未修改；上表以外删除的 10 个专属测试逐文件列在删除清单中。

## 验证结果

- `npx tsc -b`：退出码 0，原始标准输出和错误输出均为空；此命令成功时没有汇总行。
- `npm test`：最终完整运行退出码 0，原始汇总如下。

```text
 Test Files  24 passed (24)
      Tests  352 passed (352)
   Start at  20:55:21
   Duration  95.56s (transform 3.66s, setup 207.55s, collect 132.64s, tests 250.37s, environment 74.72s, prepare 5.44s)
```

- `npx vite build`：退出码 0，原始关键汇总如下。

```text
✓ 3063 modules transformed.
✓ built in 10.15s
```

- 仓库根目录 `git diff --check`：无输出，退出码 0。
- 全量测试覆盖保留的资金费率套利/建卡目标、浮窗、区间回测、报价年龄、真实时间轴、Gate 开关、设置页和标的趋势；外部请求均由原测试 mock。本次不把测试通过等同于真实后端或生产验收。
- 测试输出仍有 React `act(...)` 及 Ant Design 弃用警告，未屏蔽这些提示，最终没有失败或未处理错误。

## 线上状态、产物和下一步

- 仅完成本地前端 A 包，未推送、未备份或部署线上；线上版本和实际后端接口未做验收。
- `frontend/dist/`、`frontend/tsconfig.tsbuildinfo` 为被 Git 忽略的验收产物；日志在系统 TEMP 中，不纳入提交。开始时没有其他未跟踪文件。
- 初次受限执行在共享依赖链接目标写入 Vitest/Vite 临时缓存时收到 EPERM；使用必要执行权限继续原命令。依赖和链接保持原状。
- 后续在新任务完成后端 B 包及配置/发布 C 包，继续参考原盘点；后端必须保留 symbol-query 并移除对退役 symbol_spread 开关的依赖，保留资金费率公式共享依赖、Astro/arb/Gate 和旧数据。发布前完成前后端联合验证及独立授权的备份、推送、部署流程。
