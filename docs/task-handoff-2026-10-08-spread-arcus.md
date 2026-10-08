# Arcus 价差接入交接（2026-10-08）

## 目标与范围

- 新增独立交易所 `arcus`，不复用 Lighter/RH Lighter 的市场、缓存、盘口或资金费率。
- 范围：全市场采集、价差机会列表、标的查询及分钟统计、配对价差、多交易所价差图、浮窗标签、公开交易状态/深度与告警交易所筛选。
- 只接入 Arcus 永续。官方 Stock Tokens 现货使用独立 Spot Router/RFQ，不将永续盘口冒充现货；不接入账户、下单或链上交易。
- 不扩展负基差、独立机会雷达或 Astro 下单路线。Astro 尚无经确认的 Arcus exchange ID，保持既有不支持规则，不映射成 RH Lighter。

## 分支与基线

- 分支：`codex/frontend-localization-polish`；本地开始基线：`d794d57324e677c4fcb8e0d792b48f336c1b3bd3`。
- 开始时线上源码/镜像：`694deab808bb0057617faff150efdd75c8e8c84a`，前后端健康，受跟踪服务器文件干净。
- 已有非本任务改动：`frontend/src/pages/PairMonitorPage.tsx` 的图表控制/已保存区布局，以及 `frontend/tests/PairMonitorPage.test.tsx` 的对应两项布局测试。保留原样，这两份混合文件仅暂存 Arcus 独立差异块。

## 官方接口与业务规则

- 生产 REST：`https://api.arcus.xyz/v1`；WebSocket：`wss://api.arcus.xyz/v1/ws`。依据官方 Markdown 文档和真实只读调用核验。
- `GET /markets`：仅采集 `type=PERPETUAL`、`status=ONLINE`、`quoteAsset=USD` 且原始名与 `baseAsset-USD` 完全一致的市场。OFFLINE、现货、错误报价币或异常原始名不进入扫描。
- 单条 WebSocket 连接订阅各市场 `bbo`，读取 `subscribed.contents` 初始快照；缺失订阅报错，不静默当成完整成功。市场元数据缓存 60 秒，盘口采集缓存 12 秒。
- 当前价差用 `GET /bbo/{market}`，深度用 `GET /l2OrderBook/{market}?nLevels=...`。bid/ask 必须有限、正数、不交叉且有挂单；不拿 mark/oracle/last price 代替可成交盘口。
- 保留原始市场，如 `BTC-USD`、`NVDA-USD`。内部 `BTCUSDT` 只是既有规范索引，不宣称真实合约以 USDT 报价；USD 名义价格/成交额不进行 USD/USDT 汇率换算，来源保留 `(USD)`。
- 资金费率为每小时小数，乘 100 转百分比；分别保存 `fundingRate` 与下一期预测 `nextFundingRate`。`nextFundingAt` 为 epoch 秒；BBO、K 线及历史资金费率时间为 epoch 微秒。
- `GET /candles`：按 `[from,to)` 微秒窗口分页，每页最多 1500 根；按时间去重排序，不假设官方示例的返回顺序。成交额用 `notionalVolume`，不把 base volume 当美元。
- `GET /fundingRates`：每页最多 1000 条，`to` inclusive；下一页取最早时间减 1 微秒，避免重复及无限循环。
- 24h 成交额为 `volume24hNotional`；合约基数倍率为 1；OI base 数量乘 mark 得名义金额。保留显式跨市场比较倍率，不新增隐式 ticker 别名。
- 价差扣费继续使用既有可配置费用假设；配对默认永续 taker 0.05% 保留 `fee_is_estimated=true`，不是账户真实费率。公开状态另读取 `/feetiers` Base 档，本次实测 maker 0%、taker 0.0225%，不硬编码到交易判断，不代表账户费率。
- ONLINE/公开深度不等于账户可成交：地区、余额、保证金、场外时段价格限制、真实订单错误和 USD/USDT 差异仍须核实。不发送探测订单。
- Hyperliquid 具体 DEX、原始市场与自定义标的路径保持不变；不按相似 ticker/价格替代 Arcus、RH、Lighter 或 Hyperliquid 市场。

