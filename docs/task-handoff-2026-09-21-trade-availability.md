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
- 现货充提与告警订阅提交：`4a098874e7e1d3f1ef3f0400dd18dccb62df15a6`
- 现货充提与告警订阅提交说明：`feat: expose spot transfer availability`
- 飞书机会告警诊断提交：`6ab3eb77fce5c1b4b06b9102d2330b0b7ab81095`
- 飞书机会告警诊断提交说明：`feat: include trade availability in alerts`

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
- 标的查询页新增统一诊断表、限制告警、精确原始市场监控按钮，以及桌面/手机横向表格和固定关键列。顶部公开交易限制告警逐市场直接提供“订阅恢复通知”或“取消恢复通知”按钮，复用同一个精确市场监控，不需要先在宽表中定位操作列。
- 现货市场新增 `spot_transfer` 诊断，展示币种、充币状态、提币状态、是否全部开放、逐链开关、来源和检测时间；接口失败只让充提诊断降级，不丢失该市场的交易状态和订单簿结果。
- Binance 使用匿名 public asset service、Gate 使用公开 spot currencies、Bitget 使用公开 public coins 获取逐链开关。OKX 和 Bybit 的官方币种接口需要 API Key，Aster 没有验证到可靠匿名公开逐币接口，因此三者明确显示未知/需鉴权，不猜测为关闭。
- 所有机会飞书告警和告警历史消息追加“交易与充提状态”区块，同时展示两条腿的开仓路径、平仓路径、精确原始市场动作和基础资产充提状态；即使两条腿都是永续，也会单独查询该交易所的资产级充提状态。
- 永续腿分别显示开多、开空、平空和平多；平空明确对应 Buy + Reduce Only，平多明确对应 Sell + Reduce Only。现货腿只显示买入和卖出，不出现做空、Reduce Only 或“不适用”。
- 任一开仓必需动作公开受限或未知时，告警评级降为“需评估”。单个交易所诊断失败时只将对应腿降级为未知，原始飞书告警仍会发送。

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
- `backend/app/services/opportunity_trade_availability.py`
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
- 现货充提汇总只有在所有返回链均开启时才为 `enabled`；部分链开启为 `partial`，全部关闭为 `disabled`，字段缺失、接口不可公开访问或未验证时为 `unknown`。任一侧明确为部分开放或关闭时，`all_enabled=false`，不会因另一侧未知而误报全开。
- 充提状态是交易所公开币种/网络状态，不代表某个账户、地区或地址当前一定可以充提；系统没有使用私有 API Key，也没有发起充提或下单探测。

## 本地验证

- 后端充提专项：`7 passed`。
- 后端全量：`742 passed, 13 warnings`，耗时 `765.98` 秒。
- 飞书交易与充提消息专项：`35 passed, 2 warnings`；部署前再次执行结果相同。
- 标的查询页专项：`19 passed`，覆盖告警内精确订阅请求和现货充提状态展示。
- 前端生产构建：通过。
- Ruff：`All checks passed`。
- `git diff --check`：通过。
- 前端全量：`160 passed, 1 failed`。唯一失败是既有 Settings 测试仍查找旧文案“实盘灰度”，页面现文案为“正差价正费率实盘实验”；本任务未修改 Settings 模块。

浏览器检查：

- 桌面 `1440 x 1000`：14 个原始市场正常展示，市场列和恢复监控列固定。
- 动作文案修正后的桌面页显示“普通买入（非 Reduce Only）”“普通卖出（非 Reduce Only）”“买入平空（Reduce Only）”“卖出平多（Reduce Only）”；现货行的两种 Reduce Only 单元格为空，永续行继续显示买入平空和卖出平多。
- ZETA 桌面页的公开限制告警直接显示“取消恢复通知”，与线上已有的精确 ZETA 订阅一致；未点击或修改生产订阅。
- BTC 桌面页可见“现货充提”列和实际状态；手机 `390 x 844` 页面宽度为 `390`、文档滚动宽度为 `390`，没有整页横向溢出，宽表内部继续横向滚动。
- 最新线上截图：`output/trade-availability-transfer-zeta-deployed-desktop.png`、`output/trade-availability-transfer-btc-deployed-desktop.png`、`output/trade-availability-transfer-deployed-mobile.png`、`output/trade-availability-transfer-column-deployed-desktop.png`、`output/trade-availability-transfer-column-deployed-mobile.png`，仅作为未跟踪验证产物。

## 生产备份与部署

- 飞书机会告警扩展部署前数据库备份：`backups/radar-20260921T103705Z.db`
- 最新备份大小：`675831808` 字节
- 最新备份 SHA-256：`f7a36bf38fe6ffb553a27d95f0ec2573b7176f7dd297e185d7bb4e9c991f873e`
- 最新备份的源库、容器内备份和主机备份 `PRAGMA quick_check` 均为 `ok`；使用 SQLite 在线 backup API，校验后删除了卷内临时副本。
- 本轮部署前数据库备份：`backups/radar-20260921T092258Z.db`
- 大小：`698052608` 字节
- SHA-256：`aed4c266a8fc6c12b3efc0f7010f689f4fffcd124c01caa7e77bdd137f0bf26f`
- 源数据库 `PRAGMA quick_check = ok`
- 备份数据库 `PRAGMA quick_check = ok`
- 备份使用 SQLite 在线 backup API，已复制到服务器仓库的 `backups/`；本次创建的卷内临时副本在校验主机副本后已删除，线上源库未改动。
- 服务器使用 `git pull --ff-only` 确认更新到 `4a098874e7e1d3f1ef3f0400dd18dccb62df15a6`。
- 飞书机会告警扩展使用 `git pull --ff-only` 更新到 `6ab3eb77fce5c1b4b06b9102d2330b0b7ab81095`。
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

