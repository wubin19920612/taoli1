# 交接：Lighter / RH-Lighter 市场、价差与实时机会

日期：2026-09-22

## 目标与范围

本任务只处理 Lighter / RH-Lighter 行情采集、标的查询、精确双腿价差、实时机会以及 Astro 精确跳转。没有修改账户持仓连接、交易执行、告警阈值、Astro 下单、建卡、暂停、持仓或利润逻辑。

验收目标是：

- 查明 `rh-lighter` 的真实含义，不能复制普通 Lighter 行情伪造独立市场。
- 把 `ANTH`、`ANTHROPIC`、`ANTHROPICUSDT` 映射到同一规范标的，同时保留各交易所原始市场、市场类型、价格倍率、数量乘数和 Hyperliquid DEX。
- 标的查询列出真实市场及 Astro 路由证据；精确价差和实时机会只使用可验证、未过期的真实 bid/ask。
- Astro、标的查询和实时机会跳转到 Pair 页面时，完整保留双腿身份和方向。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`583ffe35e61bb8fc3a0dd1b86c7c1d29e2584316`（`docs: record spread type label delivery`）
- 功能提交：`cf12f7d4b3e7084f76aac955afb8ec22f475160f`（`feat: add verified Lighter market routing`）。
- 生产运行代码：`cf12f7d4b3e7084f76aac955afb8ec22f475160f`。

## RH-Lighter 结论

真实 Astro 卡片列表包含以下 ANTHROPIC 路由：

- `lighter -> rh-lighter`
- `binance -> rh-lighter`
- `hl(io:ANTH) -> rh-lighter`

Astro 列表响应没有独立的 `aSymbol`、`bSymbol`、`buySymbol` 或 `sellSymbol` 原始腿字段；当前能够确认的腿身份来自 `name + type + buyEx/sellEx + aHlDex/bHlDex`。Astro 响应仅用于发现路由，不作为价格、成交额或资金费率来源。

Lighter 官方 `funding-rates` 对 ANTHROPIC 的 `market_id=193` 只返回 `lighter`、`binance`、`bybit` 等来源，没有独立的 `rh-lighter` 市场或行情源。现有公开市场元数据和订单簿接口也没有 `rh-lighter`。

因此本任务把 `rh-lighter` 建模为 `route_only`：

- 标的查询展示 Astro 路由证据，并明确说明不支持独立实时行情。
- Pair 精确查询 `rh-lighter` 返回 HTTP 422，错误说明它是没有已验证独立公开行情源的 Astro 路由。
- 不复制 Lighter 的价格、成交额、资金费率或盘口，不生成 RH-Lighter 实时机会或重复告警。

## 已完成功能

### 市场身份与别名

- `ANTH`、`ANTHUSDT`、`ANTHROPIC`、`ANTHROPICUSDT` 统一解析为规范标的 `ANTHROPICUSDT`。
- Hyperliquid 保留 `dex=io` 和 `raw_symbol=io:ANTH`，不会与 main 或其他 builder DEX 混合。
- OKX `ANTHROPIC-USDT-SWAP` 使用已验证的价格倍率 `10`；规范行情和价差在统一价格口径计算。
- 市场和机会身份包含交易所、市场类型、原始市场、具体 DEX、价格倍率和已知数量乘数。
- 同一规范 ticker 的不同原始市场和不同 HL DEX 分别列出、分别去重；HL 多候选且没有显式 DEX 时要求用户选择，不随机匹配。

### 行情采集

- Lighter 通过官方 `orderBookDetails` 发现市场，并通过官方 WebSocket `order_book/{market_id}` 获取真实 bid/ask 和盘口数量。
- Lighter 永续扫描仍限制为 48 个市场，但 `ANTHROPIC` 与既有 `HOOD` 一起占用优先槽位；缺少优先盘口时失败关闭，不以 mark/last 代替。
- Hyperliquid `io:ANTH` 优先请求官方 `l2Book`。失败时保留元数据，但将 bid/ask 标记为估算、记录精确市场错误，并禁止生成实时机会。
- Binance、OKX、Gate、Bitget 补充公开合约元数据中的数量乘数；市场元数据请求失败不会隐藏已经取得的真实盘口。
- 快照记录数据来源、上游时间、估算字段和本地更新时间。实时机会使用上游时间进行新鲜度过滤。
- 单市场盘口错误保留其他市场快照，并通过交换所错误/健康状态暴露具体原因。

