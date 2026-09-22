# 交接：全交易所交易可用性诊断与恢复监控

日期：2026-09-22

## 目标与范围

本任务只处理全交易所交易可用性诊断与恢复监控，按已确认设计完成以下四个同层栏目：

- 交易可用性；
- 现货充提通道；
- 合约指数成分；
- 交易所市场。

覆盖 Binance、OKX、Bybit、Gate、Bitget、Aster、Hyperliquid、Lighter。所有市场严格保留 `exchange + market_type + raw_symbol`；Hyperliquid 额外保留具体 `dex`。系统没有接入交易账户、没有签名或发送探测订单，也没有人为触发恢复通知。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`b76fdbc752b915dea9341dfb53b6bf415e276076`
- 功能提交：`2ef7ad9f79eddb35aa9d4e72f2fcca807c77659a`
- 功能提交说明：`feat: finalize trade availability diagnostics`
- 功能提交已推送到 `origin/codex/frontend-localization-polish`。

## 已完成功能

### 交易动作诊断

- 永续分别输出开多、开空、平空（Reduce Only Buy）、平多（Reduce Only Sell）。
- 现货只输出买入、卖出，不展示 Reduce Only 或“不适用”。
- 平仓状态直接回答“已有对应仓位时公开市场能否平仓”，只显示可用、受限、未知。
- Hyperliquid OI 上限和 Lighter `force_reduce_only=true` 明确映射为开仓受限、两种 Reduce Only 平仓可用。
- 下架、对应方向禁用、元数据未知和盘口缺失分别映射为受限或未知，不从聚合报价推断可用。
- 旧 `conditional` 枚举仅保留数据兼容；统一诊断和机会告警会将其转为可用或未知，不再显示“账户有条件”“需空仓”“需多仓”。
- 页面顶部只显示一次“账户数据未接入 · 未发送探测订单 · 当前仅依据公开市场数据判断”，正常市场不再重复账户和真实订单说明。

### 恢复监控与飞书

- 恢复监控扩展到永续四个动作，监控记录在现有 JSON payload 中保存两种 Reduce Only 最近状态，不需要新增数据库列。
- 任一适用交易动作从受限或未知恢复为可用时发送一次飞书；首轮只建立基线，不发送恢复通知。
- 飞书消息同时包含恢复动作、全部开仓/平仓动作状态、充币/提币汇总和逐链状态，以及“未发送探测订单”说明。
- 发送失败时保留旧状态，后续轮询可以重试同一次恢复通知。

### 现货充提

- 每条网络独立展示网络名、充币、提币、数据来源和更新时间。
- `true/false/null` 分别显示开启、暂停、未知；无网络数组显示“未返回链列表 / 未知”。
- 页面不再用“部分开放”概括多条网络。

### 合约指数成分

- 统一接口新增顶层 `index_compositions`，分组身份包含 `exchange + market_type + dex + raw_symbol`。
- Binance 使用 `fapi/v1/constituents`。
- OKX 使用 `market/index-components`。
- Bybit 使用 `market/index-price-components`，兼容当前顶层 `result.components`。
- Gate 使用 `futures/usdt/index_constituents`，兼容当前 `symbols[]`、`price` 和 `time` 字段。
- Bitget 使用 `api/v3/market/index-components`，兼容当前 `data.componentList`。
- Aster、Hyperliquid、Lighter 的已验证官方接口不返回可核验成分和权重，因此明确返回 `not_returned`，不推测或补算。
- 每个面板展示官方返回的来源交易所、现货/永续、原始符号、价格、权重、指数价格、更新时间和权重合计。
- `IndexComponentSnapshot.index_price` 不进入成分哈希，指数价格本身不会触发成分变更告警。

### 交易所市场与布局

- 行情、流动性、费用和规格统一移入“交易所市场”，包含实际买一卖一、标记/指数价、0.1% 和 1% 双边深度、24h 成交额、当前/预估资金费率、周期、Maker/Taker、手续费计入状态、倍率、数量乘数、来源和三类更新时间。
- 买一使用高对比绿色，卖一和负资金费率使用红色。
- 合约指数面板在宽屏、中等宽度、手机分别为 3、2、1 列。
- 四个栏目和整页都不使用横向滚动。

