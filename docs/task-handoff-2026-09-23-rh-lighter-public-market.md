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
- 功能提交：`debdf2ac7eb173fe32a249af5bc8f8e42316e7b4`。

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

## 部署中断记录（已恢复）

- 功能提交 `debdf2a` 已推送到
  `origin/codex/frontend-localization-polish`。
- 部署前在线备份：
  `/home/ubuntu/wubin/taoli1/backups/radar-20260923T021223Z-pre-rh-lighter.db`。
  大小 `607465472` 字节，SHA-256
  `d1950c0207795abb13f5295cad27a70a26cb6c8aba59dd08e75df90496ee2db4`；
  源库、容器内备份和主机备份的 `PRAGMA quick_check` 均为 `ok`。校验后仅删除
  `/data/codex-radar-20260923T021223Z-pre-rh-lighter.db` 临时副本，未删除或替换
  `/data/radar.db`。
- 服务器已用 `git pull --ff-only` 从 `39cb41e` 快进到 `debdf2a`。
- `COMPOSE_PARALLEL_LIMIT=1 sudo docker compose build --pull` 实际仍并行启动前后端
  BuildKit；构建在前端 TypeScript/Vite 和后端依赖安装期间造成宿主机持续资源饱和。
  长时间无进展并影响线上响应后，SSH 客户端中止了构建会话。尚未执行
  `docker compose up`，没有执行 `docker compose down` 或任何卷删除。
- 中止后主机仍可 ICMP 响应、TCP/22 可连接，但当时 SSH 无法及时返回 banner，前端
  `:3000`、后端 `:8000/api/health` 均超时。以下是当时的中断状态；后续已恢复。

当时记录的恢复顺序（下文已执行）：

1. 检查并精确终止残留的 BuildKit、`npm run build`、`tsc`、`vite` 或 `pip install`
   进程；先确认旧容器和 `/data/radar.db` 的 `PRAGMA quick_check`。
2. 不并行构建两个服务，分别执行 `sudo docker compose build --pull backend` 和
   `sudo docker compose build --pull frontend`；必要时先增加临时 swap，但不要删除卷。
3. 执行 `sudo docker compose up -d --remove-orphans`，检查两个容器和
   `/api/health`。
4. 完成 RH Instrument、Pair、Opportunity、浮窗、三张暂停卡和建卡 422 的只读
   线上验收，再把最终版本与结果写回本文。

## 恢复部署与线上验收（2026-09-23）

- SSH 已恢复，旧容器在重建前均为 `healthy`。服务器当时 uptime 约 10 天，
  **本次没有执行宿主机重启**；没有残留构建进程。远端代码为功能提交 `debdf2a`，
  工作树保留已有的 `.env` 备份和 `CACHED` 未跟踪项，未清理或提交。
- 重建前后均对容器内现用 `/data/radar.db` 以 SQLite 只读连接执行
  `PRAGMA quick_check`，结果均为 `ok`。部署前备份仍在，SHA-256 与上文一致。
  没有替换或删除数据库，也没有删除 Compose 数据卷。
- 服务器原为约 1.9 GiB 内存、无 swap。为降低构建时的 OOM 风险，临时启用
  2 GiB swap，依次成功执行 `sudo docker compose build --pull backend`、
  `sudo docker compose build --pull frontend`（前端 `tsc -b && vite build` 通过），
  然后执行 `sudo docker compose up -d --remove-orphans`；确认资源余量后已卸载、
  删除本次临时 swap。未执行 `docker compose down` 或清卷操作。
- 正在运行的代码版本：`debdf2ac7eb173fe32a249af5bc8f8e42316e7b4`。
  构建镜像 ID：backend `sha256:b206e657b4d954390ab9ed47a27a050d9d4db40acdca027a96a88871f73afa93`；
  frontend `sha256:b60ea59b518f8512dd3d073be75dbd68f763b4205e3e3ff5b8e3048faa96d844`。
  前后端容器均 `running/healthy`，本机 `/api/health`、前端 `/` 和前端代理
  `/api/health` 均返回 200；清理 swap 后再次确认健康。
- 线上 RH/普通 Lighter `ANTHROPIC` 都有不同的真实 bid/ask、独立来源和更新时间。
  RH 来源明确为 `Robinhood Lighter public orderBookDetails + WebSocket order_book
  (USDG)`；样本 RH bid/ask `2191.0/2191.2`、普通 Lighter `2202.2/2202.5`，
  快照约 22 秒，`is_estimated=false`，均为 1 小时资金费率周期。
  Instrument `ANTHROPICUSDT` 中 RH 市场为 `live`，RH 四条路由均匹配原始
  `ANTHROPIC` 且为 `live_market`；Pair 两腿来源独立，返回 60 个历史点、双方
  可成交价/24 小时成交额/费率周期，手续费字段标注为预估；RH 实时机会返回 8 条。
  这些价格与数量仅为验收当时样本，不代表可执行利润或稳定币等价。
- 通过 SSH 本地只读隧道使用无登录态浏览器打开生产页面：Instrument、精确 Pair
  与实时机会页面分别出现 RH Lighter 标签且无页面 JS 异常；浮窗能打开并正常显示
  模式导航。该无登录态浏览器的 Astro 计数为 0，**不能**据此判断真实 Astro
  卡片数或证明卡片行已显示。
- Astro RH 预览返回 `can_submit=false` 和只读 blocker；在确认此结果后，用容器
  已配置凭据请求 RH 建卡端点，返回 HTTP 422 和只读提示，且请求前后目标三卡的
  `status`、`disableOpen` 完全相同，没有建卡或修改卡片。
- 需要重点确认的外部状态差异：此前验收记录为三张目标卡均 `status=false`；
  本次只读 `action=list` 显示 `lighter → rh-lighter` 为 `false`、
  `hl(io:ANTH) → rh-lighter` 为 **`true`**、`binance → rh-lighter` 为 `false`。
  另外发现反方向 `rh-lighter → lighter` 卡 `status=true`。本任务没有改动这些
  状态；请由卡片负责人确认开启是否预期，不应自动暂停或启用。

## 已知问题

- 本地公网访问生产 `:8000` 仍超时；服务器本机与 SSH 隧道内的 API 和页面正常，
  已从服务器核对 RH 最新样本。长期连接稳定性仍需后续监控。
- Astro 服务端内部是否直连官方 RH 主机没有公开证据；Radar 不依赖该内部链路。
- 三张目标卡中有一张本次查询为 `status=true`，与此前全部暂停的记录不符，
  另外存在一张运行中的反向卡；需要卡片负责人核实。无登录态浮窗不能验证真实
  Astro 卡片行与点击，因此这项 UI 验收仍有边界；没有因此修改卡片状态。
- 机器只有约 1.9 GiB 内存，后续部署应继续单独构建 backend/frontend，必要时
  临时加 swap；不要并行构建。
- 工作区有既有 `output/**`、pytest 临时目录和
  `script/dexe_bybit_bitget_chain.py` 未跟踪产物，全部保留且不提交。

## 下一步

- 持续同秒采样 RH、普通 Lighter、Binance 和 HL `io:ANTH`，量化更新时间间隔、
  相关性和滞后；不要根据单点价格接近程度推断镜像或合成关系。
- 账户连接、交易执行和 RH 建卡能力必须在独立任务中设计和验证。