BTC 线上查询返回 14 个市场且 `errors={}`。现货充提结果为：Binance `enabled / partial`（5 条链）、Gate `enabled / enabled`（2 条链）、Bitget `partial / partial`（3 条链）；OKX、Bybit、Aster 均为 `unknown / unknown`，并带有需鉴权或缺少已验证匿名接口的来源说明。永续行不展示充提内容。

ZETA 线上查询返回 11 个市场且 `errors={}`，唯一公开受限市场仍为 `Hyperliquid / future / main / ZETA / OPEN_INTEREST_CAP`，`dex=main` 和 `raw_symbol=ZETA` 均保留。线上监控列表有 1 条启用订阅，精确对应 `ZETAUSDT + hyperliquid + future + ZETA + main`，最近状态为买卖均 `blocked`，没有通知错误；本轮验收只读查询，没有新增、删除或点击生产订阅。

使用线上只读接口数据和部署后的消息构建模块生成 ZETA 告警预览，结果包含：

- 开仓路径为“不可用”：买入腿 Hyperliquid 开多因 `OPEN_INTEREST_CAP` 公开受限，卖出腿 Binance 开空公开可用；
- 平仓路径为“有条件”：买入腿平多和卖出腿平空都需要对应账户持仓、方向、数量及 Reduce Only，公开接口不能把它们确认成账户可成交；
- Hyperliquid 同时显示 `DEX main`、`raw_symbol=ZETA`、开多/开空公开受限，以及平空/平多账户有条件；
- 充提参考同时显示 Hyperliquid 和 Binance 的 ZETA。两家本次均为未知，并附带“没有匿名逐链状态”或“公开币种列表未返回该资产”的原因，没有误报为关闭。

本轮线上健康检查返回 `status=ok`、八家采集器均为 `healthy`；BTC 仍返回 14 个原始市场且 `errors={}`，买一卖一、1% 深度、24h 成交额、资金费率及周期、手续费是否计入、倍率和更新时间字段均存在。前后端容器均为 `healthy`，验证脚本没有调用飞书通知器或订单接口。

没有人为制造不可用/恢复切换，因此没有发送生产飞书测试消息。恢复通知状态机和飞书失败重试由自动化测试覆盖；真实通知仍依赖生产 `feishu_live_send_enabled` 与 Webhook 配置，以及未来真实状态切换。

## 已知问题与残余风险

- Aster、Lighter 或其他公开 API 可能偶发超时；单市场会显示降级错误，不拖累其他交易所。
- 公共 API 无法确认账户余额、仓位、保证金、地区、权限、nonce、签名或账户风控。若真实订单失败，应保留原始错误并按 `order_error` 证据单独分析。
- OKX、Bybit 现货充提官方接口需私有鉴权，Aster 尚无已验证匿名公开逐币接口；当前只能明确展示未知。Binance、Gate、Bitget 的公开开关也可能与账户、地区、维护窗口或具体地址可用性不同。
- 飞书真实发送未通过伪造市场状态验证，避免产生误通知；生产配置和下一次真实恢复事件仍是外部依赖。
- 机会告警的交易与充提诊断会并发访问两家交易所公开接口；单次诊断设有 15 秒超时，极端上游故障时会延迟告警但不会丢弃原始告警。
- 后端启动日志仍有既存 Gate 公告接口 HTTP 567，与交易可用性接口无关；本任务接口返回 200 且 `errors={}`。
- 前端全量测试保留一个与本模块无关的旧文案失败，以及既有 Ant Design 弃用和 `act(...)` 警告。
- 手机首屏会被既有“关注行情”浮动面板遮住部分横向表格，关闭或最小化面板后可查看；诊断表本身支持横向滚动。

## 工作区保护项

以下既有或验证产物未提交、未删除、未覆盖：

- `output/**`
- `script/dexe_bybit_bitget_chain.py`
- 账户持仓相关的 `backend/app/api/routes_account_positions.py`、`backend/app/models/account_position.py`、`backend/app/services/account_positions.py`、`backend/tests/test_account_positions.py`。
- 其他并行任务在 `backend/app/api/routes_settings.py`、`backend/app/db/repositories.py`、`backend/app/main.py`、`backend/app/models/settings.py`、`backend/app/services/gate_twap.py` 和 `backend/tests/test_floating_watch.py` 中的本地未提交修改。
- 其他并行任务在 `frontend/src/api/client.ts`、`frontend/src/api/types.ts`、`frontend/src/components/FloatingWatchPanel.tsx`、`frontend/src/styles.css` 和 `frontend/tests/FloatingWatchPanel.test.tsx` 中的本地未提交修改。

服务器上的 `.env.backup-codex-20260911-1425`、`.env.backup-poll-8-20260911` 和 `CACHED` 保持不动。

## 下一步建议

- 等待真实受限市场恢复，确认生产飞书 Webhook 收到一次且只收到一次恢复通知。
- 若需要诊断特定账户，应新建独立任务设计只读账户权限、仓位和原始订单错误输入，不要在本模块中加入探测订单。
- Settings 旧文案测试和手机浮动面板遮挡应分别作为独立小任务处理，避免扩大本模块范围。