## 关键代码入口

- `backend/app/exchanges/arcus.py`：市场解析、WebSocket BBO、全市场采集、L2 深度；`backend/app/services/collector.py` 注册独立 ArcusAdapter。
- `backend/app/services/pair_spread_query.py`：当前价格、分页 K 线、历史资金费率、独立元数据缓存；标的统计沿原始市场查询。
- `backend/app/models/pair_spread.py`、`backend/app/models/instrument.py`：支持列表；允许显式 `BTC-USD`，拒绝 Arcus 现货与非 Hyperliquid DEX 参数。
- `backend/app/services/trade_availability.py`：ONLINE/OFFLINE、公开 Base 费率、深度与 oracle；缺失手续费档位不覆盖有效市场状态。
- `backend/app/models/negative_basis.py`：防止配对支持列表新增成员意外开启未验收的负基差模块。
- 前端：`PairMonitorPage.tsx`、`InstrumentLookupPage.tsx`、`SymbolSpreadPage.tsx`、`SettingsPage.tsx`、`TopFilters.tsx`、`OpportunityTable.tsx`、`FloatingWatchPanel.tsx` 及 Arcus 标签样式。
- `backend/tests/test_arcus_adapter.py`：独立接入、数据单位、盘口真实性、分页、公开状态、手续费缺失及标的接口回归。

## 验证

- 后端相关回归 188 项通过：Arcus/Lighter、配对查询、采集、标的查询/统计、交易状态、价差引擎与 Astro 原始路线。
- 负基差专项 14 项通过；最终 Arcus 专项复测 20 项通过（含新增手续费缺失场景）。
- 前端 6 个文件 156 项通过，包含保留的用户布局测试；TypeScript + Vite 生产构建通过。
- Ruff `--select F,E9`、`git diff --check` 通过。
- 部署前真实只读探测：在独立进程装载待交付源码，不改运行服务/数据库；60 个 Arcus 永续获得可用 BBO，BTC/ETH/NVDA/HOOD 原始市场、小时周期、成交额及上游时间正常。
- Arcus BTC vs Binance BTC 的 4 小时/1 分钟真实查询：241 个对齐点、4 条历史资金费率，双边盘口/成交额完整，周期分别 1h/8h，无 warnings。
- 本机网页搜索未返回可用结果，技术依据直接取自官方文档/API/WS；资料摘录保留在本任务 output 产物中。官方文档：
  - https://docs.arcus.xyz/llms.txt
  - https://docs.arcus.xyz/api-reference/public/get-markets.md
  - https://docs.arcus.xyz/api-reference/public/get-best-bid-offer-bbo.md
  - https://docs.arcus.xyz/api-reference/public/get-ohlcv-candles.md
  - https://docs.arcus.xyz/api-reference/public/get-market-funding-rates.md
  - https://docs.arcus.xyz/api-reference/websocket.md
  - https://docs.arcus.xyz/concepts/perpetuals/funding.md
  - https://docs.arcus.xyz/concepts/perpetuals/fees.md

## Git 与线上交付

- 按 `docs/git-delivery-checklist.md` 与 `docs/linux-deployment.md`：仅本任务文件/差异块提交推送，等待两份 SHA 固定镜像，先备份 SQLite/校验，再 `git pull --ff-only` 和 Compose `up -d --no-build --wait`。
- 2 GiB 生产机不本地构建，不删除 volume，不修改现有 .env，不 force push。
- 核验前后端镜像、健康接口的独立 Arcus 状态、标的/配对/历史资金费率接口、桌面与手机页面。
- 提交号、Actions、备份文件/校验、部署版本与最终页面验收在完成后追加；未追加前不视为线上完成。

## 未跟踪文件与下一步

- 保留原有 `.worktrees/`、旧 pytest 目录、未提交交接草稿和 `output/` 研究/截图，不清理未知归属文件。
- 本任务官方文档摘录、只读探测、测试 JSON/日志及验收截图存于 `output/arcus-*20261008*`，不提交生成产物。
- 现货 RFQ、账户连接/下单、专属 Astro 路线、费用个性化及独立策略模块不在本次范围；下一模块请新建任务并引用本交接。
