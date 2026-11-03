# 新版价差图真实数据问题修复交接 · 2026-11-03

## 范围、分支与基线

- 工作树：`.worktrees/pair-chart-v2`；分支：`codex/pair-chart-v2`；修复基线：`cc98ce7`。
- 只修 `frontend/src/components/pairChartV2/` 与 `PairMonitorPage.tsx` 挂载回调；spec 为 `docs/pair-chart-v2-spec-2026-11-03.md`。
- 本文覆盖初版交接 `docs/task-handoff-2026-11-03-pair-chart-v2.md` 中的资金费回退、结算对齐和加载上限规则。
- 开始时工作区干净，无初始未跟踪文件。用户要求离线、本地提交；不安装依赖、不联网、不运行 WSL、不推送。

## 已修复与代码入口

1. `data.ts` 的 `priceFormatFor` 按每条腿最新价格绝对值设置精度：>=1000 两位、>=1 四位、<1 六位；指数化仍为两位。`PairChartV2.tsx` 对价格 series 应用同一 `priceFormat`，价格轴和 lastValue 标签精度一致。
2. `historyRates` 先按交易所、symbol、DEX 严格匹配，再将 UTC 时间四舍五入到最近分钟，同腿同分钟保留最新记录。`netSettlements` 与悬停 `latestAt` 共用对齐时间；多左空右仍为右费率减左费率，单腿缺失按零。保留 Hyperliquid DEX 与原始符号前缀边界。
3. 每条腿有效实时费率不足两点时使用该腿历史阶梯，再接上最新实时点；相同时间用实时值覆盖，不保留实时点之后的历史。两点以上实时数据保持优先。
4. `PairChartV2.tsx` 使用 v5 pane 的 `setStretchFactor(0.7/0.3)`，让资金费副图稳定占约 30%，替代初始化时设置固定高度。
5. `MAX_CHART_HOURS=168` 同时用于工具栏和页面回调。一天按钮只加载到 `min(hours+24,168)`；达到上限两个加载按钮禁用，一天按钮显示“超过 7 天后端会改为小时周期”。查询期间按钮也禁用。

后端只读核对：`backend/app/services/pair_spread_query.py` 的 `_resolve_historical_interval_seconds` 会对超过七天的分钟采样改用至少 3600 秒；没有修改后端或 API。页面测试的响应 mock 体现该规则，并直接断言请求 hours<=168，覆盖 160→168 和到顶不再请求。

## 结算与时区证据

- `chartTestUtils.ts` 新增 Binance/Bybit fixture，包含 Binance `.001000Z` 与 Bybit 整秒时间。
- 单测验证：08:00Z 净费率 `0.005216-0.008758=-0.003542%`，16:00Z 为 `0.009346-0.009118=0.000228%`，00:00Z 为零；另测 29.999/30 秒舍入边界、去重、单腿和最近结算不向未来取值。
- 本机实测 `.001000Z` 原来就会被截为与整秒相同的 UTC 秒；仅这组毫秒差异未能复现两柱。新分钟对齐覆盖秒级偏差。
- 悬停和时间轴继续按浏览器本地时区显示，没有固定加八小时。16:00Z 在 Asia/Shanghai 为次日 00:00，在本机 Asia/Seoul 为次日 01:00；01:00 本身符合 UTC+9，并非重复时区转换。
- 悬停组件测试在本机时区与 `TZ=Asia/Shanghai` 分别通过，独立用 Intl 验证显示时间并检查结算时间边界。

## 测试与构建

全部在 Windows 原生 `frontend` 运行，设置 npm offline，使用现有依赖。

```text
npx tsc --noEmit exit code: 0
 Test Files  28 passed (28)
      Tests  388 passed (388)
   Duration  91.94s (transform 3.61s, setup 247.09s, collect 129.27s, tests 228.84s, environment 88.69s, prepare 6.77s)
✓ 3074 modules transformed.
✓ built in 12.55s
```

- 定向测试：4 文件、32 项通过。UTC+8 悬停验证：1 项通过，其余 9 项按筛选跳过。
- 新增/调整测试：`data.test.ts`、`PairChartV2.test.tsx`、`PairMonitorPageV2.test.tsx`；`chartTestUtils.ts` 提供 fixture 和 pane API mock。原版页面回归测试未修改。
- 日志存在 antd/React 测试环境警告，完整测试退出码 0。
- `git diff --check` 通过。给定数据验证为本地 fixture/组件测试，真实 Canvas 的净柱视觉仍需浏览器复核。

## 本地交付、线上状态与后续

- 提交消息：`fix(frontend): pair chart v2 price precision, funding alignment, load cap`。提交号见 `git log -1 --oneline`。
- 未推送、未联系生产服务器、未备份或部署；线上版本未改变，无本轮线上验收结论。
- 验收日志 `pair-chart-v2-fixes-*.log` 及 `frontend/dist/` 为已有忽略规则覆盖的本地诊断/构建产物，不提交；无其他未跟踪文件。
- 若同一组数据在浏览器仍异常，下一任务应保留原始响应并核对实际浏览器时区、净柱视觉和展示版本；独立新模块另建任务，使用本文交接。
