# 标的查询价差跳转交接

## 目标与范围

本任务只处理“标的查询/价差跳转”模块：标的查询页面的“跨市场差价”表格中，点击操作列的价差图表按钮时，每次在独立的新浏览器标签页打开对应价差查询，来源页保持不动。

未修改价差计算、行情采集、告警规则、Astro 建卡、浮窗或后端接口。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`87977fc`（`docs: hand off floating watch funding update`）
- 功能提交：`c930374`（`fix: open instrument spreads in new tabs`）

## 已完成功能

- 图表按钮不再通过 `history.pushState` 替换来源标签页，而是调用 `window.open`。
- 每次点击使用标准目标名 `_blank`，不会复用命名窗口；连续点击同一路线会创建不同标签页。
- 新窗口功能串为 `noopener,noreferrer`，目标页不能通过 `window.opener` 控制来源页，也不发送 referrer。
- 继续沿用已有 URL 组装规则，保留买卖腿交易所、市场类型、原始标的、`leg2_multiplier=1`、查询时长和采样周期。
- Hyperliquid 永续继续按原始市场拆出具体 DEX；builder-deployed 市场保留如 `io`，主市场显式传递 `main`。
- 反向 SF 继续保持买腿为永续、卖腿为现货，不交换或规范化掉原始市场代码。

## 关键代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx`
  - `pairSpreadLegRoute()`：解析每条腿的原始标的与 Hyperliquid DEX。
  - `openPairSpread()`：构造价差查询 URL，并使用隔离的新标签页打开。
- `frontend/tests/InstrumentLookupPage.test.tsx`
  - 覆盖连续点击、`_blank`、`noopener,noreferrer`、来源 URL 不变、正向/反向路线、原始 symbol、倍率与 HL 子 DEX/main DEX。

## 重要业务规则

- `leg1` 始终对应表格的买入市场，`leg2` 始终对应卖出市场；反向 SF 也按该规则传递，不能为了显示统一而交换。
- 非 Hyperliquid 市场优先使用别名对应的原始标的，其次使用交易所 `raw_symbol`；不能只传规范化 ticker。
- Hyperliquid builder-deployed 永续必须同时传递去掉 DEX 前缀后的原始标的和具体 `legN_dex`。
- 未提供实际市场倍率的当前标的查询路线继续显式传递 `leg2_multiplier=1`。

## 测试结果

- `npm test -- InstrumentLookupPage.test.tsx`：17/17 通过。
- `npm run build`：TypeScript 检查和 Vite 生产构建通过。
- 前端全量测试：152/154 通过。
- 全量测试的 2 项失败均为既有 `SettingsPage.test.tsx` 基线问题：一项超过 15 秒，另一项仍查找已更名的“实盘灰度”文案；本任务未修改设置页或其调用链。
- `git diff --check`：通过。

## 数据库备份

- 文件：`backups/radar-20260921T044058Z-pre-instrument-spread-new-tabs.db`
- 大小：`723374080` 字节。
- SHA-256：`0ab1923602951831f2f719f83d8437aadfc2fdb906e1d0341621b793712ba930`。
- SQLite `PRAGMA quick_check`：`ok`。

## 线上状态

- 服务器使用 `git pull --ff-only` 更新功能代码到 `c930374`。
- `docker compose build --pull` 和 `up -d --remove-orphans` 成功；没有执行 `down -v`。
- 前端、后端容器均为 `healthy`。
- `/api/health` 返回 `status=ok`，8 个交易所均为 `healthy`，`exchange_errors={}`。
- `/api/instruments/BTCUSDT` 实际返回 8 个交易所、14 个市场、91 条跨市场价差。
- 线上浏览器连续点击同一 BTC 路线两次，创建了两个不同浏览器 target；来源 URL 始终保持在标的查询页，两页均为 `window.opener === null`。
- 两个新页均完整携带 Aster 永续原始标的、Hyperliquid 主市场 `BTC`、`leg2_dex=main`、倍率和查询窗口参数。
- 线上反向 SF 实测为 Bybit 永续买腿到 OKX 现货卖腿，目标 URL 保持该方向及双方原始标的。
- 线上 `ANTHROPICUSDT` 实测为 OKX `ANTHROPIC-USDT-SWAP` 到 Hyperliquid `ANTH`，目标 URL 携带 `leg2_dex=io` 和 `leg2_multiplier=1`。

## 已知问题与残余风险

- 浏览器或用户策略若完全禁止本站弹出窗口，仍可能阻止新标签页；当前打开动作直接发生在用户点击调用栈中，主流浏览器默认允许。
- 全量前端测试仍有上述 2 个设置页基线失败，应在独立的设置模块任务中处理。
- 测试输出仍包含既有 Ant Design 弃用提示和部分异步更新未包裹 `act(...)` 的警告，不影响本任务测试结果。

## 未跟踪文件

本任务未提交、删除或覆盖既有本地产物：

- `output/edge-profile-codex/`
- `output/dexe_bybit_bitget_chain_probe/`
- `output/floating-watch-*.png`
- `output/index-auto-watch-*.png`
- `script/dexe_bybit_bitget_chain.py`

服务器继续保留既有未跟踪运维文件：

- `.env.backup-codex-20260911-1425`
- `.env.backup-poll-8-20260911`
- `CACHED`

## 下一步建议

- 下一个功能模块请新建 Codex 任务，并同时提供根目录 `AGENTS.md`、平台总交接文档和本文件。
- 设置页两项既有测试失败若要修复，应单独归入设置模块任务，避免扩展本任务范围。