## 关键代码入口

后端：

- `backend/app/models/hyperliquid_trade_status.py`
- `backend/app/models/index_component.py`
- `backend/app/models/trade_availability.py`
- `backend/app/services/hyperliquid_trade_status.py`
- `backend/app/services/index_components.py`
- `backend/app/services/opportunity_trade_availability.py`
- `backend/app/services/trade_availability.py`
- `backend/app/api/routes_trade_availability.py`

前端：

- `frontend/src/api/types.ts`
- `frontend/src/pages/InstrumentLookupPage.tsx`
- `frontend/src/styles.css`
- `frontend/tests/InstrumentLookupPage.test.tsx`

## 测试与本地验收

- 后端专项：`46 passed, 10 warnings`，覆盖统一诊断、Hyperliquid、指数成分、机会告警和恢复通知。
- 后端全量：`760 passed, 11 warnings`，耗时 `926.84s`。
- 标的查询页专项：`19 passed`。
- 前端全量：`174 passed, 1 failed`。唯一失败是既有 Settings 测试仍查找旧文案“实盘灰度”，当前页面文案为“正差价正费率实盘实验”；本任务未修改 Settings 模块。
- TypeScript 与 Vite 生产构建：通过。
- Ruff（本任务 Python 文件）：通过；只忽略 `test_index_components.py` 中既有的 `FLY002` 和 `RUF012`。
- Python `compileall`：通过。
- `git diff --check`：通过。

Playwright 使用真实 BTC 数据验证：

- `1440px`：页面及四栏目宽度无溢出，指数 3 列。
- `1000px`：页面及四栏目宽度无溢出，指数 2 列。
- `390px`：页面及四栏目宽度无溢出，指数 1 列。
- 三个视口均未检测到栏目重叠或可见文字溢出；每次渲染 8 个合约指数面板和 14 个精确市场。
- 验收截图位于未跟踪的 `output/trade-availability-final-local-*.png`，不提交仓库。

## 真实官方接口验证

2026-09-22 只读查询 BTC：

- Binance：8 个成分，权重合计 `0.99999999`。
- OKX：5 个成分，权重合计 `1.0`。
- Bybit：6 个成分，权重合计 `1.0`。
- Gate：6 个成分，官方权重合计 `1.0002`，按原值展示，不自行归一化。
- Bitget：6 个成分，权重合计 `1.0`。
- Aster：`not_returned`，premiumIndex 只提供指数/标记/费率相关数据。
- Hyperliquid：`not_returned`，`metaAndAssetCtxs` 不提供成分和权重；身份保留 `main / BTC`。
- Lighter：`not_returned`，`orderBookDetails` 提供 `index_price` 但不提供成分和权重；身份保留原始 `BTC`。
- 本地 `/api/health` 八家采集器均为 healthy 且均产生过成功时间；BTC 返回 14 个精确市场。
- 本地网络代理有间歇性 `PoolTimeout/ConnectTimeout`，受影响的公开状态正确降级为未知，没有误报可用；生产环境已另外完成真实接口验证。

## 生产备份与部署

- 部署前数据库备份：`backups/radar-20260922T084651Z.db`。
- 备份大小：`600698880` 字节。
- SHA-256：`e1cff5df39b35657c5005b9e01e084b3224fe66d425f3545413bf480131932d9`。
- 源库、容器内在线备份和主机备份的 `PRAGMA quick_check` 均为 `ok`；主机备份校验后删除了容器内临时副本，线上 `/data/radar.db` 未替换或删除。
- 服务器使用 `git pull --ff-only` 快进到 `2ef7ad9f79eddb35aa9d4e72f2fcca807c77659a`。
- 使用 `docker compose build --pull` 和 `docker compose up -d --remove-orphans` 完成重建；没有执行 `down -v`。
- 前后端容器均为 `healthy`，生产前端根页面返回 HTTP 200。

## 生产验证

