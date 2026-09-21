# 交接：Lighter Robinhood（HOOD）套利接入

日期：2026-09-20（2026-09-21 更新实时机会排序与资金费率展示纠偏）

## 目标与范围

本任务只处理 Astro、浮窗与 Lighter 路由模块中的 Robinhood 链/`HOOD` 标的行情接入，不扩展其他交易所或功能模块。业务定义是：Lighter `HOOD` 是用于和其他等价市场比较的真实行情腿，不是一个需要固定展示的“机会”。

验收目标：

- Lighter `HOOD` 即使不在成交额前 48 名，也必须进入自动套利扫描。
- 行情必须来自真实订单簿，包含 bid、ask、盘口数量、24h 成交额、资金费率及其周期。
- 与 Binance、Bitget 等同一规范标的 `HOODUSDT` 正确配对，并保留 Lighter 原始市场 `HOOD`。
- 只有真实价差、扣费后收益或标准化资金费率达到现有筛选条件时，相关路线才作为机会出现。
- 可用路线继续使用 Astro `gc-lighter`，不创建普通 `lighter` 卡片。
- 实时机会默认列表必须继续服从全市场筛选和价差排名；不能为了证明接入成功而补取或置顶没有实际优势的 HOOD/Lighter 路线。
- 完成测试、提交、推送、生产数据库备份、部署和线上接口验证。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`f6f41fd`（`docs: add modular handoff workflow`）
- 第一阶段提交：`4879e5f80d11637c5b708ccf796126c1c114f753`（保证 `HOOD` 占用 Lighter 自动扫描优先槽位）
- WebSocket 功能提交：`f591fbb808d901d41d173dbf8bb481d60993d700`（Lighter 实时订单簿改用官方 WebSocket）
- 已撤销的实时机会可见性提交：`d19e6d5`（曾默认补取并置顶 HOOD/Lighter，同时加入 Robinhood 别名搜索）
- 排名逻辑纠偏提交：`35b804d3c63612d0998520661790f76e2b5088c5`（删除默认补取和强制置顶，保留主动搜索别名）
- 资金费率展示纠偏提交：`9111648`（不同结算周期统一显示为每小时净值和 24 小时净值）

## 已完成功能

- `HOOD` 被列为 Lighter 永续优先标的。自动扫描总量仍限制为 48，优先标的替换成交额榜末位，不额外增加扫描规模。
- 优先标的订阅顺序在其他市场之前；如果 Lighter 未返回 `HOOD` 盘口，本轮采集失败关闭，不再把缺少 `HOOD` 的结果静默标记为健康。
- 自动扫描、单市场订单簿校验和价差查询统一通过官方 `wss://mainnet.zklighter.elliot.ai/stream` 的 `order_book/{market_id}` 订阅获取真实盘口。
- 停止高频逐市场调用 `orderBookOrders` REST 接口，避免生产服务器出口触发 AWS WAF 人机验证。
- 同时兼容 REST 的 `remaining_base_amount` 和 WebSocket 的 `size` 深度字段，并保持调用方要求的订单簿档数上限。
- 明确声明直接依赖 `websockets>=13.0`。
- 实时机会页只使用现有全市场筛选后的前 120 条主请求，不额外补取任何指定标的或交易所。
- 表格继续按 `open_spread_pct` 正常排序。HOOD/Lighter 只有在满足当前筛选并进入全市场前 120 名时才会出现在默认列表，不享有特殊优先级。
- 标的搜索支持 `RH`、`Robinhood`、`RobinhoodUSDT`、`罗宾汉` 和 `HOOD`，都返回规范标的 `HOODUSDT`。
- 主动搜索结果仍使用通用机会表格，展示规范标的、交易所及原始市场、双方成交额、实际 bid/ask 计算的价差、扣费收益、资金费率周期和风险标签；不再给 HOOD 添加特殊显示名或专属样式。
- 机会表不再把 Lighter `1h` 和 Binance/Bitget `8h` 的原始费率直接相减为“周期净”。页面优先使用后端的 `net_funding_next_hourly_pct`、`net_funding_next_daily_pct`，缺少下期值时回退到当前标准化字段，并显示“每小时净”和“24h净”。

