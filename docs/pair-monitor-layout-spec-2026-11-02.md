# 价差查询页改版任务书（2026-11-02）

目标：只改 `PairMonitorPage` 的排版，让首屏是“查询 → 关键数字 → 价差曲线”。不改任何数据逻辑、接口调用、状态含义、localStorage 键和保存格式。

## 允许修改的文件
- `frontend/src/pages/PairMonitorPage.tsx`（只改 `PairMonitorPage` 组件的 JSX 结构，以及为此需要的小型展示组件）
- `frontend/src/styles.css`（`pair-` 前缀的样式）
- `frontend/tests/PairMonitorPage.test.tsx`、`frontend/tests/PairSpreadChartRange.test.tsx`（只在 DOM 结构变化导致定位失败时调整定位方式，不得删除断言或放宽断言内容）

其他文件一律不改。不要 git push，只在当前分支提交。

## 新的页面顺序（从上到下）

1. **告警**
   - `error`（查询错误，红色）和 `presetSyncError`（带“验证密码”按钮）保持原样，单独显示。
   - 其余 warning：`premiumError`、`dayCompareError`、`fundingRecordError`、`result.warnings`，合成一条状态条，默认显示“N 条提示”和第一条摘要，点击后展开全部。没有提示时不渲染。
   - 验证密码的 Modal 保持不变。
2. **查询卡**（`pair-query-panel`）
   - 第一行：模式切换放在最前，用 `Segmented`（同标的 / 自定义），替代原来第二设置栏里的“自定义”Switch，绑定同一个 `pairSymbolMode` 状态。
   - 第二行：左腿字段 → 交换按钮 → 右腿字段 → 查询范围（小时、采样、自定义秒、刷新）。交换按钮做成只有图标的小按钮（`SwapOutlined`），保留原来的 `aria-label="交换左右标的"`、title 和 `swapPairLegs` 行为。
   - 第三行：查询 / 保存 / 加入浮窗三个按钮。
   - 第四行：“已保存”。改成单行、可横向滚动；保留分组标题（标的名和数量）、点击应用、关闭删除的行为，以及 `SavedPairPresetContent` 的内容。
3. **指标行**：一行 5 张 `MetricCard`。
   - 依次是：左腿价格、可成交开仓价差、可成交平仓价差、盘口中间价差、右腿价格。
   - 左右腿卡片保留原来的报价年龄标签、成交额 `VolumeMetric` 和“查看溢价指数”按钮。
   - 标记价差（辅助）不再单独占卡，作为中间价差卡 `sub` 的末尾一段显示（比值模式显示标记比值）；原来中间价差卡的 sub 内容保留。
   - 原“周期”卡去掉，它的信息（周期、数据范围、观测时间）放到第 4 步价差曲线卡片上方的一行小字里。
   - “样本不足”标签也放在这一行。
4. **价差曲线**：`PairSpreadBacktest`，位置紧跟指标行，组件内部不改。
5. **价格与对比**
   - 先放一个工具条，把原第二设置栏里的这些控件挪进来，状态、回调、`storeLastPairSpreadState` 调用一律原样保留：
     - 溢价对比 Switch 和它的说明文字；
     - 同时段 Switch、跟随查询/指定时间 `Segmented`、天数、开始/结束时间、“查询同时段”按钮。
   - 工具条下面依次放：`PairPriceChart` → `PairDayCompareChart`（开启时）→ `PairPremiumCompareChart`（开启时）。
6. **详情标签页**：用 antd `Tabs` 收纳低频卡片，顺序如下：
   1. 资金费率差（原 `pair-funding-grid` 整块，包含分钟资金费率差图）
   2. 最近价差（原 `pair-detail-grid`）
   3. 市场质量（`PairMarketDataCard`）
   4. 持仓与 OI（`PairPositionStatsCard` + `PairOpenInterestChart`）
   5. 成交额（`PairHourlyVolumeCard`）
   6. 诊断（`PairSpreadDiagnosticCard`）

   要求：
   - 默认选中“资金费率差”。当前标签存到 localStorage（键 `pair-monitor-detail-tab`，读写都包 try/catch），刷新后恢复。
   - 标签页内的图表在切换回来时尺寸要正确，不能出现宽度为 0。如果用的图表库在隐藏状态下测不到尺寸，就对图表类标签使用 `destroyOnHidden`（或当前 antd 版本中对应的属性），或在可见时触发 resize。请先查清当前 antd 版本支持哪个属性。
   - 资金费率分钟记录、诊断等按钮触发的请求逻辑不变；不能因为切换标签而重复发请求或丢失状态（状态都在父组件里，子组件只是展示）。

## 样式
- 沿用现有配色和卡片样式（`pair-query-panel`、`pair-detail-card` 等），不引入新颜色体系。
- 删掉不再使用的 `pair-query-secondary-panel` / `pair-query-options` / `pair-secondary-metric-grid` 等样式（确认没有别处引用后再删）。
- 宽度小于 1280px 时，查询卡第二行要换行：左右腿各占一行，查询范围另起一行，不出现横向滚动条；指标行改成 2–3 列自动换行。现有的移动端媒体查询（`@media` 中的 `pair-` 规则）同步调整。

## 验收
在 `frontend` 目录运行：
1. `npx tsc -b`，不能有错误；
2. `npm test`（vitest run），全部通过，并在报告里贴出汇总行；
3. `npx vite build`，构建成功。

最后输出一份简短报告：改了哪些文件；每个测试改动的原因；antd Tabs 隐藏面板的处理方式。
