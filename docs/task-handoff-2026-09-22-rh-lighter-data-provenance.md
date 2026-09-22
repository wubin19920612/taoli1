# 交接：RH-Lighter 数据来源与 Astro 获取链路

日期：2026-09-22 至 2026-09-23（Asia/Shanghai）

## 目标与范围

本任务只调查 `RH-Lighter` 的真实数据来源与 Astro 获取链路。没有修改交易执行、账户连接、告警阈值、卡片状态、生产风险配置或其他功能模块；没有启用、创建、更新、删除或下单三张目标卡片。

验收目标是：

- 枚举 Astro 页面、SDK 和本项目代理涉及的 REST、WebSocket、SSE、GraphQL 与代理链路。
- 区分卡片配置响应、详情/资金信息、实时价格推送和本项目二次代理。
- 同时采样普通 Lighter、Binance、Hyperliquid `io:ANTH` 与 Astro SDK 中的 RH-Lighter 卡片。
- 说明 `rh-lighter` 当前能证明的身份、不能证明的身份和证据等级。
- 只在接口具备可审计价格语义和时间戳后设计代码接入；不复制普通 Lighter 行情。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 调查基线：`39cb41ed9df3c24d3b82b5d57190384844d9f722`
- 上一功能提交：`cf12f7d4b3e7084f76aac955afb8ec22f475160f`
- 上一交接：`docs/task-handoff-2026-09-22-lighter-rh-lighter-markets.md`
- 调查开始时存在大量其他任务的未跟踪截图、脚本和 pytest 临时目录；本任务未修改、删除或暂存它们。

## 最终结论

1. **未发现 Lighter 官方公开接口中的 `rh-lighter` 独立市场。** `orderBookDetails`、`order_book/{market_id}` 和 `funding-rates` 中的 ANTHROPIC 市场仍是普通 Lighter `market_id=193`。这项证据只限定 Lighter 公开接口。
2. **Astro Hub 确实实现了 RH-Lighter 实时价格链路。** 鉴权后 Dashboard 分包把 `rh-lighter` 列为期货行情源，并解析 `>>> updateFuturePrice: rh-lighter:<symbol>:<price>`；收到后写入 `window["rh-lighter"].future[<symbol>]`。
3. **实时价不来自现有 HMAC SDK 卡片列表。** 连续 5 次 `action=list` 只返回卡片配置、历史成交均价和阈值，没有当前价或时间戳。暂停卡并不妨碍 Dashboard 的全局行情流，但本次没有可安全使用的 Dashboard 登录会话取得实际 RH 推送值。
4. **当前不能判定 RH-Lighter 是独立市场、普通 Lighter 镜像、延迟价格还是合成价格。** WebSocket 日志只带单一浮点价格，没有 bid/ask、mark/index/last 语义、上游时间戳、序列号、合约单位或上游 venue 标识。
5. **`RH` 的准确展开仍未得到可验证定义。** Astro UI 的正式标签只有 `RH Lighter`；仓库、全部已抓取 Astro 分包、Astro 公开站点和精确公开检索都没有把 `RH` 展开为 `Robinhood` 或其他词。仓库中过去把 `RH` 用作 `HOOD/Robinhood` 搜索别名，与 Astro 的 `rh-lighter` 交换所 ID 不是同一证据，不能混用。
6. **本项目继续保持 `route_only` 是正确的安全决策。** 这里表示“本项目尚无可验证、可成交的 RH 行情接入”，不表示“Astro 不存在 RH 实时数据源”。

## Astro 调用链

```text
Arbitrage Radar 后端
  -> POST /<admin-prefix>/api/config/sdk-update-pair
     body: {"action":"list"}
     auth: x-timestamp + x-nonce + x-sign
     <- 卡片配置列表；无 RH 当前价和上游时间戳

Astro Hub 浏览器（另一套鉴权）
  -> GET  /<admin-prefix>/api/login/verify-token
  -> GET  /<admin-prefix>/api/config
     <- status + coinPairs + mmr + liq + meta
  -> POST /<admin-prefix>/api/funding
     body: {"items":[{"ex","coin","type","dex?"}, ...]}
     <- 按请求顺序返回资金信息字符串和 index 字段
  -> POST /<admin-prefix>/api/login/device-ws-ticket
     <- 一次性 device ticket
  -> WSS  /<admin-prefix>/ws/logs?device_ticket=<redacted>
     <- JSON type=auth / heartbeat / logs
     <- logs.data.data 中解析 updateFuturePrice: rh-lighter:<symbol>:<price>
  -> 浏览器内存 window[exchange].future[symbol]
  -> Astro 卡片差值/比例展示与执行逻辑

RH-Lighter 的 Astro 上游
  -> 未在页面、SDK 响应或公开文档中披露，仍需 Astro 产品方或可审计服务端证据确认
```

