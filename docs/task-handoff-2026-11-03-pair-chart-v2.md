# 价差查询页新版图表交接 · 2026-11-03

## 目标、范围和基线

- 按 `docs/pair-chart-v2-spec-2026-11-03.md` 实现 lightweight-charts v2，原 SVG 图表完整保留并可切换。
- 工作树：`.worktrees/pair-chart-v2`；分支：`codex/pair-chart-v2`；起始提交：`f3bc2fd9fc3a25860ecab22791f1cc048074b9b0`。
- 用户明确限定离线、本地提交，不安装依赖、不使用 WSL、不推送。本次未联系服务器，未备份或部署，线上版本未改变。
- 初始已有 `frontend/package.json`、`frontend/package-lock.json` 的 lightweight-charts 5.2.1 依赖变更，以及未跟踪的 spec；均属本次功能所需，保留原内容并纳入提交。
- 后端、`frontend/src/api/*` 和其他页面未修改。

## 实现与关键入口

| Spec | 实现 |
| --- | --- |
| 版本切换 | 原图卡片上方 `Segmented`；默认 v2；`pair-chart-version` 保存 v1/v2；读写异常容错，非法值回到 v2。 |
| 保留原版 | v1 使用原 `PairSpreadChart`、`PairPriceChart`、`PairMinuteFundingDiffChart`；v2 将这三块合并为一个双 pane 图表。其他卡片保持原挂载。 |
| 页面改动边界 | `PairMonitorPage.tsx` 新增 18 行、删除 6 行，共 24 行；主要是开关、挂载和回调传递。现有查询增加可选 `preserveInterval`，仅 v2 加载历史使用，确保超过 168 小时也不被原默认规则切为小时采样。 |
| 工具栏 | 多左空右；复用 `swapPairLegs`；时效读取与顶部盘口卡相同的 `current.leg*.age_ms`，≤2 秒绿、≤10 秒黄、其余红；缺数据显示未知。按当前结果的身份、倍率和间隔调用 `runQuery`，小时数走 `clampHours`。查询按钮显示 loading。 |
| 主图 | 一个 chart；右轴价差/原 `ratioChartPoints` 比值，绿色粗线；左轴两腿蓝/橙细线，用现有完整腿标签作为 series title。ratio 模式各自首个有效正价格=100，并标明指数化。 |
| 阈值 | 两条琥珀色虚线 `createPriceLine`；Popover 内输入开仓/平仓百分数；`pair-chart-thresholds` 容错读写；空不画，零合法。图例可隐藏三条主线或阈值。 |
| 悬停 | 显示本地时间、价差/比值、原始两腿价格，以及该时间之前最近一次净结算，不读取未来结算。没有对应点显示破折号。 |
| 区间回测 | Shift+鼠标拖动或开启区间开关后拖动；半透明选区跟随时间轴变化；仅 mouseup 提交毫秒范围。Esc/清除走原回调；原 `RangeBacktestPanel` 与 long1_short2 默认方向不变。切换版本/交易对重新挂载并清除旧选区。 |
| 资金费副图 | v5 `addSeries(..., 1)` 第二 pane，共享时间轴和十字线。两腿实时费率优先，单腿无有效实时采样时退回该腿历史阶梯线；净柱按相同结算秒的右费率减左费率，只有一腿时另一腿按零，正绿负红。 |
| 费率与倒计时 | 标题行显示两腿最新费率、可用的资金费周期，以及有效且尚未过去的 `funding_next_time` 倒计时；未知或过期的下一结算时间不推算。 |
| 主题与生命周期 | 浅色灰网格、`autoSize: true`、保留默认 attributionLogo；卸载解除监听并 `chart.remove()`。 |
| 时间 | dayjs UTC 解析、转整数秒，升序去重（同秒保留最后一条）；本地 dayjs 的 timeFormatter/tickMarkFormatter 显示。 |

组件文件位于 `frontend/src/components/pairChartV2/`：

- `PairChartV2.tsx`：主组件、工具栏、两个 pane、阈值、图例、悬停和资金费摘要。
- `useRangeSelection.ts`：选择状态、坐标转换、事件监听、缩放后选区重绘与清理。
- `data.ts`：时间、指数化、资金费匹配/净结算、比值阈值转换、最近结算查找。
- `storage.ts`：版本及阈值 localStorage 容错。
- `pairChartV2.css`：组件局部样式。
- `data.test.ts`、`storage.test.ts`、`PairChartV2.test.tsx`、`PairMonitorPageV2.test.tsx`：17 个新增测试；`chartTestUtils.ts` 提供 mock/fixture。
- `frontend/tests/setup.ts`：jsdom 下 mock lightweight-charts 的画布 API。
- `frontend/tests/PairMonitorPage.test.tsx`：仅在旧回归测试的初始化中明确选择 v1，原有断言全部保留。

## 业务规则与实现选择

- 使用现有 `pairDisplayPoints(result)`，保留原图补入最新盘口中价、历史尾点去重的口径。
- 净结算费率是百分数收益率，不是美元金额；美元回测继续由原 `RangeBacktestPanel` 处理。
- 历史资金费按交易所、标的、DEX 严格区分。Hyperliquid 显式 DEX 与原始符号前缀冲突时拒绝匹配；不能将不同 DEX 的同 ticker 合并。
- 阈值输入始终是原价差率百分数。ratio 模式按原对称价差定义转换为 `(200 - pct) / (200 + pct)`，只接受 `-200 < pct < 200` 的可绘制比值；设置弹层说明了这一口径。
- 7 天按钮在已经达到或超过 7 天时禁用，避免加载按钮反而缩短历史；“再加载 1 天”仍可继续扩展。

## 验证

全部在 Windows 原生 `frontend` 目录运行，npm 设置 offline，未安装或下载依赖。

`npx tsc --noEmit` 没有原生输出；退出码为 0。验收执行器的汇总行：

```text
npx tsc --noEmit exit code: 0
```

`npm test` 汇总原文：

```text
 Test Files  28 passed (28)
      Tests  373 passed (373)
   Duration  123.47s (transform 3.56s, setup 339.44s, collect 173.62s, tests 315.90s, environment 105.42s, prepare 8.23s)
```

`npx vite build` 汇总原文：

```text
✓ 3074 modules transformed.
✓ built in 7.92s
```

- `git diff --check` 通过。
- 测试日志有 antd 弃用警告、React act 警告及 `tests/useRadarStore.test.tsx` 中 CSS mock 的 `TypeError ... content` 输出；Vitest 退出码 0，无失败测试。本次未修改该模块。
- 浏览器插件连接成功初始化，但浏览器枚举为空，不能做真实 Canvas 浏览器视觉/交互验收。已验证的是类型、mock 下交互/生命周期、页面集成与生产构建，不能等同于真实画布验收。

## 本地交付与下一步

- 提交信息：`feat(frontend): add v2 pair spread chart with legacy toggle`。提交号可用 `git log -1 --oneline` 查看。
- 不推送；不部署；无线上验证结论。
- 验收日志保留在工作树根目录 `pair-chart-v2-tsc.log`、`pair-chart-v2-tests.log`、`pair-chart-v2-build.log`、`pair-chart-v2-targeted.log`，均受已有 `*.log` 规则忽略，不纳入提交。`frontend/dist/` 为构建产物，也不纳入提交。
- 没有其他归属不明的初始改动；初始 spec/依赖变更保留并提交。没有修改任何现有断言。
- 后续在具备浏览器连接的环境检查真实画布、窄屏、平移缩放与 Shift 拖选，并在用户另行授权后推进线上发布。下一个独立模块应新建任务，使用本交接作为上下文。
