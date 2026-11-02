# 前端改版与不用功能退役交接（2026-11-02）

## 范围、分支与基线

- 模块：C 包（部署配置、当前说明与交接）。依据 [本次任务书](remove-unused-config-docs-spec-2026-11-02.md) 和 [删除盘点](remove-unused-inventory-2026-11-02.md)。
- 当前分支：`codex/ui-refresh`；开始基线：`9db3d01d9d589ba5a9fe2157d8a1095a20b7a410`。开始时工作区、暂存区均干净，无未跟踪文件。
- 已合入前端改版、A 包 `fe4330d` 和 B 包 `6a1601c`（合入提交分别为 `7e42cf4`、`f233a40`）；下文业务实现属于这些已有提交，本次只更新配置和文档。
- 本次只改 `.env.example`、`docker-compose.yml`、`README.md`、`docs/linux-deployment.md`、`docs/next-task.md` 和本交接。仅在当前分支本地提交，不 push、不部署，不启动、停止或重启服务，不修改 `frontend/`、`backend/`、部署脚本、CI 或 Compose override。

## 已合入的前端改版

以下依据本分支提交及源码核对，不代表本次重新完成浏览器验收。

| 页面 / 入口 | 已合入内容 | 提交 / 关键入口 |
| --- | --- | --- |
| 实时机会 | 屏蔽操作收进弹出层，统计及链路状态紧凑显示，详情可折叠；Astro 预览中文化并折叠技术细节 | `dc3a5f7`；`frontend/src/pages/DashboardPage.tsx` |
| 价差查询 | 查询、关键指标、曲线前置，低频详情改成标签页；详情面板保持挂载以保留分页状态 | `2140616`、`a619cd0`；`frontend/src/pages/PairMonitorPage.tsx` |
| 标的查询 | 查询卡紧凑化、来源与口径弹出层、区块锚点；跨市场差价前置，市场明细支持紧凑 / 完整，充提与指数区默认折叠 | `6a3274a`、`a8d4a84`；`frontend/src/pages/InstrumentLookupPage.tsx` |
| 交易所公告 | 公告表前置，抓取与推送设置默认折叠 | `f71c909`；`frontend/src/pages/AnnouncementsPage.tsx` |
| 指数成分变更 | 监控列表默认折叠，图表和变更表前置，查询操作精简 | `f71c909`；`frontend/src/pages/IndexComponentChangesPage.tsx` |
| 资金费率套利 | 策略参数与 Astro 预建面板折叠，主候选表前置；预建与自动观察错误保持可见 | `f71c909`、`1db1de1`；`frontend/src/pages/FundingArbitragePage.tsx` |
| 溢价指数 | 图表前置，指标按指数 / 溢价与资金费率分组，提示集中展示 | `f71c909`；`frontend/src/pages/PremiumIndexPage.tsx` |
| 账户连接 | 测试结果放在账户表上方，有效密码可折叠 | `f71c909`；`frontend/src/pages/AccountConnectionsPage.tsx` |
| 参数与告警 / 告警历史 | 告警规则、风险参数、Astro、访问与服务、告警历史五个标签；表单保持挂载，历史按标签激活加载；旧 `history` 入口兼容到设置历史标签；删除实盘实验面板 | `80bb327`；`frontend/src/pages/SettingsPage.tsx`、`AlertHistoryPage.tsx`、`frontend/src/components/AppShell.tsx` |

详细排版目标见 [实时机会](dashboard-layout-spec-2026-11-02.md)、[价差查询](pair-monitor-layout-spec-2026-11-02.md)、[标的查询](instrument-layout-spec-2026-11-02.md)、[五页改版](pages-layout-spec-2026-11-02.md)、[设置标签页](settings-tabs-spec-2026-11-02.md)。这些文件和旧交接作为历史设计、验收证据保留。

## 退役功能