本项目还有两层只读代理：

- `GET /api/astro/pairs`：前端携带本项目 Dashboard 密码访问 Radar 后端，Radar 后端再调用 Astro HMAC SDK `action=list`。
- `GET /api/instruments/{symbol}`：Radar 后端内部调用同一个 SDK `list`，10 秒缓存，只用于发现 Astro 路由证据。

这两层都没有取得 Astro Hub 管理页的 `/api/funding` 或 `/ws/logs` 数据。

## 仓库代码入口与字段来源

- `backend/app/services/astro_client.py`：`AstroSdkConfig.list_path` 生成 `/<admin-prefix>/api/config/sdk-update-pair`；`AstroSdkClient.list_pairs()` 发送 `{"action":"list"}`，`_post()` 负责 HMAC 请求头并只返回响应中的 `data` 列表。该客户端没有 `/api/config`、`/api/funding` 或 `/ws/logs` 方法。
- `backend/app/api/routes_astro.py`：Radar 的 `GET /api/astro/pairs` 校验本项目 Dashboard 密码后调用 `AstroSdkClient.list_pairs()`，不添加 RH 行情字段。这里的 Radar Dashboard 密码不是 Astro Hub 的 Cookie、设备签名或 WebSocket ticket。
- `frontend/src/api/client.ts`：`listAstroPairs()` 只请求 Radar 的 `/astro/pairs` 并验证结果为对象数组；前端没有直接连接 Astro Hub WebSocket。
- `backend/app/api/routes_instruments.py`：标的查询同样调用 `list_pairs()`，使用 10 秒缓存，并从 `name/type/buyEx/sellEx/aHlDex/bHlDex` 派生路由身份；`rh-lighter` 无法映射到已验证市场时返回 `route_only`。
- `frontend/src/components/FloatingWatchPanel.tsx`：浮窗按 Astro 卡片字段解析两腿，但价格来自 Radar 的标的行情快照；遇到 `rh-lighter` 且没有已验证市场时停止价差计算，不使用 Astro 卡片历史均价代替实时报价。
- `backend/app/models/pair_spread.py`：Pair 查询把 `rh-lighter` 保留在 route-only 集合中，拒绝将它当作已验证的公开市场数据源。

因此，仓库当前链路只把 Astro SDK 当作卡片和路由发现源；Astro Hub 分包中的 RH 实时价格链没有接入 Radar。

## 网络接口证据

### 页面启动期实际抓包

2026-09-22T15:44:39.798Z 使用全新、无 Cookie 的浏览器上下文打开 Astro Hub：

| 方法 | 脱敏路径 | 状态 | 类型 | 说明 |
| --- | --- | ---: | --- | --- |
| GET | `/<admin-prefix>/` | 200 | document | Astro Hub HTML |
| GET | `/<admin-prefix>/theme-init-v3.js` | 200 | script | 主题初始化 |
| GET | `/<admin-prefix>/assets/index-8Yi8AkNZ.js` | 200 | script | 主包 |
| GET | `/<admin-prefix>/assets/index-B8Lyj8C_.css` | 200 | stylesheet | 主样式 |
| GET | `/<admin-prefix>/assets/zh-CN-CMf_Sbg-.js` | 200 | script | 中文资源 |
| GET | `/<admin-prefix>/assets/index-qc9t8lUK.js` | 200 | script | 登录页分包 |
| GET | `/<admin-prefix>/assets/LanguageSwitcher-DeDBG-gb.js` | 200 | script | 登录、设备密钥与 WS ticket 逻辑 |
| GET | `/<admin-prefix>/icon.png` | 200 | image | 页面图标 |
| GET | `/<admin-prefix>/api/login/verify-token` | 401 | XHR | 无会话返回未授权 |

该无登录页面没有建立 WebSocket，没有 SSE，也没有 GraphQL 请求。

### 静态构建产物

- HTML：2948 字节，SHA-256 `aa03d58e6831e6f9788904d1910243128005ba0313add48c156435b11bb1ce1d`。
- 主包 `index-8Yi8AkNZ.js`：546001 字节，SHA-256 `fff17146ff7b8a1834b6821ad572000f0e61eeb173a9e98f648c8317b0e29dbf`。
- Dashboard 分包 `index-7UXlNyu3.js`：791965 字节，SHA-256 `8e9f18eed6d75f09ce878478aa36d3f5fe85c5602810e0000ea9e2c7c17c5a91`。
- 两个主分包都没有公开 source map；文件名是部署哈希，后续版本可能变化。
- 主包没有硬编码 `rh-lighter`；Dashboard 分包有 7 处，说明该能力属于鉴权后的管理页面。

