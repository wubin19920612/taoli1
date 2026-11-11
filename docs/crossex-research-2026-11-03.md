# Gate CrossEx 调研：能不能当 taoli1 / arb-mvp 的中心化交易所执行通道

日期：2026-11-03 · 作者：Claude · 来源：Gate 官方 API 文档 v1.0.6、产品页、帮助中心（链接见文末）；公开接口 `GET /crossex/rule/symbols` 本机实测（12671 个交易对，原始数据在会话临时目录，未入库）。

## 结论（先看这里）

**适合，而且比预想的更合适，但它把风险集中到了 Gate 一家。** 建议路线：

- **HL、Lighter 两条通道都要**：每条腿可选 arb-mvp 原生直连（非托管，钱在链上自己手里）或走 CrossEx；原生直连先维护稳；
- **Bitget 直连**（CrossEx 没有 Bitget；arb-mvp 已有 Bitget 适配器，需要你做主网冒烟后加入白名单）；
- **其余中心化交易所腿统一走 CrossEx**：arb-mvp 只需新增**一个** `crossex` 交易适配器、只要**一套 Gate API key**，就能下 Binance、OKX、Bybit、Gate、Aster、Kraken、Deribit 的永续，不用再逐家写适配器、逐家冒烟、逐家放钱；
- 放进 CrossEx 的钱按"一家交易所"算敞口上限（设计稿 §2.2），不能因为它背后连着 8 家就当成分散了。

**第一步要你做的事：去 Gate 申请开通 CrossEx 账户**（官方定位是"专业投资者、量化团队、机构"，开户门槛文档没写，可能要审核）。

## 1. 它是什么

Gate 推出的跨交易所交易和清算平台：在 Gate 开一个 CrossEx 账户、用 Gate 的 APIv4 key，通过统一接口在多家交易所下单；保证金可以多家共用。**只能用 API 交易**，没有网页下单。

## 2. 覆盖范围（本机实测 `rule/symbols`，2026-11-03）

| 交易所 | 永续 | 现货 | 合约结算币 | 永续最小名义额 |
| --- | --- | --- | --- | --- |
| Gate | 1069 | 1606 | USDT | 按张数 |
| Bybit | 889 | 386 | USDT | 5 |
| Binance | 791 | 494 | USDT | 5（少数 20/50） |
| OKX | 776 | 463 | USDT | 按张数 |
| Aster | 564 | — | USDT | — |
| **Hyperliquid** | **319** | — | USDC | 10 |
| Kraken | 207 | — | USD | — |
| **Lighter** | **191** | — | USDC | 10 |
| Deribit | 142 | 4 | USDC | — |

另有美股、港股、日股、韩股和美股 ETF 约 4700 个（和价差套利无关）。

- **Bitget 不在里面**。BMT 那个 bitget 案例、以及 taoli1 里 bitget 的机会，走不了 CrossEx。Aster 在列表里，文档的交易所清单没写，以接口为准。
- **跨所覆盖**：同一个币在 ≥2 家有永续的有 694 个，≥4 家的 624 个。BMT 在 Aster、Binance、Bybit、Gate 都有。
- **Pre-IPO 合约都在**：Binance、Bybit、OKX、Gate、Aster 都有 ANTHROPIC；Lighter 也有 ANTHROPIC；HL 有 ANTH；Kraken 叫 ANTHROPICX；Deribit 叫 ANTH。**同一个标的各家名字不一样，映射要人工核对**（和 arb-mvp 已知的坑一样：HL 的 xyz:GOLD 对 Lighter 的 XAU）。
- **HL 的 DEX 区分**：CrossEx 的 HL 合约有 319 个，名字里不带 DEX 前缀，但 `rule/symbols` 的 `tag` 字段给出了 DEX：`""` 主 DEX 179 个（主 DEX 全集）、`xyz` 116 个、`para` 24 个，319 个名字没有重复。taoli1 现有的 `gate_crossex_hyperliquid.py`（给 Astro 的 GC 卡用）已经按 `tag` 判断，可以复用。
- 目前没有永续合约处于下架倒计时（`delist_time` 全为 0）。

## 3. 资金共用（你最关心的）

- 两种账户模式：**跨所统一保证金**（默认）和**按交易所分开**。统一模式下 USDT、USDC、BTC、ETH 等都能当保证金（有折扣率）。
- **盈亏能抵，保证金不抵**：全仓下各所仓位的未实现盈亏合并进账户权益，所以"A 所多头亏、B 所空头赚"不会让亏的那腿先爆仓——**这对价差套利是最大的好处**（现在分开放钱时，单边行情可能先爆掉一腿）。但官方明确说两边**占用的保证金不互相抵消**，开一对仓仍要两份初始保证金。
- 资金不再分散：不用给每家交易所各放一份钱、各留余量，资金效率明显提高。
- Hyperliquid 腿有 10,000 USDC 免息额度，超出计息；有借贷就有小时利息（`/crossex/interest_rate`）。

## 4. 接口能否满足 arb-mvp 的执行模型