## 关键代码入口

- `backend/app/exchanges/lighter.py`
  - `lighter_order_books`：WebSocket 订阅、ping/pong、快照解析和超时处理。
  - `LighterAdapter._fetch_tickers`：48 个永续槽位、`HOOD` 优先选择、真实行情快照。
  - `LighterAdapter.fetch_order_book`：订单簿校验使用 WebSocket。
- `backend/app/services/pair_spread_query.py`
  - `_fetch_lighter_current`：实时价差查询使用 WebSocket 盘口，资金费率仍来自 REST。
- `backend/tests/test_lighter_adapter.py`
  - 覆盖 WebSocket 协议、`HOOD` 优先槽位、缺失时失败关闭、订单簿与价差查询。
- `backend/pyproject.toml`
  - WebSocket 运行依赖。
- `backend/app/api/routes_opportunities.py`
  - Robinhood 人类名称别名解析；仍先完成服务端筛选，再应用 `limit`。
- `frontend/src/state/useRadarStore.ts`
  - 每轮只按当前筛选请求一次机会列表；不再发送 HOOD/Lighter 补充请求。
- `frontend/src/components/OpportunityTable.tsx`
  - `Open spread` 使用通用数值排序；没有 HOOD/Lighter 特殊优先级。
  - `normalizedFundingEdges` 使用后端标准化后的小时和 24 小时资金费率差，不直接比较不同结算周期的原始值。
- `frontend/tests/DashboardPage.test.tsx`
  - 回归检查默认机会请求不包含 `symbol`、`exchange`，并保持 `limit=120`。
- `frontend/tests/OpportunityTable.test.tsx`
  - 回归检查 Lighter `1h` 与 Bitget `8h` 混合周期时只展示标准化净值，不展示原始费率直接差。

## 重要业务规则

- Robinhood 链/`HOOD` 是目标标的，Lighter 是本任务需要采集的行情来源之一。它必须作为比较腿参与价差和资金费率计算，但接入成功本身不构成套利机会。
- Lighter 规范标的是 `HOODUSDT`，原始市场必须保留为 `HOOD`；不要把原始市场字段覆盖掉。
- Lighter Astro 路由仍只能使用 `gc-lighter`，且仅与已知 GC 或 Bitget 路由配对。
- 资金费率不能脱离周期比较。双方原始费率和 `1h`、`8h` 等结算周期继续展示，但净收益比较必须使用标准化的每小时或 24 小时值。
- 套利判断继续使用真实 bid/ask。mark/index 仅用于风险信息，不能代替可成交价。
- 页面出现正价差不等于扣费后盈利。线上验收样本的 Lighter -> Bitget 开仓价差约 `0.036%`，但扣除手续费和滑点后的 `fee_adjusted_open_pct` 为负，不应视为交易建议。
- 接入和可搜索不等于默认展示。没有进入当前筛选后全市场前 120 名的路线不得通过前端附加请求绕过排名。
- 最终交易判断必须同时检查实际可成交价格、手续费和滑点、市场倍率、双方成交额和盘口深度、资金费率周期与结算时间、风险标签，以及数据是否为预估。

## 本地验证