Dashboard 分包中的关键非秘密逻辑：

- 交换所下拉选项包含 `{value:"rh-lighter", label:"RH Lighter"}`。
- 期货支持集合包含 `rh-lighter`；资金请求键为 `rh-lighter-F-<coin>`。
- 卡片请求优先使用 `aMarketSymbol/bMarketSymbol`，缺失时从 `name + type` 派生标的。
- 实时正则明确接受 `updateFuturePrice: rh-lighter:<symbol>:<price>`。
- 同一分包还把 `rh-lighter` 纳入 MMR、强平信息和“清仓可增加反向仓位”的执行能力列表，说明它不只是一个前端显示标签，而是 Astro 内部适配器/路由 ID。
- 行情推送到达后只保存单一价格，并触发 500 ms 合并的 UI 缓存变更事件；没有保存服务端或上游时间戳。
- WebSocket 每 5 秒检查一次心跳，45 秒无消息则重连；失败后 3 秒重试。这些是连接健康参数，不是 RH 行情更新频率。

### 鉴权边界

Astro SDK 与 Astro Hub 管理页使用不同鉴权：

- SDK：HMAC `x-timestamp/x-nonce/x-sign`，已验证可调用 `sdk-update-pair action=list`。
- 管理页：登录 Cookie、CSRF、浏览器 IndexedDB 设备私钥签名；WebSocket 还需要 `POST /api/login/device-ws-ticket` 返回的一次性票据。

实测结果：

- 对管理端 `GET /<admin-prefix>/api/config` 添加 SDK HMAC，返回 `401 bad access token`。
- 对管理端 `POST /<admin-prefix>/api/funding` 发送 `{"items":[]}` 并添加 SDK HMAC，返回 `401 bad access token`。
- 正确 Origin、无 ticket 连接 `WSS /<admin-prefix>/ws/logs`，服务端以 `1008 bad session` 关闭。
- 错误或缺失 Origin 会先得到 `1008 bad origin`。

没有输出或提交 Cookie、票据、密码、API Key、签名值、设备私钥或 2FA 数据。

### REST 详情与资金接口

`GET /api/config` 的编译期响应读取字段为：

- `data.status`
- `data.coinPairs`
- `data.mmr`
- `data.liq`
- `data.meta.pinnedPairs`

`POST /api/funding` 的请求项字段为：

- `ex`：例如 `lighter`、`rh-lighter`、`binance`、`hl`
- `coin`：优先使用 `aMarketSymbol/bMarketSymbol`，否则从卡片名派生
- `type`：`F` 或 `S`
- `dex`：仅在特定 DEX 路由需要时携带，例如 Hyperliquid

前端按请求顺序解析每个响应元素：数组第 1 项为类似 `--/--/--` 的资金信息字符串，第 2 项命名为 `index`。未取得鉴权后原始响应，不能进一步断言这个 `index` 是指数价格、指数差或其他内部指标，也不能把它当作 RH 可成交报价。

### WebSocket、SSE、GraphQL 与代理检查

- Astro Hub 实时协议是 WebSocket，不是 SSE。
- 已抓取的 HTML、主包、Dashboard 分包、登录分包和语言分包中没有 `EventSource` 或 GraphQL 客户端/路径。
- Astro Hub 前端通过首个 URL path segment 把 `/api/...` 改写为 `/<admin-prefix>/api/...`，属于同源管理前缀，不是独立公开 API。
- Astro 公开资金费率页面另有 `https://funding.astro-btc.xyz/api/proxy`，但其 1.7 MB 构建包中没有 `rh-lighter`、`RH Lighter`、`Robinhood` 或 `Lighter`，没有证据表明它参与本次 RH 链路。
- 本项目 Nginx `/api/` 只代理 Radar 前端到 Radar 后端；不会把浏览器直接代理到 Astro Hub。

## 三张暂停卡片

2026-09-22T15:36:55.929358Z，SDK `action=list` 返回 85 张卡，其中 3 张使用 `rh-lighter`，均保持暂停：

| 卡片 ID | `name` | `type` | 买入路由 | 卖出路由 | HL DEX | `status` |
| --- | --- | --- | --- | --- | --- | --- |
| `wjDf8JvPty` | `ANTHROPIC` | `FF` | `lighter` | `rh-lighter` | - | `false` |
| `66bgZ2qVTv` | `ANTH-ANTHROPIC` | `FR` | `hl` | `rh-lighter` | `aHlDex=io`, `aEffectiveHlDex=io` | `false` |
| `ABLeJVRm7z` | `ANTHROPIC` | `FF` | `binance` | `rh-lighter` | - | `false` |

