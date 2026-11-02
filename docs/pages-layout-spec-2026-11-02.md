# 五个页面排版优化任务书（2026-11-02）

目标：只改排版和文案，让每页的主表或主图进入首屏。数据逻辑、接口调用、状态含义、表单字段 name、保存行为一律不变。

## 允许修改的文件
- `frontend/src/pages/AnnouncementsPage.tsx`
- `frontend/src/pages/IndexComponentChangesPage.tsx`
- `frontend/src/pages/FundingArbitragePage.tsx`
- `frontend/src/pages/PremiumIndexPage.tsx`
- `frontend/src/pages/AccountConnectionsPage.tsx`
- 这些页面用到的子组件，仅当子组件只被这一页使用时才可修改（例如 `FundingFollowPanel`）。修改前先用 grep 确认引用方。
- `frontend/src/styles.css`：只改上述页面专用的规则，新增规则放在该页现有规则附近。不要在文件末尾追加，也不要改 `pair-`、`instrument-`、Dashboard 和设置页的规则（另有分支在并行修改）。
- 上述页面对应的 `frontend/tests/*.test.tsx`：只在 DOM 结构变化导致定位失败时调整定位方式，不得删除断言或放宽断言内容。

不要 git push，只在当前分支提交。

## 各页要求

### 1. 交易所公告 `AnnouncementsPage`
- “公告记录”表格紧跟页头，进入首屏。
- 设置面板改为默认折叠的 `Collapse`“抓取与推送设置”，放在表格上方，只占一行标题。展开状态存 localStorage，键 `announcements-settings-open`，读写都包 try/catch。
- 删除和表单重复的 10 个状态标签以及重复的提示条，信息以表单为准。
  - 如果某个标签显示的是表单里看不到的运行状态（例如上次抓取时间、错误），不要删除，改成折叠标题右侧的一行小字。
- 页面只保留一个页头标题。

### 2. 指数成分变更 `IndexComponentChangesPage`
- “监控标的”面板改为默认折叠的 `Collapse`，标题显示监控数量。展开状态存 localStorage，键 `index-components-watch-open`。
- 市场图表和变更表上移，紧跟筛选栏。
- 标题栏里“查询”和刷新两个按钮调用的是同一个 load，只保留一个“查询”按钮，并保留它的 loading 状态。
- 两行开关的说明文字精简成一句；完整说明放进 Tooltip。

### 3. 资金费率套利 `FundingArbitragePage`
- 主候选表紧跟统计卡。
- “策略参数”表单和“Astro 预建”面板分别改成默认折叠的 `Collapse`，放在主表上方。
  - 折叠标题右侧显示一句摘要，例如关键参数值、预建开关状态。
  - 用 `forceRender`，或保持 Form 始终挂载，保证折叠后表单状态和校验不丢。
  - 保存按钮放在各自折叠区的标题栏右侧，或折叠区顶部，不要只放在底部。
- 统计卡标签中文化：ENTER → 可入场，HOLD → 持有，ADL → 减仓风险（如果代码里 ADL 指别的含义，按实际含义翻译，并在报告中说明）。
- 页面其他英文：“滚动一周期 carry 预览” → “滚动一周期收益预览”；“Hyper 优先” → “Hyperliquid 优先”。
- 预建面板里多条警告和说明合并成一条 Alert，用列表展示。

### 4. 溢价指数 `PremiumIndexPage`
用户用这一页看更详细的指数和资金费率信息，所以这些信息不能删减，只调整组织方式。
- 页面顶部最多 3 条告警，合成一条可展开的提示。红色错误仍单独显示。
- 新顺序：
  1. 查询栏和已保存
  2. 图表
  3. 指标区
  4. `FundingFollowPanel`
  5. “最近溢价/偏离”表
- 指标区把 7 张指标卡按主题分成两组，每组一行，组标题用小字：
  - “指数与溢价”：标记价、指数价、溢价、偏离等；
  - “资金费率”：当前、预估、跟随估算等，按代码中卡片的实际含义归组。
- 英文副标签 mark price / index price 改为“标记价格”和“指数价格”。
- 查询栏在宽度小于 1280px 时换行，不出现横向滚动条。

### 5. 账户连接 `AccountConnectionsPage`
- 测试结果 Alert 挪到账户表格上方，紧跟错误和警告。
- 仪表盘密码输入栏：已保存有效密码时折叠为一行“已验证 · 修改”，未保存时保持展开。判断方式沿用页面现有的状态，不新增接口。

## 验收
在 `frontend` 目录运行：
1. `npx tsc -b`，不能有错误；
2. `npm test`（vitest run），全部通过，贴出汇总行；
3. `npx vite build`，构建成功。

报告要逐页说明改了什么，以及每个测试改动的原因。