- Lighter WebSocket 阶段专项测试：`11 passed`。
- 最新后端全量测试：`720 passed, 11 warnings`，耗时约 4 分 46 秒。
- Robinhood 别名与筛选顺序专项：`6 passed`。
- 最新前端专项：`DashboardPage.test.tsx` 与 `OpportunityTable.test.tsx` 共 `24 passed`，包含 Lighter `1h` 与 Bitget `8h` 混合周期标准化回归用例。
- 前端生产构建：通过，包含 TypeScript 检查和 Vite build。
- 排名纠偏没有重跑前端全量；最近一次全量为 `150 passed, 1 failed`。唯一失败仍是既有 `SettingsPage` 旧文案断言，测试查找“实盘灰度”，页面已更名为“正差价正费率实盘实验”；与本模块无关。
- `python -m compileall`：通过。
- `git diff --check`：通过。
- 本次排名纠偏仅修改前端，无需运行 Ruff；Robinhood 后端别名专项和前端构建均已通过。
- WebSocket 阶段 Ruff：`backend/tests/test_lighter_adapter.py` 通过。
- 两个既有生产文件仍有 6 条历史 Ruff 告警：Lighter 的异常处理 3 条、`pair_spread_query.py` 的既有类型/异常处理 3 条；本任务没有扩大清理范围。
- 本机真实行情验证：Lighter 返回 48 个永续市场并包含 `HOODUSDT`；单标的查询返回真实 bid/ask、24h 成交额和 1 小时资金费率。

## 数据库备份

资金费率标准化展示部署前备份：

- 文件：`backups/radar-20260921T005449Z-pre-normalized-funding-display.db`
- 大小：`720613376` 字节。
- SHA-256：`9f1b9e4af53101c6ce7d3be59f2575916d4ed42db4fc9554ed621c849a3a68ab`
- SQLite `PRAGMA quick_check`：`ok`。

排名逻辑纠偏部署前备份：

- 文件：`backups/radar-20260921T003355Z-pre-restore-ranked-opportunities.db`
- 大小：`724926464` 字节。
- SHA-256：`ddac0b26ae67facb82e9519be6578f7fdc5472cf6b0796537dfd0f7bdddc1153`
- SQLite `PRAGMA quick_check`：`ok`。

实时机会可见性部署前备份：

- 文件：`backups/radar-20260920T123520Z-pre-rh-lighter-visibility.db`
- 大小：`735129600` 字节。
- SHA-256：`90dbe8962e63062d2821925b41b2bc2f619eaee1587f976dbce2dcd0c0c40f40`
- SQLite `PRAGMA quick_check`：`ok`。

最终部署前备份：

- 文件：`backups/radar-20260920T065027Z-pre-lighter-websocket.db`
- 大小：`732524544` 字节。
- SHA-256：`84f7c2e4cd5a3d67090db042142324fdb6c78e338a9baa923be922617992c0cb`
- SQLite `PRAGMA quick_check`：`ok`。

第一阶段部署前另有备份：

- 文件：`backups/radar-20260920T062449Z-pre-lighter-hood.db`
- 大小：`733646848` 字节。
- SHA-256：`e42008fb5fde02a262733cb1ea5c4fc9ba935c7fd91ca07cdfe2cc5e7548ad58`。
- SQLite `PRAGMA quick_check`：`ok`。

## 线上状态

