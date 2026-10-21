# Aster GC 建卡与多交易所实时溢价告警（2026-10-21）

## 目标与基线

- 分支 `codex/frontend-localization-polish`，本地基线 `418829f`，生产应用基线 `06c50b3b1bf724eef2ff1afadcef87994cd4332d`。
- 用户要求 Aster 建卡也支持 `gc-aster`；同标的多个交易所实时指数超过阈值时告警，初始阈值 2，网页可修改。
- 已确认口径：至少两个不同交易所的实时溢价指数绝对值严格超过 2%，正负均计入。用户询问来源建议后，本任务采用官方 Premium Index 优先，与实时溢价页面的来源策略一致；明确说明全市场逐市场轮询延迟。

## 已实现与代码入口

- `backend/app/services/market_labels.py`：Aster 增加独立 `gc-aster` 映射，从非 GC 交易所列表移出。告警、实盘实验、人工建卡、预建及预览使用同一选路函数，遵守已有 `gc/non_gc/both` 设置。
- `backend/app/services/astro_alerts.py`：旧 `gc-gate -> aster` 等混合路线仅作为同族卡片识别，允许补建 `gc-gate -> gc-aster`，不修改旧卡。HL 同名卡片 DEX 冲突检查仍保留。
- `backend/app/services/multi_exchange_premium.py`：新增独立监控；`main.py` 注册后台循环。无自动建卡、开仓或其他交易动作。
- `backend/app/services/premium_index_query.py::monitor_point`：复用已有行情查询源，减少重复请求盘口及费率；保留 Premium Index 的原始分钟时间，不把拉取时间当成数据时间。
- `backend/app/models/settings.py::RiskSettings`：`multi_exchange_premium_alert_enabled=true`，`multi_exchange_premium_threshold_pct=2`，`multi_exchange_premium_cooldown_seconds=900`。存入已有 JSON 设置，无数据库 schema 迁移。
- 网页“设置 → 风险参数 → 多交易所实时溢价指数告警”可保存开关、阈值和冷却秒数。使用已有面板密码鉴权和 `/api/settings/risk`。
- `/api/settings/premium-index-alert-status`：监控运行条件、符合身份要求的标的数、批次时间、完整扫描耗时、查询失败数、官方及回退样本数。网页设置加载时显示扫描耗时与失败数。
- 告警经现有飞书文本通知发送，保存到 `alert_events`，`rule_id=multi-exchange-premium`。不伪造套利机会，不受 Astro 卡片创建结果影响。

## 关键规则与边界

- 只监控 Binance、OKX、Bybit、Gate、Bitget、Aster、Hyperliquid 合约，范围与现有溢价页面一致。
- 按经过核验且未过期的资产 ID、资产类型和合约口径聚合；同名代码不会直接合并。忽略黑名单标的和忽略交易所。
- 保留原始市场、DEX、单位核验与绑定版本。查询后重新读取配置、黑名单、身份；过期/冲突或中途改变的绑定不参与判断。
- 同一交易所多个市场/DEX 只算一个交易所，通知保留各市场具体值、来源、时间。两个方向都可计入，不要求同正或同负。
- Binance/Aster/Bybit/Gate/OKX 使用官方分钟 P 优先；官方值缺失或过期时可使用当前标记价/指数价偏离，明确标为回退估算。Bitget/Hyperliquid 当前页面本来就使用标记价/指数价，监控保持该口径。请求异常跳过，不拿失败请求伪造新鲜数据。
- 官方样本最长 90 秒；回退样本遵守“数据过期秒数”（默认 30 秒）；命中样本时间窗口最长相差 60 秒。非有限值、未来时间、等于阈值均不命中。
- 每批最多 16 个资产，每交易所最多启动两个市场查询/秒，全局最多 12 个查询并发，单查询超时 15 秒。逐标的查完就判断，不等待不相关慢市场。超时或 429 暂缓该所 60 秒。
- 完整扫描存在延迟，不保证全市场秒级触发。状态接口和网页显示实测扫描耗时。冷却默认 15 分钟；通知失败 60 秒后可重试，冷却状态为进程内状态，重启后重新计算。
- 只有现有飞书真实发送开关和 webhook 都可用时才运行通知扫描。新增告警开关默认开启。

## 验证