11 个独立页面：乌龙回测、资金研究、负基差埋伏、原油新闻、机会雷达、1s 采样、挤仓结构（含路线研究、纸面交易子视图）、独立跨所价差、价差事件榜、TradFi 价差、负费率 FS 观察。另删除“正差价正费率实盘实验”（live pilot）设置面板及后端分支，共 12 项。

A 包删除菜单、页面及专用 API / 类型 / 样式 / 测试，处理旧导航回退；B 包卸载专属路由、服务、模型、后台任务及 settings 读取方。资金研究的离线 CLI 随研究包一并删除。独立跨所价差页的共享查询接口是保留例外，见下一节。

本次 C 包：

- 已通过 `git grep -i` 检查 `backend/app`，下列 11 个环境变量及其小写字段名均无读取方，故从 `.env.example` 删除，无需因共享读取而保留的候选变量：`FUNDING_POLL_INTERVAL_SECONDS`、`FUNDING_RESEARCH_ENABLED`、`FUNDING_RESEARCH_MANAGE_PAPER_TRADES`、`FUNDING_RESEARCH_SNAPSHOT_RETENTION_HOURS`、`SQUEEZE_MONITOR_ENABLED`、`SQUEEZE_MONITOR_MODE`、`SQUEEZE_MONITOR_SYMBOLS`、`SQUEEZE_MONITOR_EXCLUDE_SYMBOLS`、`SQUEEZE_ROUTE_ENABLED`、`SQUEEZE_PAPER_ENABLED`、`OIL_NEWS_FEISHU_LIVE_SEND_ENABLED`。
- `docker-compose.yml` 仅删除显式 `FUNDING_POLL_INTERVAL_SECONDS`，其余配置不变；不修改真实 `.env`。
- README 删除资金研究说明、扫描 / 循环 / 回测 / 网格搜索命令及研究 API 列表，其余保留。
- 部署说明标记挤仓 worker 已退役，保留旧 route 库备份、保留策略和恢复流程；更新下一任务入口。

## 共享契约与数据保留

- **symbol-query 例外**：保留 `GET /api/pair-spread/symbol-query`、`PairSpreadQueryService`、实时序列缓存及前端 `querySymbolExchangeSpreads` / `SymbolSpread*`；标的查询展开走势仍使用它。`backend/app/api/routes_pair_spread.py` 已移除该端点的 `symbol_spread` feature guard，不能将其纳入退役 404 清单。
- **公式分类迁移**：`FORMULA_FAMILIES`、`exchange_formula_family` 已迁入 `backend/app/services/funding_formula_families.py`，`funding_arbitrage.py` 从此导入；交易所映射及 `unknown` 回退语义保留。前端共享 `FundingOpportunityType` 保留，资金费率套利仍使用。
- **`fs_identity` 保留**：`backend/app/services/fs_identity.py` 的 `SPEC_URLS`、`reviewed_fs_bindings` 仍由 `market_identity_catalog.py` 使用。退役 FS 观察不等于删除市场身份能力；精确 DEX、原始市场、单位、证据与有效期规则继续保留。
- 继续保留实时机会、价差查询与区间回测、历史记录与统计、Astro / arb-mvp 普通建卡及手动逆向 SF 能力、浮窗、账户连接 / 持仓、Gate 定时减仓、公告、指数成分、溢价告警、飞书 / 电话告警和运维能力。Gate 客户端仍供持仓 provider 使用。
- 普通 alert-loop 的规则、校验、并发隔离、通知去重、preadd 和建卡链路继续使用；live pilot 专属分支退出，不自动处理外部平台已经存在的卡片。
- **旧表和旧 `app_settings` 行全部保留、未删除**。没有 DROP、删行、修改 CHECK 或旧 route 数据复制；本次没有打开或修改本地 / 生产业务数据库。
- `backend/app/db/retired_squeeze_schema.py` 承载旧挤仓建表 / 加列定义，避免 schema 反向依赖退役 service 包；`schema.py` 中研究、新闻等旧表定义继续保留。旧 live pilot attempt 的历史 source 兼容性保留。
- `/data/radar.db` 与存在的 `/data/radar-squeeze-route.db` 及其历史备份继续保留；发布时仍须备份并校验，恢复步骤见 [部署文档](linux-deployment.md)。源码保留不等于已核验生产表、记录数或备份状态。

