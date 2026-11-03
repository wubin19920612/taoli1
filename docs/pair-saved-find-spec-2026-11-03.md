# 价差查询“已保存”区好找化（2026-11-03）

## 问题
价差查询页（`frontend/src/pages/PairMonitorPage.tsx`，`savedPresetGroups` 渲染处，约 5908 行）的“已保存”区是单行横向滚动（`.pair-saved-group-list { overflow-x: auto }`），存了十几个价差对后只能左右拖动找；同一标的（如 ANTHROPIC 跨 hl/rh/bg/gt/li/ok）每个都单独成块、重复写标的名。用户反馈“不好找我想要的”。

## 目标
1. **分组按左腿标的**：`groupSavedPairPresets` 改为按左腿短标的（`shortSavedSymbol(leg1_symbol)`）分组；右腿标的不同的自定义对也放进左腿标的组，标签内照常显示 `左 / 右` 标的。组标题 = 标的名 + 数量。组内标签不再重复显示组标的名（同标的对只显示交易所组合；自定义对显示 `交易所 左标的 / 交易所 右标的`）。组按最近保存时间倒序，组内顺序保持现状。
2. **换行布局，去掉横向滚动**：每组一行（组标题在左，标签区 `flex-wrap: wrap`），组之间纵向排列；窄屏（现有手机断点）组标题在上、标签在下。
3. **搜索框**：“已保存”标题旁加 antd `Input`（`allowClear`，size small，placeholder“搜标的/交易所”，宽约 180px）。不区分大小写，匹配左右腿短标的、交易所 id（如 `hyperliquid`）、`ExchangeChip` 的缩写（如 `hl`）和中文名（若有）。过滤后空组隐藏；无结果显示“无匹配”。搜索词只存组件状态，不持久化。
4. **折叠**：未搜索时，组数 > 4 只显示前 4 组，底部“展开全部（共 N 组）/收起”文字按钮；搜索时显示全部匹配组。展开状态可存 localStorage（try/catch 包裹），键名 `pair-saved-expanded`。
5. **当前项高亮**：与当前表单配置相同（复用 `pairConfigId` 比较）的标签加 `pair-saved-tag-active` 类（边框加深/底色）。
6. 点击应用、× 删除、服务端同步等现有行为全部不变；`MAX_SAVED_PAIR_PRESETS`、存储格式不变；不改后端。

## 范围
- 只改：`frontend/src/pages/PairMonitorPage.tsx`、`frontend/src/styles.css`（只动 `.pair-saved-*` 相关规则）、`frontend/tests/PairMonitorPage.test.tsx`（新增用例）。
- 不改其他页面（`premium-saved-*` 与 `.pair-saved-presets` 共用规则时不能影响溢价指数页：需要时拆分选择器）。

## 测试（新增）
- 同左腿标的多个对合成一组，组标题含数量；组内标签不重复标的名。
- 搜索 `hl` 只留含 hyperliquid 的标签；搜索不存在的词显示“无匹配”；清空恢复。
- 5 组时默认只见 4 组，点“展开全部”见 5 组。
- 当前查询配置对应标签有 active 类。

## 验收命令（在 frontend 目录）
- `npx tsc -b`
- `npm test`
- `npx vite build`

## 约束
- 不要 git push，不要部署；完成后在当前分支本地提交（`feat(frontend): make saved pair presets searchable and grouped`）。
- 不跑 WSL；不改 node_modules（是目录链接）。