- 初始 Aster 回归：market-labels / planner / alerts / instrument-routes / preadd 共 374 项通过。
- 补充旧混合卡兼容测试后，Aster 与 HL 定向回归 179 项通过（67 项未选），结果记录于 `output/aster-premium-legacy-tests-20261021.log`。
- 新监控、配置鉴权与持久化、官方优先及过期回退、现有 PremiumIndex 查询：38 项通过；`output/aster-premium-backend-final-20261021.log`。
- SettingsPage 16 项通过，包含默认 2%、修改保存；`output/aster-premium-settings-green-20261021.log`。
- TypeScript 与 Vite 生产构建通过；`output/aster-premium-build-final-20261021.log`。
- Playwright 实际本地页面验证 1440/390 像素：将阈值改成 3.5、保存、刷新仍为 3.5、恢复 2；检查开关、900 秒、GC 帮助文字；无页面异常、无横向溢出。截图 `output/aster-premium-local-{1440,390}-20261021.png`。
- 新增服务/测试及改动的 Astro、设置模型/路由 lint 通过，`git diff --check` 通过。`main.py` 和 `premium_index_query.py` 有既有 lint 问题，本任务未批量整理无关代码。

## 线上状态

- 功能提交 `4bdff00b4a93d642818810fb2a42943e73503882` 已推送至原分支。GitHub Actions `39526378460` 两个镜像任务均成功，服务器两镜像 manifest 校验通过。
- 部署前主库备份：`backups/radar-before-4bdff00b4a93-20261021T011844Z.db`，`integrity_check=ok`，主机/容器 SHA-256 一致：`de2439467793ebef5810a2b85852f390044d874db4ecda8914d576161bdc8a69`。
- 独立研究库备份：`backups/squeeze-route-before-4bdff00b4a93-20261021T011844Z.db`，`integrity_check=ok`，主机/容器 SHA-256 一致：`e1da1c47b11fb501042d41fb34829e0d78415ce1d910a5eac08a36cd4ea4e193`。
- 2026-10-21 09:20（北京时间）部署成功。服务器 `git pull --ff-only` 到功能提交，Compose 使用两份该 SHA 镜像 `up -d --no-build --wait`，双容器 healthy；未在生产机现场构建、删除数据卷或修改环境配置。原备份保留策略自动清理过期部署备份，当前两份备份保留。完整日志 `output/aster-premium-deployment-20261021.log`。
- 初次线上检查 `/api/health`：status=ok，11365 市场、3943 机会、exchange_errors={}。容器检查时后端约 455.6 MiB / 768 MiB，前端 3.719 MiB / 96 MiB。
- 实际 `POST /api/astro/instrument/preview` 对 BTC Gate→Aster、Aster→Gate 均 `can_submit=true`，GC 路线分别为 `gc-gate→gc-aster`、`gc-aster→gc-gate`。默认 `card_variant=gc`、`open_enabled=false` 保持原值。
- 线上 `/api/settings/risk` 确认新告警启用、阈值 2%、冷却 900 秒；状态接口 `running=true`，246 个符合身份要求且至少有两个交易所的标的，已取得官方和回退样本。稳定标的集合的首轮完整扫描实测 **177.8 秒**，随后开始下一轮；启动时仅少量市场就绪的 5.4 秒不作为全量扫描耗时。
- 线上 Playwright 1440/390 页面均确认开关、阈值、冷却和 GC 帮助文字，无 pageerror、无横向溢出，截图 `output/aster-premium-production-{1440,390}-20261021.png`。线上仅只读验证，持久化写入已在本地真实 API 与页面验证。
- 09:23 最终读取到自然告警 `746f25c7ca2b45d0aaf7596aad0c78c4`：MEWUSDT，Bitget -2.3035%、Gate -2.3077%，原始市场 MEWUSDT / MEW_USDT；样本时间分别 09:20:49 / 09:20:50，告警时间 09:20:54（北京时间）。两侧都是标记价/指数价回退值，消息明确标注“回退估算”。数据库 status=sent，表示飞书接口响应已通过检查，不代表用户已阅读。
- 最终健康检查 status=ok，12186 市场、3574 机会、exchange_errors={}。后端约 547.7 MiB / 768 MiB，前端 3.906 MiB / 96 MiB，双容器 RestartCount=0、OOMKilled=false。观察窗口未出现新监控循环/通知发送异常；同期日志中的公告及交易可用性第三方 HTTP 错误属于其他模块，未扩大本任务处理。
- 监控连续观察证据：`output/aster-premium-scan-observations-20261021.json`；接口、预览和自然事件证据：`output/aster-premium-production-20261021.json`。验收未人为创建交易卡或发送模拟告警；外部 Astro 实际新增路线写入仍需在正常业务触发时观察。

## 工作区与后续

- 保留开始时已有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 两份 WIP；不提交这两份文件。
- 保留所有既有 `.worktrees/`、pytest 临时目录、`output/`、未跟踪交接及 `script/dexe_bybit_bitget_chain.py`。本次输出证据也留在 output，不批量纳入提交。
- 后续重点观察官方源查询错误、完整扫描耗时和自然告警。其他模块请新建任务，避免与本模块验证混在一起。
