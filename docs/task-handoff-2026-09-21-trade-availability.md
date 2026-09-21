# 交接：全交易所交易可用性诊断与恢复监控

日期：2026-09-21

## 目标与范围

本任务只处理全交易所交易可用性诊断与恢复监控。核心覆盖 Binance、OKX、Bybit、Gate、Bitget，保留既有 Hyperliquid 能力，并完成 Aster、Lighter 的公开接口评估接入。

诊断严格保留 `exchange + market_type + raw_symbol`；Hyperliquid 额外保留具体 `dex`。每个原始市场分别判断：

- 普通买入（非 Reduce Only）；
- 普通卖出（非 Reduce Only）；
- 买入平空（Reduce Only）；
- 卖出平多（Reduce Only）。

系统不接入交易账户、不签名、不发送探测订单。公开市场限制、未核验的账户级条件和未提供的真实订单错误分别展示，不能把公开状态解释为特定账户一定可成交。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`7225db9831521471deb8bcc85c6151aeb48e4682`
- 功能提交：`18ad979429cbd609ba18ff0b58096966d63c45c9`
- 功能提交说明：`feat: diagnose exchange trade availability`
- 动作文案修正提交：`693cda8b687530a004ce46667d5129c780451114`
- 动作文案修正说明：`fix: clarify trade action labels`

## 已完成功能

- 新增统一交易状态接口，按行情仓库中的每个原始市场查询对应交易所公开 instrument/product 状态和实时订单簿。
- 核心五家使用各自公开市场元数据；Gate 现货保留 `tradable / buyable / sellable` 的单向限制。
- Hyperliquid 复用 OI cap、下架状态和 `l2Book` 逻辑，严格保留具体 DEX 与原始市场。
- Aster 使用 Binance 兼容的 `exchangeInfo`、24h ticker、premium index 和实时订单簿；补充行情失败时局部降级。
- Lighter 识别 `active`、`is_frozen`、`force_reduce_only`，并使用 WebSocket 实时订单簿。
- API 对现货 Reduce Only 保留结构化 `not_applicable`，前端现货行不渲染两种 Reduce Only 单元格；永续 Reduce Only 在公开状态和盘口允许时显示账户条件 `conditional`。
- 每个市场固定包含 `public_market`、`account`、`order_error` 三类诊断证据。没有私有授权或真实订单错误时明确显示 `not_checked` / `not_provided`。
- 展示实际买一卖一、1% 双边深度、24h 成交额、资金费率及周期、标记价、指数价、Maker/Taker 公开费率、手续费是否计入、价格倍率、合约数量乘数和三类更新时间。
- OKX 永续深度按 `ctVal * ctMult` 换算；Gate 永续深度按 `quanto_multiplier` 换算。
- 实时订单簿失败时可展示聚合行情回退，但普通动作不会因此被误判为公开可用。
- 新增统一恢复监控表和后台轮询。普通买卖状态从非 `available` 恢复为 `available` 时发送一次飞书；不监控 Reduce Only。
- 飞书发送失败时恢复旧状态并记录错误，使后续轮询能够重试同一次恢复通知。
- 标的查询页新增统一诊断表、限制告警、精确原始市场监控按钮，以及桌面/手机横向表格和固定关键列。

## API 与代码入口

接口：

```text
GET    /api/trade-status/{symbol}
GET    /api/trade-status/watches/list
POST   /api/trade-status/watches
DELETE /api/trade-status/watches/{watch_id}
```

后端入口：

- `backend/app/models/trade_availability.py`
- `backend/app/services/trade_availability.py`
- `backend/app/api/routes_trade_availability.py`
- `backend/app/db/schema.py`
- `backend/app/main.py`
- `backend/tests/test_trade_availability.py`

前端入口：

- `frontend/src/api/types.ts`
- `frontend/src/api/client.ts`
- `frontend/src/pages/InstrumentLookupPage.tsx`
- `frontend/src/styles.css`
- `frontend/tests/InstrumentLookupPage.test.tsx`

数据库新增表：

```text
trade_availability_watchlist
```

唯一市场身份为 `exchange, market_type, raw_symbol, dex`，非 Hyperliquid 市场的 `dex` 持久化为空字符串。

## 重要业务规则

- 普通买卖只有在公开元数据允许且对应方向实时盘口存在时才为 `available`。
- 公开元数据允许但实时盘口缺失时为 `unknown`，聚合快照仅供展示。
- `conditional` 表示还需要正确持仓、方向、数量、余额、保证金、权限和账户风控，不表示真实订单一定成功。
- Reduce Only 平空必须使用 Buy；Reduce Only 平多必须使用 Sell；数量不能超过对应持仓。
- Hyperliquid 必须同时核对钱包/子账户、DEX、原始市场和持仓方向，不能只按规范化 ticker 匹配。
- 1% 深度是订单簿快照而不是成交承诺；盘口价格和状态未计入手续费。
- 资金费率必须与 `funding_interval_hours` 一起解释；预估费率与当前费率分开显示。
- 恢复监控只比较普通买卖，不会用公开 Reduce Only 状态替代账户级判断。

## 本地验证

