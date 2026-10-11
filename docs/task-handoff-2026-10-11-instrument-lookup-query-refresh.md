# 交接：标的查询输入被自动刷新覆盖

日期：2026-10-11（Asia/Shanghai）

## 目标与范围

- 模块：标的查询页的查询输入与自动刷新。
- 用户场景：用户输入 OPENAI 查询时，旧标的行情自动刷新完成后把旧 symbol 写回输入框，导致查询失败或被覆盖。
- 验收目标：旧标的后台刷新不会修改用户正在输入的内容；输入 OPENAI 后可继续提交并显示 OPENAI 结果；手动查询期间不再启动旧标的的自动刷新。
- 基线：分支 `codex/frontend-localization-polish`，开始时 HEAD `eb4f9a99740d525bf329325a7e95463b092e4a4f`。

## 已完成与代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx` 的 `runLookup`：仅非后台查询成功时回写 `query`；后台刷新仍更新行情、active symbol、URL 和缓存，但不会改动输入框。
- 同文件自动刷新 `useEffect`：依赖 `loading`，手动查询进行期间跳过旧标的刷新，查询完成后按新的 active symbol 重建定时器。
- `frontend/tests/InstrumentLookupPage.test.tsx`：新增迟到后台响应回归，覆盖输入 OPENAI、旧 BTC 刷新完成、继续查询并显示 OPENAI。
- 未修改后端、数据模型、市场匹配、Hyperliquid DEX 信息或交易逻辑。

## 重要业务规则

- 输入框是用户编辑状态，后台行情刷新不能覆盖它。
- 成功的手动查询会把规范化 symbol（例如 `OPENAIUSDT`）写回输入框；成功的后台刷新不会写回。
- 请求仍使用 `requestIdRef` 丢弃迟到响应；本轮额外在手动加载期间阻止自动刷新发起竞争请求。

## 验证结果

- `npx vitest run InstrumentLookupPage.test.tsx --reporter=dot`：38/38 通过。
- `npx vitest run InstrumentLookupPage.test.tsx -t "does not let an older background refresh" --reporter=dot`：新增回归通过。
- `npx tsc -b && npx vite build`：生产构建通过。
- `git diff --check`：通过。
- 运行测试/构建时普通沙箱曾对 Node 依赖返回 EPERM，改用已授权的本地读取执行后通过；不是代码错误。

## 线上状态

- 功能提交 `d090c374cf745fadd86668e1e4cb98a6506bc2cf` 已推送；GitHub Actions run `38124369191` 的 backend/frontend 两项镜像构建均成功。
- 生产机已按 `deploy/linux-update.sh` 快进并运行双镜像，当前 backend/frontend 均 healthy，镜像 tag 均为 `sha-d090c374cf745fadd86668e1e4cb98a6506bc2cf`；未在生产机执行构建，也未使用 `docker compose down -v`。
- 发布前主库备份：`backups/radar-before-d090c374cf74-20261011T082130Z.db`，`integrity_check=ok`，SHA-256 `5d25d759fc43f201bfa001fc0603395313079e7ffd7fa487be2e5829753d5422`。
- 发布前 squeeze-route 备份：`backups/squeeze-route-before-d090c374cf74-20261011T082130Z.db`，`integrity_check=ok`，SHA-256 `9732baec65463a36510f646ec5178ff8ab642bc8ebefa25d9abd79a7750064a9`。
- 后端直连和前端代理 `/api/health` 均 HTTP 200；生产 `OPENAIUSDT` 接口返回规范标的、8 个交易所、9 个精确市场。通过临时 SSH 隧道的 Playwright 验收：等待自动刷新后输入 OPENAI 未被覆盖，提交后显示 OPENAI / USDT，无 page error。

## 工作区与下一步

- 本轮文件：`frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/tests/InstrumentLookupPage.test.tsx`、本交接文档。
- 现有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 修改属于其他任务，不能暂存、回退或覆盖；大量 `.worktrees/`、`backend/.pytest_*`、`output/` 未跟踪产物同样保留。
- 下一步：后续标的查询相关工作从本交接文档继续；不要改动或回退已部署的输入保护逻辑。
- 已知限制：自动刷新保护依赖浏览器页面仍处于可见状态；页面隐藏时本来就暂停刷新，恢复可见后按现有定时器策略继续。
