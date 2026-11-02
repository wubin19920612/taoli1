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

## v2 方案（Claude，2026-11-02）：改为最小侵入
第一版（分支 codex/s8-polling，f18bb8b..d0e2a47）把 13 处轮询都改成了统一的 `usePolling` hook。三轮只读审查每次都查出新的回归，原因是各页面原来的 effect 里有不少细节：依赖变化时重置周期、手动刷新后重新计时、排队刷新等，hook 很难一一还原。这一版放弃统一 hook，改为**保留各页面原有的 effect 和定时器结构不动**，只加可见性判断：
1. 新增 `frontend/src/utils/pageVisibility.ts`，只导出两个函数：
   - `isPageHidden(): boolean`
   - `onPageVisible(callback): () => void`：注册一个回调，页面从隐藏变为可见时触发；返回值用于注销。
2. 13 处轮询，每处只做两个改动：
   - 在定时器回调开头加 `if (isPageHidden()) return;`。这样隐藏时只跳过发请求，定时器、周期、依赖重置的逻辑都和原来一样。
   - 在同一个 effect 里注册 `onPageVisible(() => <与定时器回调相同的刷新调用>)`，并在 cleanup 里注销，让页面恢复可见时立即刷新一次。如果这个 effect 在 enabled 或暂停之类的条件下不会启动定时器，就也不注册这个回调，和原来不轮询的行为保持一致。
   - FloatingWatchPanel 的 setTimeout 链也照此处理：隐藏时跳过 `refresh` 但继续排下一次；可见时立即刷新一次，仍走原来的 `refreshQueue`。
   - FsMonitorPage 每秒的本地倒计时 tick 不改。
3. 首轮立即加载、手动刷新、依赖变化后立即加载、周期设置等其他代码一律不动。
验收：每一处的 diff 只包含上面两个改动。新增测试：
- `pageVisibility` 单元测试；
- 每个页面各一条测试：隐藏 60 秒内没有请求，变为可见后立即请求一次（FsMonitor、NegativeBasis、PairMonitor、FloatingWatchPanel 必须有，其余页面尽量补）。
原有测试全部通过，并且不能修改原有测试的断言。`npm test` 和 `npx tsc --noEmit` 都要通过。提交信息用 `perf(frontend): skip polling requests while the tab is hidden`。

## 实现记录（v2，2026-11-02）
- 新增 `frontend/src/utils/pageVisibility.ts`（`isPageHidden`、`onPageVisible`）；13 个文件的定时器回调开头加 `isPageHidden()` 跳过，同 effect 注册 `onPageVisible` 刷新并在 cleanup 注销；条件不建定时器的 effect 不注册。
- FloatingWatchPanel：首轮 poll 照常，后续链式 poll 隐藏时跳过 `refresh` 但继续排下一次；可见回调走原 `refresh`（仍进 `refreshQueue`）。
- PairMonitorPage 有两处定时器（资金费记录状态 60s、自动刷新查询），都已处理。
- 红灯（只加测试、未改实现）：Test Files 4 failed | 30 passed (34)；Tests 6 failed | 374 passed (380)。
- 绿灯：Test Files 34 passed (34)；Tests 380 passed (380)；`npx tsc --noEmit` 通过。
- 新增页面测试：FsMonitor、NegativeBasis、PairMonitor（含资金费 watched 两种）、FloatingWatchPanel（standalone 两种）；另有 `pageVisibility.test.ts` 单元测试。其余 9 个页面未补页面级测试。
