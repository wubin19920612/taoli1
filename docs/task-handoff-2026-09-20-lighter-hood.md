# 交接：Lighter Robinhood（HOOD）套利接入

日期：2026-09-20

## 目标与范围

本任务只处理 Astro、浮窗与 Lighter 路由模块中的 Robinhood `HOOD` 市场接入，不扩展其他交易所或功能模块。

验收目标：

- Lighter `HOOD` 即使不在成交额前 48 名，也必须进入自动套利扫描。
- 行情必须来自真实订单簿，包含 bid、ask、盘口数量、24h 成交额、资金费率及其周期。
- 与 Binance、Bitget 等同一规范标的 `HOODUSDT` 正确配对，并保留 Lighter 原始市场 `HOOD`。
- 可用路线继续使用 Astro `gc-lighter`，不创建普通 `lighter` 卡片。
- 完成测试、提交、推送、生产数据库备份、部署和线上接口验证。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`f6f41fd`（`docs: add modular handoff workflow`）
- 第一阶段提交：`4879e5f80d11637c5b708ccf796126c1c114f753`（保证 `HOOD` 占用 Lighter 自动扫描优先槽位）
- 最终功能提交：`f591fbb808d901d41d173dbf8bb481d60993d700`（Lighter 实时订单簿改用官方 WebSocket）
- 本交接文档所在提交仅更新文档；生产功能版本仍为 `f591fbb`。

## 已完成功能

- `HOOD` 被列为 Lighter 永续优先标的。自动扫描总量仍限制为 48，优先标的替换成交额榜末位，不额外增加扫描规模。
- 优先标的订阅顺序在其他市场之前；如果 Lighter 未返回 `HOOD` 盘口，本轮采集失败关闭，不再把缺少 `HOOD` 的结果静默标记为健康。
- 自动扫描、单市场订单簿校验和价差查询统一通过官方 `wss://mainnet.zklighter.elliot.ai/stream` 的 `order_book/{market_id}` 订阅获取真实盘口。
- 停止高频逐市场调用 `orderBookOrders` REST 接口，避免生产服务器出口触发 AWS WAF 人机验证。
- 同时兼容 REST 的 `remaining_base_amount` 和 WebSocket 的 `size` 深度字段，并保持调用方要求的订单簿档数上限。
- 明确声明直接依赖 `websockets>=13.0`。

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

## 重要业务规则

- Lighter 规范标的是 `HOODUSDT`，原始市场必须保留为 `HOOD`；不要把原始市场字段覆盖掉。
- Lighter Astro 路由仍只能使用 `gc-lighter`，且仅与已知 GC 或 Bitget 路由配对。
- 资金费率不能脱离周期比较。线上验收时 Lighter 为 1 小时周期，Bitget 为 8 小时周期。
- 套利判断继续使用真实 bid/ask。mark/index 仅用于风险信息，不能代替可成交价。
- 页面出现正价差不等于扣费后盈利。线上验收样本的 Lighter -> Bitget 开仓价差约 `0.036%`，但扣除手续费和滑点后的 `fee_adjusted_open_pct` 为负，不应视为交易建议。

## 本地验证

- Lighter 专项测试：`11 passed`。
- 后端全量测试：`715 passed, 11 warnings`，耗时约 4 分 25 秒。
- `python -m compileall`：通过。
- `git diff --check`：通过。
- Ruff：`backend/tests/test_lighter_adapter.py` 通过。
- 两个既有生产文件仍有 6 条历史 Ruff 告警：Lighter 的异常处理 3 条、`pair_spread_query.py` 的既有类型/异常处理 3 条；本任务没有扩大清理范围。
- 本机真实行情验证：Lighter 返回 48 个永续市场并包含 `HOODUSDT`；单标的查询返回真实 bid/ask、24h 成交额和 1 小时资金费率。

## 数据库备份

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

- 服务器使用 `git pull --ff-only` 更新到功能提交 `f591fbb`。
- `docker compose build --pull` 和 `up -d --remove-orphans` 成功；没有执行 `down -v`。
- 前端、后端容器均为 `healthy`，`/api/health` 返回 `status=ok`。
- 连续跨三个采集时点检查：冷启动首轮市场元数据遇到一次 405，下一轮自动恢复；之后两轮 `lighter=healthy`、失败次数为 0、错误为空，`HOOD` 均持续存在。
- `/api/markets?exchange=lighter&symbol=HOOD` 返回 `HOODUSDT`、`raw_symbol=HOOD`、非交叉 bid/ask、盘口数量、24h 成交额、资金费率和 1 小时周期。
- `/api/instruments/HOOD` 返回 8 个交易所、10 个市场，并生成 9 条含 Lighter 的实时路线（数量随行情变化）。
- `/api/opportunities?symbol=HOOD&exchange=lighter&include_risky=true` 返回自动机会；验收时 `lighter:HOOD -> bitget:HOODUSDT` 保留双方成交额和 `1h/8h` 资金费率周期。
- 对应 `/api/astro/preview/{opportunity_id}` 返回 `gc-lighter -> bitget`、类型 `FF`、`can_submit=true`、无 blocker；本任务只验证预览，没有实际创建卡片。
- 前端 3000 端口代理能返回同一 Lighter `HOOD` 市场。
- 部署后日志中 `orderBookOrders` 调用为 0，Lighter WebSocket 错误为 0。

## 已知问题与残余风险

- 冷启动第一轮 `orderBookDetails` 仍可能触发 Lighter AWS WAF 的 `405`；现有短冷却重试已在下一轮恢复。实时订单簿已迁移到 WebSocket，不再持续触发该 WAF。
- Gate 公告接口仍有交接基线记录的 4 类 HTTP `567`；与本任务无关，后端和其他交易所采集未受阻。
- WebSocket 当前为每轮建立连接、取得初始快照后关闭，不维护长期连接。现有 12 秒刷新周期线上稳定；若未来连接次数受限，可在单独 Lighter 性能任务中改为持久订阅和增量维护。
- Lighter 上游若不返回 `HOOD` 优先盘口，本轮会明确失败并进入采集器重试，不会用 mark/index 或陈旧估算价冒充可成交价。

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
