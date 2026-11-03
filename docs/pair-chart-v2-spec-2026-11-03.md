# 价差查询页 新版图表（v2，可切回原版）spec 2026-11-03

## 目标
价差查询页（`frontend/src/pages/PairMonitorPage.tsx`）增加"新版图表"，用 lightweight-charts@5.2.1（已安装，package.json 已改）实现；**原版图表全部保留**，用户可切换，先试用。

## 切换
- 在原价差曲线卡片位置上方放一个 antd `Segmented`：「新版图表 / 原版」，默认「新版图表」，选择存 localStorage key `pair-chart-version`（值 `v2`/`v1`，读写 try/catch，非法值按 v2）。
- v1：页面与现在完全一致（PairSpreadChart+拖选回测、PairPriceChart、PairMinuteFundingDiffChart 等原样渲染）。
- v2：用新组件替换 **PairSpreadChart 区块、PairPriceChart、PairMinuteFundingDiffChart** 三处；其他卡片（同时段对比、溢价对比、OI、成交量等）不动。RangeBacktestPanel 拖选收益在 v2 里继续可用（见下）。

## 新组件（放新文件 `frontend/src/components/pairChartV2/`，不要再往 PairMonitorPage.tsx 塞大段代码；PairMonitorPage 只加切换和挂载，≤60 行改动）
1. **工具栏**
   - 方向：`{左腿交易所} 多 / {右腿交易所} 空`（按现有 long1_short2 口径），旁边「⇆ 切换」复用页面已有的左右互换逻辑（把回调传进来）。
   - 两腿时效：`Binance 0.2s · Bitget 0.1s`，用页面已有的盘口时效数据（卡片上"盘口中价 0.0s"那个值），≤2s 绿、≤10s 黄、否则红。
   - 加载更多：「再加载 1 天」「加载至 7 天」，用现有查询函数以 hours+24 / hours=168 重新查询（保持交易所、标的、间隔不变；hours 已有 clampHours）。查询中按钮 loading。
2. **主图（一个 chart，两个价格轴）**
   - 右轴：价差 `spread_pct`（ratio 模式时用现有 ratioChartPoints/priceRatio 口径，标题跟随原版），粗线 #1D9E75，lastValue 标签开启。
   - 左轴：两腿价格 `leg1_close` 蓝 #378ADD、`leg2_close` 橙 #D85A30，细线，lastValue 标签带交易所名（用 series title）。两腿价格量级差很大（价格比值模式 pairUsesRatio=true）时左轴改为"以首点=100 的指数化"并在图例注明"指数化"。
   - 阈值线：开仓阈值、平仓阈值两条 `createPriceLine` 虚线（琥珀色），数值由图上「设置」小弹层输入（%），存 localStorage `pair-chart-thresholds`，空则不画。
   - 图例：价差 / 价格 A / 价格 B / 阈值，可点击隐藏（applyOptions visible）。
   - 十字线悬停：图内浮层显示时间、价差、两腿价格、该时刻最近一次结算的净资金费。
   - 拖选区间：按住 Shift+拖动（或工具栏「区间」开关打开后拖动）选区，松开调用和原版相同的 onRangeSelect(startMs,endMs)，Esc/清除按钮调用 onRangeClear；选区用半透明矩形叠加层画出。
3. **资金费副图**（同一 chart 用 v5 panes 的第 2 个 pane，或独立 chart 但时间轴严格同步 + 十字线同步）
   - 两条线：左/右腿资金费率（来源 `realtime_funding` 的 left_rate_pct/right_rate_pct；没有时用 `funding_history` 按交易所分组做阶梯线）。
   - 净结算柱：`funding_history` 每个结算时刻，按方向（多左空右）净收益 = 右腿费率 − 左腿费率（同一时刻两腿都有才算；只一腿有时按另一腿 0），正绿 #639922 负红 #E24B4A 的 histogram。
   - 标题行显示两腿最新费率和下次结算倒计时（若数据里能算出，算不出就不显示）。
4. 署名：保留 lightweight-charts 默认 attributionLogo。
5. 主题：浅色，网格/文字颜色读现有 CSS 变量或用与页面一致的灰色；容器宽度自适应（ResizeObserver 或 autoSize:true）；卸载时 chart.remove()。
6. 时间：bucket_at 转 UTC 秒时间戳，按 lightweight-charts 要求升序去重；轴上时间按本地时区显示（localization.timeFormatter / tickMarkFormatter 用 dayjs local）。

## 不可修改
后端所有文件；`src/api/*` 不改接口（可只读类型）；其他页面。

## 测试（vitest，放 `frontend/src/components/pairChartV2/*.test.ts(x)`）
- 纯函数单测：时间戳转换去重升序、净结算柱计算（两腿/单腿/方向）、指数化、阈值 localStorage 读写容错、版本切换 key 读写容错。
- PairMonitorPage 现有测试全部保持通过（jsdom 下 lightweight-charts 需 mock：在新测试/`setupTests` 里 vi.mock，不得放宽或删除旧断言）。
- 加 1 个页面测试：默认 v2 时渲染新图容器，切到「原版」后原版 `pair-spread-chart` 出现，刷新后保持。

## 验收命令（frontend 目录）
- `npx tsc --noEmit`
- `npm test`（全部通过）
- `npx vite build`

不要 git push；完成后 git commit 到当前分支 codex/pair-chart-v2，提交信息 `feat(frontend): add v2 pair spread chart with legacy toggle`。
