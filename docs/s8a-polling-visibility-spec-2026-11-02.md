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

## 返工 1（Claude，2026-11-02，依据只读审查）
1. `usePolling.ts:43`：StrictMode 下 effect 先清理再重新挂载时，会留下 pending 标记。结果是第一次 callback 完成后马上又调一次，第一个周期还没到就调用了两次。要求修复：StrictMode 下第一个周期之前只调用一次。测试要断言具体次数（==1），不能拿实际跑出来的次数当基准。
2. `FloatingWatchPanel.tsx:804`：现在只在入队前检查一次可见性，所以切换模式后排队的刷新，在页面隐藏之后仍会发出去。要求：真正发请求前再检查一次，页面隐藏就跳过，等重新可见后再立即刷新。补测试。
3. `OilNewsPage.tsx:264`，以及 TradfiPerp、SecondLevelSampling 里同类的“依赖变化后立即加载”：这次立即加载和定时轮询之间没有串行保护，也不会重置周期，比如第 59.9 秒切换筛选，第 60 秒又会重复发一次请求。改之前（基线）不会这样，要恢复到基线行为。建议做法：让 `usePolling` 接受 `deps`，依赖变化时重新调度，并立即执行一次；或者另外提供一个 `trigger()`，和轮询共用同一个串行队列。每个页面各补一条测试：切换筛选后，下一次请求按完整周期重新计时，不会出现重叠请求。
其余要求不变：只改任务书允许的文件，`npm test` 和 `npx tsc --noEmit` 都要通过，追加返工记录，另起一个提交，提交信息用 `fix(frontend): polling strictmode, floating queue and dep refresh`。

## 返工 1 执行记录（Codex，2026-11-02）

- 模块与验收：S8-A 前端轮询可见性，仅修复上述三项；分支 `codex/s8-polling`，本轮基线 `5943235aa0d1ea2b925ca3132a552c5348e2005f`。开始时工作区干净。
- 先补回归测试并运行红灯，再修改实现。所有测试和类型检查均在本工作树的 `frontend/` 下运行；沿用 `--no-cache`，避免 Vitest 向共享依赖目录写测试缓存。

### 红灯

```text
npm test -- --no-cache tests/usePolling.test.tsx tests/FloatingWatchPanel.test.tsx tests/OilNewsPage.test.tsx tests/TradfiPerpMonitorPage.test.tsx tests/SecondLevelSamplingPage.test.tsx
Test Files  5 failed (5)
     Tests  8 failed | 63 passed (71)
退出码 1
```

- StrictMode 的同步和延迟完成两个用例都直接断言首个完整周期前调用次数为 1；原实现实际为 2，不再使用运行次数作为基准。
- 普通浮窗和独立浮窗两个用例：首轮 Astro 请求未完成时切换交易对模式，随后隐藏页面并完成首轮；原实现仍执行排队刷新，设置请求从 1 次增至 2 次。
- OilNews 在 59.9 秒、TradfiPerp 在 9.9 秒、SecondLevelSampling 在 0.9 秒修改筛选并挂起请求；越过原截止时间后原实现请求均从 2 次增至 3 次，复现重叠。
- 新增共享队列 `trigger()` 的测试在旧实现上因尚无返回函数失败；验证目标是请求中多次触发合并、使用最新 callback，并在完成后等待完整周期。
- 完成三项修复后，另补“关闭自动轮询时的显式刷新”测试，先复现隐藏时仍调用的问题：`Test Files  1 failed | 4 passed (5)`，`Tests  1 failed | 71 passed (72)`，再改为执行时读取当前可见性。
- 正式红灯日志：`%TEMP%\s8a-rework1-red-final.log`；补充边界红灯日志：`%TEMP%\s8a-rework1-green-targeted.log`。

### 修复与绿灯

- `usePolling` 区分配置变化和 StrictMode 的相同配置 effect 重放；重放只恢复调度，不留下额外立即刷新的 pending 意图。同步异常时也沿用同一判断。
- `usePolling` 返回稳定的 `trigger()`，与定时轮询共用 running/pending 串行保护，清除旧定时器，待刷新完成后重新等待完整周期。关闭自动轮询时仍可显式触发筛选加载，不安装轮询定时器或可见性监听；隐藏时不执行，卸载后触发无效。
- `FloatingWatchPanel.refresh` 在队列任务实际开始时再次检查 `document.visibilityState`；隐藏时跳过，恢复可见后由已有监听立即刷新当前模式，保留 `refreshQueue`。
- OilNews、TradfiPerp、SecondLevelSampling 的首轮与筛选依赖加载改走 `trigger()`。SecondLevelSampling 通过 ref 保留筛选变化后强制加载成分明细的意图，日常轮询仍按指纹判断是否加载明细。手动按钮及接口参数保持原有行为。
- 三个页面的新测试均继续验证：挂起期间没有重叠请求；完成筛选请求后，在完整周期的最后 1ms 前没有请求，到期恰好新增 1 次。