FR 卡片另有 `regressionValue="1"`、`rateMultiply="1"`。三张卡的 `avgOpenAExPrice/avgOpenBExPrice/avgCloseAExPrice/avgCloseBExPrice` 都为 `0`；这些字段是历史成交均价，不是当前报价。

SDK 全部卡片响应可见的字段包括卡片 ID、名称、类型、路由、开平仓阈值、仓位/限额、杠杆、状态、HL DEX、回归倍率、历史成交均价、最大仓位和已实现利润。没有 `bid`、`ask`、`markPrice`、`indexPrice`、`lastPrice`、`quoteTimestamp` 或等价的当前行情字段。

本次只执行 `action=list`。没有调用 SDK 的 `add/update/delete`，没有调用管理页的卡片动作接口。

## 同时采样

采样窗口：2026-09-23 00:03:40 至 00:04:12 CST，即 2026-09-22T16:03:40.655477Z 至 16:04:12.786528Z。

| 来源 | 原始请求 | 样本/消息 | 价格变化 | 时间戳情况 |
| --- | --- | ---: | ---: | --- |
| Astro SDK RH-Lighter | `POST sdk-update-pair {"action":"list"}`，约每 5 秒 | 5 次 | 无当前价格字段 | 无行情时间戳 |
| Lighter ANTHROPIC | WSS `order_book/193` | 1 个订阅快照 + 36 个增量 | 6 个可完整解析的不同 bid/ask | `last_updated_at` 微秒时间戳 |
| Binance ANTHROPICUSDT | `GET /fapi/v1/ticker/bookTicker`，约每 2 秒 | 12 次 | 3 组不同 bid/ask | 响应 `time` 毫秒时间戳 |
| Hyperliquid `io:ANTH` | `POST /info {"type":"l2Book","coin":"io:ANTH"}`，约每 2 秒 | 12 次 | 1 组 bid/ask | 响应 `time` 毫秒时间戳 |

窗口早段可对齐样本：

- Lighter：bid/ask `2169.0 / 2170.0`；上游时间 `2026-09-22T16:03:41.591332Z`；本地收到 `16:03:44.573814Z`。
- Binance：bid/ask `2102.34 / 2102.64`；响应时间 `2026-09-22T16:03:23.624Z`；本地轮询 `16:03:41.592710Z`。
- Hyperliquid `io:ANTH`：bid/ask `2156.2 / 2156.3`；响应时间 `2026-09-22T16:03:42.228Z`；本地轮询 `16:03:41.592710Z`，表明本机和上游时钟存在约 0.6 秒偏差，不能用单点负延迟解释数据新鲜度。
- RH-Lighter：同一窗口内 SDK 3 张卡都没有当前报价或上游时间戳；无法给出诚实的数值对比。

更新频率观察：

- Lighter 30 秒内收到 36 条 `update/order_book`，约 1.2 条/秒；增量消息不一定同时包含完整双边，6 条可直接还原非交叉 bid/ask。
- Binance 2 秒轮询观察到 3 组不同 BBO；部分响应的 `time` 值出现回退或缓存，因此只能描述“轮询观察频率”，不能当作严格推送频率。
- Hyperliquid 12 次响应的 BBO 未变化，但响应 `time` 持续更新；这表示接口持续返回新快照，不表示市场价格每 2 秒变化。
- RH-Lighter 的真实更新频率无法从 SDK 列表推断；Dashboard WebSocket 的 45 秒心跳阈值也不是行情频率。

由于缺少实际 RH 值，**不能**把 RH-Lighter 分类为独立行情、普通 Lighter 镜像、延迟行情或合成路由。只能确认 Astro 设计上把它作为独立行情键接收和缓存。

## 身份与证据等级

