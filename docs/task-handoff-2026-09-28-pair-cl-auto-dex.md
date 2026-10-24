# 价差查询：Hyperliquid CL 自动选择 DEX

## 目标与范围

- 模块：价差查询页及其后端查询响应。
- 用户输入 `CL` / `CLUSDT`，无需事先知道 Hyperliquid DEX；唯一市场自动选择，同名市场跨多个 DEX 时提示明确选择。
- 本次不改其他市场查询页、交易执行或告警规则。

## 分支与基线

- 分支：`codex/frontend-localization-polish`。
- 生产基线：`a50f0137aa489c64bcc47c9feb39e5d53d331263`。
- 功能提交：`3043bcdf58b4adbad522970222925de2385dcf97`（前端自动选择）；`081b1aa096907823eb741aafbd9737b0a8e07822`（后端响应保留解析后的 DEX）。均已推送。
- 最终生产代码与镜像：`081b1aa096907823eb741aafbd9737b0a8e07822`，GitHub Actions 运行 `36422988464` 的 backend/frontend 作业均成功。

## 已完成与关键入口

- `frontend/src/pages/PairMonitorPage.tsx`：DEX 下拉提供“自动匹配”；标的候选覆盖所有 DEX；查询和未查询直接保存预设时从市场目录解析唯一 DEX；改动标的、市场或交易所时清除旧 DEX；同名多 DEX 时要求选择。保存预设不会复用上次结果的原始市场身份。
- `backend/app/services/pair_spread_query.py`：历史与秒级实时查询返回前，将未指定 DEX 的 Hyperliquid 腿补为实际解析的 DEX，避免 URL、缓存及后续刷新误回退到 `main`。
- `frontend/tests/PairMonitorPage.test.tsx`、`backend/tests/test_pair_spread_query.py`：覆盖旧 `main + CL`、旧非主站 DEX、同名歧义、改标的后清除旧值、保存预设、历史与秒级响应身份。

## 业务规则

- 2026-09-28 生产市场目录中，`CL` 的唯一有效匹配是 `xyz:CL`；主站 `main` 没有 CL。保留 `(exchange, market_type, raw_symbol, dex)`，不能仅用规范化 ticker 合并市场。
- 未指定 DEX 时只有唯一匹配才能自动选择；多个 DEX 同名时必须明确选择。用户明确选中的、与市场目录匹配的 DEX 保留；目录没有字面匹配时保留非主站 DEX 供后端处理已配置别名。
- 截图中的左腿 Lighter `XAUUSDT` 与右腿 Hyperliquid `xyz:CL` 属于不同标的；显示的价差只是跨资产价格计算，不能当作同标的套利信号。

## 验证与生产状态

- 前端 `PairMonitorPage.test.tsx`：40/40 通过；`npm run build`（含 TypeScript）通过；`git diff --check` 通过。
- 后端 `test_pair_spread_query.py`：60/60 通过；`test_api.py -k pair_spread`：8/8 通过。
- 部署脚本先备份并校验数据库，再以 `git pull --ff-only` 快进，拉取预构建镜像并执行 `docker compose up -d --no-build --wait`。最终备份：`backups/radar-before-081b1aa09690-20260928T124321Z.db`，`integrity_check=ok`，SHA-256 `e020b39f91998a1b296b115cd8508a2e6edfa699f5f3f566c2a350aa965cc7ae`；`backups/squeeze-route-before-081b1aa09690-20260928T124321Z.db`，`integrity_check=ok`，SHA-256 `cdcc4924107c4ee03dd6264c22d4ec9e9e22fe203b02168d895b1bccdaf9469d`。此前首轮部署也保留了 `3043bcdf58b4` 对应的两份校验通过的备份。
- 生产 backend/frontend 容器均 healthy；`/api/health` 返回 `ok`、`exchange_errors={}`。不传 DEX 的 480 小时、1 小时采样查询返回 `leg2.dex=xyz`、`current.leg2.raw_symbol=xyz:CL`、480 个历史点、无警告。
- Playwright 通过旧 `leg2_dex=main&leg2_symbol=CLUSDT` 链接验收：桌面 1440x900、手机 390x844 均自动改写 URL 为 `leg2_dex=xyz`，DEX 控件显示 `xyz · XYZ`，图表可见、无页面脚本错误和整体横向溢出。手机关闭常驻关注浮窗后表单与操作按钮无重叠。

## 未跟踪文件与下一步

- 本次浏览器截图留在未跟踪的 `output/pair-cl-auto-dex-production-*.png`，未纳入提交。工作区原有 `.worktrees/`、`backend/.pytest_*`、其他 `output/` 和文档等未跟踪文件均未清理或提交。
- 本模块已部署。后续若扩展其他页面的自动市场选择，请新建任务，先核对该页面的数据契约、歧义提示与精确原始市场身份。
