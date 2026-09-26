# Astro 浮窗价差跳转交接

## 目标与范围

本任务只处理“Astro/浮窗跳转”模块：独立关注行情浮窗的 Astro 列表中，点击交易对名称后，按该卡片实际买腿、卖腿打开对应价差查询。

未修改 Astro 建卡、价差计算、行情采集、告警规则、持仓与利润计算、普通关注标的跳转或已保存交易对跳转。

验收目标：

- 使用卡片实际买卖腿，而不是仅根据显示名称或规范化 ticker 构造查询。
- 完整携带双方交易所、现货/永续类型、原始标的、市场倍率和 Hyperliquid 具体 DEX。
- 正向、反向、现货/永续混合及不同原始标的卡片都保持正确方向。
- 独立浮窗始终保留，目标在同源主应用或隔离的新标签页打开。
- 名称有明确可点击状态，同时保持紧凑布局、滚动和拖动行为。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`08001a2`（`docs: hand off trade availability monitor`）
- 功能提交：`ba2b4ea`（`feat: open Astro cards in spread queries`）
- 部署时同分支最新提交：`693cda8`（并行任务的交易动作标签修复）；`ba2b4ea` 是其直接祖先。

## 已完成功能

- Astro 名称改为紧凑按钮，增加蓝色价差图标、悬停下划线、键盘焦点轮廓、单行省略和行情未就绪时的禁用状态。
- 只有双方实际 `MarketSnapshot` 均已解析时才允许打开，路线使用卡片 `buyEx`、`sellEx`、`type` 和两份已匹配行情快照。
- 非 Hyperliquid 腿优先携带 `symbol_alias_original_symbol`，其次使用交易所 `raw_symbol`，不退化为只传规范化 ticker。
- Hyperliquid 永续从原始市场代码中拆分 DEX 与标的，例如 `io:ANTH` 传 `symbol=ANTH&dex=io`；无前缀主市场显式传 `dex=main`。
- 比率卡片按两个页面的计算约定换算倍率。Astro 使用 `sell_price * regressionValue`，价差页使用 `leg2_price / leg2_multiplier`，因此 URL 传 `1 / regressionValue`；Astro `1:10` 对应 `leg2_multiplier=0.1`。
- 普通主应用浮窗继续在当前主应用内导航。独立浮窗优先通知同源 opener 导航；没有可用同源 opener 时使用 `_blank` 和 `noopener,noreferrer`，不再以当前独立浮窗兜底导航。

## 关键代码入口

- `frontend/src/components/FloatingWatchPanel.tsx`
  - `astroNavigationRoute()`：只有两条实际市场快照均存在时生成可导航路线。
  - `astroPairSpreadLegRoute()`：保留原始标的并解析 Hyperliquid 具体 DEX。
  - `astroPairSpreadMultiplier()`：把 Astro 回归倍率转换为价差页右腿除数。
  - `openAstroPair()`：按实际买卖腿构造价差查询参数。
  - `navigateFromWatch(..., preserveStandalone)`：保留独立浮窗，目标交给主应用或新标签页。
- `frontend/src/styles.css`
  - `.floating-watch-astro-link`：紧凑按钮、可点击状态、溢出和焦点样式。
- `frontend/tests/FloatingWatchPanel.test.tsx`
  - 覆盖正向/反向 ZETA、LSK-LSK 现货/永续、ANTHROPIC-ANTHROPIC `1:10` 和 Hyperliquid `io` 的 ANTHROPIC-ANTH。

## 重要业务规则

- `leg1` 始终是 Astro 卡片买腿，`leg2` 始终是卖腿；反向卡片不能为了显示统一而交换。
- 市场类型直接取卡片 `type` 对应腿；例如 `SR` 为买腿现货、卖腿永续。
- 跳转标的来自与该腿交易所、类型及 DEX 匹配的实际行情快照，不能仅用卡片名称拼接。
- Hyperliquid 必须保留具体 DEX。builder-deployed 市场不能与 `main` 合并，主市场也显式传 `main`。
- 回归倍率不是交易所 symbol alias 的价格倍率；URL 倍率必须遵守价差页的“右腿价格除以倍率”语义。
- 独立浮窗没有 opener 且浏览器阻止弹窗时保持原页面，不以替换独立浮窗作为兜底。

## 测试结果

