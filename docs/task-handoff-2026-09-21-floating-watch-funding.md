# 浮窗资金费率展示交接

## 目标与范围

本任务只处理 Astro/浮窗模块：在运行中的 Astro 卡片行内增加双腿资金费率信息，同时保持监控窗口简洁紧凑。不修改告警规则、资金费率策略、Astro 建卡逻辑或其他业务模块。

验收目标：

- 每个能取得双腿实时行情的 Astro 标的显示买腿、卖腿的当前单次结算资金费率。
- 每条永续腿同时显示自己的结算周期，不能把异周期费率混成一个周期值。
- 现货腿显示“现货”，缺失永续费率显示 `--`，不把缺失值冒充为 0。
- 保留 Hyperliquid 具体 DEX 查询，不按规范化 ticker 模糊替代。
- 桌面和窄屏均保持单行、无横向溢出，不增加独立卡片或说明区。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`5a665d6`（`docs: clarify Lighter HOOD market role`）
- 功能提交：`7389c67`（`feat: show funding rates in Astro watch`）

## 已完成功能

- Astro 卡片在“当前价差”下增加一条紧凑的“资金费率”行。
- 展示格式为 `买 +0.0123%/8h · 卖 -0.0046%/4h`，保留正负号、四位小数和各腿原始周期。
- 现货腿显示为 `买 现货` 或 `卖 现货`；永续费率缺失时显示 `--/?h` 或已知周期。
- 复用卡片实时行情快照，不新增接口或额外轮询。
- 局部缩小 Astro 行内间距和资金数值字号；浮窗宽度、交互、价差、阈值和持仓区均保持不变。

## 关键代码入口

- `frontend/src/components/FloatingWatchPanel.tsx`
  - `astroFundingMetric()` 格式化现货、缺失值、费率和周期。
  - Astro 卡片渲染区输出买腿/卖腿资金费率行。
- `frontend/src/styles.css`
  - `.floating-watch-astro-funding` 约束字号和单行展示。
- `frontend/tests/FloatingWatchPanel.test.tsx`
  - 覆盖异周期正负费率、现货腿和缺失永续费率。

## 重要业务规则

- 资金费率直接使用各腿 `MarketSnapshot.funding_rate_pct`，周期使用 `funding_interval_hours`；不在浮窗内做跨周期归一化或收益推断。
- 买/卖顺序与 Astro 卡片实际买腿、卖腿一致；上方路线行保留交易所、市场类型、价格和 Hyperliquid DEX。
- 现货没有资金费率，必须明确显示“现货”，不能显示 `0.0000%`。
- 实时行情缺失时继续显示既有明确错误，不伪造资金费率。
- 此行只是行情信息，不替代完整交易判断；实际执行仍需结合可成交价格、双边成交额、手续费、市场倍率和数据时效。

## 测试结果

- `npm test -- FloatingWatchPanel.test.tsx`：14/14 通过。
- `npm run build`：TypeScript 检查和 Vite 生产构建通过。
- `git diff --check`：通过。
- 全量前端测试：151/153 通过；未改动的 `SettingsPage.test.tsx` 有 2 项基线失败，一项超过 15 秒，一项找不到既有“实盘灰度”文案。本任务专项测试和生产构建均通过。
- 真实数据视觉检查：`900px` 桌面和 `390px` 窄屏均无资金行或数值区溢出；窄屏每条资金行高度为 `17px`。

## 数据库备份

- 文件：`backups/radar-20260921T021100Z-pre-floating-watch-funding.db`
- 大小：`721825792` 字节。
- SHA-256：`d888b0419cf9b35951ad392c214601d38d3561ad5fa6df8477ff3d5fd6d54dc5`
- SQLite `PRAGMA quick_check`：`ok`。

## 线上状态

- 服务器使用 `git pull --ff-only` 更新到功能提交 `7389c67`。
- `docker compose build --pull` 和 `up -d --remove-orphans` 成功；没有执行 `down -v`。
- 前端、后端容器均为 `healthy`。
- `/api/health` 返回 `status=ok`，验收时 8 个交易所均为 `healthy`、`exchange_errors={}`。
- 生产独立浮窗实际返回 `Astro 9 · 1 持仓`；可用行情卡片显示真实买卖腿费率和 `1h/8h` 等各自周期。
- 生产 `390px` 浏览器检查的 6 条可见资金行均无自身或数值区溢出，页面无告警提示。

## 已知问题

- 如果任一腿实时行情缺失，卡片按既有规则显示明确行情错误，不显示资金费率行；例如验收时 `GIGADEVICE` 缺少 Hyperliquid 永续实时行情。没有可靠数据时不会估算或补零。
- 全量前端测试仍有上述 2 个设置页基线失败，与本任务文件和调用链无关；后续应在单独设置模块任务中处理。
- 资金费率数值会随交易所行情变化，页面展示的是当前快照，不是收益承诺。

## 未跟踪文件

本任务未提交、删除或覆盖既有本地产物：

- `output/edge-profile-codex/`
- `output/dexe_bybit_bitget_chain_probe/`
- `output/floating-watch-*.png`，包括本任务本地和线上视觉验收截图。
- `output/index-auto-watch-*.png`
- `script/dexe_bybit_bitget_chain.py`

服务器继续保留既有未跟踪运维文件：

- `.env.backup-codex-20260911-1425`
- `.env.backup-poll-8-20260911`
- `CACHED`

## 下一步建议

- 下一个功能模块请新建 Codex 任务，并同时提供根目录 `AGENTS.md`、平台总交接文档和本文件。
- 若继续改进浮窗，可在新的 Astro/浮窗任务中评估无行情卡片的降级信息，但不能用估算费率冒充交易所实时值。