| arb-mvp 需要 | CrossEx 提供 | 评估 |
| --- | --- | --- |
| 两腿同时 IOC | `POST /crossex/orders`，`time_in_force` 支持 IOC/FOK/POC，`reduce_only` | 满足 |
| 自定义订单号、防重发 | `text` 字段（≤64 位，字母数字和 `-`、`_`），**重复 `text` 直接拒**（`TRADE_ORDER_DUPLICATE_ERROR`） | 满足，而且天然防重发；arb-mvp 的十进制订单号可直接用 |
| 结果不明时查单 | `GET /crossex/orders/{order_id}` 可以用 `text` 查；但"已完成的订单可能查不到 text"，所以 ACK 回来的 `order_id` 必须立即落库 | 满足，适配器要注意 |
| 下单回报 | **异步**：先回 ACK（只有 order_id、text），再做风控、再转发到目标交易所；状态 NEW→OPEN/FILLED/CANCELLED，`FAIL`（CrossEx 校验失败，带标准 label）与 `REJECT`（交易所拒单，原始文本） | 和 Bybit/OKX/Lighter 的"受理≠成交"同一类，arb-mvp 已有处理模式 |
| 成交和手续费 | `GET /crossex/history_trades`：成交 id、价格、数量、手续费、币种、maker/taker、已实现盈亏；按时间过滤，每页 1000 | 满足设计稿 §1 的记账；不能按订单号过滤，按时间窗补拉 |
| 私有推送 | 文档提到"订单推送"，**但没有给 WebSocket 地址和频道** | 不满足，先用轮询（订单查询 200 次/10 秒），要问 Gate（mm@gate.com） |
| 盘口行情 | **没有订单簿接口**，只有 tickers 和资金费（都要鉴权、1 次/秒） | 盘口继续用各交易所自己的公开接口（arb-mvp 和 taoli1 都已经有） |
| 账户、保证金、持仓 | `/crossex/accounts`（保证金率、各所分账户余额）、`/crossex/positions`（数量、开仓价、标记价、资金费） | 满足保证金告警和对账 |
| 交易规则 | `/crossex/rule/symbols`（免鉴权：最小量、最小名义额、步长、状态、下架时间） | 满足 |
| 限频 | 下单 100 次/10 秒、撤单 100 次/10 秒、查询 200 次/10 秒、未结订单 ≤1000 | 小单量化够用（设计稿最大并发 5 个开仓轮） |
| HL 专属额度 | 每账户 10 万次请求额度，每成交 1 USDC 回补 1 次 | 15 USD 一单回补 15 次，正常下单不会耗尽；频繁撤单会 |

## 5. 费用

- 费率按 **Gate VIP 等级**定，`GET /crossex/fee` 按交易所分别返回。文档示例：合约吃单 0.022%、挂单 0.006%，Lighter 0.005%/0.005%——**只是示例**，真实费率要等开户后用 key 查。
- 对比直连：HL 吃单 0.045%、Binance 约 0.05%；Lighter 直连 Standard 档是 0。**如果示例费率接近真实，CEX 和 HL 腿走 CrossEx 反而更便宜**，Lighter 直连更便宜。
- 清算费：帮助中心一处写 2%，另一处没写比例；`rule/symbols` 里逐合约给了 `liquidation_fee`（例如 0.5%）。
- 资金划转：统一模式内的 USDT 划转和 Gate 相关划转免费。

## 6. 风险（必须讲清楚）

1. **风险集中到 Gate**：所有放进 CrossEx 的钱，安全性取决于 Gate 一家。托管方是谁、钱是否真的分到各交易所子账户，文档**没有说明**。设计稿 §2.3 讲的"交易所倒闭 / 停提"，现在是 Gate 一家出事就全部受影响，而且它连着的任何一家出事（例如 Binance 暂停），那一家的腿也会卡住。**所以 CrossEx 整体按"一家交易所"设上限，HL、Lighter 直连的钱不要搬进去**。
2. **清算先平多头**：保证金率 ≤100% 时强平，顺序是"先平多、后平空"。对一多一空的套利来说，这意味着被强平时**先变成单边空头**。必须把 CrossEx 账户的保证金率当成熔断信号（设计稿 §5.3），远离 100%。
3. **ADL 在底层交易所发生**：CrossEx 自己不撮合，ADL 由各交易所原生执行，CrossEx 再被动跟着减仓；有 `/crossex/adl_rank` 可以查排名。
4. **多一跳延迟**：订单先到 Gate（AWS 东京），风控后再转发；文档没给延迟数据。我们服务器在首尔，到东京很近，但两腿 IOC 的时间差需要实测。
5. **"交易通道繁忙"**：错误码 `NOT_BEST_ACCOUNT_ROUTER` 说明 Gate 内部用自己的账户池路由，高峰时可能拒单。
6. **没有私有推送文档、没有测试网**：只能用轮询；冒烟测试只能小额主网（由你执行）。
7. **名称映射**：各家同一标的名字不同（ANTHROPIC / ANTH / ANTHROPICX），映射错了就是"假机会"；HL 的 DEX 用 `tag` 字段区分（见 §2）。

## 7. 对现有方案的影响

