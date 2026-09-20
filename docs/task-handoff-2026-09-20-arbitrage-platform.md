# 交接：价差系统当前基线与后续模块化协作

日期：2026-09-20

这份文档供新的 Codex 任务直接接手项目使用。它记录当前功能基线、关键业务规则、验证状态、部署约束和下一任务的启动方法。后续每个功能模块完成后，应新建自己的日期化交接文档，不要把所有长期开发继续堆在同一个任务中。

## 仓库与当前基线

- 仓库：`https://github.com/wubin19920612/taoli1.git`
- 开发分支：`codex/frontend-localization-polish`
- 当前功能基线：`f5e229e86878c8de938c0beebf9af7932dbccb00`
- 功能基线提交：`feat: connect monitoring signals and harden Astro routing`
- 生产项目目录：`~/wubin/taoli1`
- 前后端由 Docker Compose 运行，SQLite 数据库位于后端容器 `/data/radar.db`。
- 本文档及根目录 `AGENTS.md` 是功能基线之后的文档与流程变更；接手时以 `git log -1 --oneline` 显示的最新提交为准。

不要把服务器地址、SSH 私钥、`.env`、Webhook 或 API Key 补写到本文件。部署凭据只从受控运行环境取得。

## 系统定位

项目是一个自托管的 CEX 价差、资金费率、新币公告和指数成分监控系统。后端使用 FastAPI、异步交易所采集器和 SQLite；前端使用 React/TypeScript/Vite；通知主要通过飞书；Astro SDK 用于预览、创建和管理套利卡片。

主要代码入口：

- 后端应用编排：`backend/app/main.py`
- HTTP 路由：`backend/app/api/`
- 交易所适配器：`backend/app/exchanges/`
- 业务服务：`backend/app/services/`
- 数据库 schema 与 repository：`backend/app/db/`
- 前端 API 类型与客户端：`frontend/src/api/`
- 页面和浮窗：`frontend/src/pages/`、`frontend/src/components/FloatingWatchPanel.tsx`
- 后端测试：`backend/tests/`
- 前端测试：`frontend/tests/`

## 当前已完成能力

### Astro 卡片与浮窗

- 浮窗汇总正在运行的 Astro 卡片，并提示标的是否正在交易。
- 持仓区域显示 U 本位仓位、盈利、当前价差等核心信息。
- Astro 没有返回盈利时会展示“预估”值：普通路线扣除 `0.2%`，单边 HL 扣除 `0.13%`，双边 HL 扣除 `0.05%`。
- 当前价差按卡片使用的百分比价差表达，支持非 1:1 标的倍率，例如 1:10。
- 标的查询可以保存，价格带深度等参数可在页面调整。

### Hyperliquid 市场路由

- HL builder-deployed perp 必须按 `raw_symbol` 和具体 DEX 路由，不能把 `main`、`io`、`xyz` 等市场压成同一条后随机选择。
- Astro 浮窗会传递 `aHlDex`、`bHlDex` 或有效 DEX 字段进行行情查询。
- 标的 API 支持 `GET /api/instruments/{symbol}?dex=<dex>`。
- 例如 Astro 腿 `ANTH` 实际对应 `io:ANTH`，规范标的是 `ANTHROPICUSDT`。显式 DEX 和唯一别名均可定位到正确市场。
- 老卡片缺少 DEX 时，仅在别名唯一时自动推断；多个 DEX 都可能匹配时不应随机猜测。

关键文件：

- `backend/app/api/routes_instruments.py`
- `backend/app/services/symbol_aliases.py`
- `frontend/src/api/client.ts`
- `frontend/src/components/FloatingWatchPanel.tsx`

### 价差与资金费率告警

- 正价差、正资金费率或做多侧资金费率更小的路线可使用较低的可配置通知阈值，当前业务期望值为 `0.9%`。
- 通知标题带评级：典型状态包括“强烈推荐”“推荐”“需评估”。评级使用通知前的最新价差、资金差、双边 24h 成交额、风险标签和 Astro/盘口校验结果。
- 校验失败必须降为“需评估”；历史告警不事后重新评级。
- 通知正文必须写清双方资金费率及其各自周期，不能把不同周期的费率并列成看似可直接比较的同周期数值。
- 实际可成交有效收益需要以盘口可成交价、方向、手续费和资金费率为基础；无法取得真实值时必须明确标记为预估。
- Bybit 若价差主要由资金费率机制引起，不按常规“价差收敛”开仓，只考虑获取资金费率的反向开仓方式。

关键文件：

- `backend/app/services/alert_messages.py`
- `backend/app/services/orderbook_validator.py`
- `backend/app/services/pair_spread_query.py`
- `backend/app/main.py`

### 新币公告与预建卡

- 公告通知展示公告中的上线时间和交易时间，不能只发标的名称。
- OKX 之外的已支持交易所遵循相同的公告通知信息标准。
- 可根据资金费率或溢价指数提前为 Binance 和 Bitget（业务简称 `bgbn`）交易对预建 Astro 候选。
- 页面支持启用开关、目标交易所、资金费率阈值、溢价指数阈值和 24h 成交额阈值，并解释参数含义。
- 候选表展示双方溢价指数、实时资金费率、费率周期、24h 成交额和“开仓方式”。主动创建卡片后会发送通知。
- 创建涉及 HL 的卡片前必须确认具体市场和 DEX；默认市场不匹配会导致卡片不可用。
- Bybit 资金费率型候选的开仓方式遵循上一节的反向开仓规则。

关键文件：

- `backend/app/services/new_listing_monitor.py`
- `backend/app/models/new_listing.py`
- `frontend/src/pages/NewListingMonitorPage.tsx`

### Lighter