```text
npm test -- --no-cache tests/usePolling.test.tsx tests/FloatingWatchPanel.test.tsx tests/OilNewsPage.test.tsx tests/TradfiPerpMonitorPage.test.tsx tests/SecondLevelSamplingPage.test.tsx
Test Files  5 passed (5)
     Tests  72 passed (72)
退出码 0

npm test -- --no-cache
Test Files  35 passed (35)
     Tests  397 passed (397)
  Duration  117.54s
退出码 0

npx tsc --noEmit
无输出，退出码 0
```

- 定向绿灯日志：`%TEMP%\s8a-rework1-green-targeted-final.log`；全量日志：`%TEMP%\s8a-rework1-green-full.log`；最终类型检查日志：`%TEMP%\s8a-rework1-tsc-final.log`。
- 全量日志保留既有 Ant Design 弃用与 React act 警告；无失败用例。已复核实现与测试差异，`git diff --check` 通过。

### 本轮文件与交付边界

仅修改以下 11 个允许的文件：

```text
docs/s8a-polling-visibility-spec-2026-11-02.md
frontend/src/utils/usePolling.ts
frontend/src/components/FloatingWatchPanel.tsx
frontend/src/pages/OilNewsPage.tsx
frontend/src/pages/TradfiPerpMonitorPage.tsx
frontend/src/pages/SecondLevelSamplingPage.tsx
frontend/tests/usePolling.test.tsx
frontend/tests/FloatingWatchPanel.test.tsx
frontend/tests/OilNewsPage.test.tsx
frontend/tests/TradfiPerpMonitorPage.test.tsx
frontend/tests/SecondLevelSamplingPage.test.tsx
```

- `TradfiPerpMonitorPage.test.tsx` 为本轮新增测试文件；日志均在系统临时目录，未作为仓库产物提交。
- `frontend/node_modules` 联接未改，目标仍为 `.worktrees/s9-test-gate/frontend/node_modules`；未运行 npm install，未使用 WSL。
- 本轮只创建一个本地提交，提交信息为 `fix(frontend): polling strictmode, floating queue and dep refresh`；实际提交号以交付回复及 `git log -1` 为准。不推送、不部署，未操作线上数据库或服务，未进行线上验收。
- 本任务书末尾记录作为本轮交接；后续导航分组 S8-B 另开任务。

## 返工 2（Claude，2026-11-02，依据只读复审）
复审发现 3 个问题，都出在 `trigger()` 和 `enabled`、页面隐藏这些状态互相作用的地方：
1. P2 `usePolling.ts:91`：TradfiPerp 页面上，请求还没回来时切换了筛选，接着又关掉自动刷新，排在队列里的那次请求会被清掉，页面停在旧筛选的结果上（改之前不会这样）。
2. P2 `usePolling.ts:83`：SecondLevelSampling 在“暂停采样”状态、而且页面隐藏时挂载，首次加载被跳过了；之后页面变回可见，60 秒内也没有补上请求（改之前会加载）。
3. P3 `OilNewsPage.tsx:260`：页面在 effect 里调用 `trigger()` 时，React 严格模式下第一个周期之前仍会请求两次。

修复原则（请按这个统一语义实现，避免继续打补丁）：
- **trigger 是“一定要执行的刷新”**：不受 `enabled` 影响，`enabled=false` 也要执行；关闭自动刷新也不能清掉已经排队的 trigger。页面隐藏时只是推迟：记下一个待执行标记，等页面可见后立即执行一次。只要还有待执行标记，就必须保持对 `visibilitychange` 的监听，不管 `enabled` 是什么值。
- **定时轮询受 `enabled` 和隐藏状态控制**：`enabled=false` 或页面隐藏时不排定时器；页面重新可见且 `enabled=true` 时立即执行一次，然后恢复周期。
- trigger 和定时轮询共用同一条串行队列；执行完一次 trigger 后，下一次定时轮询重新按完整周期计时；同时排了多个 trigger 时合并成一次执行。
- 卸载以后什么都不执行。严格模式下的重放，包括页面在 effect 里调用 trigger 的情况，在第一个周期之前都只执行一次。具体做法：同一个 tick 内重复的 trigger 合并成一次，或者跳过挂载后的重复调用。
- 给以上每条语义各写一条 hook 级别的测试；上面 3 个问题，各写一条页面级别的复现测试（先红灯，再绿灯）。
其余要求不变：只改任务书允许的文件；`npm test` 和 `npx tsc --noEmit` 都要通过；追加返工记录；另起一个提交，提交信息用 `fix(frontend): unify polling trigger semantics`。

## 返工 2 执行记录（Codex，2026-11-02）

- 模块与验收：S8-A 前端轮询可见性，统一显式 trigger 与自动轮询语义，复现并修复上述三个页面问题；分支 `codex/s8-polling`，本轮基线 `f8838574c7cac4b19164bb3e343521c824e03538`。开始时工作区干净。
- 全文阅读任务书后先修改测试、运行红灯，再修改 hook。没有修改页面实现、接口参数或周期。下列命令全部在本工作树的 `frontend/` 运行；继续使用 `--no-cache` 避免向共享依赖目录写入 Vitest 缓存。

### 红灯与复现证据