## 旧开关兼容

`backend/app/core/config.py` 的 `OPTIONAL_FEATURES` 仅保留 `gate_twap`。旧 `DISABLED_FEATURES` 中以下 8 个已知退役 key 接受后被忽略，不再暴露或生效：

`squeeze`、`negative_basis`、`oil_news`、`opportunity_radar`、`symbol_spread`、`fat_finger`、`second_level_sampling`、`funding_research`。

`gate_twap` 仍有效，旧配置中禁用它的状态不会被自动解除；未知拼写仍报错。不要原样照搬历史开关示例作为新部署策略，也不要为使 symbol-query 可用而解除 Gate 禁用。

## 本次验证与线上状态

- 对配置候选变量执行只读源码检索，确认没有读取方；核对已合入提交及共享接口 / 依赖入口。
- `git diff --check` 通过，无输出；提交前仅暂存任务书允许的 6 个文件。
- 本机 PATH 未找到 `docker`，未运行 `docker compose --env-file .env.example -f docker-compose.yml config --quiet`；留待有 Docker 的环境执行配置解析，不启动服务。
- 本次未运行后端测试、前端测试、类型检查、构建或浏览器操作，以遵守不触碰前后端目录及现有服务的边界；不将前序任务的通过数字冒充本次结果。已有回归入口包括 `backend/tests/test_retired_features.py`、`test_feature_flags.py`、`frontend/tests/AppShell.test.tsx`、`SettingsPage.test.tsx`、`InstrumentLookupPage.test.tsx`。
- 未连接服务器、备份、推送或部署；线上版本和行为本次未核验。此处的“已退役”描述当前分支源码，不能当作生产已上线证据。最终本地提交号以交付回执 / `git log -1` 为准。
- 本次新增文件只有本交接；开始时无其他未跟踪产物，未删除或覆盖其他任务文件。

## 已知问题与下一任务

- **原有 lifespan 重入问题**：同一个 app 对象在同一进程内第二次进入 lifespan 时，Astro、飞书、Gate 客户端已经关闭。这是原有问题，生产按进程重启所以不受该重入场景影响，后续另开任务修复；不要把相关生命周期测试视为已经解决客户端重建。
- 按任务书要求 README 除资金研究部分外保持原样，仍有原油新闻旧说明及旧变量示例；该能力已退役，这些旧文字不构成可运行入口，后续另行清理。历史 FS / 实验 / 挤仓交接及架构索引也保留为历史证据，优先依据本文判断当前能力。
- 下一个模块、客户端重入修复或发布应新建任务；发布前复核 A / B 包测试记录和跨包页面行为，补上 Docker 配置解析。当前不扩展到测试、服务操作或部署。

## 后续上线检查清单（本次未执行）

1. 按 [部署流程](linux-deployment.md) 准备审核版本的双预构建镜像，备份并校验主库及存在的旧 route 库；`git pull --ff-only` 后按既定 Compose 流程更新，保留数据库、卷、真实 `.env` 和备份。
2. 核对后端 / 前端容器、`/api/health` 和部署提交号；用 `backend/tests/test_retired_features.py` 的 `RETIRED_ENDPOINTS` 逐项检查退役端点返回 404（包括 live pilot settings）。
3. 用有效查询参数检查保留的 `/api/pair-spread/symbol-query` 返回 200；标的查询展开走势后能实际出图，不能只用 HTTP 200 替代图表验收。
4. 设置页正常加载，不请求 live pilot；标签切换不丢未保存表单，告警历史标签正常加载，旧 `history` 入口兼容。
5. 检查实时机会、资金费率套利、价差查询 / 区间回测、Gate 展示与浮窗，以及桌面 / 手机布局。不要用真实交易、降低身份 / 成本门槛或发送真实通知来制造验收结果。
