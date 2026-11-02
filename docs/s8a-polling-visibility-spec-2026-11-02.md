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

## Codex 本地交接记录

- 日期：2026-11-02。模块：S8-A 前端轮询可见性；分支：`codex/s8-polling`；本轮开始基线：`f569548`（任务书提交，调查代码基线为 `ac6d8d9`）。
- 在上一轮留下的 4 个页面测试修改及新增 `usePolling.test.tsx` 上继续，先运行红灯，再实现，再运行绿灯。
- 新增 `usePolling`：保存最新 callback，以串行 `setTimeout` 等待异步调用完成再调度；隐藏时清除定时器，重新可见时立即刷新；处理 enabled、卸载、周期变化、异常及 StrictMode。请求进行中发生多次恢复时，合并为一次后续刷新。
- 指定的 13 个文件均已接入。PairMonitor 的查询与资金费率记录状态两条轮询均暂停。FsMonitor 保留首次配置加载、登录重试和每秒本地倒计时；FloatingWatchPanel 保留 `refreshQueue`，在原调度内加入可见性处理，普通面板和独立浮窗均覆盖测试。
- 手动刷新、接口参数及配置的周期值沿用；隐藏前已发起的请求允许完成。未改动导航、AppShell、useRadarStore、InstrumentLookupPage、SpreadEventsPage 或后端。

### 红灯记录

在 `frontend/` 运行：

```text
npm test -- tests/usePolling.test.tsx tests/FsMonitorPage.test.tsx tests/NegativeBasisMonitorPage.test.tsx tests/PairMonitorPage.test.tsx tests/FloatingWatchPanel.test.tsx

Test Files  5 failed (5)
     Tests  6 failed | 125 passed (131)
```

- hook 实现文件尚不存在，`usePolling.test.tsx` 导入失败，11 个用例尚未收集。
- 4 个页面的 6 个隐藏用例全部按预期失败：隐藏 60 秒后，FsReport 被调用 13 次；NegativeBasis 请求从 2 增至 16；FloatingWatchPanel 两种窗口模式的请求均从 3 增至 21；PairMonitor 未记录/已记录资金费率两种状态的请求分别从 5 增至 18/19。
- 本次运行结束另有 Vitest 写入联接目录下 `.vite/vitest/results.json` 的 `EPERM` 缓存权限错误。后续命令统一加 `--no-cache`，未改联接、依赖或项目配置。原始日志：`%TEMP%\s8a-red.log`。
- 第一次全量回归另发现旧 PremiumIndex 测试绑定 `setInterval`：`Test Files  1 failed | 33 passed (34)`，`Tests  1 failed | 388 passed (389)`。已改为用假定时器验证实际请求在 2999ms 前不发生、3000ms 时发生，并继续按 3 秒刷新，同时验证配置持久化。

### 绿灯与类型检查

所有命令均在本工作树的 `frontend/` 下运行，退出码均为 0：

```text
npm test -- --no-cache tests/usePolling.test.tsx tests/FsMonitorPage.test.tsx tests/NegativeBasisMonitorPage.test.tsx tests/PairMonitorPage.test.tsx tests/FloatingWatchPanel.test.tsx
Test Files  5 passed (5)
     Tests  142 passed (142)

npm test -- --no-cache tests/PremiumIndexPage.test.tsx
Test Files  1 passed (1)
     Tests  10 passed (10)

npm test -- --no-cache
Test Files  34 passed (34)
     Tests  389 passed (389)
  Duration  113.23s

npx tsc --noEmit
无输出，退出码 0（PremiumIndex 测试调整后再次通过）。
```

- hook 的 11 个用例覆盖串行、防叠加、隐藏及恢复、隐藏时挂载、enabled 切换、请求中卸载、同步/异步异常、最新 callback、改周期、关闭隐藏暂停、请求中多次切换及 StrictMode。
- 全量日志保留原有 Ant Design 弃用及 React act 警告，测试均通过。
- 最终全量日志：`%TEMP%\s8a-green-full-final.log`；最终类型检查日志：`%TEMP%\s8a-tsc-final.log`。

### 改动文件（21 个）

```text
docs/s8a-polling-visibility-spec-2026-11-02.md
frontend/src/utils/usePolling.ts
frontend/src/components/FloatingWatchPanel.tsx
frontend/src/pages/FsMonitorPage.tsx
frontend/src/pages/GateTwapPage.tsx
frontend/src/pages/IndexComponentChangesPage.tsx
frontend/src/pages/NegativeBasisMonitorPage.tsx
frontend/src/pages/OilNewsPage.tsx
frontend/src/pages/OpportunityRadarPage.tsx
frontend/src/pages/PairMonitorPage.tsx
frontend/src/pages/PremiumIndexPage.tsx
frontend/src/pages/SecondLevelSamplingPage.tsx
frontend/src/pages/SqueezeArbitragePage.tsx
frontend/src/pages/SymbolSpreadPage.tsx
frontend/src/pages/TradfiPerpMonitorPage.tsx
frontend/tests/usePolling.test.tsx
frontend/tests/FloatingWatchPanel.test.tsx
frontend/tests/FsMonitorPage.test.tsx
frontend/tests/NegativeBasisMonitorPage.test.tsx
frontend/tests/PairMonitorPage.test.tsx
frontend/tests/PremiumIndexPage.test.tsx
```

### 环境与交付边界

- 保持用户已设好的 `frontend/node_modules` 目录联接，目标为 `.worktrees/s9-test-gate/frontend/node_modules`；未运行 npm install，未使用 WSL。
- 仅提交以上任务文件，提交信息：`perf(frontend): pause polling while the tab is hidden`。提交号由本次提交后的 `git log -1` 确认。
- 按本次明确要求仅本地提交，不推送、不部署；未操作生产数据库或线上服务，未进行线上验收。
- 后续导航分组 S8-B 应在新任务中继续。
