# 浮窗 Astro 401 停止轮询：任务书（Claude，2026-10-28）

基线：`8779d40`（当前生产版本）。worktree `.worktrees/astro-401-poll`，分支 `codex/astro-401-poll`。`frontend/node_modules` 是指向主检出目录的 junction，不要改动或提交它。

## 1 现状
- `frontend/src/components/FloatingWatchPanel.tsx:662`：`refresh()` 每次都会调用 `listAstroPairs()`，不管当前是 symbols、pairs 还是 positions 模式；轮询间隔 `REFRESH_INTERVAL_MS = 10_000`，在 refresh 结束后才开始计时。
- 浏览器里没有保存有效的面板密码时，后端在调用 Astro 上游之前就返回 401 `Invalid dashboard password`（`backend/app/api/routes_astro.py:467`）。`client.ts:205` 会把它转换成中文提示，`ApiError` 带有 `status`。浮窗只把错误文字存进 `astroError`，下一轮照样请求。生产日志里大约每 12 秒出现一次 `GET /api/astro/pairs 401`。

## 2 设计（只改 FloatingWatchPanel.tsx）
1. 新增 ref `astroAuthBlocked: string | null`，记录**被拒绝时**浏览器里保存的密码值，没有保存密码时记为 `""`。
2. `listAstroPairs()` 抛出 `ApiError` 且 `status === 401`，并且错误消息是 `client.ts` 中“面板密码无效”的那段中文文案时：设置 `astroAuthBlocked = 当前 localStorage 中的 dashboard_password ?? ""`，`astroError` 照常显示。为避免重复写文案，可以在 `client.ts` 导出一个判断函数 `isDashboardPasswordError(error)`；如果改了 `client.ts`，只允许新增这个导出。
3. 之后每次 refresh：如果 `astroAuthBlocked !== null`，并且当前 localStorage 里的密码**等于**被拒绝时的值，就**跳过** `listAstroPairs()`，保留上一次的 astroPairs 和 astroError；其他行情刷新照常进行。密码变化（用户重新验证）后自动恢复请求。
4. 用户手动刷新（浮窗里已有的刷新按钮，或者切换模式触发的那次 refresh）时，清除 `astroAuthBlocked`，重新请求一次。
5. 上游转发过来的 401（错误消息不是面板密码那条）、其他错误和网络错误：行为不变，继续轮询。
6. 不做：修改轮询间隔、其他请求、后端、密码输入 UI。

## 3 文件与测试
- 修改：`frontend/src/components/FloatingWatchPanel.tsx`（如有必要，`frontend/src/api/client.ts` 只允许新增一个导出函数）
- 测试：在 `frontend/tests/FloatingWatchPanel.test.tsx` 中**新增**用例，不能修改或放宽已有用例。
- 新建 `docs/task-handoff-2026-10-28-astro-401-poll.md`
- 禁止修改其他文件。**不要修改** `frontend/src/pages/PairMonitorPage.tsx`。
测试要先写（用 fake timers 和 mock 的 listAstroPairs）：
1. 首次请求返回面板密码 401 后，后续 3 轮轮询都不再调用 listAstroPairs，而 getFloatingWatchSettings 等其他请求照常调用；错误提示仍然显示。
2. 修改 localStorage 中的 dashboard_password 后，下一轮恢复调用。
3. 手动刷新会恢复调用一次。
4. 上游 401（消息不同）和 500 继续轮询。
验收命令（在 frontend 下执行）：`npx vitest run tests/FloatingWatchPanel.test.tsx tests/ApiClient.test.ts`、`npx tsc --noEmit -p .`、`npx vitest run`（前端全量），把汇总行原样贴出来。

## 4 派发
- Codex：gpt-6.1-sol xhigh。不要跑 WSL，不要联网，不要运行 npm install。上线需另行申请批准。
