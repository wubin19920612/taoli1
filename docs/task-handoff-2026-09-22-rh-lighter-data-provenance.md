# 交接：RH-Lighter 数据来源与 Astro 获取链路（已更正）

日期：2026-09-22 至 2026-09-23（Asia/Shanghai）

> 2026-09-23 更正：上一版只检查普通 Lighter 主网
> `mainnet.zklighter.elliot.ai`，遗漏 Robinhood Lighter 独立实例。上一版关于
> “RH 无法确认”“独立市场未证明”“只能作为 Astro route-only”的结论均已撤销。

## 目标与边界

本任务调查并接入 RH-Lighter 的公开只读行情。没有修改账户连接、告警阈值、
Astro 卡片状态、交易执行或下单能力；三张目标卡片始终保持暂停。本次实现仍明确
拒绝任何包含 `rh-lighter` 的 Astro 建卡请求。

## 最终结论

1. `RH` 的准确含义是 **Robinhood**，不是根据缩写推测。Lighter 官方 SDK 把该
   endpoint profile 命名为 `ROBINHOOD`；官方 PR 标题为 `Rh instance`。
2. RH-Lighter 是 Lighter 部署在 Robinhood Chain 上的独立实例。它有独立 REST、
   WebSocket、市场 ID、账户/API Key 空间、签名域和 USDG 抵押/现货报价资产。
3. 普通 Lighter `ANTHROPIC` 是 `market_id=193`；Robinhood Lighter 同名市场是
   `market_id=38`。两者盘口、mark/index、OI 和更新时间独立，不能互相复制。
4. Astro 的 `rh-lighter` 路由高置信度对应 Robinhood Lighter 实例。Astro 卡片
   `action=list` 仍只用于发现路由；Radar 的价格直接来自 Robinhood Lighter 官方
   公共 REST/WS，而不是卡片历史均价或普通 Lighter 镜像。
5. Astro 服务端内部究竟直接连接同一公共主机，还是经过自有代理，尚未观察到
   服务端证据。这不影响该市场身份和官方公共行情源的可验证性。

## 官方和生态证据

- Lighter 官方 SDK PR：<https://github.com/elliottech/lighter-python/pull/161>
  - 标题 `Rh instance`
  - 合并时间 `2026-07-09T09:38:34Z`
  - merge SHA `f50fe76c511db28fa76daac5e1b05349e2191dad`
- 官方 endpoint profile：
  <https://github.com/elliottech/lighter-python/blob/main/lighter/endpoint_profiles.py>

```python
ROBINHOOD = EndpointProfile(
    name="robinhood",
    api_url="https://api.rh.lighter.xyz",
    ws_url="wss://api.rh.lighter.xyz/stream",
    chain_id=466324,
)
```

- Robinhood Chain L1/桥接 chain ID 是 `4663`；Robinhood Lighter L2 的签名域是
  `466324`。普通 Lighter L2 的签名域是 `304`，三者不能混用。
- 官方 Agent Kit 配置明确接受 `LIGHTER_HOST=https://api.rh.lighter.xyz`，并说明
  普通 Lighter 与 Robinhood Lighter 的 account index/API key 不通用。
- LI.FI perps SDK PR：<https://github.com/lifinance/perps-sdk/pull/257>
  - 标题明确为 `add lighter-rh instance`
  - 合并 SHA `e5df3a5b712fa8c1f0ba55e7161318473de1c762`
- DefiLlama：
  - <https://github.com/DefiLlama/dimension-adapters/pull/8028>：
    `Lighter-Robinhood: track lighter perps robinhood instance`
  - <https://github.com/DefiLlama/dimension-adapters/pull/8341>：
    `Add Lighter Robinhood perps fees adapter`

官方 API 文档共检查 106 个页面。Get Started 与 WebSocket 页面仍主要列普通
mainnet/testnet，只有充值/转账相关页面提到 Robinhood。结论是文档导航尚未完整
覆盖该实例，不能据此否定官方 SDK 和真实公共端点。

## 公共接口与原始字段

Robinhood Lighter：

```text
REST https://api.rh.lighter.xyz/api/v1
WSS  wss://api.rh.lighter.xyz/stream
```

已只读验证的接口：