- 服务器使用 `git pull --ff-only` 更新到资金费率展示纠偏提交 `9111648`。
- `docker compose build --pull` 和 `up -d --remove-orphans` 成功；没有执行 `down -v`。
- 前端、后端容器均为 `healthy`，`/api/health` 返回 `status=ok`；验收时 `lighter=healthy`、`exchange_errors={}`。
- 连续跨三个采集时点检查：冷启动首轮市场元数据遇到一次 405，下一轮自动恢复；之后两轮 `lighter=healthy`、失败次数为 0、错误为空，`HOOD` 均持续存在。
- `/api/markets?exchange=lighter&symbol=HOOD` 返回 `HOODUSDT`、`raw_symbol=HOOD`、非交叉 bid/ask、盘口数量、24h 成交额、资金费率和 1 小时周期。
- `/api/instruments/HOOD` 返回 8 个交易所、10 个市场，并生成 9 条含 Lighter 的实时路线（数量随行情变化）。
- `/api/opportunities?symbol=HOOD&exchange=lighter&include_risky=true` 返回自动机会；验收时 `lighter:HOOD -> bitget:HOODUSDT` 保留双方成交额和 `1h/8h` 资金费率周期。
- 对应 `/api/astro/preview/{opportunity_id}` 返回 `gc-lighter -> bitget`、类型 `FF`、`can_submit=true`、无 blocker；本任务只验证预览，没有实际创建卡片。
- 前端 3000 端口代理能返回同一 Lighter `HOOD` 市场。
- 部署后日志中 `orderBookOrders` 调用为 0，Lighter WebSocket 错误为 0。
- 线上 `/api/opportunities` 对 `RH`、`Robinhood`、`罗宾汉`、`HOOD` 主动查询均能返回 `HOODUSDT` 比较路线，别名筛选在 `limit` 之前生效。最新浏览器实测输入 `RH` 返回 12 条 HOOD 路线，其中 3 条包含 Lighter；路线数量会随行情和筛选结果动态变化。
- 线上 Lighter `HOOD` 验收样本包含非交叉 bid/ask、双边成交额、Lighter `1h` 资金费率周期和对手方周期；价格和费率会随市场变化。
- 浏览器实测的资金费率区域已显示“每小时净”和“24h净”，不再显示会误导混合周期比较的“周期净”。
- 一次资金费率引擎诊断中，精确 `HOODUSDT` 找到 10 个等价市场并生成 44 个比较对，其中 9 个包含 Lighter；当时完整成本后的候选为 `EXIT_NOW`，说明接入已参与计算，但当时并不存在可执行套利。该结果和数值均会随行情变化。
- 生产前端代理的默认机会请求返回 120 条，按 `open_spread_pct` 降序；验收时末位约 `0.369%`，HOOD/Lighter 为 0 条。
- 浏览器网络日志只出现标准的 `limit=120` 机会 URL，没有 `symbol=HOOD` 或 `exchange=lighter` 补取请求。自动刷新会重复同一个标准 URL，不会增加重点路线请求。
- 部署后截图显示 `Opportunities 120`，首行按正常价差排名展示 `BPUSDT`，没有 HOOD/Lighter 强制置顶；Lighter 状态为 `healthy`，页面无明显重叠或截断。

## 已知问题与残余风险

- 冷启动第一轮 `orderBookDetails` 仍可能触发 Lighter AWS WAF 的 `405`；现有短冷却重试已在下一轮恢复。实时订单簿已迁移到 WebSocket，不再持续触发该 WAF。
- Gate 公告接口仍有交接基线记录的 4 类 HTTP `567`；与本任务无关，后端和其他交易所采集未受阻。
- WebSocket 当前为每轮建立连接、取得初始快照后关闭，不维护长期连接。现有 12 秒刷新周期线上稳定；若未来连接次数受限，可在单独 Lighter 性能任务中改为持久订阅和增量维护。
- Lighter 上游若不返回 `HOOD` 优先盘口，本轮会明确失败并进入采集器重试，不会用 mark/index 或陈旧估算价冒充可成交价。
- 主动搜索可发现接入路线，但结果仍可能带 `LOW_VOLUME`、`HUGE_SPREAD_VERIFY` 等风险标签；搜索命中不代表可交易，必须继续以 `Net fee adj.`、风险标签、双边成交额、资金费率周期和实际可成交价格判断。

## 工作区保护项

本任务未提交、删除或修改以下既有本地产物：

- `output/edge-profile-codex/`
- `output/dexe_bybit_bitget_chain_probe/`
- `output/floating-watch-*.png`
- `output/index-auto-watch-*.png`
- `script/dexe_bybit_bitget_chain.py`

服务器仍保留既有未跟踪运维文件：

- `.env.backup-codex-20260911-1425`
- `.env.backup-poll-8-20260911`
- `CACHED`

## 下一步建议

- 下一个功能模块请新建 Codex 任务，并同时提供根目录 `AGENTS.md`、`docs/task-handoff-2026-09-20-arbitrage-platform.md` 和本文件。
- 若继续改进 Lighter，建议单独处理持久 WebSocket、增量订单簿 nonce 校验和连接指标，不要与告警、新币或指数模块混在同一任务中。