- `npm test -- FloatingWatchPanel.test.tsx`：18/18 通过。
- `npm run build`：TypeScript 检查和 Vite 生产构建通过；服务器 Docker 前端构建也通过。
- `git diff --check`：通过。
- 功能提交前的前端全量测试：159 通过、1 失败。
- 唯一失败是既有 `SettingsPage > loads and saves Live Pilot settings`，仍查找已更名的“实盘灰度”文案；本任务未修改设置页或其调用链。
- 测试输出仍有既有 Ant Design 弃用提示和部分异步更新未包裹 `act(...)` 的警告，不影响专项测试结果。
- 本地真实生产数据浏览器检查：900px 与 390px 均无横向溢出，名称按钮不改变卡片高度、滚动或拖动区域。

## 数据库备份

- 文件：`backups/radar-astro-navigation-20260921T080928Z.db`
- 大小：`707584000` 字节。
- SHA-256：`e7143e9707805756befbaf29a40ef56303abd6c565ab0bfe95298e13be2c0a1a`。
- SQLite `PRAGMA quick_check`：`ok`。

## 线上状态

- 服务器使用 `git pull --ff-only origin codex/frontend-localization-polish` 更新，部署时 HEAD 为 `693cda8`，并确认 `ba2b4ea` 是祖先。
- `docker compose build --pull` 和 `up -d --remove-orphans` 成功；没有执行 `down -v`。
- 前端、后端容器均为 `healthy`。
- `/api/health` 返回 `status=ok`，8 个交易所均为 `healthy`，`exchange_errors={}`。
- 生产独立浮窗实际显示 8 张 Astro 卡片；ZETA、LSK-LSK、ANTHROPIC-ANTHROPIC、ANTHROPIC-ANTH 均可点击。
- 生产 ZETA 实际路线：OKX 永续 `ZETA-USDT-SWAP` 买腿 -> Hyperliquid 永续 `ZETA` 卖腿，`leg2_dex=main`、倍率 1。
- 生产 LSK-LSK 实际路线：OKX 现货 `LSK-USDT` 买腿 -> Binance 永续 `LSKUSDT` 卖腿，倍率 1。
- 生产 ANTHROPIC-ANTHROPIC 实际路线：Bitget 永续 `ANTHROPICUSDT` 买腿 -> OKX 永续 `ANTHROPIC-USDT-SWAP` 卖腿，`leg2_multiplier=0.1`。
- 生产 ANTHROPIC-ANTH 实际路线：Bitget 永续 `ANTHROPICUSDT` 买腿 -> Hyperliquid io 永续 `ANTH` 卖腿，`leg2_dex=io`、倍率 1。
- 四次生产点击后源 URL 始终保持 `?floating_watch=standalone`，目标均以 `_blank`、`noopener,noreferrer` 打开。
- 四个目标页都完成加载，存在价差查询表单且没有错误级 Alert；页面随后把原始标的解析为规范化展示标的，DEX 和倍率继续保留。
- 生产 900px 与 390px 检查均为 `document.scrollWidth === clientWidth`，所有 Astro 行均为 `scrollWidth === clientWidth`；截图位于未跟踪的 `output/floating-watch-astro-navigation-production-*.png`。

## 已知问题与残余风险

- 生产当前只有一张正向 ZETA 卡片，没有可直接点击的反向 ZETA 实例；反向买卖腿顺序、原始 symbol 和 HL main DEX 已由专项回归测试覆盖。
- 生产验收时 GIGADEVICE 仍缺少 Hyperliquid 永续实时行情，因此名称保持禁用并显示既有明确错误；没有可靠双方市场时不会猜测路线。
- 浏览器若完全禁止本站弹出窗口，孤立独立浮窗不会被替换，但目标新标签页也无法创建；有同源主应用 opener 时不依赖弹窗。
- 全量前端测试仍有上述设置页基线失败，应在独立的设置模块任务中处理。

## 未跟踪文件

本任务未提交、删除或覆盖既有本地产物：

- `output/edge-profile-codex/`
- `output/dexe_bybit_bitget_chain_probe/`
- `output/floating-watch-*.png`，包括本任务本地和生产视觉验收截图。
- `output/index-auto-watch-*.png`
- `output/trade-availability-*.png`
- `script/dexe_bybit_bitget_chain.py`

服务器继续保留既有未跟踪运维文件：

- `.env.backup-codex-20260911-1425`
- `.env.backup-poll-8-20260911`
- `CACHED`

## 下一步建议

- 下一个功能模块请新建 Codex 任务，并同时提供根目录 `AGENTS.md`、平台总交接文档和本文件。
- 后续若要增强 Astro 跳转，应继续以卡片实际腿和已匹配市场快照为唯一数据源，不增加按规范化 ticker 猜测 DEX 或原始市场的降级路径。