```text
npm test -- --no-cache tests/usePolling.test.tsx tests/TradfiPerpMonitorPage.test.tsx tests/SecondLevelSamplingPage.test.tsx tests/OilNewsPage.test.tsx
Test Files  4 failed (4)
     Tests  10 failed | 23 passed (33)
退出码 1
```

- TradfiPerp：挂起初始请求，修改筛选后关闭自动刷新；释放初始请求后应有第 2 次请求，旧实现仍只有 1 次。绿灯继续检查第二次请求含最新 `min_volume_24h_k=2000`，且之后 60 秒没有自动轮询。
- SecondLevelSampling：暂停状态下隐藏挂载，隐藏 60 秒不发起样本请求；恢复可见后应立即加载 1 次，旧实现为 0 次。绿灯同时确认样本呈现、成分明细加载，以及随后 60 秒不轮询。
- OilNews：StrictMode 重放页面 effect 后，首个完整 60 秒周期前应只有 1 次新闻请求，旧实现为 2 次；绿灯验证 59,999ms 仍为 1 次、到期后为 2 次。
- hook 测试覆盖：关闭自动刷新保留排队 trigger 并使用最新 callback；隐藏 trigger 保留监听且不受 enabled/周期变更影响；自动恢复请求可随 disabled 取消；同 tick trigger 合并、串行执行并重新等待完整周期；StrictMode effect trigger 的同步/异步回调均只执行一次；卸载后待执行 trigger 不执行。
- 初次定向修复通过 `4 passed / 33 passed` 后，复核新增两个组合边界测试。其中“可见挂载且 disabled，之后隐藏再 trigger”发现安装监听时使用旧可见性的问题，先运行补充红灯，再修复：

```text
npm test -- --no-cache tests/usePolling.test.tsx
Test Files  1 failed (1)
     Tests  1 failed | 23 passed (24)
退出码 1
```

- 另一个组合边界确认：请求中排队的 trigger 经隐藏、关闭自动刷新、旧请求完成后仍保留，重新可见时执行一次，之后不轮询。
- 红灯日志：`%TEMP%\s8a-rework2-red.log`；补充红灯：`%TEMP%\s8a-rework2-red-visibility.log`；初次定向绿灯：`%TEMP%\s8a-rework2-green-targeted.log`。

### 统一实现与最终验证

- `frontend/src/utils/usePolling.ts` 在跨 effect 的 ref 中分别保存显式 trigger、自动 poll、运行中状态及微任务合并状态，共用一个串行执行入口。配置变化只取消自动意图；显式 trigger 继续保留。
- trigger 在同一 tick 的微任务中合并；隐藏只延后执行，disabled 不取消。可见性监听按“自动轮询开启或仍有待执行 trigger”维护，安装时同步当前可见性；disabled 的 trigger 消费完后移除监听，不创建周期定时器。
- 自动轮询仅在 enabled 且可见时调度；恢复可见时触发一次。所有刷新完成后才开始完整间隔，回调异常仍由 hook 静默处理。StrictMode 重放不会增加一次 trigger；卸载清除定时器、监听和执行入口，异步完成及待执行微任务不会再发起 callback。
- 本轮新增 13 个用例（hook 10 个、页面 3 个），并加强已有 disabled trigger 用例：恢复可见后无需再次手动 trigger 就必须补执行。

```text
npm test -- --no-cache
Test Files  35 passed (35)
     Tests  410 passed (410)
  Duration  97.86s
退出码 0

npx tsc --noEmit
无输出，退出码 0
```

- 全量包含全部既有页面测试；FsMonitor、NegativeBasis、PairMonitor、FloatingWatchPanel、PremiumIndex 及返工 1 的三个筛选重排周期测试均通过。最终 hook 文件共 24 个用例全部通过。
- 全量日志仍有 Ant Design 弃用及 React act 警告，没有失败用例。最终全量日志：`%TEMP%\s8a-rework2-green-full.log`；类型检查日志：`%TEMP%\s8a-rework2-tsc.log`。
- 已人工复核 hook 生命周期、串行队列、监听安装/移除及测试差异；`git diff --check` 通过。

### 文件与交付边界

本轮仅修改以下 6 个允许文件：

```text
docs/s8a-polling-visibility-spec-2026-11-02.md
frontend/src/utils/usePolling.ts
frontend/tests/usePolling.test.tsx
frontend/tests/TradfiPerpMonitorPage.test.tsx
frontend/tests/SecondLevelSamplingPage.test.tsx
frontend/tests/OilNewsPage.test.tsx
```

- `frontend/node_modules` 联接保持原样，目标仍为 `.worktrees/s9-test-gate/frontend/node_modules`；未运行 npm install，未使用 WSL。测试日志仅写入系统临时目录，无新增未跟踪交付文件。
- 按要求创建一个本地提交，信息为 `fix(frontend): unify polling trigger semantics`；实际提交号以交付回复及 `git log -1` 为准。不推送、不部署，未操作线上数据库或服务，未进行线上验收。
- 本记录作为本轮交接。后续如继续 S8-B 导航分组，应另开任务。
