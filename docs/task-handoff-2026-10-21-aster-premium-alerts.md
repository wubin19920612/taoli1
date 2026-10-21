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
- 补充旧混合卡兼容测试后，执行 Aster 与 HL 定向回归，结果记录于 `output/aster-premium-legacy-tests-20261021.log`。
- 新监控、配置鉴权与持久化、官方优先及过期回退、现有 PremiumIndex 查询：38 项通过；`output/aster-premium-backend-final-20261021.log`。
- SettingsPage 16 项通过，包含默认 2%、修改保存；`output/aster-premium-settings-green-20261021.log`。
- TypeScript 与 Vite 生产构建通过；`output/aster-premium-build-final-20261021.log`。
- Playwright 实际本地页面验证 1440/390 像素：将阈值改成 3.5、保存、刷新仍为 3.5、恢复 2；检查开关、900 秒、GC 帮助文字；无页面异常、无横向溢出。截图 `output/aster-premium-local-{1440,390}-20261021.png`。
- 新增服务/测试及改动的 Astro、设置模型/路由 lint 通过，`git diff --check` 通过。`main.py` 和 `premium_index_query.py` 有既有 lint 问题，本任务未批量整理无关代码。

## 线上状态

- 待本次功能提交后等待双镜像、由部署脚本备份并校验主库及独立研究库，再快进更新并启动预构建镜像。部署后在此补充提交号、备份校验、接口、页面、监控扫描实测结果。
- 不以预览或模拟通知通过证明外部 Astro 实际写入或飞书自然触发成功；部署验收不人为创建交易卡或发送模拟告警。

## 工作区与后续

- 保留开始时已有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 两份 WIP；不提交这两份文件。
- 保留所有既有 `.worktrees/`、pytest 临时目录、`output/`、未跟踪交接及 `script/dexe_bybit_bitget_chain.py`。本次输出证据也留在 output，不批量纳入提交。
- 后续重点观察官方源查询错误、完整扫描耗时和自然告警。其他模块请新建任务，避免与本模块验证混在一起。
