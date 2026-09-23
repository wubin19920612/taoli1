# 交接：Robinhood Lighter 公开市场接入

日期：2026-09-23（Asia/Shanghai）

## 目标与范围

本任务把已验证的 Robinhood Lighter 独立实例接入 Radar 的公开只读行情、标的
查询、精确 Pair、实时机会和 Astro 浮窗。没有新增 RH 账户连接、交易执行、下单、
建卡或告警阈值；三张目标 Astro 卡没有启用或修改。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`7624625ddc3df939f7f720757e5c682949bacfcf`
- 上一功能提交：`cf12f7d4b3e7084f76aac955afb8ec22f475160f`
- 来源调查提交：`7624625ddc3df939f7f720757e5c682949bacfcf`
- 本次提交：部署后补充。

## 核心结论

- `RH` 是 Robinhood。
- 官方端点为 `https://api.rh.lighter.xyz/api/v1` 和
  `wss://api.rh.lighter.xyz/stream`。
- Robinhood Lighter L2 签名域 chain ID 为 `466324`，普通 Lighter 为 `304`；
  Robinhood Chain L1/桥接 chain ID 为 `4663`。
- RH ANTHROPIC 使用 `market_id=38`，普通 Lighter 使用 `market_id=193`。
- RH 以 USDG 为抵押/现货报价资产；普通 Lighter 使用 USDC。
- Astro `rh-lighter` 路由可精确匹配 RH 官方公共行情。Astro 卡片只提供路由身份，
  不作为价格源。

完整证据、原始接口、样本和调用链见
`docs/task-handoff-2026-09-22-rh-lighter-data-provenance.md`。

## 实现

- `backend/app/exchanges/lighter.py`
  - 参数化 Lighter REST、WS、spot quote 和 data source。
  - 新增 `RobinhoodLighterAdapter`，exchange ID 为 `rh-lighter`。
  - 两实例各自请求 `orderBookDetails`、`funding-rates` 和 `order_book/{market_id}`。
- `backend/app/services/collector.py`
  - 默认注册普通 Lighter 和 Robinhood Lighter 两个独立采集器。
- `backend/app/services/pair_spread_query.py`
  - 市场详情缓存按实例隔离。
  - RH K 线、当前盘口和资金费率请求只使用 RH 主机与 WS。
- `backend/app/models/pair_spread.py`
  - `rh-lighter` 从 route-only 拒绝项升级为受支持 Pair 交易所。
- `backend/app/models/instrument.py`、`backend/app/api/routes_instruments.py`
  - 标的查询支持 RH，Astro route 映射为 `rh-lighter` 并匹配真实快照。
- `backend/app/api/routes_astro.py`
  - RH 预览保留 Pair 和报价，但返回 `can_submit=false` 与明确 blocker；任何包含
    `rh-lighter` 的 Astro 建卡请求返回 HTTP 422。
- `backend/app/services/astro_alerts.py`
  - 在服务层统一拒绝 RH 的自动告警、新币、实盘实验、人工和预建卡路径，防止
    后台任务绕过 API 只读边界。
- 前端 Instrument、Pair、Opportunity 和浮窗
  - 增加 `RH Lighter` 标签、Pair 选项和视觉区分。
  - 浮窗用 RH 自身 bid/ask；删除旧 route-only 特判。

## 重要业务规则

- exchange ID、market ID、REST/WS 和缓存都必须区分 `lighter` 与 `rh-lighter`。
- RH `/funding-rates` 的响应仍可能写 `exchange=lighter`；实例身份以请求主机为准。
- 统一标的继续使用 `ANTHROPICUSDT` 便于跨市场比较，但原始市场和来源保留 RH
  的 USDG 身份，不代表稳定币风险或结算完全等价。
- 只使用真实 bid/ask；不允许用 mark、last 或普通 Lighter 价格补 RH 盘口。
- 资金费率周期、成交额、盘口时间、合约倍率、手续费和估算字段仍需独立展示。
- Astro HMAC `action=list` 只用于卡片/路由发现，不作为 RH 报价接口。

## 测试

- 初始 RH 后端专项：`62 passed, 2 warnings`。
- 最终 Astro 只读边界专项：`5 passed`；Astro 服务完整测试：`44 passed`；RH
  预览/建卡定向测试：`1 passed, 2 warnings`。
- 前端专项：`90 passed`；仅有既有 Ant Design deprecated/React act 警告。
- 后端全量：`784 passed, 13 warnings`，耗时 `975.13s`。该运行在最终服务层硬
  限制之前启动；最终改动由上述受影响范围的 `44 + 1` 项测试覆盖。
- 前端全量：`182 passed, 1 failed`。唯一失败是既有 `SettingsPage.test.tsx`
  仍查找旧文案“实盘灰度”，与本任务无关。
- `npm run build`、`python -m compileall -q app`、定向 Ruff 和 `git diff --check`
  均通过。

专项覆盖：

- RH REST/WS、USDG spot symbol、ANTHROPIC `market_id=38` 和数据来源。
- 普通/RH `193`/`38` 的市场缓存和 WebSocket URL 隔离。
- Collector 同时注册两个实例。
- Pair validator、current、candles 和 funding 的 RH dispatch。
- Instrument 将 Astro `rh-lighter` 匹配为真实市场。
- 浮窗不借用普通 Lighter 价格。
- 前端 RH 标签、选择和精确 Pair 导航。
- RH 可以只读预览且 `can_submit=false`；API 建卡返回 422，所有服务层建卡路径均
  返回 `unsupported`，不会调用 Astro `list/add/update`。

## 部署与线上状态

- Git 提交/推送：交付后补充。
- 数据库备份、大小、SHA-256、`PRAGMA quick_check`：部署前补充。
- 服务器版本、容器健康和 `/api/health`：部署后补充。
- RH Instrument、Pair、Opportunity、浮窗验证：部署后补充。

## 已知问题

- 最后一次本地四源复核时，本地网络到 RH/Binance 短时连接超时；此前成功样本和
  测试已确认接口契约，部署后需要从服务器复核持续连接。
- Astro 服务端内部是否直连官方 RH 主机没有公开证据；Radar 不依赖该内部链路。
- 三张 RH Astro 卡当前暂停，本任务不改变业务状态，因此线上浮窗真实卡片行只能
  在卡片自然恢复运行后验证；接口、市场匹配和暂停状态可只读验证。
- 工作区有既有 `output/**`、pytest 临时目录和
  `script/dexe_bybit_bitget_chain.py` 未跟踪产物，全部保留且不提交。

## 下一步

- 持续同秒采样 RH、普通 Lighter、Binance 和 HL `io:ANTH`，量化更新时间间隔、
  相关性和滞后；不要根据单点价格接近程度推断镜像或合成关系。
- 账户连接、交易执行和 RH 建卡能力必须在独立任务中设计和验证。