- `/api/health` 返回 `status=ok`；Binance、OKX、Bybit、Gate、Bitget、Aster、Hyperliquid、Lighter 八家采集器均为 `healthy`、有最新成功时间、连续失败数为 0。
- `/api/trade-status/BTCUSDT` 返回 HTTP 200、`errors={}`、14 个精确市场和 8 个指数面板；每个市场都保留 `exchange + market_type + raw_symbol`，Hyperliquid 精确保留 `dex=main / raw_symbol=BTC`。
- BTC 的 Binance、OKX、Bybit、Gate、Bitget 官方指数成分分别为 8、5、6、6、6 个，权重合计分别为 `0.99999999`、`1.0`、`1.0`、`1.0002`、`1.0`。31 个官方成分均包含来源交易所、市场类型、原始符号、价格和权重。
- Aster、Hyperliquid、Lighter 指数面板均为 `not_returned`；其中 Aster 和 Lighter 只展示官方已返回的指数价格，Hyperliquid 保留 `main / BTC`，三家都没有推测或补算成分。
- BTC 逐链数据实际返回 Binance 5 条、Gate 2 条、Bitget 3 条；OKX、Bybit、Aster 没有公开链列表，保持未知并展示相应官方来源说明。
- `/api/trade-status/ZETAUSDT` 返回 HTTP 200、`errors={}`、11 个精确市场和 6 个指数面板。Hyperliquid 身份为 `future / main / ZETA`，验收时开多、开空、平空、平多均为可用；这是查询时公开状态快照，不代表特定账户订单一定成交。
- `/api/trade-status/watches/list` 返回 HTTP 200。生产已有 SAGA、ZETA 两条 Hyperliquid `main` 订阅，本轮只读检查，没有新增、删除或修改订阅，也没有发送订单或人为触发恢复通知。
- 生产 Playwright 验收覆盖 `1440px / 1000px / 390px`：指数面板分别为 3/2/1 列；整页及四个栏目 `scrollWidth === clientWidth`，没有栏目重叠或可见文字溢出；三个视口均渲染 8 个指数面板和 14 个精确市场，并显示统一账户数据说明。

## 已知问题与残余风险

- 公开市场状态不能证明特定账户的余额、仓位、保证金、地区、权限或风控状态；真实订单拒绝必须保留原始错误单独诊断。
- 盘口和深度是查询时快照，不是成交保证；手续费未计入动作可用性。
- 官方指数成分端点可能调整字段或限频；解析失败时面板显示查询失败/未返回，不使用其他行情推测。
- Gate 官方返回的权重合计可能略偏离 100%，系统按原值展示，不自行归一化。
- 前端全量仍有一条与本模块无关的 Settings 旧文案失败，以及既有 Ant Design 弃用和 `act(...)` 警告。

## 工作区保护项

本任务未暂存、回滚、删除或覆盖工作期间出现的其他模块已跟踪修改，包括：

- `backend/app/api/routes_instruments.py`
- `backend/app/exchanges/lighter.py`
- `backend/app/models/instrument.py`
- `backend/app/models/market.py`
- `backend/app/models/opportunity.py`
- `backend/app/models/pair_spread.py`
- `backend/app/services/collector.py`
- `backend/app/services/instrument_spreads.py`
- `backend/app/services/spread_engine.py`
- `backend/app/services/symbol_aliases.py`
- `backend/tests/test_instrument_lookup.py`
- `backend/tests/test_instrument_spreads.py`
- `backend/tests/test_lighter_adapter.py`
- `backend/tests/test_spread_engine.py`
- `backend/tests/test_symbol_aliases.py`

既有 `output/**`、浏览器 profile、pytest 临时目录和 `script/dexe_bybit_bitget_chain.py` 也保持未跟踪，不纳入提交。

## 下一步

- 等待真实受限或未知动作恢复，核对下一次生产飞书通知是否同时包含全部开仓/平仓动作和逐链充提状态；不要人为制造状态切换。
- 若要接入特定账户的仓位、风控或真实订单错误，应新建独立任务并明确只读授权与秘密管理边界。
- Settings 页旧文案测试属于其他模块，应在新任务中单独处理。