- 后端全量：`735 passed, 13 warnings`。
- 全交易所与 Hyperliquid 专项：`10 passed, 2 warnings`。
- 标的查询页专项：动作文案修正后 `19 passed`。
- 前端生产构建：通过。
- Ruff 本模块专项：通过。
- `git diff --check`：通过。
- 两个曾因并行资源争用超时的 Settings 用例串行复跑均通过。
- 前端全量：`155 passed, 1 failed`。唯一失败是既有 Settings 测试仍查找旧文案“实盘灰度”，页面现文案为“正差价正费率实盘实验”；本任务未修改 Settings 模块。

浏览器检查：

- 桌面 `1440 x 1000`：14 个原始市场正常展示，市场列和恢复监控列固定。
- 动作文案修正后的桌面页显示“普通买入（非 Reduce Only）”“普通卖出（非 Reduce Only）”“买入平空（Reduce Only）”“卖出平多（Reduce Only）”；现货行的两种 Reduce Only 单元格为空，永续行继续显示买入平空和卖出平多。
- 手机 `390 x 844`：页面宽度正常，诊断表保留横向滚动；既有关注行情浮窗打开时会覆盖表格区域。
- 最新线上截图：`output/trade-availability-action-labels-deployed-desktop.png`、`output/trade-availability-action-labels-deployed-mobile.png`，仅作为未跟踪验证产物。

## 生产备份与部署

- 动作文案修正部署前数据库备份：`backups/radar-20260921T081155Z.db`
- 大小：`707584000` 字节
- SHA-256：`df421baecbe6a291e29f1508f609102c73b0cecbd530c9e9fe5ff383ce14469a`
- 源数据库 `PRAGMA quick_check = ok`
- 备份数据库 `PRAGMA quick_check = ok`
- 服务器使用 `git pull --ff-only` 确认更新到 `693cda8b687530a004ce46667d5129c780451114`。
- 使用 `docker compose build --pull` 和 `docker compose up -d --remove-orphans` 重建，没有执行 `down -v`。
- 前后端容器均为 `healthy`。
- `/api/health` 返回 `status=ok`，八家交易所采集状态均为 `healthy`。
- 前端页面、`/api/trade-status/BTCUSDT` 和 `/api/trade-status/watches/list` 均返回 HTTP 200。

## 线上实际验证

BTC 查询返回 14 个原始市场，`errors={}`，包括：

- Binance、OKX、Bybit、Gate、Bitget 的现货和永续；
- `Hyperliquid / future / main / BTC`；
- Aster 现货和永续；
- Lighter 永续。

所有返回项均保留 `exchange + market_type + raw_symbol`，Hyperliquid 保留 `dex=main`；每项均有四个动作、三类诊断证据、买一卖一、1% 深度、24h 成交额、手续费未计入、倍率和更新时间。线上观察到 OKX 永续数量乘数为 `0.01`、Gate 永续为 `0.0001`，Hyperliquid 和 Lighter 资金周期为 1 小时。验证期间未发送任何订单。

前端不再把普通卖出描述为做空，也不在现货行显示“平空/平多不适用”。现货仍正常展示普通 `Buy` 和 `Sell`；只有永续及其他有持仓语义的市场展示 `Buy / 平空` 和 `Sell / 平多`。

监控列表当时为空，没有人为制造不可用/恢复切换，因此没有发送生产飞书测试消息。恢复通知状态机和飞书失败重试由自动化测试覆盖；真实通知仍依赖生产 `feishu_live_send_enabled` 与 Webhook 配置，以及未来真实状态切换。

## 已知问题与残余风险

- Aster、Lighter 或其他公开 API 可能偶发超时；单市场会显示降级错误，不拖累其他交易所。
- 公共 API 无法确认账户余额、仓位、保证金、地区、权限、nonce、签名或账户风控。若真实订单失败，应保留原始错误并按 `order_error` 证据单独分析。
- 飞书真实发送未通过伪造市场状态验证，避免产生误通知；生产配置和下一次真实恢复事件仍是外部依赖。
- 后端启动日志仍有既存 Gate 公告接口 HTTP 567，与交易可用性接口无关；本任务接口返回 200 且 `errors={}`。
- 前端全量测试保留一个与本模块无关的旧文案失败，以及既有 Ant Design 弃用和 `act(...)` 警告。
- 手机首屏会被既有“关注行情”浮动面板遮住部分横向表格，关闭或最小化面板后可查看；诊断表本身支持横向滚动。

## 工作区保护项

以下既有或验证产物未提交、未删除、未覆盖：

- `output/**`
- `script/dexe_bybit_bitget_chain.py`

服务器上的 `.env.backup-codex-20260911-1425`、`.env.backup-poll-8-20260911` 和 `CACHED` 保持不动。

## 下一步建议

- 等待真实受限市场恢复，确认生产飞书 Webhook 收到一次且只收到一次恢复通知。
- 若需要诊断特定账户，应新建独立任务设计只读账户权限、仓位和原始订单错误输入，不要在本模块中加入探测订单。
- Settings 旧文案测试和手机浮动面板遮挡应分别作为独立小任务处理，避免扩大本模块范围。