| 设计稿章节 | 变化 |
| --- | --- |
| 路线图阶段 5（其他交易所逐家冒烟） | **大幅提前、大幅简化**：只做一次 CrossEx 冒烟，就覆盖 7 家中心化交易所 |
| §2.2 单交易所上限 | CrossEx 整体算一家，按 D5 二档（10%）起步，跑稳再提；里面再按底层交易所做二级上限 |
| §5.3 交易所异常 | 加 CrossEx 账户保证金率、`FAIL`/`REJECT` 比例、`NOT_BEST_ACCOUNT_ROUTER` 频率 |
| §1 记账 | 成交来自 `history_trades`，字段齐全，可以直接拆账 |
| taoli1 机会 | 每个机会标注"两腿都能通过 CrossEx 或直连执行"；bitget 腿的机会只进影子账本 |
| 之前讨论的"taoli1 直接接 Astro" | 有了 CrossEx 可以不依赖 Astro：执行和成交记录都在我们自己手里 |

## 7.1 通道选择（11-03 用户确定）

| 交易所 | 通道 | 说明 |
| --- | --- | --- |
| Hyperliquid | **原生 或 CrossEx，按腿选** | 原生已主网验证；走 CrossEx 可与 CEX 腿共用保证金 |
| Lighter | **原生 或 CrossEx，按腿选** | 原生 Standard 档 0 费率；CrossEx 示例费率 0.005% |
| Bitget | 原生直连 | CrossEx 不支持；适配器已有，待你做主网冒烟 |
| Binance / OKX / Bybit / Gate / Aster / Kraken / Deribit | CrossEx | 不再逐家写直连 |

arb-mvp 的做法：腿的 `venue` 仍表示**底层交易所**（行情、标签、标的映射都按它），新增可选字段 `route: 'native' | 'crossex'`（缺省 `native`）决定用哪个交易适配器。同一张卡两腿可以混用（例如 HL 原生 + Binance 经 CrossEx）。这样记账、分桶、行情都不用因为通道不同而分叉，只在交易层切换。按腿选通道的取舍：两腿都走 CrossEx 才能共用保证金、盈亏互抵；一腿原生一腿 CrossEx 时，两边各自要放保证金。

## 8. 建议的接入步骤

| 步骤 | 内容 | 谁做 |
| --- | --- | --- |
| C0 | 申请 CrossEx 账户；建 API key：开 CrossEx 交易权限，**不开提现**，绑定服务器 IP 白名单；只往里放小额 | **你** |
| C1 | 只读核对：用 key 查真实费率、账户模式、各交易对；生成"标的映射表"（各所名字 ↔ 同一标的，HL 按 `tag` 对到 DEX），名字不一致的人工复核 | Claude（只读） |
| C2 | arb-mvp 新增 `crossex` 交易适配器（下单、按 order_id/text 查单、撤单、成交补拉、账户与保证金）；腿写法见 §7.1（`venue` 是底层交易所，`route:'crossex'`），**盘口仍用底层交易所的公开行情**；先在模拟盘跑 | Codex 编码，Claude 验收 |
| C3 | 主网 dry-run（读余额、设杠杆，不下单）由 Claude 做；小额下单冒烟由你执行 | Claude + 你 |
| C4 | taoli1 机会标注 CrossEx 可执行、建卡支持 crossex 腿；纳入设计稿的影子账本与分桶 | Codex 编码 |

**不变的部分**：HL、Lighter 的直连适配器继续维护（你说的"先把 arb-mvp 的 DEX 维护好"）；主网下单仍由你执行。

## 9. 还没弄清楚、需要问 Gate 的

1. 开户门槛（是否只限机构、资产要求、KYC、地区）；
2. 托管方，以及资金是否实际划到各交易所子账户；
3. 私有 WebSocket 的地址和频道；
4. 下单到底层交易所的典型延迟；
5. 真实费率（按你的 VIP 等级）、清算费比例（2% 还是按合约）；
6. 历史成交保留多久；
7. HL 腿走 CrossEx 时，仓位是记在 Gate 的 HL 账户池里，还是可以绑定自己的 HL 地址。

技术联系人：mm@gate.com；机构开户：Institutional@gate.com。

## 来源

- [Gate CrossEx API 文档（英文，v1.0.6）](https://www.gate.com/docs/developers/crossex/en/)
- [Gate CrossEx 产品页](https://www.gate.com/crossex)
- [CrossEx 帮助中心：功能说明](https://www.gate.com/help/crossex/functional)、[清算规则 49325](https://www.gate.com/help/crossex/functional/49325)、[常见问题 49324](https://www.gate.com/help/crossex/functional/49324)
- [Gate 发布文章](https://www.gate.com/learn/articles/gate-launches-cross-ex-a-pioneering-cross-exchange-platform-redefining-institutional-crypto-trading-infrastructure/13421)（只写了 Gate、Binance、OKX，已过时）
- 本机实测：`https://api.gateio.ws/api/v4/crossex/rule/symbols`（免鉴权）、HL `info` 接口 `meta`（主 DEX 与 xyz）