### 标的查询与指定价差

- `/api/instruments/ANTH`、`ANTHROPIC`、`ANTHROPICUSDT` 返回相同规范标的和所有真实精确市场。
- 每个市场返回并展示交易所、市场类型、原始市场、规范标的、HL DEX、价格倍率、数量乘数、bid/ask、24h 成交额、资金费率及周期、来源、上游时间、年龄和实时/过期状态。
- Astro 路由单独展示；`route_only` 不混入真实市场列表。
- 标的查询的正反向价差使用买入 ask 和卖出 bid。跳转 Pair 时保留双腿原始市场、市场类型、DEX、价格倍率和数量乘数。
- Pair 当前行情展示双方 bid/ask、成交额、资金费率与周期、数据来源、上游时间、估算字段、估算 taker 手续费、价格倍率和数量乘数。
- Pair URL、保存预设、浮窗预设打开和左右交换均保留上述市场身份。

### 实时机会与 Astro 联动

- 实时机会只接受具有真实 bid/ask 且未超过 `stale_after_seconds` 的市场。
- 机会身份和去重键包含双方交易所、市场类型、原始市场、DEX、方向、价格倍率和已知数量乘数。
- 机会展示和跳转保留双腿来源、时间、估算状态、手续费、倍率、成交额、资金费率及各自周期。
- Astro 浮窗优先匹配精确市场列表；HL 使用卡片 `aHlDex/bHlDex`，RH-Lighter 明确显示仅路由且禁用伪实时价差入口。
- Astro 比率卡会把交换所价格别名倍率纳入换算，避免 OKX `10x` 等别名与 Astro `regressionValue` 重复应用。

## 关键代码入口

- 市场与路由查询：`backend/app/api/routes_instruments.py`
- Lighter 真实订单簿：`backend/app/exchanges/lighter.py`
- Hyperliquid `io:ANTH` 盘口与 DEX：`backend/app/exchanges/hyperliquid.py`
- 别名与倍率：`backend/app/services/symbol_aliases.py`
- 精确 Pair 查询：`backend/app/services/pair_spread_query.py`
- 标的价差：`backend/app/services/instrument_spreads.py`
- 实时机会身份、新鲜度和估算过滤：`backend/app/services/spread_engine.py`
- 采集错误隔离：`backend/app/services/collector.py`
- 标的查询页面：`frontend/src/pages/InstrumentLookupPage.tsx`
- Pair 页面：`frontend/src/pages/PairMonitorPage.tsx`
- Astro 浮窗：`frontend/src/components/FloatingWatchPanel.tsx`
- 实时机会跳转：`frontend/src/components/OpportunityTable.tsx`

## 真实公开数据证据

2026-09-22 本地验收期间从公开接口取得：

- Lighter `ANTHROPIC`：`market_id=193`、`status=active`；WebSocket 样本 bid/ask 为 `2169.9 / 2170.1`，原始 `last_updated_at=1790075560263588`，解析为 `2026-09-22T11:12:40.263588Z`。后续 Pair 实测为 `2168.6 / 2169.0`。来源是 Lighter `orderBookDetails + WebSocket order_book`。
- Hyperliquid `io:ANTH`：`szDecimals=3`、`maxLeverage=6`、`onlyIsolated=true`；`l2Book` 样本 bid/ask 为 `2155.3 / 2155.4`，后续 Pair 实测为 `2157.1 / 2157.2`。
- Binance `ANTHROPICUSDT`：`contractType=TRADIFI_PERPETUAL`、`status=TRADING`、`baseAsset=ANTHROPIC`。
- OKX `ANTHROPIC-USDT-SWAP`：`ctVal=1`、`ctMult=1`；原始价格约为统一口径的十分之一，因此应用 `price_multiplier=10`。
- Gate ANTHROPIC 合约元数据：`quanto_multiplier=0.01`。
- Bitget `ANTHROPICUSDT` 合约元数据：`sizeMultiplier=0.01`。

上述样本只记录公开市场名、市场元数据、价格和数据时间，没有记录秘密或账户数据。

## 本地验证

### 自动化测试与静态检查