- Lighter 已加入价差采集、查询和行情服务。
- Astro 创建 Lighter 卡片时只允许 `gc-lighter` 路由，不创建 Lighter 本体卡片。
- `gc-lighter` 只与已知 GC 或 Bitget 路由配对；未知路由必须阻止提交。
- Lighter 市场元数据会缓存复用；排查报错时先区分交易所上游接口错误、市场映射错误和本地 HTTP 连接问题。

关键文件：

- `backend/app/exchanges/lighter.py`
- `backend/app/services/pair_spread_query.py`
- `backend/app/services/astro_planner.py`

### 指数成分联动

- 已有“指数成分变更”模块，可监控 Binance、OKX、Bybit、Bitget 和 Gate 的支持市场。
- 自动联动开关开启后，系统从浮窗监控标的、持仓标的和 Astro 卡片同步需要监控的指数；标的不再被监控或持仓后，会撤销相应自动监控。
- 手工监控与自动监控应保持可区分，自动同步不能误删用户手工添加项。
- 指数成分发生变化时发送通知，并附变化后指数的初始趋势信息。

关键文件：

- `backend/app/services/index_components.py`
- `backend/app/api/routes_index_components.py`
- `backend/app/models/index_component.py`
- `frontend/src/pages/IndexComponentChangesPage.tsx`

## 最新验证基线

功能提交 `f5e229e` 已完成以下验证：

- 后端全量测试：`712 passed`
- 浮窗前端测试：`13 passed`
- 前端 TypeScript/Vite 生产构建：通过
- `git diff --check`：通过
- 线上前端和后端容器：`healthy`
- 线上接口验证：`/api/instruments/ANTH?dex=io`、`/api/instruments/ANTH`、`/api/instruments/ANTHUSDT` 均解析到 `ANTHROPICUSDT` 和 `io:ANTH`

前端全量测试当时为 `149 passed, 1 failed`。唯一失败是：

```text
SettingsPage > loads and saves Live Pilot settings
Unable to find an element with the text: 实盘灰度
```

该测试仍在查找旧文案“实盘灰度”，而页面曾更名为“正差价正费率实盘实验”。接手相关模块时应先确认这是测试过期还是条件渲染问题，再做最小修复。

Ruff 还有既有告警：

- `backend/app/services/symbol_aliases.py` 中 `PairSpreadPoint` 未使用。
- `backend/tests/test_api.py` 曾发现既有未使用变量。

不要在无关模块中顺带做大范围静态检查清理。

## 已知运行问题

- Gate 公告接口偶发 HTTP `567`，涉及 `newspotlistings`、`newfutureslistings`、`newconvertlistings`、`delisted`。这属于上游接口异常；应确认后端健康和其他采集器未被连带阻塞。
- 前端 Docker 构建中的 `npm audit` 曾报告 12 项现有依赖提示：1 low、4 moderate、6 high、1 critical。尚未授权执行可能破坏兼容性的依赖大版本升级。
- SQLite 数据库体积较大，部署前备份可能需要一定时间和磁盘空间。必须等备份完成并校验后再更新容器。

## 工作区保护项

以下是已知本地产物或研究脚本，不属于当前已跟踪源码。除非用户明确要求处理，否则不要提交、删除或回滚：

- `output/edge-profile-codex/`
- `output/floating-watch-*.png`
- `output/index-auto-watch-*.png`
- `output/dexe_bybit_bitget_chain_probe/`
- `script/dexe_bybit_bitget_chain.py`

服务器上也有本地运维文件，应保留：

- `.env.backup-codex-20260911-1425`
- `.env.backup-poll-8-20260911`
- `CACHED`

## 标准交付流程

每个功能模块必须完成以下闭环：

1. 开始前运行 `git status --short --branch`，确认并记录已有用户文件。
2. 实现后运行相关测试；共享逻辑或跨模块改动应扩大测试范围。
3. 运行 `git diff --check`，只 `git add` 本任务的明确文件并复核暂存差异。
4. 提交并执行 `git push origin HEAD`，确认本地分支与远端同步。
5. 部署前用 SQLite backup API 备份 `/data/radar.db`，记录备份名称、大小和 SHA-256。
6. 服务器 Git 不使用 `sudo`；执行 `git pull --ff-only origin codex/frontend-localization-polish`。
7. Docker 使用 `sudo docker compose build --pull` 和 `sudo docker compose up -d --remove-orphans`。
8. 检查 `sudo docker compose ps`、后端 `/api/health`，并实际调用本模块接口或检查页面。
9. 新增本模块交接文档，提醒用户为下一个功能模块新建 Codex 任务。

更完整命令见：

- `docs/git-delivery-checklist.md`
- `docs/linux-deployment.md`
- `deploy/linux-update.sh`

不得执行 `docker compose down -v`，因为这会删除命名卷中的生产数据库。

## 新任务启动模板

在新 Codex 任务中先提供本文件，然后只写一个模块的目标。例如：

```text
请先阅读 AGENTS.md 和 docs/task-handoff-2026-09-20-arbitrage-platform.md。
本任务只处理【模块名称】。
目标：【期望行为】。
验收：【页面、接口、通知或数据应满足的条件】。
完成后按项目规则测试、提交、推送、备份数据库、部署并验证线上，再为下一模块更新交接文档。
```

建议继续按以下模块拆分任务：

- Astro、浮窗、HL DEX 与 Lighter 路由
- 价差告警、资金费率、评级与真实可成交收益
- 新币公告、预建候选与自动建卡
- 指数成分采集、联动监控与趋势通知
- 服务器、数据库、依赖升级与性能问题

涉及 `backend/app/main.py`、数据库 schema、`frontend/src/api/client.ts` 等共享核心文件的模块不要并行修改，以减少合并冲突和线上行为交叉影响。
