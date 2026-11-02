# 标的查询页改版任务书（2026-11-02）

目标：只改 `InstrumentLookupPage` 的排版，让跨市场差价表靠前，市场明细默认紧凑显示。数据逻辑、接口调用、状态含义、建卡和保存行为一律不变。

## 允许修改的文件
- `frontend/src/pages/InstrumentLookupPage.tsx`
- `frontend/src/styles.css`：只改 `instrument-` 前缀的规则，新增规则放在现有 `instrument-` 规则附近。不要在文件末尾追加，也不要改其他页面的规则（另有两个分支在并行改 `pair-` 和 Dashboard 样式，要避免合并冲突）。
- `frontend/tests/InstrumentLookupPage.test.tsx`：只在 DOM 结构变化导致定位失败时调整定位方式，不得删除断言或放宽断言内容。

不要 git push，只在当前分支提交。

## 新的页面顺序

1. **查询卡**（一张卡片，包含以下三部分）
   - 标题行：标题“标的查询”和 base/quote 副标题保留；搜索框、查询、保存、加入浮窗、立即刷新、自动刷新放在同一行，保留原有 aria-label 和禁用逻辑。原来单独一行的“诊断口径”改成标题旁一个 `InfoCircleOutlined` 图标，Tooltip 里放原文案；保留 `role="note"` 的可访问文本（可以用视觉隐藏的元素承载）。
   - 已保存：放在搜索行下方，单行横向滚动，行为不变。
   - 摘要：原 `instrument-summary-band` 的 8 项改成一行紧凑的“标签 值”序列，窄屏换行。原 `instrument-ratio-source` 一行的内容（合约/现货 24h 成交额统计、市值来源、CoinGecko 币种选择、重试按钮、口径说明）收进摘要行末尾的“来源与口径”`Popover`。CoinGecko 选择器和重试按钮在 Popover 里仍然可用，aria-label 不变。
2. **告警**：`error`、同名资产 Alert、无精确匹配 Alert、底层资产 Select 保持原逻辑，放在查询卡下方。
3. **页内导航**：一行锚点标签，滚动时吸顶（`position: sticky`，注意不要被页面头部遮挡），依次为：跨市场差价 N、市场明细 N、走势、充提通道 N、指数成分 N。没有数据的区块不显示对应标签。点击后平滑滚动到对应 section（给各 section 加 id）。
4. **区块顺序**：
   1. 跨市场差价（原样，含筛选和固定排序）
   2. 市场明细（原“精确行情市场”）
   3. 走势与价差（原样，保持默认折叠）
   4. 现货充提通道
   5. 合约指数成分

   其中 4 和 5 改成默认折叠，标题行显示数量，点击展开；展开状态存 localStorage，键分别为 `instrument-transfer-open`、`instrument-index-open`，读写都包 try/catch。
5. **市场明细的紧凑模式**
   - 标题行加一个 `Segmented`：紧凑 / 完整，默认紧凑，选择存 localStorage，键 `instrument-market-density`。
   - 紧凑模式下每个市场一行，列为：市场（交易所 + 现货/永续 + DEX + 原始符号，过期或身份待核验时显示小标签）、Bid / Ask、24h 成交额、资金费率（现货显示“现货”）、0.1% 买/卖深度、交易限制标签（沿用原有的颜色和文字判断）。
   - 点击某一行展开，显示该市场原来的完整内容：6 组指标加 `MarketDiagnosticDetails` 或缺诊断时的提示，直接复用现有 JSX，抽成组件即可，不要重写判断逻辑。
   - 完整模式与现在的显示完全一致。
   - 原有筛选（市场类型、状态、只看异常）对两种模式都生效。
   - `role="table"`、`role="row"`、`role="cell"` 和 `aria-label="精确行情市场"` 要保留：紧凑模式下，展开后的完整内容在同一 row 内，或作为紧随其后的 row。
6. Astro 弹窗不改。

## 样式
- 沿用现有 `instrument-` 配色和卡片样式，不引入新颜色体系。
- 宽度小于 1280px 时搜索行和摘要换行，紧凑表格允许在自身容器内横向滚动，页面不出现横向滚动条。

## 验收
在 `frontend` 目录运行：
1. `npx tsc -b`，不能有错误；
2. `npm test`（vitest run），全部通过，贴出汇总行；
3. `npx vite build`，构建成功。

如果现有测试在默认紧凑模式下找不到完整模式里的元素，可以在测试里先切到“完整”或展开对应行再断言，并在报告中逐条说明。最后输出简短报告：改了哪些文件，以及每个测试改动的原因。
