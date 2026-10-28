# Astro / 浮窗：面板密码 401 后暂停轮询

## 目标与范围

- 按 `docs/astro-401-poll-spec-2026-10-28.md` §2、§3 实现：面板密码被拒绝后暂停 Astro 卡片请求，其他行情继续刷新。
- 分支：`codex/astro-401-poll`；worktree：`.worktrees/astro-401-poll`。
- 实际起始 HEAD：`7db045673825c6b9df97fd7ed39c0a29bf000267`（任务书提交）。任务书所列生产基线：`8779d40678ff6ddc0a49522a74ddac85a9430e9b`；本任务离线，未核验当前生产版本。
- 仅修改 `frontend/src/components/FloatingWatchPanel.tsx`、`frontend/src/api/client.ts`、`frontend/tests/FloatingWatchPanel.test.tsx`，新增本文档。

## 实现与关键入口

- `client.ts` 只新增导出函数 `isDashboardPasswordError(error)`。要求错误同时满足 `ApiError`、HTTP 401、既有面板密码中文提示；复用原错误解析函数判断文案，不改既有请求或错误转换。
- `FloatingWatchPanel.tsx` 的 `astroAuthBlocked` ref 保存拒绝发生时的 `dashboard_password ?? ""`，`null` 表示允许请求。
- `refresh()` 在刷新队列执行时判断密码是否改变。相同密码仍被阻断时跳过 `listAstroPairs()`，不覆盖已有 `astroPairs`、`astroError`，其他请求按原流程执行。
- 手动刷新传入重试标记；`lastRefreshMode` 识别模式切换。两者均在队列执行时清除阻断，避免前一个在途请求的拒绝覆盖本次重试。
- 上游 401、500、网络错误继续轮询。原轮询间隔和完成后再计时的调度方式保持不变。
- 不涉及 `PairMonitorPage.tsx`、后端、密码输入 UI、交易逻辑或市场身份处理。

## 测试先行与验收

先新增 17 个测试，再修改实现。实现前回归结果为 `12 failed | 29 passed (41)`，失败全部位于新增阻断/恢复用例；原有 24 个用例通过。原有用例未修改或放宽。

新增用例覆盖：连续三轮阻断而设置与标的行情继续刷新；缓存卡片和警告保留；密码新增、修改、删除及旧值恢复；手动刷新；切换交易对、Astro、持仓模式；上游 401、500、网络错误、错误类型和文案边界；拒绝时读取密码；在途请求期间排队的手动刷新和模式切换。

以下验收均在 `frontend` 下通过本机 Windows Node/npm 执行。设置 `NPM_CONFIG_OFFLINE=true`、`NPM_CONFIG_YES=false`，npm 缓存及日志位于系统临时目录。为遵守不得改动 junction 的约束，两个 Vitest 命令均附加 `--no-cache`；未修改测试配置或依赖。

`npx vitest run tests/FloatingWatchPanel.test.tsx tests/ApiClient.test.ts --no-cache`：

```text
 Test Files  2 passed (2)
      Tests  49 passed (49)
   Duration  15.69s (transform 255ms, setup 9.42s, collect 3.31s, tests 6.12s, environment 2.26s, prepare 472ms)
```

`npx tsc --noEmit -p .`：无输出，退出码 `0`。

`npx vitest run --no-cache`：

```text
 Test Files  28 passed (28)
      Tests  296 passed (296)
   Duration  92.18s (transform 7.69s, setup 172.28s, collect 100.96s, tests 261.88s, environment 34.45s, prepare 3.88s)
```

`git diff --check` 通过。测试输出存在 React `act(...)` 和 Ant Design 弃用提示，所有断言通过。

## 本地文件与线上状态

- 开始时工作区干净；无需要保留的其他任务改动或未跟踪文件。
- `frontend/node_modules` 保持原 junction，未运行 WSL、联网、`npm install`，未改动或暂存依赖。
- 类型检查生成了已被 Git 忽略的 `frontend/tsconfig.tsbuildinfo`；验收日志保留在系统临时目录，未纳入提交。
- 提交信息：`fix: stop floating watch astro polling after dashboard 401`；末尾 trailer 为 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- 按本任务离线要求仅本地提交，未推送、备份或部署；未执行线上验收。任务书 §4 要求上线另行申请批准。

## 后续

本模块本地验收完成。需要上线时，新建服务器交付任务，先获得上线授权，再按项目交付文档推送、构建镜像、备份数据库、部署并验证接口；不要把本文的离线测试结果作为线上验收。
