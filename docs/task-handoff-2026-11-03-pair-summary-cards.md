# 价差查询页顶部摘要卡交接（2026-11-03）

## 目标、范围与基线

- 依据 `docs/pair-summary-cards-spec-2026-11-03.md`，替换价差查询页中间三张摘要卡。
- 分支：`codex/pair-summary-cards`；基线：`fa88409`。
- 本次用户明确要求仅离线、本地验证与提交：不安装依赖、不联网、不运行 WSL、不推送、不部署。
- 左右标的卡、后端、`frontend/src/api/*`、`components/pairChartV2/` 和其他页面均未修改。
- `PairMonitorPage.tsx` 只增加组件导入并替换挂载：新增 4 行、删除 35 行，合计 39 行。

## 已完成功能与入口

- `frontend/src/components/pairSummary/PairSummaryCards.tsx`：三张卡及资金费倒计时。
- `frontend/src/components/pairSummary/metrics.ts`：资金费归一化、开平差、结算选择、分位和区间统计的纯计算。
- `frontend/src/components/pairSummary/pairSummary.css`：局部样式，沿用原五列布局及响应式规则。
- `frontend/src/pages/PairMonitorPage.tsx`：传入现有格式化工具、开平色调和交易所名称。

### 可成交价差 / 比值

- 开仓保留左 ask / 右 bid，平仓保留左 bid / 右 ask；下方显示对应报价。
- 价差沿用 `signedPct`、`spreadTone`、`closeSpreadTone`；比值沿用 `priceRatio`、`formatPriceRatio` 和原中性色调。
- 开平差 = 开仓 − 平仓；比值同时显示相对平仓的百分比。任一报价缺失时显示 `-`。
- API 价格已做倍率归一化，不再次乘除市场倍率。

### 资金费（左多 / 右空）

- 每腿按自身周期折成小时费率，周期缺失默认 8h，净小时费率 = 右 − 左。
- 主值为净小时费率 × 8；日化 × 24，简单年化 × 24 × 365，单位均为百分数。
- 净收入绿、净支出红、零或缺失中性；现货按零计入并标明无资金费。
- 永续费率缺失保持未知，不当成零；非法周期也不参与计算。
- 逐腿显示交易所、原周期和费率；Hyperliquid 保留 DEX，原始市场名保留在标题提示中。
- 两腿有效结算时间取早者，每秒刷新倒计时，卸载或时间变化时清理旧定时器；已过时间显示 `0h 0m`。
- 下期净值按相同算法归一化到 8h；任一需要资金费的腿缺少下期费率时隐藏该段。双现货不显示下期净值。

### 价差位置

- 分布仅取 `result.points`，比值序列经现有 `ratioChartPoints` 转换。
- 当前快照优先；无有效快照时使用最新有效历史点。
- 分位为区间中小于等于当前值的比例，四舍五入；全相等时为 P100。
- 低 / 均 / 高、紫色当前标记、灰色均值标记和均值偏离均已实现；全相等时两个标记居中，越界标记钳制在横条端点。
- 比值偏离同时显示差值和相对均值百分比，均值为零时不构造相对百分比。
- 近 1h 变化使用不晚于“当前时间 − 1h”的最近样本；无该样本显示 `-`，不插值。历史回退同时回退到历史点时间。
- σ 使用总体标准差（除以 N）；有效点不足两个时，近 1h 变化及 σ 显示 `-`。
- 原标记价差 / 比值辅助行已删除。

## 测试与验收

- `frontend/tests/pairSummaryMetrics.test.ts`：17 条测试，覆盖同 / 不同周期、现货、缺失、年化、下期净费率、较早结算、分位边界、均值偏离、1h 历史不足及 σ。
- `frontend/tests/PairSummaryCards.test.tsx`：4 条测试，覆盖比值及倍率、缺失展示、现货与 DEX、净费率颜色、倒计时刷新及清理。
- `frontend/tests/PairMonitorPage.test.tsx`：只调整受替换卡片影响的断言，保留其他断言；新增一条查询后三卡集成测试，现共 68 条。
- 定向测试：3 个文件、89 条通过。
- 完整测试：30 个文件、410 条通过；有既有 Ant Design 弃用 / React act 警告，无失败。
- `git diff --check` 通过。

frontend 目录下原命令验收汇总：

```text
npx tsc --noEmit: PASS (exit 0)
npm test: PASS (exit 0)
npx vite build: PASS (exit 0)
```

所有 npm / npx 命令均设置 `npm_config_offline=true`，使用现成依赖。首次沙箱运行拦截了依赖链接目录内的 Vitest 缓存及 Vite 临时配置写入；允许该本地写入后，原命令完整重跑通过。未改动依赖或配置文件。

## Git、线上状态与遗留文件

- 本地提交标题：`feat(frontend): merge open/close cards, add funding and spread position cards`。
- 按用户要求不 push；未接触服务器、未备份线上数据库、未部署，线上版本和行为未在本任务验证。
- 初始仅有未跟踪文件 `docs/pair-summary-cards-spec-2026-11-03.md`，保持原样且不纳入代码提交。
- 本地验证日志 `frontend/pair-summary-*.log` 和 `frontend/dist/` 为忽略产物，不提交；既有 `frontend/node_modules` 目录链接保持原样。
- 无已知功能偏差；上述 1h 选点、总体 σ、全相等标记居中是明确采用的边界口径。
- 尚未做浏览器视觉验收。若后续需要视觉微调或线上发布，请新建任务，在本分支交付提交上继续；发布需另行授权联网和推送。