| 判断 | 结论 | 证据等级 | 依据 |
| --- | --- | --- | --- |
| Lighter 公开独立市场 | 未发现 | 高 | 官方公开市场元数据、订单簿、资金费率均无 `rh-lighter` |
| Astro 执行路由/适配器 ID | 是 | 高 | 三张真实卡、交换所下拉、执行能力列表、MMR/强平/价格缓存逻辑 |
| Astro 内部实时价格键 | 是 | 高 | Dashboard 的 `updateFuturePrice` 正则和内存写入路径 |
| HMAC SDK 当前行情源 | 否 | 高 | 5 次同时采样无当前行情字段；SDK 只实现卡片 CRUD |
| 独立可交易市场 | 未证明 | 低 | 没有上游市场 ID、合约规格、盘口或官方说明 |
| Lighter 内部账户/路由类型 | 可能，但未证明 | 低 | 名称和执行能力与 Lighter 有关；无服务端定义 |
| Astro 合成价格 | 未证明 | 低 | 前端只消费一个价格，没有合成公式或来源字段 |
| 普通 Lighter 镜像/延迟源 | 未证明 | 低 | 没有 RH 实际同步样本和服务端时间戳 |
| `RH` 等于 Robinhood | 不成立为已验证结论 | 高 | 可检查资料均未展开 `RH`；旧 HOOD 别名不是同一证据 |
| 可由鉴权后接口继续验证 | 是 | 高 | `/api/config`、`/api/funding`、device ticket、`/ws/logs` 调用链完整存在 |

## 最小接入方案

当前不建议直接接入管理页 `/ws/logs`。它是设备绑定的管理日志流，不是稳定行情 SDK，且只给单价、无上游时间戳。最小可靠方案应先满足以下接口契约：

1. 由 Astro 提供 HMAC 只读行情接口或服务账号 WebSocket，不复用浏览器 Cookie、设备私钥或一次性 ticket。
2. 每条 RH 行情至少返回：`exchange_id`、准确的 `rh` 定义、`market_id/raw_symbol`、`market_type`、`bid`、`ask`、`mark/index/last` 的明确语义、`upstream_timestamp`、`sequence`、`contract_size/price_multiplier`、上游 venue/source 和是否可交易。
3. 新适配器先只记录 RH 快照，不进入机会、告警或交易判断；同步记录普通 Lighter、Binance、HL `io:ANTH`，至少覆盖活跃和静默时段。
4. 用价格相关性、固定偏差、滞后相关、更新时间间隔和序列连续性区分独立、镜像、延迟或合成；不能只看单个时点接近程度。
5. 只有真实 bid/ask、合约倍率、手续费、成交额和时间戳都验证后，才允许从 `route_only` 升级为实时市场。

若 Astro 只能提供当前 WebSocket 日志，可做一个隔离的研究采集器，但必须标记 `price_field=unknown_single_price`、`is_executable=false`、`upstream_timestamp=null`，且不得进入交易机会。该方案只适合取证，不适合生产价差计算。

## 验证结果

- Astro SDK `action=list`：成功，85 张卡，3 张 RH 目标卡均为暂停。
- Astro 页面无登录 Playwright 抓包：成功，记录全部启动期请求；无 WebSocket/SSE/GraphQL。
- 管理端静态分包反查：成功，定位 REST、设备 ticket、WebSocket、RH 价格正则和浏览器缓存路径。
- 管理端接口鉴权隔离：成功验证 SDK HMAC 不能访问 `/api/config` 和 `/api/funding`。
- WebSocket 未鉴权边界：正确 Origin 下返回 `1008 bad session`。
- 30 秒同步公开行情采样：成功，Lighter、Binance、HL 均取得 bid/ask 与上游时间字段。
- 精确公开检索与 Astro 公开站点抓取：没有找到 `RH` 的官方展开。
- 未执行代码测试或生产构建：本任务没有修改运行代码。

## 线上状态与残余风险

- 三张目标卡始终为 `status=false`；没有改变生产业务状态。
- 没有取得或输出 Astro 管理页密码、2FA、Cookie、device ticket、设备私钥、API Key 或签名。
- 没有部署运行代码，也没有修改线上数据库，因此没有创建本任务数据库备份。
- 最大残余风险是 Astro 前端证明了 RH 实时通道存在，但没有证明其上游来源和可成交性。把单一推送价当作盘口会制造错误价差和交易判断。
- 构建分包会随 Astro 升级变化；后续复核应记录新的分包哈希和接口行为，不只依赖本次文件名。

## 下一步建议

- 向 Astro 产品方索取 `RH` 的正式定义和只读行情接口字段说明，重点询问上游 venue、价格语义、合约单位和时间戳。
- 在可安全使用的 Astro 管理会话中，仅打开 Dashboard，不启用目标卡，抓取 `/api/config`、`/api/funding` 和 `/ws/logs` 30 至 60 分钟；所有认证值在采集时即脱敏。
- 对 `rh-lighter/ANTHROPIC` 的单价与普通 Lighter bid/ask、Binance bid/ask、HL `io:ANTH` bid/ask 做同秒样本和滞后分析。
- 若取得满足最小接口契约的证据，再新建“RH-Lighter 只读行情适配器”任务；不要在本任务中顺带修改交易、账户或告警模块。