- 后端专项：`170 passed`。
- 新增适配器、Pair 和机会精选专项：`105 passed`。
- 后端全量：`781 passed, 11 warnings`，耗时约 `1495.53s`。
- 前端专项：`88 passed`。
- 前端全量：`180 passed, 1 failed`。唯一失败是既有 Settings 测试仍查找旧文案“实盘灰度”，属于禁止触碰的设置/交易模块，本任务未修改。
- `python -m compileall -q app`：通过。
- 本任务 24 个 Python 文件定向 Ruff：`All checks passed!`。
- `npm run build`：通过。
- `git diff --check`：通过。

误运行一次全仓 Ruff 得到 227 个既有错误，没有批量修改无关模块。

### 真实接口与浏览器验收

- 三种查询别名都返回 `symbol=ANTHROPICUSDT`，并列出 6 个精确市场：Binance、Bitget、Gate、Hyperliquid `io:ANTH`、Lighter、OKX。
- Astro 路由证据返回 12 条腿，其中 RH-Lighter 腿均为 `route_only`。
- Lighter -> Binance：Lighter ask `2169.0`，Binance bid `2107.4`，开仓价差 `-2.8809%`。
- Binance -> Lighter：Binance ask `2107.97`，Lighter bid `2168.5`，开仓价差 `+2.8308%`。
- Lighter -> HL：保留 `raw_symbol=io:ANTH` 和 `dex=io`，当次使用真实 `l2Book`，未估算。
- OKX -> Lighter：OKX 腿 `price_multiplier=10`，统一后 bid/ask 为 `2169.6 / 2169.7`。
- RH-Lighter Pair 请求返回 HTTP 422，并明确说明仅为 Astro 路由、没有独立公开行情源。
- 使用 6 个真实市场快照直接运行机会生成器，得到 15 个 FF 机会；没有 RH-Lighter 伪机会。
- 标的查询桌面 1280px 与手机 390px 均无页面或市场行横向溢出。
- Pair 桌面和手机 390px 均无页面级横向溢出，双腿身份、来源和错误信息无文字重叠。
- HL 或 Lighter 上游超时时页面显示具体上游失败，不统一误报为“没有行情”。
- 本地截图位于未跟踪目录 `output/lighter-rh-lighter-local/`，不提交仓库。

## 生产配置与部署

部署前只读检查发现生产风险设置的 `excluded_symbols` 包含 `ANTHROPICUSDT`。该项会在采集和机会 API 两层过滤 ANTHROPIC，因此与本任务“ANTHROPIC 应生成实时机会”的验收冲突。

完成数据库在线备份后，已通过受密码保护的 Settings API 只从 `excluded_symbols` 移除 `ANTHROPICUSDT`：排除项数量从 14 变为 13，脚本逐字段断言其他风险配置完全不变。没有修改 `stale_after_seconds`、成交额阈值、价差阈值、手续费、滑点、告警阈值或交易设置。

- 在线备份：`backups/radar-20260922T133053Z-pre-lighter-rh-lighter.db`。
- 备份大小：`600522752` 字节。
- SHA-256：`22b8b1425e9ae4c100a90e2742abaeca4e7a05aad3bae724db52a28a669ce0d2`。
- 源库、容器内副本和主机副本的 `PRAGMA quick_check` 均为 `ok`，容器与主机副本大小及 SHA-256 一致。
- 主机备份校验后删除了卷内临时副本 `/data/codex-radar-20260922T133053Z-pre-lighter-rh-lighter.db`；线上 `/data/radar.db` 未删除、替换或回滚。
- 部署后再次确认 `/data/radar.db` 存在，大小 `598024192` 字节，`PRAGMA quick_check=ok`。
- 服务器通过 `git pull --ff-only origin codex/frontend-localization-polish` 快进到 `cf12f7d`。
- 执行了 `docker compose build --pull`；首次并行构建失去子进程后，使用 `COMPOSE_PARALLEL_LIMIT=1` 顺序重跑同一构建命令并成功生成前后端镜像。
- 执行 `docker compose up -d --remove-orphans`，没有执行 `docker compose down -v`；前后端容器均为 `healthy`。

### 线上接口验收

