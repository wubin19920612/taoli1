# 交接：标的查询市场诊断与开平仓价差

日期：2026-09-25（Asia/Shanghai）

## 目标与范围

- 模块：标的查询页的精确市场诊断和跨市场差价表。用户确认本地布局预览后要求按该格式实现。
- 市场诊断标题直接标出交易所、现货/永续、原始市场符号及 Hyperliquid DEX，便于区分同页各市场。
- 差价类型标出买入到卖出方向的交易所简称；表格显示开仓和平仓价差，移除中价差和价差额。
- 分支：`codex/frontend-localization-polish`。开始时基线 `4e814fb395e1aa76d429dac7f5562b18be836b6f`；功能提交 `632316d4e38e72dd00d31524d0f6ded9e11ac0d7`。并行 Astro 文档提交随后将分支推进到 `4b827ac6e019badc9fb4567cef50f9bb4514c388`，其中没有本模块代码变动。

## 已完成与代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx`：市场诊断摘要显示精确市场身份；差价类型用 `bn`、`hl`、`by`、`gate`、`bg`、`lit`、`rh-lit`、`aster`、`okx` 标明买入到卖出方向；开仓/平仓各自显示盘口方向及提示。常态“行情实时”标签不再占位，过期或缺失行情的警告保留。
- `frontend/src/styles.css`：桌面压紧差价表列宽；手机按方向、双腿行情、并排双价差和操作区排列。诊断标题在窄屏可换行。
- `backend/app/models/instrument.py`、`backend/app/services/instrument_spreads.py`、`frontend/src/api/types.ts`：同一价差快照新增 `buy_bid`、`sell_ask` 和 `close_spread_pct`，供平仓价差及盘口说明使用。原有开仓价差仍用于选方向和默认排序。
- `backend/tests/test_instrument_spreads.py`、`backend/tests/test_instrument_lookup.py`、`frontend/tests/InstrumentLookupPage.test.tsx`：覆盖新接口字段、计算口径、交易所简称、诊断身份和移除旧列。

## 业务规则

- 开仓价差：`2 * (卖方 Bid - 买方 Ask) / (卖方 Bid + 买方 Ask) * 100`；平仓价差：`2 * (卖方 Ask - 买方 Bid) / (卖方 Ask + 买方 Bid) * 100`。沿用 `spread_engine.midpoint_spread_pct` 的口径，平仓值不是平仓净收益。
- 两项价差都使用当前公开盘口报价，未扣手续费、滑点和资金费用，也不保证目标成交额可按买一/卖一完成。实际交易判断仍须核对双方深度、市场倍率、资金费率周期及报价是否预估。
- 市场仍按 `(exchange, market_type, raw_symbol, multiplier, dex)` 保留精确身份；Hyperliquid 不能只按规范化 ticker 合并。诊断与行情各自保留时间和失败/过期提示。

## 本地验证

- 后端：`python -m pytest -p no:cacheprovider tests/test_instrument_spreads.py tests/test_instrument_lookup.py -q`，12/12 通过。
- 前端：`npm test -- InstrumentLookupPage.test.tsx FloatingWatchPanel.test.tsx --run`，52/52 通过；`npm run build` 和 `git diff --check` 通过。测试运行时有 React `act(...)` 警告，但无失败项。
- 用固定公开行情响应的本地 Playwright 检查 1440、1000、390、320 px：没有页面横向溢出或数值单元格截断；`rh-lit → aster` 可容纳。截图位于 Codex 可视化目录，文件名前缀 `instrument-layout-local-`。这些截图不是线上验收。

## 线上状态与后续

- 功能提交 `632316d` 已推送；GitHub Actions run `36092243451` 的 backend、frontend 两个镜像构建成功，生产机可读取对应镜像 manifest。
- 首次部署前脚本创建 `backups/radar-before-632316d4e38e-20260925T035640Z.db`，`integrity_check=ok`，容器与主机 SHA-256 一致：`fcb52cca42069d21b8f4ad55d5f99f15093bd658bd4463fea9c090ec0b413aef`。
- 备份期间远端分支被并行文档提交推进到 `222b486`。生产仓库经 `git pull --ff-only` 到该提交后，脚本因 HEAD 不等于已验证镜像提交而停止，**未重启容器**；当时后端、前端仍运行健康的 `4e814fb` 镜像。
- 下一步：为最新分支顶端构建并验证两份精确镜像，重新运行 `deploy/linux-update.sh`，检查容器、`/api/health`、`/api/instruments/{symbol}` 的新增字段与线上桌面/手机页面，然后在本文记录最终备份、部署版本和验收结果。
- `.worktrees/`、`output/`、其他模块交接文档与脚本产物属于并行任务；本任务不暂存、删除或回退它们。后续其他功能模块请新开任务，并以本文件交接。
