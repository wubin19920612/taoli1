# 交接：精确行情市场展示深度与交易限制

日期：2026-09-25（Asia/Shanghai）

## 目标与范围

- 模块：标的查询页的精确行情市场表格。
- 用户要求：概览移除“倍率”和“行情数据状态”两列，改为直接展示盘口深度和交易限制；刷新时不影响读数。
- 分支：`codex/frontend-localization-polish`。开始实现时基线为 `17ccab1`；并行任务随后将基线推进到 `43cf672`。本模块功能提交为 `6ec13caee912924563c9d4dc5cd88ae25295bfdc`。

## 已完成与代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx`：每个精确市场概览显示 0.1%/1% 买卖双边深度、公开限制原因及买卖/开平动作状态。无诊断时显示加载、失败或无可靠关联提示；诊断过期或刷新失败时保留上次读数，并以“上次”状态和警示标记，避免误作当前可交易结论。
- 同文件 `exactMarketKey` / `associateMarkets` 继续按唯一的 `(exchange, market_type, raw_symbol, dex)` 关联；Hyperliquid 原始市场与 DEX 不合并。展开诊断继续保留来源、时间、费用和合约规格。
- `frontend/src/styles.css`：调整两列宽度；中等宽度下限制区占满最后一行；手机纵向排列。仅修改精确市场相关选择器。
- `frontend/tests/InstrumentLookupPage.test.tsx`：覆盖新列、不同 DEX 的深度与限制、现货待核实、过期与失败诊断、后台刷新保留数值及展开状态。
- 没有修改后端接口、报价计算、交易执行或资金费率口径。倍率字段仍由 API 提供，且保留在交易相关逻辑和展开诊断中，只从该表概览移除。

## 业务规则

- 深度来自 `/api/trade-status/{symbol}` 的市场诊断，不将 `/api/instruments/{symbol}` 的买一/卖一数量冒充盘口深度。两个接口独立计时；盘口深度不是目标成交额的成交保证。
- “公开可用”只代表已获取的公开市场证据，不代表账户权限或真实订单已核验。公开限制原因优先展示；`conditional`、`unknown` 与适用动作的 `not_applicable` 不标绿。
- 诊断失败、过期或缺失时不跨市场借用数据。已有关联的旧诊断可供回看，概览明确标注旧状态，展开区仍显示原始时间与来源。

## 验证

- `cd frontend; npm test -- InstrumentLookupPage.test.tsx --reporter=dot --silent`：27/27 通过。
- `cd frontend; npm run build`：TypeScript 和 Vite 生产构建通过；`git diff --check` 通过。
- 本地 Playwright 用固定市场响应检查 1440/1000/390 px：表格、单元格和文档无横向溢出。模拟后台诊断失败，市场行高度前后均为 `180.78px`，旧深度和限制状态保留。截图在 Codex 可视化目录 `instrument-depth-{desktop,medium,mobile}.png`。

## 线上交付与验收

- 功能提交 `6ec13caee912924563c9d4dc5cd88ae25295bfdc` 已推送到 `origin/codex/frontend-localization-polish`。GitHub Actions run `36084785659` 的 backend、frontend 两个镜像构建均为 `completed/success`；生产机可读取两份目标镜像 manifest。
- 更新前生产仓库为 `8e0e29f9caff786a1a2790b0a1a547aaa986beaa`，跟踪文件无本地修改，双容器健康，磁盘可用约 25.5 GiB。通过 `deploy/linux-update.sh` 备份并校验后 `git pull --ff-only`，拉取预构建镜像并 `docker compose up -d --no-build --wait`，没有在 2 GiB 生产机上构建或删除数据库卷。
- 备份：`backups/radar-before-6ec13caee912-20260925T021039Z.db`，大小 596,537,344 字节，`integrity_check=ok`，容器与主机 SHA-256 一致；复核 SHA-256 为 `9372afb73a8ce7b7c9cb300b2c56fa96c45f15e1b8b25c39c3b4e753463d6d2d`。保留策略本次删除 0 份备份。
- 生产仓库和两个运行镜像均为 `6ec13caee912924563c9d4dc5cd88ae25295bfdc`，两个容器 healthy；后端和前端代理 `/api/health` 均 HTTP 200。OURAUSDT 行情和诊断接口均 HTTP 200，分别返回 6 个精确市场。
- Playwright 实测生产 OURAUSDT：桌面和 390 px 手机均显示 6 行及新表头，深度和限制在同一精确市场行，Bybit 的公开开仓限制直接可见；无横向溢出或脚本错误。手动刷新后首行 DOM 身份不变、展开诊断仍打开。截图在 Codex 可视化目录 `instrument-depth-production-{desktop,mobile}.png`。

## 工作区与后续

- 本任务功能提交只包含页面、样式和专项测试；此文档作为独立交接提交。原有 `.worktrees/`、`backend/.pytest_module_prune_20260925/`、其他模块交接文档及 `output/`、`script/` 未跟踪产物均未暂存、删除或回退。
- 公开接口无法确认账户级权限、订单是否最终成交；深度为指定价格区间的盘口快照。真实市场增删、限制变化或价差排序仍可能改变页面内容。继续处理其他模块时按 `AGENTS.md` 新建任务，并从本文件恢复上下文。
