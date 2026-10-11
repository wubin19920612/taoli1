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

- 生产机只读检查：服务器代码仍为 `822d6c7b39c6f41dc78ab789e1269b18d8922f3b`，前后端容器均 healthy，根分区可用约 14.6 GiB。
- 本轮修复尚未部署；待提交推送并完成 GitHub Actions 双镜像后，按 `deploy/linux-update.sh` 先备份 `/data/radar.db`，再使用 `git pull --ff-only` 和预构建镜像更新。

## 工作区与下一步

- 本轮文件：`frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/tests/InstrumentLookupPage.test.tsx`、本交接文档。
- 现有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 修改属于其他任务，不能暂存、回退或覆盖；大量 `.worktrees/`、`backend/.pytest_*`、`output/` 未跟踪产物同样保留。
- 下一步：只暂存本轮三个文件，提交并推送当前分支；等待 backend/frontend 镜像构建；运行部署脚本并记录数据库备份校验、容器健康、`/api/health` 和线上 OPENAI 实际查询结果。
- 已知限制：本轮验证使用专项接口模拟覆盖迟到响应；线上最终验收仍需真实 OPENAI 页面/API 请求确认。
