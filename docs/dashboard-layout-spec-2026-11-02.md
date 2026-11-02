# 实时机会页改版任务书（2026-11-02）

目标：只改 `DashboardPage` 的排版，让机会表进入首屏。数据逻辑、接口调用、状态含义、屏蔽与建卡行为一律不变。

## 允许修改的文件
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/styles.css`：只改 `blocked-strip`、`metric-row`、`exchange-error`、`spread-history`、`astro-preview` 相关规则，或在 Dashboard 现有规则旁新增 `dashboard-` 前缀的规则。不要在文件末尾追加，也不要改其他页面的规则（另有两个分支在并行改 `pair-`、`instrument-` 样式，要避免合并冲突）。
- `frontend/tests/DashboardPage.test.tsx`：只在 DOM 结构变化导致定位失败时调整定位方式，不得删除断言或放宽断言内容。

`TopFilters`、`OpportunityTable` 等组件不改。不要 git push，只在当前分支提交。

## 新的页面顺序

1. **筛选行**：`TopFilters` 原样保留，右侧（或紧接其下同一行）放一个“屏蔽 N”按钮（N 为已屏蔽数量）。点击后弹出 `Popover`，里面是原“全局屏蔽”的输入框、屏蔽按钮和取消屏蔽列表。保留原有的 aria-label（`输入要屏蔽的标的`、`屏蔽输入标的`、`取消屏蔽 X`）、禁用与 loading 逻辑。删除原来单独一行的 `blocked-strip`。
2. **状态行**：一行紧凑文字，替代原来 4 个 `Statistic`、链路异常 Alert、Exchange states 表格和 `excludedStale` 文本。依次显示：
   - 机会数、市场数、列表最大开仓价差（3 位小数加 %，计算方式不变）、已剔除过期 N（N>0 时才显示）。
   - 最右侧是“链路异常 N”标签：N>0 时为警告色，N=0 时显示“链路正常”；health 未加载时显示“链路状态加载中”。点击后展开或收起详情区。
   - 详情区内容：原链路异常列表（不再截断 6 条，全部列出）和原 Exchange states 表格（列定义不变）。展开状态存 localStorage，键 `dashboard-health-expanded`，读写都包 try/catch。
   - `error`（红色查询错误 Alert）保持单独显示在状态行上方。
3. **机会列表**：标题“机会列表”，旁边放 `InfoCircleOutlined` 图标，Tooltip 内容是原 `Typography.Paragraph` 的说明文字（措辞不变）。下面直接是 `OpportunityTable`，props 不变。
4. **两个弹窗**：
   - **价差历史**：不改。
   - **Astro 预览**：
     - 顺序调整为：建卡目标 → arb 提示 → 加载/错误/提交结果/告警/阻断 Alert → 摘要 `Descriptions`（标签中文化）→ 金额与路线表单 → 一个默认折叠的 `Collapse`“技术细节”。
     - 摘要标签中文化：标的 / 机会 ID / 模式 / 允许提交。
     - “技术细节”收纳生成的开平仓阈值和将创建路线的 `Descriptions`、Field assumptions 表、Pair payload、SDK payload。
     - 文案中文化：Generated card defaults → 生成的卡片默认值；Field assumptions → 字段假设；Position value USDT → 单笔金额 USDT；Leverage → 杠杆；Minimum/Maximum notional USDT → 最小/最大名义金额 USDT；Loading Astro preview... → 正在生成预览；Blockers → 当前不能创建；Astro dry-run → Astro 建卡预览。
     - footer、提交逻辑、表单字段 name 不变。
5. 原 `Statistic` 的英文标题不再出现。测试里如果有按 “Opportunities”“Markets”“Exchange states” 定位的断言，改为按新文案定位，断言的数值保持不变。

## 样式
- 状态行高度和单行输入框接近，窄屏时自动换行，不出现横向滚动。
- 沿用现有配色，不引入新颜色体系。

## 验收
在 `frontend` 目录运行：
1. `npx tsc -b`，不能有错误；
2. `npm test`（vitest run），全部通过，贴出汇总行；
3. `npx vite build`，构建成功。

最后输出简短报告：改了哪些文件，以及每个测试改动的原因。