- `/api/health` 返回 `status=ok`，8 个采集器全部为 `healthy`，验收时有 10965 个市场快照和 8734 个实时机会。
- `/api/instruments/ANTH`、`ANTHROPIC`、`ANTHROPICUSDT` 均返回 `symbol=ANTHROPICUSDT` 和 8 个实时精确市场：Aster、Binance、Bitget、Bybit、Gate、Hyperliquid `io:ANTH`、Lighter、OKX。
- 三种别名均显示 3 条 `rh-lighter` route-only 证据；RH-Lighter 没有出现在真实市场列表。
- 精确规格包括：Binance 数量乘数 `1`、Bitget `0.01`、Gate `0.01`、Lighter `1`、HL io `1`、OKX 价格倍率 `10` 且数量乘数 `1`。
- ANTHROPIC 机会接口返回 26 条机会；其中 7 条包含 Lighter、7 条包含 HL `io`，RH-Lighter 为 0 条。
- Lighter -> Binance 当前 bid/ask 为 `2171.4 / 2174.3` 与 `2105.09 / 2105.77`，按 Lighter ask、Binance bid 得到开仓价差 `-3.234573%`。
- Binance -> Lighter 使用相反方向的真实盘口，开仓价差 `+3.068852%`。
- Lighter -> HL `io:ANTH` 返回真实 `l2Book`：HL bid/ask `2157.0 / 2157.1`，`dex=io`、`raw_symbol=io:ANTH`，没有估算 bid/ask。
- OKX -> Lighter 保留 `raw_symbol=ANTHROPIC-USDT-SWAP` 和 `price_multiplier=10`，统一口径 bid/ask `2162.5 / 2163.0`。
- RH-Lighter Pair 请求返回 HTTP 422，错误明确为 Astro route unsupported，不误报为“没有行情”。
- 生产前端首页及标的、Pair 页面返回 HTTP 200。

### 线上浏览器验收

- 标的页 1280px：页面宽度 `1280/1280`，精确市场区 `1030/1030`，8 行均无内部溢出。
- 标的页 390px：页面宽度 `390/390`，精确市场区 `372/372`，8 行均无内部溢出。
- 两个视口都可见 Lighter、`io:ANTH` 和“仅 Astro 路由”。
- Pair 390px：页面宽度 `390/390`，行情质量卡 `372/372`，可见 Lighter、`io:ANTH` 和“双腿市场身份与行情质量”，没有错误提示。
- Astro 独立浮窗在 900px 和 390px 均无页面或卡片行横向溢出；输入生产面板密码后实际读取到 9 张运行卡，没有鉴权错误。
- 三张目标 RH-Lighter 卡均真实存在，但生产 Astro 返回 `status=false`：`lighter/rh-lighter`、`hl(io:ANTH)/rh-lighter`、`binance/rh-lighter`。浮窗只展示运行卡，因此没有在生产中擅自启用卡片来制造点击样本；暂停路由的精确身份、禁用行为和跳转参数由前端自动化测试覆盖。
- 生产截图保存在未跟踪的 `output/lighter-rh-lighter-production-*.png`，不提交仓库。

## 已知问题与残余风险

- RH-Lighter 没有独立公开实时行情，只能作为 Astro 路由证据；除非未来找到可验证的独立官方市场接口，否则不能把它升级为实时市场。
- Astro 当前列表没有独立原始腿字段；现有卡片身份依赖 `name + type + buyEx/sellEx + HL DEX`。若 Astro SDK 后续增加原始腿字段，应优先改用新字段。
- 三张 RH-Lighter 目标卡在验收时均为暂停状态，生产浮窗无法在不改变业务状态的情况下完成真实点击；本任务没有擅自启用、暂停或重建卡片。
- Lighter WebSocket、订单簿详情和 HL `l2Book` 可能偶发超时。代码会隔离单市场失败并禁止估算盘口进入机会，但短时可能看不到 ANTHROPIC 路线。
- 资金费率下一结算时间在部分交易所是按已知周期推算，页面通过 `estimated_fields` 明确标记。
- Pair 手续费当前使用按现货/永续区分的估算 taker 费率，不代表具体账户 VIP 费率。
- 前端全量测试仍有一个与本任务无关的 Settings 旧文案失败。
- 工作区有大量既有 `output/**`、`backend/.pytest_task_account_connections_*` 和 `script/dexe_bybit_bitget_chain.py` 未跟踪产物，本任务不提交、不删除、不覆盖。

## 下一步建议

- 等待真实 ANTHROPIC 市场变化，继续观察 Lighter WebSocket 和 HL `io:ANTH` 的上游稳定性与时间戳。
- 若发现 RH-Lighter 独立官方接口，应新建独立任务重新验证市场身份、合约单位和数据来源，不在现有 Lighter 快照上复制一条路线。
- 下一个无直接依赖的功能模块应在新的 Codex 任务中继续，并以本文档和最新分支为交接基线。
