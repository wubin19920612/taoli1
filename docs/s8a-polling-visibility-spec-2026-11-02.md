# S8-A：浏览器标签页隐藏时暂停前端轮询（2026-11-02）

## 背景（只读调查，基于 ac6d8d9）
- AppShell 每次只渲染当前页（`<CurrentPage />`），所以“后台”指的是**浏览器标签页或窗口被隐藏**（`document.visibilityState === "hidden"`）。
- 已经会在隐藏时暂停的：`state/useRadarStore.ts`（总览）、`pages/InstrumentLookupPage.tsx`、`pages/SpreadEventsPage.tsx`。
- 隐藏时仍在轮询的（13 处）：
  | 文件 | 位置 | 周期 |
  | --- | --- | --- |
  | `components/FloatingWatchPanel.tsx` | 799 setTimeout 链 | 10s |
  | `pages/FsMonitorPage.tsx` | 180 load；181 是每秒更新倒计时的本地 tick | 5s / 1s |
  | `pages/GateTwapPage.tsx` | 333 | — |
  | `pages/IndexComponentChangesPage.tsx` | 650 | — |
  | `pages/NegativeBasisMonitorPage.tsx` | 594 | 8s |
  | `pages/OilNewsPage.tsx` | 259 | 60s |
  | `pages/OpportunityRadarPage.tsx` | 242 | 60s |
  | `pages/PairMonitorPage.tsx` | 5217 | 用户设置 |
  | `pages/PremiumIndexPage.tsx` | 1296 | 用户设置（分钟） |
  | `pages/SecondLevelSamplingPage.tsx` | 398 | — |
  | `pages/SqueezeArbitragePage.tsx` | 148 | 15s |
  | `pages/SymbolSpreadPage.tsx` | 516 | 用户设置 |
  | `pages/TradfiPerpMonitorPage.tsx` | 290 | — |
- 浏览器里没有声音或系统通知这类本地告警，暂停轮询不会漏掉告警（告警都在后端）。浮窗是独立的弹出窗口，可见性按它自己的窗口判断：浮窗可见时照常刷新。

## 要做的事
1. 新增 `frontend/src/utils/usePolling.ts`，提供 `usePolling(callback, intervalMs, { enabled, immediate, pauseWhenHidden = true })`：
   - 用 `setTimeout` 串行调度：上一次 callback（可以是 async）完成后，才开始等下一个间隔，不会叠加请求。callback 用 ref 保存最新的那个，周期改变时重新调度。
   - 页面隐藏时不发起新的调用，也不保留定时器；重新可见时立即调用一次，然后恢复周期。
   - `enabled=false` 或组件卸载时，清掉定时器和事件监听；卸载后不再调用 callback。
   - callback 抛出的异常不能打断后续调度。页面自己负责显示错误；hook 只把异常吞掉，不打印日志。
2. 把上表 13 处换成 `usePolling`，或者等价地接入可见性判断。要求：
   - 周期、首轮立即加载、手动刷新、用户自定义周期等现有行为都不变。
   - FsMonitorPage 每秒的本地倒计时 tick 不发请求，可以保留；隐藏时也可以一起停。
   - FloatingWatchPanel 保留串行刷新队列（`refreshQueue`），只加可见性判断。
3. `useRadarStore`、InstrumentLookup、SpreadEvents 已经有自己的实现，**不改**。

## 不改
- 不改导航分组（S8-B，另做），不改 AppShell、页面 key 和 URL。
- 不改后端，也不改任何接口的参数和周期。

## 验收
- `frontend/tests/usePolling.test.tsx`：
  - 用假定时器，加上 `Object.defineProperty(document, "visibilityState")` 和派发 `visibilitychange` 事件来测试；
  - 覆盖这些场景：串行不叠加；隐藏后不再调用；可见后立即调用并恢复周期；enabled 切换；卸载后不调用；callback 抛错后照常调度；改变周期后按新周期调度。
- 至少给 4 个页面加“隐藏 60 秒内没有请求、可见后立即请求一次”的测试：FsMonitor、NegativeBasis、PairMonitor、FloatingWatchPanel。
- 已有前端测试全部通过（`npm test`），`npx tsc --noEmit` 通过。

## 范围
- 可改：新增 `frontend/src/utils/usePolling.ts`；上表 13 个文件；对应的测试文件（新增或修改）；本任务书。
- 不可改：其他文件（包括 AppShell、useRadarStore、InstrumentLookupPage、SpreadEventsPage、后端）。

## 环境
- 工作树 `.worktrees/s8-polling` 里没有 `frontend/node_modules`。如果没有，就在 `frontend/` 下建一个目录联接（junction），指向主检出的 `C:\Users\wubin\Desktop\code\codex\taoli1\frontend\node_modules`，不要提交它，也不要运行 `npm install`。
- 在 `frontend/` 下运行：`npm test`、`npx tsc --noEmit`。
- 不要跑 WSL；不要 git push。

## 输出
- 提交号（提交信息用 `perf(frontend): pause polling while the tab is hidden`）、改动文件清单、红灯/绿灯记录、测试汇总行、tsc 结果。