| 请求 | 作用 | 关键非秘密字段 |
| --- | --- | --- |
| `GET /orderBookDetails` | 永续/现货市场发现 | `market_id`, `symbol`, `status`, `mark_price`, `index_price`, `open_interest`, `daily_quote_token_volume` |
| `GET /orderBookOrders?market_id=38&limit=20` | 订单簿快照 | `bids[].price`, `asks[].price`, `remaining_base_amount`, `last_updated_at` |
| `GET /orderBooks?market_id=38` | 订单簿查询 | 市场订单簿字段 |
| `GET /funding-rates` | 当前资金费率 | `exchange`, `market_id`, `rate` |
| `GET /assetDetails` | 资产和抵押品 | USDG 资产字段 |
| `GET /candles?...` | Pair 历史 K 线 | `t`, `c`, `v`, `V` |
| `GET /fundings?...` | 历史资金费率 | `timestamp`, `rate`, `direction` |
| WS `ticker/38` | ticker 快照与更新 | `subscribed/ticker`, `update/ticker`, `last_updated_at` |
| WS `order_book/38` | 可成交盘口 | `subscribed/order_book`, `update/order_book`, `last_updated_at` |

RH `/funding-rates` 中 `exchange` 仍写 `lighter`。实例身份由请求主机和签名域决定，
不能通过在响应体中搜索字面值 `rh-lighter` 来判断实例。

## 实时样本

同步采样 `2026-09-23T00:32:36Z`：

| 字段 | 普通 Lighter | Robinhood Lighter |
| --- | ---: | ---: |
| 主机 | `mainnet.zklighter.elliot.ai` | `api.rh.lighter.xyz` |
| ANTHROPIC market ID | `193` | `38` |
| 实例市场总数 | `235` | `57` |
| 抵押/现货报价资产 | USDC | USDG |
| bid / ask | `2204.0 / 2204.5` | `2193.0 / 2193.1` |
| mark / index | `2204.3 / 2204.7` | `2192.9 / 2192.0` |
| open interest | 约 `860` | 约 `2997` |
| min base amount | `0.00250` | `0.00320` |

WebSocket 观察：

- RH `ticker/38` 在 10 秒内收到初始快照和 18 条 `update/ticker`。
- 普通 `ticker/193` 在同一静默窗口只收到初始快照。
- 两个实例都返回各自的微秒级 `last_updated_at`；RH REST 连续轮询时盘口数量
  独立变化。这排除了“把普通 Lighter 快照复制成 RH”的实现模型。

公开四源比较分为两个相邻窗口，不能伪装成同一毫秒原子快照：

| UTC 窗口 | 来源 | bid / ask | 上游时间 |
| --- | --- | ---: | --- |
| `2026-09-22T16:03:40Z` | 普通 Lighter | `2169.0 / 2170.0` | `16:03:41.591332Z` |
| 同窗口 | Binance | `2102.34 / 2102.64` | `16:03:23.624Z` |
| 同窗口 | Hyperliquid `io:ANTH` | `2156.2 / 2156.3` | `16:03:42.228Z` |
| `2026-09-23T00:32:36Z` | 普通 Lighter | `2204.0 / 2204.5` | 独立微秒时间戳 |
| 同窗口 | Robinhood Lighter | `2193.0 / 2193.1` | 独立微秒时间戳 |

`2026-09-23T01:20:52.440Z` 再次复核时，普通 Lighter 为
`2201.2 / 2201.9`，HL `io:ANTH` 为 `2185.3 / 2185.5`；本地到 RH 和 Binance
连接超时，因此没有把不完整窗口写成四源同步样本。

目前可下的结论：RH 是独立部署和独立更新的真实市场，不是普通 Lighter 镜像。
仅凭这些短窗口还不应断言它与 Binance/HL 之间是否存在统计性领先、延迟或指数
合成关系；这需要持续同秒采样，而不是比较单个价差。

## Astro 获取链路

```text
Astro HMAC SDK action=list
  -> 卡片配置：name/type/buyEx/sellEx/aHlDex/bHlDex/status
  -> 发现 rh-lighter 路由，不提供当前 bid/ask

Astro Hub 鉴权后 Dashboard（观察到的前端链路）
  -> GET  /<admin-prefix>/api/config
  -> POST /<admin-prefix>/api/funding
  -> POST /<admin-prefix>/api/login/device-ws-ticket
  -> WSS  /<admin-prefix>/ws/logs?device_ticket=<redacted>
  -> updateFuturePrice: rh-lighter:<symbol>:<single-price>
  -> window["rh-lighter"].future[symbol]

Radar 本次实现
  -> 官方 RH GET /orderBookDetails：发现 ANTHROPIC market_id=38
  -> 官方 RH WSS order_book/38：取得真实 bid/ask、数量、上游时间
  -> MarketSnapshot(exchange="rh-lighter", raw_symbol="ANTHROPIC")
  -> Instrument / Pair / Opportunity / Astro 浮窗精确匹配
  -> Astro 卡片只用于路由发现，不作为报价源
```

Astro Hub 的管理 WebSocket 只有单一价格，缺 bid/ask 语义和上游时间，因此 Radar
不接入该管理日志流。Radar 使用官方可审计盘口，不需要 Cookie、device ticket、
2FA 或浏览器私钥。

## 身份分类与证据等级

| 判断 | 结论 | 等级 | 依据 |
| --- | --- | --- | --- |
| 独立可交易市场 | 是 | 高 | 官方 endpoint profile、独立账户/API key、市场 ID、订单簿、资金费率和链 ID |
| Lighter 内部账户类型 | 否 | 高 | 官方定义为另一 endpoint/chain domain；账户索引和 key 不共享 |
| Astro 执行路由 | 是 | 高 | 三张真实卡、Dashboard 适配器 ID 和实时键 |
| Astro 合成价格 | 未发现证据 | 中 | Astro 日志只暴露单价，无公式；Radar 不使用该字段 |
| 普通 Lighter 镜像 | 否 | 高 | 独立 market ID、BBO、OI、时间戳和更新节奏 |
| 官方公开实时源 | 是 | 高 | `api.rh.lighter.xyz` REST/WSS 可匿名只读访问 |
| Astro 服务端直接使用同一源 | 未公开验证 | 中低 | 前端没有披露服务端上游连接细节 |

## 实现边界

- Radar exchange ID 使用 `rh-lighter`，保持与 Astro 路由一致。
- 普通 Lighter 和 RH 各自使用独立 REST、WS、market cache 和 market ID。
- 规范聚合 quote 保持 `USDT` 口径；RH 的真实 USDG 身份保留在原始 spot symbol
  （如 `ETH/USDG`）和 `data_source` 中，不能据此伪造普通 USDT/USDC 市场。
- `rh-lighter` 可用于 Instrument、Pair、实时机会和 Astro 浮窗只读报价。
- Astro 预览可显示 RH 市场，但返回 `can_submit=false` 和明确的只读 blocker；所有
  包含 RH 的建卡请求返回 HTTP 422。Astro 服务层还在自动告警、新币、实盘实验、
  人工和预建卡五条路径统一失败关闭，不会调用 Astro `list/add/update`；交易执行和
  账户连接没有新增 RH 能力。
- 三张暂停卡没有启用、重建、更新或下单。

## 验证与残余风险

- 初始 RH 专项后端测试：`62 passed`；最终只读边界专项 `5 passed`，Astro 服务
  完整文件 `44 passed`，RH 预览/建卡定向测试 `1 passed`。
- 专项前端测试：`90 passed`。
- 后端全量：`784 passed, 13 warnings`。该次全量运行在最终服务层硬限制之前启动；
  最终增量由上述 `44 + 1` 项受影响范围测试覆盖。
- 前端全量：`182 passed, 1 failed`。唯一失败为既有 Settings 测试仍查找旧文案
  “实盘灰度”，与本任务无关；生产构建通过。
- `python -m compileall -q app`、定向 Ruff 和 `git diff --check` 均通过。
- 测试覆盖端点/WS、USDG、`38`/`193` 缓存隔离、Pair dispatch、Instrument 路由、
  浮窗精确报价、交易所标签，以及 RH 在所有 Astro 建卡路径上的只读拒绝。
- 本地网络在最后一次四源复核时对 RH/Binance 超时；成功样本、官方端点和自动化
  行为不受影响，但部署后仍需从服务器验证持续连接健康度。
- RH 使用 USDG；把所有价格统一展示为 USDT 名义口径便于比较，但不代表抵押资产、
  稳定币风险和提现路径完全等价。
- 资金费率周期为每小时；成交额、盘口、手续费、合约单位和上游时间必须继续分别
  展示，不能仅凭价差触发交易判断。
